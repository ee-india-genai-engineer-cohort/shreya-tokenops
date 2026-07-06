---
inclusion: always
---

# Product: TokenOps

## Purpose

TokenOps is a drop-in LLM cost intelligence proxy for engineering teams
building AI-powered products. It sits between application code and LLM
providers, intercepting every request to cache redundant calls, route tasks
to the cheapest capable model, and track spend by team and feature tag.

What makes it more than a proxy is the autonomous optimizer agent. A LangGraph
agent runs every 15 minutes, observes traffic patterns in the cost ledger,
and rewrites its own routing and cache rules — without human intervention.
It validates every change against historical quality data before applying it,
making it safe to run autonomously in production.

The problem it solves: engineering teams have no visibility into which feature,
team, or prompt pattern is responsible for their LLM bill. They over-provision
models, make redundant API calls, and discover budget problems on the monthly
invoice. TokenOps changes this in real time.

## Core features

1. **Semantic cache** — ANN lookup on prompt embeddings, not exact match.
   Catches semantically identical prompts phrased differently.

2. **Complexity classifier + model router** — classifies each request as
   low / mid / high using an LLM-as-judge call, then routes to the
   appropriate model tier (Haiku / Sonnet / Opus).

3. **Cost ledger** — one Postgres row per request: model, tokens, cost,
   tag, latency, cache hit, quality score.

4. **Optimizer agent** — LangGraph agent with three tools: cache threshold
   tuner, routing rule optimizer, quality sampler. Runs on schedule, writes
   decisions to `agent_decisions` table, applies changes to `routing_rules`.

5. **Dashboard** — Streamlit. Four panels: headline metrics, cost by tag,
   agent decision log, request log.

6. **Host application** — FastAPI with four AI endpoints that generate
   realistic, varied traffic through the proxy for demo purposes.

## What this is NOT (v1)

- Not a multi-tenant SaaS (no per-customer API key auth)
- Not a streaming proxy (`stream: true` not supported)
- Not a fine-tuned classifier (prompt-based complexity scoring only)

## Business objectives

Every design decision in v1 creates a data asset or primitive that
a real FinOps-in-AI needs:

- Cost ledger → billing primitive (add Stripe, you have SaaS billing)
- `routing_rules` + `agent_decisions` → governance audit trail
- Tag-based attribution → maps to cost centres in multi-tenant product
- Quality scores → proof that savings don't degrade output quality

The jump from this portfolio build to a paying-customer MVP is:
add API key auth (one middleware), add `tenant_id` column to all tables,
deploy proxy behind a load balancer. No architecture change required.

## Success metrics (portfolio demo)

| Metric | Target |
|--------|--------|
| Cache hit rate after 200 requests | ≥ 35% |
| Requests routed to Haiku (low tier) | ≥ 40% |
| Optimizer agent rule changes during demo | ≥ 1 visible change |
| Proxy overhead (p99) | < 50ms |
| Dashboard panels populated with real data | All 4 |
