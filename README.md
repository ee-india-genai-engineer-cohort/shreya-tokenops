# TokenOps

TokenOps is an LLM cost intelligence proxy: a FastAPI service that sits between application code and LLM Providers(like Anthropic, OpenAI, etc), semantically caches prompts via ANN, routes each request to the cheapest capable model tier, and tracks spend per team and feature in Postgres. A LangGraph optimizer agent runs every 15 minutes, observes traffic, and autonomously retunes routing and cache rules within hard safety bounds.

## Architecture

```
host_app endpoints  (port 8001)
  /sql-analyst   /code-reviewer
  /log-explainer /doc-writer
                  │
                  ▼
          proxy/main.py  (port 8000)
          POST /v1/chat/completions  (OpenAI-compatible)
                  │
                  ├─ Routing mode?
                  │   X-TokenOps-Route: auto  → classifier → tier → MODEL_MAP
                  │   default (passthrough)   → honor req.model as-is
                  │
   cache.lookup ──┼──▶ Modal embedder ──▶ Qdrant
                  │       (4s timeout — failure is silent)
                  │       (skipped with X-TokenOps-Cache: skip)
                  │
   classifier   ──┼──▶ Haiku  (LLM-as-judge, mid band only)
   router       ──┼──▶ MODEL_MAP  (Haiku / Sonnet / Opus)
   ChatOpenAI   ──┼──▶ OpenRouter ──▶ Anthropic
                  │
   fire-and-forget:
     cache.store  ──▶  Qdrant
     log_request  ──▶  Postgres: requests
                  │
   Response: OpenAI-spec JSON body + X-TokenOps-* headers
     X-TokenOps-Request-ID, X-TokenOps-Tier,
     X-TokenOps-Cost-USD, X-TokenOps-Cached

Agent plane (separate process)
          agent/scheduler.py — every 15 min
                  │
        observe → analyse → validate → apply
                  │
                  ├──▶ Postgres: routing_rules     ──▶ proxy reloads every 60s
                  └──▶ Postgres: agent_decisions   ──▶ dashboard panel 3
```

The proxy never imports `agent/`. The agent never imports `proxy/cache.py`
or `proxy/main.py`. They communicate only through three Postgres tables.

### Routing modes

- **Passthrough (default):** The proxy honours the `model` field from the request body. Cost is still tracked and cached, but no classifier runs. If `model` is omitted, the classifier runs as a fallback.
- **Auto (opt-in):** Send `X-TokenOps-Route: auto` to enable classifier-based routing — the proxy classifies complexity and picks the cheapest capable tier (Haiku / Sonnet / Opus).

![TokenOps Architecture](architecture.png)

## Quick start

```bash
docker-compose up -d                                # Qdrant + Postgres (schema auto-applied)
cp .env.example .env                                # then fill in OPENROUTER_API_KEY
pip install -r requirements.txt -r requirements-dev.txt
modal deploy modal_app/embedder.py                  # one-time

uvicorn proxy.main:app --port 8000 --reload &
uvicorn host_app.main:app --port 8001 --reload &
python agent/scheduler.py &
streamlit run dashboard/app.py
```

Generate demo traffic and run tests:

```bash
python scripts/seed_demo_traffic.py
pytest tests/ -v
```

## Modal setup
The semantic cache requires an embedding model to convert prompts into vectors. Deployed once, called like an API, scales to zero when idle. 

```
pip install modal
modal setup
modal deploy modal_app/embedder.py
```
This deploys `BAAI/bge-small-en-v1.5` (384-dim, normalized) as a persistent cloud function. The proxy calls it via modal.Function.from_name() with a 4-second timeout.

For cloud deployment, TokenOps also includes Modal apps for the proxy, dashboard, and agent scheduler in `modal_app/` — these target Neon (Postgres), Qdrant Cloud, and Langfuse as managed backends.

### Dashboard Preview:
![Streamlit Dashboard 1](streamlit1.png)
![Streamlit Dashboard 2](streamlit2.png)

Dev dependencies (`pytest`, `pytest-asyncio`) live in `requirements-dev.txt`;
install them in addition to `requirements.txt`.

## How the optimizer agent works

- **Observe.** Aggregates the last 24h of the `requests` ledger: total volume, cache hit rate, per-tier quality and cost.
- **Decide and validate.** Haiku reads the stats and picks which tools to invoke (`cache_tune`, `route_optimize`, `quality_sample`). Each proposed rule change back-tests against the last 500 requests; rejected if projected quality drops more than 5%, or any value lands outside the hard-coded safety range.
- **Apply.** Accepted proposals become a new row in `routing_rules`. The proxy picks it up at the next 60s reload — no restart, no shared state. Every proposal (accepted or rejected) is recorded in `agent_decisions` with its observation, reasoning, and verdict.




