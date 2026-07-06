"""Model selection and cost arithmetic. Pure functions, no I/O.

Lives outside the request/response flow so it can be unit-tested without
fixtures. The dashboard's "savings vs Opus" metric depends on
counterfactual_cost; a regression here corrupts every savings figure.
"""

import logging

logger = logging.getLogger(__name__)

MODEL_MAP: dict[str, str] = {
    "low":  "anthropic/claude-haiku-4-5",
    "mid":  "anthropic/claude-sonnet-4-5",
    "high": "anthropic/claude-opus-4-5",
}

COST_PER_1K_TOKENS: dict[str, dict[str, float]] = {
    "anthropic/claude-haiku-4-5":  {"in": 0.00025, "out": 0.00125},
    "anthropic/claude-sonnet-4-5": {"in": 0.003,   "out": 0.015},
    "anthropic/claude-opus-4-5":   {"in": 0.015,   "out": 0.075},
}

_OPUS_MODEL = MODEL_MAP["high"]

# Fallback rate for passthrough models not in the pricing table.
# Uses Sonnet-tier pricing as a reasonable middle estimate.
_FALLBACK_RATES: dict[str, float] = {"in": 0.003, "out": 0.015}


def select_model(tier: str) -> str:
    """Map a classifier tier to a concrete model name."""
    return MODEL_MAP[tier]


def compute_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    """Dollar cost of an LLM call at the model's published rate.

    Falls back to Sonnet-tier pricing for unknown passthrough models
    and logs a warning so operators can add the real rate.
    """
    rates = COST_PER_1K_TOKENS.get(model)
    if rates is None:
        logger.warning("no pricing for model %s, using fallback rates", model)
        rates = _FALLBACK_RATES
    return (tokens_in / 1000) * rates["in"] + (tokens_out / 1000) * rates["out"]


def counterfactual_cost(tokens_in: int, tokens_out: int) -> float:
    """What Opus would have charged for the same token counts.

    Used by the dashboard to compute savings against a full-power baseline.
    Always uses the Opus rate by definition — do not parameterise.
    """
    return compute_cost(_OPUS_MODEL, tokens_in, tokens_out)
