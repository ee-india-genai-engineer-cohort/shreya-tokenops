"""TokenOps dashboard — four-panel Streamlit view over the proxy ledger.

Reads directly from Postgres (the same tables the proxy writes). No API
layer; the proxy and agent never see this process. Each refresh re-issues
the panel queries — at the demo refresh interval (10s) and ~4 panels per
tick the per-query connection overhead is well under a second.

Run with:
    streamlit run dashboard/app.py
"""

import asyncio
import time
from datetime import date, datetime, timedelta, timezone

import altair as alt
import asyncpg
import pandas as pd
import streamlit as st

from agent.graph import run_optimizer
from proxy.config import settings


# Model colour palette — green = cheap (Haiku), amber = mid (Sonnet),
# red = expensive (Opus).
COLOR_HAIKU  = "#22c55e"
COLOR_SONNET = "#f59e0b"
COLOR_OPUS   = "#ef4444"
COLOR_CACHE  = "#6366f1"

# Keys match the OpenRouter model strings stored in the DB.
MODEL_COLORS: dict[str, str] = {
    "anthropic/claude-haiku-4-5":  COLOR_HAIKU,
    "anthropic/claude-sonnet-4-5": COLOR_SONNET,
    "anthropic/claude-opus-4-5":   COLOR_OPUS,
}

COLOR_PASSTHROUGH = "#3b82f6"

TIER_COLORS: dict[str, str] = {
    "low":         COLOR_HAIKU,
    "mid":         COLOR_SONNET,
    "high":        COLOR_OPUS,
    "cache":       COLOR_CACHE,
    "passthrough": COLOR_PASSTHROUGH,
}

TIER_DISPLAY: dict[str, str] = {
    "low":         "Haiku",
    "mid":         "Sonnet",
    "high":        "Opus",
    "cache":       "Cache",
    "passthrough": "Passthrough",
}


def _short_model(name: str) -> str:
    """Strip the 'anthropic/' vendor prefix for compact display."""
    return name.replace("anthropic/", "")


def _tier_display(tier: str) -> str:
    """Map tier code to display name."""
    return TIER_DISPLAY.get(tier, tier)


# ----------------------------------------------------------------- db helpers
async def _fetch_async(query: str, *args: object) -> list[dict[str, object]]:
    conn = await asyncpg.connect(settings.database_url)
    try:
        rows = await conn.fetch(query, *args)
    finally:
        await conn.close()
    return [dict(r) for r in rows]


@st.cache_data(ttl=10, show_spinner=False)
def fetch(query: str, *args: object) -> pd.DataFrame:
    """Sync wrapper for async DB queries, cached for 10 s."""
    return pd.DataFrame(asyncio.run(_fetch_async(query, *args)))


# ----------------------------------------------------------------- page setup
st.set_page_config(page_title="TokenOps", layout="wide", page_icon="💰")

st.markdown(
    """
    <style>
    [data-testid="stMetricValue"] { font-size: 1.6rem; }
    [data-testid="stMetricLabel"] { font-size: 0.8rem; color: #6b7280; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("TokenOps - LLM Cost Intelligence")
st.divider()


# ---------------------------------------------------------------------- sidebar
with st.sidebar:
    st.header("Controls")

    refresh = st.selectbox(
        "Auto-refresh",
        options=[10, 30, 60],
        index=0,
        format_func=lambda s: f"Every {s}s",
    )

    today = date.today()
    week_ago = today - timedelta(days=7)
    date_pick = st.date_input("Date range", value=(week_ago, today))
    if isinstance(date_pick, tuple) and len(date_pick) == 2:
        start_date, end_date = date_pick
    elif isinstance(date_pick, tuple) and len(date_pick) == 1:
        start_date = end_date = date_pick[0]
    elif isinstance(date_pick, date):
        start_date = end_date = date_pick
    else:
        start_date = end_date = today

    st.divider()
    if st.button("⚡ Run optimizer now", type="primary", use_container_width=True):
        with st.spinner("Running optimizer (10–30 s)…"):
            st.session_state["last_run_summary"] = run_optimizer()

    if "last_run_summary" in st.session_state:
        st.caption("Last run result")
        st.json(st.session_state["last_run_summary"], expanded=False)


start_ts = datetime.combine(start_date, datetime.min.time(), tzinfo=timezone.utc)
end_ts   = datetime.combine(
    end_date + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc
)


# ========================================================= Panel 1 — headline
panel1_df = fetch(
    """
    SELECT
        COALESCE(SUM(cost_usd), 0)::float                  AS spend,
        COALESCE(SUM(counterfactual_cost_usd), 0)::float   AS opus_cost,
        COUNT(*)::int                                      AS total,
        SUM(CASE WHEN cached THEN 1 ELSE 0 END)::int       AS hits,
        COUNT(*) FILTER (WHERE tier = 'low')::int          AS tier_low,
        COUNT(*) FILTER (WHERE tier = 'mid')::int          AS tier_mid,
        COUNT(*) FILTER (WHERE tier = 'high')::int         AS tier_high,
        COUNT(*) FILTER (WHERE tier = 'cache')::int        AS tier_cache
    FROM requests
    WHERE ts >= $1 AND ts < $2
    """,
    start_ts, end_ts,
)

row = panel1_df.iloc[0] if not panel1_df.empty else pd.Series(
    {"spend": 0.0, "opus_cost": 0.0, "total": 0, "hits": 0,
     "tier_low": 0, "tier_mid": 0, "tier_high": 0, "tier_cache": 0}
)

spend       = float(row["spend"]     or 0.0)
opus_cost   = float(row["opus_cost"] or 0.0)
total       = int(row["total"]       or 0)
hits        = int(row["hits"]        or 0)
savings     = opus_cost - spend
hit_rate    = (hits / total * 100.0) if total else 0.0
savings_pct = (savings / opus_cost * 100.0) if opus_cost else 0.0

st.subheader("Overview")
m1, m2, m3, m4 = st.columns(4)
m1.metric("Total requests", f"{total:,}")
m2.metric("Actual spend", f"${spend:.4f}")
m3.metric(
    "Saved vs all-Opus",
    f"${savings:.4f}",
    delta=f"{savings_pct:.1f}%",
    delta_color="normal",
)
m4.metric(
    "Cache hit rate",
    f"{hit_rate:.1f}%",
    delta=f"{hits:,} of {total:,} served free",
    delta_color="off",
)

tier_df = pd.DataFrame({
    "tier":  ["low", "mid", "high", "cache"],
    "count": [
        int(row["tier_low"]), int(row["tier_mid"]),
        int(row["tier_high"]), int(row["tier_cache"]),
    ],
})
tier_df = tier_df[tier_df["count"] > 0]

if not tier_df.empty:
    tier_df_display = tier_df.copy()
    tier_df_display["tier"] = tier_df_display["tier"].apply(_tier_display)

    tier_chart = (
        alt.Chart(tier_df_display)
        .mark_bar(cornerRadiusTopRight=4, cornerRadiusBottomRight=4, size=36)
        .encode(
            y=alt.Y(
                "tier:N",
                sort=["Cache", "Haiku", "Sonnet", "Opus"],
                axis=alt.Axis(title=""),
            ),
            x=alt.X("count:Q", axis=alt.Axis(title="requests")),
            color=alt.Color(
                "tier:N",
                scale=alt.Scale(
                    domain=list(TIER_DISPLAY.values()),
                    range=list(TIER_COLORS.values()),
                ),
                legend=None,
            ),
            tooltip=["tier:N", "count:Q"],
        )
        .properties(height=len(tier_df) * 80, title="Requests by tier")
    )
    st.altair_chart(tier_chart, use_container_width=True)

st.divider()


# ======================================================= Panel 2 — cost by tag
st.subheader("Cost by tag")

panel2_df = fetch(
    """
    SELECT
        tag,
        REPLACE(model, 'anthropic/', '') AS model,
        COALESCE(SUM(cost_usd), 0)::float                AS cost,
        COALESCE(SUM(counterfactual_cost_usd), 0)::float AS opus_cost,
        COUNT(*)::int                                     AS calls
    FROM requests
    WHERE ts >= $1 AND ts < $2
      AND tag IS NOT NULL
      AND tier IN ('low', 'mid', 'high', 'passthrough')
    GROUP BY tag, model
    ORDER BY tag, model
    """,
    start_ts, end_ts,
)

if panel2_df.empty:
    st.info("No non-cached LLM calls in the selected date range.")
else:
    short_model_colors = {_short_model(k): v for k, v in MODEL_COLORS.items()}

    col_chart, col_table = st.columns([3, 2], gap="large")

    with col_chart:
        cost_chart = (
            alt.Chart(panel2_df)
            .mark_bar(cornerRadiusTopRight=4, cornerRadiusBottomRight=4)
            .encode(
                x=alt.X("cost:Q", axis=alt.Axis(title="Cost ($)", format="$.4f")),
                y=alt.Y("tag:N", axis=alt.Axis(title=""), sort="-x"),
                color=alt.Color(
                    "model:N",
                    scale=alt.Scale(
                        domain=list(short_model_colors.keys()),
                        range=list(short_model_colors.values()),
                    ),
                    legend=alt.Legend(title="Model", orient="bottom"),
                ),
                tooltip=[
                    alt.Tooltip("tag:N",   title="Tag"),
                    alt.Tooltip("model:N", title="Model"),
                    alt.Tooltip("cost:Q",  title="Cost ($)", format="$.6f"),
                    alt.Tooltip("calls:Q", title="Calls"),
                ],
            )
            .properties(height=220, title="Spend by tag & model")
        )
        st.altair_chart(cost_chart, use_container_width=True)

    with col_table:
        savings_df = (
            panel2_df.groupby("tag")
            .agg(cost=("cost", "sum"), opus_cost=("opus_cost", "sum"),
                 calls=("calls", "sum"))
            .reset_index()
        )
        savings_df["savings_%"] = (
            (savings_df["opus_cost"] - savings_df["cost"])
            / savings_df["opus_cost"].replace(0, pd.NA)
            * 100
        ).round(1)
        savings_df["cost"]      = savings_df["cost"].round(6)
        savings_df["opus_cost"] = savings_df["opus_cost"].round(6)

        st.dataframe(
            savings_df.rename(columns={
                "tag":       "Tag",
                "calls":     "Calls",
                "cost":      "Spend ($)",
                "opus_cost": "Baseline ($)",
                "savings_%": "Saved (%)",
            }),
            hide_index=True,
            use_container_width=True,
            column_config={
                "Spend ($)":    st.column_config.NumberColumn(format="$%.6f"),
                "Baseline ($)": st.column_config.NumberColumn(format="$%.6f"),
                "Saved (%)":    st.column_config.NumberColumn(format="%.1f%%"),
            },
        )

st.divider()


# =================================================== Panel 3 — agent decisions
st.subheader("Agent decisions")

panel3_df = fetch(
    """
    SELECT ts, tool_used, proposal, validated, applied, reasoning
    FROM agent_decisions
    ORDER BY ts DESC
    LIMIT 20
    """
)

if panel3_df.empty:
    st.info("No agent runs yet. Use **Run optimizer now** in the sidebar.")
else:
    display3 = panel3_df.copy()
    display3["proposal"]  = display3["proposal"].fillna("").str.slice(0, 100)
    display3["reasoning"] = display3["reasoning"].fillna("").str.slice(0, 100)
    display3["validated"] = display3["validated"].map({True: "✓", False: "✗", None: "—"})
    display3["applied"]   = display3["applied"].map({True: "✓", False: "✗", None: "—"})

    st.dataframe(
        display3,
        hide_index=True,
        use_container_width=True,
        column_config={
            "ts":        st.column_config.DatetimeColumn("Time", format="HH:mm:ss"),
            "tool_used": st.column_config.TextColumn("Tool", width="small"),
            "proposal":  st.column_config.TextColumn("Proposal", width="large"),
            "validated": st.column_config.TextColumn("Valid", width="small"),
            "applied":   st.column_config.TextColumn("Applied", width="small"),
            "reasoning": st.column_config.TextColumn("Reasoning", width="large"),
        },
    )

st.divider()


# ================================================= Panel 4 — live request feed
st.subheader("Recent requests")

panel4_df = fetch(
    """
    SELECT ts, tag, model, tier, cached, latency_ms,
           cost_usd::float AS cost_usd, quality_score::float AS quality_score
    FROM requests
    WHERE ts >= $1 AND ts < $2
    ORDER BY ts DESC
    LIMIT 100
    """,
    start_ts, end_ts,
)

if panel4_df.empty:
    st.info("No requests in the selected date range.")
else:
    tier_emoji = {"low": "🟢", "mid": "🟡", "high": "🔴", "cache": "🟣"}

    display4 = panel4_df.copy()
    display4["model"]        = display4["model"].apply(_short_model)
    display4["cached"]       = display4["cached"].map({True: "✓", False: ""})
    display4["latency_ms"]   = display4["latency_ms"].round(0).astype("Int64")
    display4["cost_usd"]     = display4["cost_usd"].apply(
        lambda c: round(float(c), 6) if pd.notnull(c) else None
    )
    display4["quality_score"] = display4["quality_score"].apply(
        lambda q: round(float(q), 2) if pd.notnull(q) else None
    )
    display4["tier"] = display4["tier"].apply(
        lambda t: f"{tier_emoji.get(t, '')} {_tier_display(t)}"
    )

    st.dataframe(
        display4[["ts", "tag", "model", "tier", "cached",
                  "latency_ms", "cost_usd", "quality_score"]].rename(columns={
            "ts":            "Time",
            "tag":           "Tag",
            "model":         "Model",
            "tier":          "Tier",
            "cached":        "Cached",
            "latency_ms":    "Latency (ms)",
            "cost_usd":      "Cost ($)",
            "quality_score": "Quality",
        }),
        hide_index=True,
        use_container_width=True,
        column_config={
            "Time":         st.column_config.DatetimeColumn(format="HH:mm:ss"),
            "Cost ($)":     st.column_config.NumberColumn(format="$%.6f"),
            "Quality":      st.column_config.NumberColumn(format="%.2f"),
            "Latency (ms)": st.column_config.NumberColumn(format="%d ms"),
        },
    )


# ----------------------------------------------------------------- auto-refresh
time.sleep(refresh)
st.rerun()
