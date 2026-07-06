"""LangGraph optimizer agent — observe → analyse → validate → apply.

Runs every AGENT_RUN_INTERVAL_MINUTES via agent.scheduler. Communicates
with the proxy exclusively through the routing_rules and agent_decisions
Postgres tables — never shares in-process state with the data plane.

Safety bounds are hard-coded constants in this module. They cannot be
overridden by the LLM analyser, by tool proposals, or by the agent's own
state — validate_node enforces them every run. Per tech.md, the agent is
allowed to retune within these bounds but never outside.

All node functions are sync per CLAUDE.md. DB I/O bridges to asyncpg via
asyncio.run; LLM calls use sync chain.invoke. The cost of asyncio.run
per query is negligible at this run frequency (every 15 minutes).
Switch to a persistent async context per run if query count per run
exceeds 50.
"""

import asyncio
import json
import logging
import uuid
from typing import Literal, TypedDict

import asyncpg
from langchain_openai import ChatOpenAI
from langfuse.langchain import CallbackHandler as LangfuseCallbackHandler
from langgraph.graph import END, StateGraph
# LangGraph 0.2.x — update import path if upgrading
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel

from agent.tools import cache_tune, quality_sample, route_optimize
from proxy.config import settings

logger = logging.getLogger(__name__)


# --------------------------------------------------------------- safety bounds
# These are the hard limits the agent is allowed to operate within. Values
# pulled from tech.md / structure.md. Treat as constants — never read from
# config, never mutate. validate_node rejects any proposal that exits the bound.

CACHE_THRESHOLD_RANGE: tuple[float, float] = (0.85, 0.97)
LOW_MAX_TOKENS_RANGE: tuple[int, int] = (200, 500)
HIGH_MIN_TOKENS_RANGE: tuple[int, int] = (600, 1200)
MAX_QUALITY_DROP_PCT: float = 0.05

_BACKTEST_WINDOW = 500
_OBSERVE_WINDOW_HOURS_DEFAULT = 24
_ANALYSIS_MODEL = "anthropic/claude-haiku-4-5"


# --------------------------------------------------------------------- state
class OptimizerState(TypedDict, total=False):
    run_id: str
    window_hours: int
    stats: dict[str, object]
    proposals: list[dict[str, object]]
    validated_proposals: list[dict[str, object]]
    reasoning: str
    applied_rules: dict[str, object]


# ----------------------------------------------------- analyser structured out
class AnalysisPlan(BaseModel):
    """LLM-emitted plan for which tools to invoke this run."""

    tools_to_run: list[Literal["cache_tune", "route_optimize", "quality_sample"]]
    reasoning: str


_analysis_chain = None
_langfuse_handler: LangfuseCallbackHandler | None = None


def _get_analysis_chain():
    global _analysis_chain
    if _analysis_chain is None:
        llm = ChatOpenAI(
            model=_ANALYSIS_MODEL,
            api_key=settings.openrouter_api_key,
            base_url=settings.openrouter_base_url,
            timeout=15,
            max_retries=0,
        )
        _analysis_chain = llm.with_structured_output(AnalysisPlan)
    return _analysis_chain


def _get_langfuse() -> LangfuseCallbackHandler | None:
    """Lazy Langfuse handler. Returns None if creds are absent.

    The agent runs as a Modal scheduled function, so there's no FastAPI
    lifespan to hook — initialise on first use instead. The env-var write
    here is the sanctioned counterpart to the proxy's lifespan init."""
    global _langfuse_handler
    if _langfuse_handler is not None:
        return _langfuse_handler
    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        return None
    import os
    os.environ["LANGFUSE_PUBLIC_KEY"] = settings.langfuse_public_key
    os.environ["LANGFUSE_SECRET_KEY"] = settings.langfuse_secret_key
    os.environ["LANGFUSE_HOST"] = settings.langfuse_host
    _langfuse_handler = LangfuseCallbackHandler()
    return _langfuse_handler


# ------------------------------------------------------------- db helpers (async)
async def _fetch_stats_async(window_hours: int) -> dict[str, object]:
    """Aggregate the recent window of requests for the agent to reason about.

    Excludes cache hits (tier='cache') from per-tier metrics — they don't
    represent a routing decision the agent can act on. cache_hit_rate is
    computed across all rows including cache hits.
    """
    conn = await asyncpg.connect(settings.database_url)
    try:
        row = await conn.fetchrow(
            """
            SELECT
                COUNT(*)::int                                                    AS total,
                COALESCE(SUM(CASE WHEN cached THEN 1 ELSE 0 END), 0)::int        AS hits,
                COUNT(*) FILTER (WHERE tier = 'low')::int                        AS vol_low,
                COUNT(*) FILTER (WHERE tier = 'mid')::int                        AS vol_mid,
                COUNT(*) FILTER (WHERE tier = 'high')::int                       AS vol_high,
                AVG(quality_score) FILTER (WHERE tier = 'low'  AND quality_score IS NOT NULL) AS q_low,
                AVG(quality_score) FILTER (WHERE tier = 'mid'  AND quality_score IS NOT NULL) AS q_mid,
                AVG(quality_score) FILTER (WHERE tier = 'high' AND quality_score IS NOT NULL) AS q_high,
                AVG(cost_usd) FILTER (WHERE tier = 'low')                        AS cost_low,
                AVG(cost_usd) FILTER (WHERE tier = 'mid')                        AS cost_mid,
                AVG(cost_usd) FILTER (WHERE tier = 'high')                       AS cost_high,
                COUNT(*) FILTER (WHERE quality_score IS NULL
                                   AND tier IN ('low','mid','high'))::int       AS unscored
            FROM requests
            WHERE ts > NOW() - ($1::text || ' hours')::interval
            """,
            str(window_hours),
        )
        rules_row = await conn.fetchrow(
            """
            SELECT id, cache_threshold, low_max_tokens, high_min_tokens
            FROM routing_rules
            ORDER BY id DESC
            LIMIT 1
            """
        )
    finally:
        await conn.close()

    total = int(row["total"] or 0)
    hits = int(row["hits"] or 0)
    return {
        "window_hours": window_hours,
        "total_requests": total,
        "cache_hits": hits,
        "cache_hit_rate": (hits / total) if total else 0.0,
        "volume_by_tier": {
            "low": int(row["vol_low"] or 0),
            "mid": int(row["vol_mid"] or 0),
            "high": int(row["vol_high"] or 0),
        },
        "quality_by_tier": {
            "low":  float(row["q_low"])  if row["q_low"]  is not None else None,
            "mid":  float(row["q_mid"])  if row["q_mid"]  is not None else None,
            "high": float(row["q_high"]) if row["q_high"] is not None else None,
        },
        "cost_by_tier": {
            "low":  float(row["cost_low"]  or 0.0),
            "mid":  float(row["cost_mid"]  or 0.0),
            "high": float(row["cost_high"] or 0.0),
        },
        "unscored_requests": int(row["unscored"] or 0),
        "current_rules": {
            "id": int(rules_row["id"]) if rules_row else 0,
            "cache_threshold": float(rules_row["cache_threshold"]) if rules_row else 0.92,
            "low_max_tokens": int(rules_row["low_max_tokens"]) if rules_row else 15,
            "high_min_tokens": int(rules_row["high_min_tokens"]) if rules_row else 40,
        },
    }


async def _fetch_backtest_rows_async() -> list[dict[str, object]]:
    """Pull the last N requests with their scored quality and prompt snip
    for the back-test in validate_node. Skips cache hits."""
    conn = await asyncpg.connect(settings.database_url)
    try:
        rows = await conn.fetch(
            """
            SELECT id, prompt_snip, tier, cached, quality_score
            FROM requests
            WHERE tier IN ('low', 'mid', 'high')
            ORDER BY ts DESC
            LIMIT $1
            """,
            _BACKTEST_WINDOW,
        )
    finally:
        await conn.close()
    return [dict(r) for r in rows]


async def _insert_routing_rules_async(rules: dict[str, object], notes: str) -> int:
    conn = await asyncpg.connect(settings.database_url)
    try:
        new_id = await conn.fetchval(
            """
            INSERT INTO routing_rules (cache_threshold, low_max_tokens, high_min_tokens, notes)
            VALUES ($1, $2, $3, $4)
            RETURNING id
            """,
            float(rules["cache_threshold"]),
            int(rules["low_max_tokens"]),
            int(rules["high_min_tokens"]),
            notes,
        )
    finally:
        await conn.close()
    return int(new_id)


async def _insert_agent_decision_async(
    run_id: str,
    tool_used: str,
    observation: str,
    proposal: str,
    validated: bool,
    applied: bool,
    reasoning: str,
) -> None:
    conn = await asyncpg.connect(settings.database_url)
    try:
        await conn.execute(
            """
            INSERT INTO agent_decisions
                (run_id, tool_used, observation, proposal, validated, applied, reasoning)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            run_id, tool_used, observation, proposal, validated, applied, reasoning,
        )
    finally:
        await conn.close()


# -------------------------------------------------------------- node functions
def observe_node(state: OptimizerState) -> OptimizerState:
    window_hours = state.get("window_hours", _OBSERVE_WINDOW_HOURS_DEFAULT)
    stats = asyncio.run(_fetch_stats_async(window_hours))
    logger.info(
        "observe complete",
        extra={
            "run_id": state.get("run_id"),
            "total_requests": stats["total_requests"],
            "cache_hit_rate": stats["cache_hit_rate"],
            "unscored": stats["unscored_requests"],
        },
    )
    return OptimizerState(stats=stats)


def analyse_node(state: OptimizerState) -> OptimizerState:
    stats = state.get("stats", {})

    try:
        handler = _get_langfuse()
        callbacks = [handler] if handler else []
        plan: AnalysisPlan = _get_analysis_chain().invoke(
            _ANALYSIS_INSTRUCTIONS.format(stats=json.dumps(stats, indent=2, default=str)),
            config={
                "callbacks": callbacks,
                "metadata": {
                    "run_id": state.get("run_id"),
                    "component": "optimizer.analyse",
                },
            },
        )
        tools = list(plan.tools_to_run)
        reasoning = plan.reasoning
    except Exception as exc:
        logger.warning(
            "analyser LLM failed — running all tools as fallback",
            extra={"run_id": state.get("run_id"), "error": str(exc)},
        )
        tools = ["cache_tune", "route_optimize", "quality_sample"]
        reasoning = f"LLM analysis failed ({exc}); ran all tools defensively"

    current = stats.get("current_rules", {})
    proposals: list[dict[str, object]] = []

    for tool_name in tools:
        if tool_name == "cache_tune":
            proposals.append(
                cache_tune.propose(
                    hit_rate=float(stats.get("cache_hit_rate", 0.0)),
                    current_threshold=float(current.get("cache_threshold", 0.92)),
                )
            )
        elif tool_name == "route_optimize":
            proposals.append(
                route_optimize.propose(
                    quality_by_tier=stats.get("quality_by_tier", {}),
                    volume_by_tier=stats.get("volume_by_tier", {}),
                    current_low_max=int(current.get("low_max_tokens", 15)),
                    current_high_min=int(current.get("high_min_tokens", 40)),
                )
            )
        elif tool_name == "quality_sample":
            proposals.append(quality_sample.run())

    logger.info(
        "analyse complete",
        extra={
            "run_id": state.get("run_id"),
            "tools_run": tools,
            "proposal_count": len(proposals),
        },
    )
    return OptimizerState(proposals=proposals, reasoning=reasoning)


_ANALYSIS_INSTRUCTIONS = (
    "You are the TokenOps optimizer agent. Based on the observation stats "
    "below, decide which tools to invoke this run.\n\n"
    "Available tools:\n"
    "  - cache_tune       adjusts the semantic cache cosine threshold; "
    "useful when cache_hit_rate is below 0.30 or above 0.70.\n"
    "  - route_optimize   widens the low tier when its quality is strong "
    "and volume is sufficient. Useful when quality_by_tier.low is high.\n"
    "  - quality_sample   scores unscored requests via LLM-as-judge. "
    "Useful when unscored_requests is large; required input for route_optimize "
    "to ever have evidence to act on.\n\n"
    "Stats:\n{stats}\n\n"
    "Return the tools to run and brief reasoning."
)


def validate_node(state: OptimizerState) -> OptimizerState:
    proposals = state.get("proposals", [])
    if not proposals:
        return OptimizerState(validated_proposals=[])

    backtest_rows = asyncio.run(_fetch_backtest_rows_async())
    validated: list[dict[str, object]] = []
    annotated_proposals: list[dict[str, object]] = []

    for proposal in proposals:
        tool = proposal.get("tool")
        if tool == "cache_tune":
            verdict = _validate_cache_proposal(proposal, backtest_rows)
        elif tool == "route_optimize":
            verdict = _validate_route_proposal(proposal, backtest_rows)
        elif tool == "quality_sample":
            verdict = {
                "valid": True,
                "reason": "observational tool — writes scores, does not change rules",
            }
        else:
            verdict = {"valid": False, "reason": f"unknown tool: {tool!r}"}

        proposal_with_verdict = {**proposal, **{"validation": verdict}}
        annotated_proposals.append(proposal_with_verdict)
        if verdict["valid"]:
            validated.append(proposal_with_verdict)

    logger.info(
        "validate complete",
        extra={
            "run_id": state.get("run_id"),
            "proposals": len(proposals),
            "validated": len(validated),
        },
    )
    return OptimizerState(
        proposals=annotated_proposals,
        validated_proposals=validated,
    )


def _validate_cache_proposal(
    proposal: dict[str, object],
    backtest_rows: list[dict[str, object]],
) -> dict[str, object]:
    """Hard bound on CACHE_THRESHOLD_RANGE, plus a data-driven sanity check:
    if cached responses have systematically lower quality than uncached ones
    and the proposal lowers the threshold (which would produce more hits),
    reject."""
    proposed = float(proposal["proposed_threshold"])
    lo, hi = CACHE_THRESHOLD_RANGE
    if not (lo <= proposed <= hi):
        return {
            "valid": False,
            "reason": f"proposed_threshold {proposed:.3f} outside {CACHE_THRESHOLD_RANGE}",
        }

    if proposal["action"] == "lower":
        cached_qs = [
            float(r["quality_score"]) for r in backtest_rows
            if r["cached"] and r["quality_score"] is not None
        ]
        uncached_qs = [
            float(r["quality_score"]) for r in backtest_rows
            if not r["cached"] and r["quality_score"] is not None
        ]
        if cached_qs and uncached_qs:
            cached_avg = sum(cached_qs) / len(cached_qs)
            uncached_avg = sum(uncached_qs) / len(uncached_qs)
            if cached_avg < uncached_avg * (1 - MAX_QUALITY_DROP_PCT):
                return {
                    "valid": False,
                    "reason": (
                        f"lowering threshold would expand cache use, but cached "
                        f"quality {cached_avg:.3f} is more than "
                        f"{MAX_QUALITY_DROP_PCT:.0%} below uncached {uncached_avg:.3f}"
                    ),
                }

    return {
        "valid": True,
        "reason": f"within bounds {CACHE_THRESHOLD_RANGE} and no quality concern",
    }


def _validate_route_proposal(
    proposal: dict[str, object],
    backtest_rows: list[dict[str, object]],
) -> dict[str, object]:
    """Range check both bands; then back-test by projecting how each
    historical request would re-tier under the new bands and what the
    projected average quality would be."""
    new_low = int(proposal["proposed_low_max"])
    new_high = int(proposal["proposed_high_min"])

    lo_lo, lo_hi = LOW_MAX_TOKENS_RANGE
    if not (lo_lo <= new_low <= lo_hi):
        return {
            "valid": False,
            "reason": f"proposed_low_max {new_low} outside {LOW_MAX_TOKENS_RANGE}",
        }
    hi_lo, hi_hi = HIGH_MIN_TOKENS_RANGE
    if not (hi_lo <= new_high <= hi_hi):
        return {
            "valid": False,
            "reason": f"proposed_high_min {new_high} outside {HIGH_MIN_TOKENS_RANGE}",
        }
    if new_low >= new_high:
        return {
            "valid": False,
            "reason": f"low_max ({new_low}) must be < high_min ({new_high})",
        }

    if proposal["action"] == "no_change":
        return {"valid": True, "reason": "no-op proposal"}

    # Back-test: project tier reassignment for each historical request and
    # compute projected avg quality, assuming each request retiered into the
    # new tier inherits that tier's historical average quality.
    scored = [r for r in backtest_rows if r["quality_score"] is not None]
    if not scored:
        return {
            "valid": True,
            "reason": "no scored historical requests — accepting on bounds alone",
        }

    avg_q_by_tier: dict[str, float] = {}
    for tier in ("low", "mid", "high"):
        tier_scores = [float(r["quality_score"]) for r in scored if r["tier"] == tier]
        if tier_scores:
            avg_q_by_tier[tier] = sum(tier_scores) / len(tier_scores)

    current_avg = sum(float(r["quality_score"]) for r in scored) / len(scored)

    def _project_tier(words: int) -> str:
        if words <= new_low:
            return "low"
        if words >= new_high:
            return "high"
        return "mid"

    projected: list[float] = []
    for r in scored:
        words = len((r["prompt_snip"] or "").split())
        new_tier = _project_tier(words)
        if new_tier == r["tier"]:
            projected.append(float(r["quality_score"]))
        elif new_tier in avg_q_by_tier:
            projected.append(avg_q_by_tier[new_tier])
        else:
            projected.append(float(r["quality_score"]))

    projected_avg = sum(projected) / len(projected)
    drop = current_avg - projected_avg
    drop_pct = (drop / current_avg) if current_avg else 0.0

    if drop_pct > MAX_QUALITY_DROP_PCT:
        return {
            "valid": False,
            "current_avg_quality": round(current_avg, 4),
            "projected_avg_quality": round(projected_avg, 4),
            "drop_pct": round(drop_pct, 4),
            "reason": (
                f"projected quality drop {drop_pct:.2%} exceeds bound "
                f"{MAX_QUALITY_DROP_PCT:.0%}"
            ),
        }

    return {
        "valid": True,
        "current_avg_quality": round(current_avg, 4),
        "projected_avg_quality": round(projected_avg, 4),
        "drop_pct": round(drop_pct, 4),
        "reason": (
            f"projected quality drop {drop_pct:.2%} within bound "
            f"{MAX_QUALITY_DROP_PCT:.0%}"
        ),
    }


def apply_node(state: OptimizerState) -> OptimizerState:
    run_id = state["run_id"]
    proposals = state.get("proposals", [])
    validated = state.get("validated_proposals", [])
    stats = state.get("stats", {})
    reasoning = state.get("reasoning", "")

    current = dict(stats.get("current_rules", {}))
    new_rules = {
        "cache_threshold": float(current.get("cache_threshold", 0.92)),
        "low_max_tokens": int(current.get("low_max_tokens", 15)),
        "high_min_tokens": int(current.get("high_min_tokens", 40)),
    }

    # Fold validated, non-no_change proposals into the new rule set.
    rules_changed = False
    for proposal in validated:
        if proposal["action"] == "no_change":
            continue
        if proposal["tool"] == "cache_tune":
            new_rules["cache_threshold"] = float(proposal["proposed_threshold"])
            rules_changed = True
        elif proposal["tool"] == "route_optimize":
            new_rules["low_max_tokens"] = int(proposal["proposed_low_max"])
            new_rules["high_min_tokens"] = int(proposal["proposed_high_min"])
            rules_changed = True
        # quality_sample is observational — never changes rules.

    new_rules_id: int | None = None
    if rules_changed:
        new_rules_id = asyncio.run(
            _insert_routing_rules_async(
                new_rules,
                notes=f"agent run {run_id}",
            )
        )

    # Audit every proposal regardless of outcome.
    observation_json = json.dumps(stats, default=str)
    for proposal in proposals:
        verdict = proposal.get("validation", {})
        was_validated = bool(verdict.get("valid", False))
        was_applied = (
            was_validated
            and proposal["action"] != "no_change"
            and proposal["tool"] in ("cache_tune", "route_optimize")
            and rules_changed
        )
        asyncio.run(
            _insert_agent_decision_async(
                run_id=run_id,
                tool_used=str(proposal["tool"]),
                observation=observation_json,
                proposal=json.dumps(proposal, default=str),
                validated=was_validated,
                applied=was_applied,
                reasoning=reasoning,
            )
        )

    logger.info(
        "apply complete",
        extra={
            "run_id": run_id,
            "rules_changed": rules_changed,
            "new_rules_id": new_rules_id,
            "validated_count": len(validated),
        },
    )
    return OptimizerState(
        applied_rules={
            "rules_changed": rules_changed,
            "new_rules_id": new_rules_id,
            "rules": new_rules,
        },
    )


# ------------------------------------------------------------------- assembly
def build_graph() -> CompiledStateGraph:
    """Compile the four-node optimizer pipeline."""
    graph = StateGraph(OptimizerState)
    graph.add_node("observe", observe_node)
    graph.add_node("analyse", analyse_node)
    graph.add_node("validate", validate_node)
    graph.add_node("apply", apply_node)
    graph.set_entry_point("observe")
    graph.add_edge("observe", "analyse")
    graph.add_edge("analyse", "validate")
    graph.add_edge("validate", "apply")
    graph.add_edge("apply", END)
    return graph.compile()


def run_optimizer(window_hours: int = _OBSERVE_WINDOW_HOURS_DEFAULT) -> dict[str, object]:
    """Single end-to-end agent run. Returns a summary suitable for logging."""
    run_id = str(uuid.uuid4())
    graph = build_graph()
    initial: OptimizerState = OptimizerState(
        run_id=run_id,
        window_hours=window_hours,
        stats={},
        proposals=[],
        validated_proposals=[],
        reasoning="",
    )
    final = graph.invoke(initial)

    applied = final.get("applied_rules", {})
    summary = {
        "run_id": run_id,
        "proposals_total": len(final.get("proposals", [])),
        "proposals_validated": len(final.get("validated_proposals", [])),
        "rules_changed": bool(applied.get("rules_changed", False)),
        "new_rules_id": applied.get("new_rules_id"),
    }
    logger.info("optimizer run complete", extra=summary)
    return summary
