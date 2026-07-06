"""Quality sampling tool.

Pulls N unscored requests from the ledger and asks Haiku to judge whether
the routing decision was appropriate for the prompt. Writes the resulting
score back to `requests.quality_score` so the dashboard and the
route_optimize back-test have signal to work with.

LIMITATION (v1): the `requests` table does not store the actual response
text, so the judge cannot evaluate response quality directly. Instead it
judges *routing appropriateness* — given this prompt and this model tier,
was the choice reasonable? This is a useful proxy:
  - if Haiku consistently handled prompts judged as 'high complexity' poorly,
    the score drops, route_optimize sees low low-tier quality and stops
    widening the band
  - if Opus consistently handled prompts that look 'low complexity', the
    score drops slightly (overspend) — flags overprovisioning to the dashboard

Adding a `response TEXT` column to `requests` would let this become a true
response-quality judge. Tracked as v1.1 work.
"""

import asyncio
import logging

import asyncpg
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from proxy.config import settings

logger = logging.getLogger(__name__)

# OpenRouter-style model identifier — keeps every LLM call routed through
# OpenRouter for unified billing and to avoid pulling in langchain-anthropic.
_JUDGE_MODEL = "anthropic/claude-haiku-4-5"
_DEFAULT_SAMPLE_SIZE = 20
_LOW_QUALITY_THRESHOLD = 0.60


class QualityJudgement(BaseModel):
    """Structured judge output. The Literal-equivalent constraint on score
    (ge=0.0, le=1.0) keeps malformed LLM outputs out of the ledger."""

    score: float
    reason: str


_PROMPT = (
    "You are evaluating a routing decision made by an LLM proxy.\n"
    "The proxy uses three model tiers based on prompt complexity:\n"
    "  - low  → claude-haiku-4-5  (cheap, fast, simple tasks)\n"
    "  - mid  → claude-sonnet-4-6 (balanced, moderate reasoning)\n"
    "  - high → claude-opus-4-6   (full power, complex reasoning)\n"
    "An ideal routing decision uses the cheapest tier that can handle the\n"
    "prompt well. Over-provisioning (Opus for trivial work) wastes money;\n"
    "under-provisioning (Haiku for complex reasoning) sacrifices quality.\n\n"
    "Score 0.0 = clearly wrong tier.\n"
    "Score 1.0 = ideal tier.\n\n"
    "Prompt (first 120 chars):\n{prompt_snip}\n\n"
    "Model used: {model}\n"
    "Tier:       {tier}\n\n"
    "Return the score and a one-line reason."
)


_chain = None


def _get_chain():
    global _chain
    if _chain is None:
        llm = ChatOpenAI(
            model=_JUDGE_MODEL,
            api_key=settings.openrouter_api_key,
            base_url=settings.openrouter_base_url,
            timeout=15,
            max_retries=0,
        )
        _chain = llm.with_structured_output(QualityJudgement)
    return _chain


async def _run_async(n: int) -> dict[str, object]:
    chain = _get_chain()
    conn = await asyncpg.connect(settings.database_url)
    try:
        rows = await conn.fetch(
            """
            SELECT id, prompt_snip, model, tier
            FROM requests
            WHERE quality_score IS NULL
              AND tier IN ('low', 'mid', 'high')  -- skip cache hits
            ORDER BY ts DESC
            LIMIT $1
            """,
            n,
        )

        if not rows:
            return {
                "tool": "quality_sample",
                "action": "no_change",
                "sampled": 0,
                "avg_score": 0.0,
                "low_quality_count": 0,
                "trigger": "no unscored requests in window",
            }

        scores: list[float] = []
        low_count = 0
        for row in rows:
            try:
                judgement: QualityJudgement = await chain.ainvoke(
                    _PROMPT.format(
                        prompt_snip=row["prompt_snip"] or "",
                        model=row["model"],
                        tier=row["tier"],
                    )
                )
            except Exception as exc:
                logger.warning(
                    "quality judge failed: %s", exc,
                    extra={"request_db_id": row["id"]},
                )
                continue

            score = max(0.0, min(1.0, float(judgement.score)))
            await conn.execute(
                "UPDATE requests SET quality_score = $1 WHERE id = $2",
                score,
                row["id"],
            )
            scores.append(score)
            if score < _LOW_QUALITY_THRESHOLD:
                low_count += 1

        avg = sum(scores) / len(scores) if scores else 0.0
        return {
            "tool": "quality_sample",
            "action": "score_sample",
            "sampled": len(scores),
            "avg_score": round(avg, 3),
            "low_quality_count": low_count,
            "trigger": f"sampled {len(scores)} unscored requests",
        }
    finally:
        await conn.close()


def run(n: int = _DEFAULT_SAMPLE_SIZE) -> dict[str, object]:
    """Sample up to n unscored requests, judge them, persist the scores.

    Sync entry point — bridges to the async helper via asyncio.run because
    the rest of the agent graph runs sync per CLAUDE.md.
    """
    return asyncio.run(_run_async(n))
