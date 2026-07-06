# TokenOps

**LLM cost intelligence proxy that caches, routes, tracks, and self-optimizes your AI spend.**

Point your base URL at TokenOps. It intercepts every LLM call, serves semantically identical prompts from cache, routes the rest to the cheapest capable model, and logs every cent by team and feature — while an autonomous agent continuously tunes its own rules.

```
Your app  ──▶  TokenOps proxy  ──▶  LLM providers (via OpenRouter)
                    │
                    ├── semantic cache       meaning-match, not string-match
                    ├── complexity router    Haiku / Sonnet / Opus by task
                    ├── cost ledger          per-request attribution by tag
                    └── optimizer agent      rewrites its own rules every 15 min
```

### Demo results (350+ requests)

| Metric | Value |
|--------|-------|
| Cost reduction vs all-Opus | **68%** ($1.86 actual vs $3.94 baseline) |
| Cache hit rate | **52%** — 186 of 359 requests served free |
| Proxy overhead (p99) | **< 50ms** added latency |
| Optimizer interventions | Cache threshold tuned 0.92 → 0.86 autonomously |

---

## Dashboard

<p>
  <img src="streamlit1.png" alt="Dashboard — overview and cost by tag" width="100%">
</p>
<p>
  <img src="streamlit2.png" alt="Dashboard — agent decisions and request feed" width="100%">
</p>

Four live panels reading directly from Postgres: headline metrics and tier breakdown, cost by tag with model-tier colour split, agent decision audit log, and a sortable request feed showing model, tier, cache status, latency, and cost for every call.

---

## How it works

Three planes, strictly separated — they communicate only through Postgres tables.

![Architecture](architecture.png)

### Data plane — the proxy (port 8000)

Every request flows through a single endpoint: `POST /v1/chat/completions` (OpenAI-compatible).

```
request arrives
    │
    ├── cache lookup (Qdrant ANN on prompt embeddings)
    │       hit  → return cached response instantly, cost = $0.00
    │       miss → continue
    │
    ├── classify complexity (word-count short-circuit + Haiku LLM-as-judge)
    │
    ├── route to cheapest capable model (Haiku / Sonnet / Opus)
    │
    ├── call LLM (ChatOpenAI via OpenRouter)
    │
    ├── fire-and-forget: cache.store + ledger.log_request
    │
    └── return OpenAI-spec JSON + X-TokenOps-* metadata headers
```

Responses are standard OpenAI format (`id`, `object`, `created`, `model`, `choices`, `usage`). TokenOps metadata — tier, cost, cache status, request ID — rides in response headers so existing client code doesn't break.

**Routing modes:**
- **Passthrough (default)** — honours the caller's `model` field. Cost is tracked and cached, but no classifier runs. Falls back to classifier if `model` is omitted.
- **Auto** — send `X-TokenOps-Route: auto` to enable classifier-based routing.
- **Cache skip** — send `X-TokenOps-Cache: skip` to bypass the semantic cache.

### Semantic cache

Not exact match — **meaning match**. Prompts are embedded on a Modal GPU endpoint (`BAAI/bge-small-en-v1.5`, 384-dim) and matched via cosine similarity in Qdrant. "What's the capital of France?" and "France's capital city?" resolve to the same cache entry.

The similarity threshold isn't static — the optimizer agent tunes it. During the demo it went from 0.92 → 0.86, pushing hit rate from ~10% to 52%.

### Agent plane — the optimizer (every 15 min)

A LangGraph state machine running in a separate process. It reads traffic data, proposes rule changes, back-tests them, and applies the ones that pass.

```
observe ──▶ analyse ──▶ validate ──▶ apply
   │           │            │           │
 aggregate   pick tools   back-test    write to
 last 24h    to invoke    vs 500 reqs  routing_rules
               │           reject if
               ├── cache_tune        quality drops > 5%
               ├── route_optimize
               └── quality_sample
```

**Safety:** bounds on every tunable parameter are hard-coded constants the agent cannot override. Every proposal — accepted or rejected — is logged with its observation data and reasoning, visible in the dashboard's audit panel.

### Control plane — the dashboard (port 8501)

Streamlit. Four panels: headline metrics (spend, savings, cache hit rate, tier breakdown), cost by tag, agent decision log, and a request-level feed. Auto-refreshes every 10 seconds.

---

## Quick start

**Prerequisites:** Docker, Python 3.11+, a [Modal](https://modal.com) account, an [OpenRouter](https://openrouter.ai) API key.

```bash
# Infrastructure
docker-compose up -d                                     # Qdrant + Postgres
docker compose exec -T postgres psql -U tokenops -d tokenops < db/schema.sql

# Environment
cp .env.example .env                                     # fill in OPENROUTER_API_KEY
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

# GPU embedding endpoint (one-time)
modal setup && modal deploy modal_app/embedder.py
```

Run four processes (each in its own terminal with the venv active):

```bash
python -m uvicorn proxy.main:app --port 8000 --reload    # proxy
python -m uvicorn host_app.main:app --port 8001 --reload  # demo host app
python -m agent.scheduler                                  # optimizer agent
streamlit run dashboard/app.py                             # dashboard
```

Seed demo traffic and watch the dashboard:

```bash
curl http://localhost:8000/health
python -m scripts.seed_demo_traffic                       # 200 requests
# open http://localhost:8501
```

### Cloud deployment

`modal_app/` includes Modal apps for the proxy, dashboard, and agent — targeting Neon (managed Postgres), Qdrant Cloud, and Langfuse for LLM observability.

---

## Project structure

```
proxy/                  DATA PLANE — every LLM request flows through here
  main.py                 FastAPI app, request orchestration, OpenAI-compat response
  cache.py                Qdrant semantic cache (embed → ANN lookup → store)
  classifier.py           word-count pre-classifier + Haiku LLM-as-judge
  router.py               model selection + cost math (pure functions, no I/O)
  ledger.py               async Postgres writes (fire-and-forget)
  config.py               Pydantic Settings + hot-reload from routing_rules

agent/                  AGENT PLANE — off the hot path, runs every 15 min
  graph.py                LangGraph state machine (observe → analyse → validate → apply)
  scheduler.py            APScheduler entry point
  tools/
    cache_tune.py           adjusts cosine similarity threshold
    route_optimize.py       adjusts tier token boundaries
    quality_sample.py       LLM-as-judge scoring on sampled responses

host_app/               DEMO — 4 AI endpoints generating realistic traffic
  main.py                 /sql-analyst, /code-reviewer, /log-explainer, /doc-writer
  prompts.py              30 prompts per endpoint (120 total)

dashboard/              CONTROL PLANE — Streamlit, 4 panels
modal_app/              Modal cloud deploy (proxy, dashboard, agent) + GPU embedder
db/schema.sql           single source of truth for all table definitions
```

### Database tables

| Table | Written by | Read by | Purpose |
|-------|-----------|---------|---------|
| `routing_rules` | Optimizer agent | Proxy (60s hot-reload) | Cache threshold, tier bands |
| `requests` | Proxy ledger | Agent + Dashboard | One row per request, full cost data |
| `agent_decisions` | Optimizer agent | Dashboard | Audit trail with reasoning |

---

## Tech stack

| Layer | Technology | Role |
|-------|-----------|------|
| Proxy API | FastAPI + Uvicorn | Async request handling, OpenAI-compatible endpoint |
| LLM calls | LangChain + OpenRouter | Provider-agnostic model calls with usage metadata |
| Agent | LangGraph + APScheduler | Stateful optimization loop on a 15-min schedule |
| Semantic cache | Qdrant | ANN vector search on prompt embeddings |
| Embeddings | Modal + `bge-small-en-v1.5` | Serverless GPU endpoint, scales to zero |
| Database | PostgreSQL + asyncpg | Cost ledger, routing rules, agent decisions |
| Observability | Langfuse | Per-call traces, token costs, prompt versioning |
| Dashboard | Streamlit | Real-time cost visibility |

---

## Tests

```bash
pytest tests/ -v                                    # unit tests
pytest tests/integration/ -v --run-integration      # requires Docker services
```
