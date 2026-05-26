"""Proxy entrypoint: FastAPI app, lifespan, /health, /v1/chat/completions.

Hot-path orchestration only. Business logic lives in:
  - proxy.cache         semantic cache (Qdrant + Modal)
  - proxy.classifier    complexity classifier
  - proxy.router        model selection + cost arithmetic
  - proxy.ledger        Postgres writes
  - proxy.config        env + in-memory routing_rules
"""

import asyncio
import json
import logging
import os
import sys
import time
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Header, HTTPException, status
from langchain_openai import ChatOpenAI
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
)
from langfuse.langchain import CallbackHandler as LangfuseCallbackHandler
from pydantic import BaseModel

from proxy import cache, classifier, config, ledger, router
from proxy.config import settings


# --------------------------------------------------------------------- logging
class _JsonFormatter(logging.Formatter):
    """One-line JSON formatter. Promotes `extra=` keys to top-level JSON."""

    _RESERVED = {
        "name", "msg", "args", "levelname", "levelno", "pathname",
        "filename", "module", "exc_info", "exc_text", "stack_info",
        "lineno", "funcName", "created", "msecs", "relativeCreated",
        "thread", "threadName", "processName", "process", "message",
        "asctime", "taskName",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in self._RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def _setup_logging() -> None:
    root = logging.getLogger()
    if root.handlers:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    root.addHandler(handler)
    root.setLevel(logging.INFO)


_setup_logging()
logger = logging.getLogger(__name__)


# -------------------------------------------------------------------- lifespan
@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Bring up external dependencies; reverse the order on shutdown."""
    # Langfuse SDK reads its config from env vars; populate them from Settings
    # here (the one place where os.environ assignment is sanctioned — required
    # by langfuse>=4's OTel-based init). If keys are absent the handler is
    # never constructed and instrumentation is a no-op.
    if settings.langfuse_public_key and settings.langfuse_secret_key:
        os.environ["LANGFUSE_PUBLIC_KEY"] = settings.langfuse_public_key
        os.environ["LANGFUSE_SECRET_KEY"] = settings.langfuse_secret_key
        os.environ["LANGFUSE_HOST"] = settings.langfuse_host
        global _langfuse_handler
        _langfuse_handler = LangfuseCallbackHandler()
        logger.info("langfuse handler initialised", extra={"host": settings.langfuse_host})

    await ledger.init_pool()
    await cache.ensure_collection()

    initial = await ledger.get_latest_rules()
    config.current_rules.__dict__.update(initial.__dict__)
    logger.info("routing_rules bootstrapped", extra={"rules_id": initial.id})

    reload_task = asyncio.create_task(
        config.reload_rules_loop(), name="reload_rules_loop"
    )
    logger.info("proxy ready", extra={"rules_id": initial.id})

    try:
        yield
    finally:
        reload_task.cancel()
        try:
            await reload_task
        except asyncio.CancelledError:
            pass
        await ledger.close_pool()
        logger.info("proxy shut down")


app = FastAPI(title="TokenOps Proxy", lifespan=lifespan)


# --------------------------------------------------- request / response models
class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    tag: str | None = None
    # `model` accepted for OpenAI-API compatibility but ignored — the proxy
    # chooses based on the classifier tier.
    model: str | None = None


class ChatChoiceMessage(BaseModel):
    role: str
    content: str


class ChatChoice(BaseModel):
    index: int
    message: ChatChoiceMessage
    finish_reason: str


class ChatResponse(BaseModel):
    choices: list[ChatChoice]
    model: str
    tier: str
    cost_usd: float
    cached: bool
    request_id: str


# --------------------------------------------------------------------- helpers
_llm_cache: dict[str, ChatOpenAI] = {}

# Langfuse callback handler — constructed once in lifespan if credentials
# are set, otherwise stays None and instrumentation is a no-op.
_langfuse_handler: LangfuseCallbackHandler | None = None

# Strong references to background tasks so the asyncio scheduler does not
# GC them mid-flight (event loop only holds weak refs to tasks).
_background_tasks: set[asyncio.Task] = set()


def _fire_and_forget(coro) -> None:
    """Schedule a coroutine and keep a strong reference until completion."""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def _llm_for(model: str) -> ChatOpenAI:
    """Cache one ChatOpenAI instance per model. Creating a new instance
    per request would defeat the SDK's internal connection pooling."""
    if model not in _llm_cache:
        _llm_cache[model] = ChatOpenAI(
            model=model,
            api_key=settings.openrouter_api_key,
            base_url=settings.openrouter_base_url,
            timeout=60,
            max_retries=0,
        )
    return _llm_cache[model]


def _to_lc_messages(messages: list[ChatMessage]) -> list[BaseMessage]:
    """Convert OpenAI-style {role, content} dicts to LangChain message
    objects. Unknown roles fall back to HumanMessage."""
    converted: list[BaseMessage] = []
    for m in messages:
        if m.role == "system":
            converted.append(SystemMessage(content=m.content))
        elif m.role == "assistant":
            converted.append(AIMessage(content=m.content))
        else:
            converted.append(HumanMessage(content=m.content))
    return converted


def _prompt_key(messages: list[ChatMessage]) -> str:
    """Single string representing the conversation, used as the cache key
    and the classifier input. Role labels are included so an identical user
    message in different contexts cannot collide in the cache."""
    return "\n".join(f"{m.role}: {m.content}" for m in messages)


def _single_choice(content: str) -> list[ChatChoice]:
    return [
        ChatChoice(
            index=0,
            message=ChatChoiceMessage(role="assistant", content=content),
            finish_reason="stop",
        )
    ]


# ---------------------------------------------------------------------- routes
@app.get("/health")
async def health() -> dict[str, object]:
    return {"status": "ok", "rules_version": config.current_rules.id}


@app.post("/v1/chat/completions", response_model=ChatResponse)
async def chat_completions(
    req: ChatRequest,
    x_tag: str | None = Header(default=None, alias="X-Tag"),
) -> ChatResponse:
    request_id = str(uuid.uuid4())
    tag = req.tag or x_tag or "default"
    prompt = _prompt_key(req.messages)
    start = time.perf_counter()

    try:
        # 1. Cache lookup. Returns None on miss, timeout, or any failure —
        #    never raises (Rule 5).
        cached = await cache.lookup(prompt)
        if cached is not None:
            latency_ms = (time.perf_counter() - start) * 1000.0
            # TODO: pull tier from cache payload once cache.store accepts tier param
            _fire_and_forget(
                ledger.log_request(
                    request_id=request_id,
                    prompt=prompt,
                    tag=tag,
                    model=cached["model"],
                    tier="cache",
                    tokens_in=0,
                    tokens_out=0,
                    cost_usd=0.0,
                    counterfactual_cost_usd=router.counterfactual_cost(
                        cached.get("tokens_in", 0),
                        cached.get("tokens_out", 0),
                    ),
                    cached=True,
                    latency_ms=latency_ms,
                )
            )
            logger.info(
                "served from cache",
                extra={
                    "request_id": request_id,
                    "tag": tag,
                    "model": cached["model"],
                    "cached": True,
                    "latency_ms": latency_ms,
                },
            )
            return ChatResponse(
                choices=_single_choice(cached["response"]),
                model=cached["model"],
                tier="cache",
                cost_usd=0.0,
                cached=True,
                request_id=request_id,
            )

        # 2. Classify and pick a model.
        tier = await classifier.classify(prompt)
        model = router.select_model(tier)

        # 3. LLM call.
        callbacks = [_langfuse_handler] if _langfuse_handler else []
        result = await _llm_for(model).ainvoke(
            _to_lc_messages(req.messages),
            config={
                "callbacks": callbacks,
                "metadata": {
                    "request_id": request_id,
                    "tag": tag,
                    "tier": tier,
                    "model": model,
                },
            },
        )
        content: str = result.content  # langchain returns str for plain chat
        usage = result.usage_metadata or {}
        tokens_in = int(usage.get("input_tokens", 0))
        tokens_out = int(usage.get("output_tokens", 0))

        cost = router.compute_cost(model, tokens_in, tokens_out)
        counterfactual = router.counterfactual_cost(tokens_in, tokens_out)
        latency_ms = (time.perf_counter() - start) * 1000.0

        # 4. Fire-and-forget writes — must not add latency to the response.
        _fire_and_forget(
            cache.store(prompt, content, model, tokens_in, tokens_out)
        )
        _fire_and_forget(
            ledger.log_request(
                request_id=request_id,
                prompt=prompt,
                tag=tag,
                model=model,
                tier=tier,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_usd=cost,
                counterfactual_cost_usd=counterfactual,
                cached=False,
                latency_ms=latency_ms,
            )
        )

        logger.info(
            "llm call complete",
            extra={
                "request_id": request_id,
                "tag": tag,
                "model": model,
                "tier": tier,
                "cached": False,
                "latency_ms": latency_ms,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "cost_usd": cost,
            },
        )

        return ChatResponse(
            choices=_single_choice(content),
            model=model,
            tier=tier,
            cost_usd=cost,
            cached=False,
            request_id=request_id,
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "request failed",
            extra={"request_id": request_id, "tag": tag, "error": str(exc)},
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": "upstream_failed", "detail": str(exc)},
        )
