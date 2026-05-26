"""Semantic prompt cache backed by Qdrant + Modal-hosted embedder.

This module sits on the proxy hot path. The two contracts that matter:

  1. lookup() and store() never raise. A failed cache returns None or is
     dropped silently — the proxy then routes the request normally. Rule 5:
     Modal failure is silent.

  2. The 4-second Modal timeout is hard. asyncio.wait_for cancels the
     embed call past that bound so cold starts never block the proxy.

cache_threshold is read from proxy.config.current_rules at every lookup,
so the optimizer agent can tighten or relax the cache without a restart.
"""

import asyncio
import logging
import uuid

import modal
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from proxy import config
from proxy.config import settings

logger = logging.getLogger(__name__)

COLLECTION_NAME = "prompt_cache"
VECTOR_DIM = 384
MODAL_TIMEOUT_SEC = 8.0

# Fixed UUID namespace so the same prompt always maps to the same point ID.
# An upsert with a duplicate prompt then overwrites in place rather than
# accumulating near-duplicate vectors in the index.
_POINT_NAMESPACE = uuid.UUID("8c3a5c00-0000-4000-8000-000000000001")

_client: AsyncQdrantClient | None = None
_embed_fn: modal.Function | None = None


def _qdrant() -> AsyncQdrantClient:
    """Module-level Qdrant client singleton. The client is lazy — no
    network call until an operation is invoked."""
    global _client
    if _client is None:
        _client = AsyncQdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key,
        )
    return _client


def _embedder() -> modal.Function:
    """Resolve the deployed Modal embedder once per process."""
    global _embed_fn
    if _embed_fn is None:
        _embed_fn = modal.Function.from_name(settings.modal_embedder_app, "embed")
    return _embed_fn


def _point_id(prompt: str) -> str:
    return str(uuid.uuid5(_POINT_NAMESPACE, prompt))


async def _embed(prompt: str) -> list[float] | None:
    """Embed a single prompt via Modal with a hard timeout.

    Returns None on timeout or any other failure. Never raises — callers
    must handle None as 'cache unavailable, route normally'.
    """
    try:
        result = await asyncio.wait_for(
            _embedder().remote.aio([prompt]),
            timeout=MODAL_TIMEOUT_SEC,
        )
        return result[0]
    except asyncio.TimeoutError:
        logger.warning(
            "modal embed timed out — skipping cache",
            extra={"timeout_sec": MODAL_TIMEOUT_SEC},
        )
        return None
    except Exception as exc:
        logger.warning(
            "modal embed failed — skipping cache",
            extra={"error": str(exc)},
        )
        return None


async def ensure_collection() -> None:
    """Idempotent Qdrant collection setup. Called from main.py's lifespan."""
    client = _qdrant()
    existing = await client.get_collections()
    if COLLECTION_NAME in {c.name for c in existing.collections}:
        logger.info(
            "qdrant collection present",
            extra={"collection": COLLECTION_NAME},
        )
        return
    await client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=VectorParams(size=VECTOR_DIM, distance=Distance.COSINE),
    )
    logger.info(
        "qdrant collection created",
        extra={"collection": COLLECTION_NAME, "dim": VECTOR_DIM},
    )


async def lookup(prompt: str) -> dict | None:
    """Semantic cache lookup.

    Returns the cached payload on a hit, None on a miss or any failure
    (Modal timeout, Qdrant error, no neighbour above the active threshold).
    Reads the threshold from config.current_rules at call time so the
    agent's hot-reloaded rules take effect on the very next request.
    """
    vector = await _embed(prompt)
    if vector is None:
        return None

    try:
        response = await _qdrant().query_points(
            collection_name=COLLECTION_NAME,
            query=vector,
            limit=1,
            score_threshold=config.current_rules.cache_threshold,
        )
    except Exception as exc:
        logger.warning("qdrant lookup failed", extra={"error": str(exc)})
        return None

    if not response.points:
        return None
    return dict(response.points[0].payload or {})


async def store(
    prompt: str,
    response: str,
    model: str,
    tokens_in: int,
    tokens_out: int,
) -> None:
    """Embed and upsert (prompt, response) into Qdrant.

    Always invoked via asyncio.create_task() from the proxy hot path — must
    not raise. A dropped write only costs one redundant LLM call on the
    next near-identical request.
    """
    try:
        vector = await _embed(prompt)
        if vector is None:
            logger.warning("skipping cache store — embed unavailable")
            return

        await _qdrant().upsert(
            collection_name=COLLECTION_NAME,
            points=[
                PointStruct(
                    id=_point_id(prompt),
                    vector=vector,
                    payload={
                        "prompt": prompt,
                        "response": response,
                        "model": model,
                        "tokens_in": tokens_in,
                        "tokens_out": tokens_out,
                    },
                )
            ],
        )
    except Exception as exc:
        logger.warning("cache store failed", extra={"error": str(exc)})
