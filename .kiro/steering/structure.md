---
inclusion: always
---

# Structure: TokenOps

## Repository layout

```
tokenops/
│
├── .kiro/
│   └── steering/
│       ├── product.md          # what we are building and why
│       ├── tech.md             # stack, conventions, commands
│       └── structure.md        # this file — where everything lives
│
├── proxy/                      # DATA PLANE — hot path, every LLM request
│   ├── main.py                 # FastAPI app, routes, lifespan
│   ├── cache.py                # Qdrant semantic cache (lookup + store)
│   ├── classifier.py           # LLM-as-judge complexity scorer
│   ├── router.py               # model selection + cost calculation
│   ├── ledger.py               # async Postgres writes per request
│   └── config.py               # Pydantic Settings, hot-reload logic
│
├── agent/                      # AGENT PLANE — off hot path, scheduled
│   ├── graph.py                # LangGraph state machine definition
│   ├── scheduler.py            # APScheduler, fires graph every 15 min
│   └── tools/
│       ├── cache_tune.py       # adjusts cosine similarity threshold
│       ├── route_optimize.py   # adjusts tier token boundaries
│       └── quality_sample.py   # LLM-as-judge on sampled responses
│
├── host_app/                   # DEMO HOST — generates realistic traffic
│   ├── main.py                 # FastAPI, 4 AI endpoints
│   └── prompts.py              # prompt templates per endpoint
│
├── dashboard/
│   └── app.py                  # Streamlit, 4 panels
│
├── modal_app/
│   ├── embedder.py             # Modal GPU endpoint, bge-small-en-v1.5
│   ├── proxy_app.py            # Modal web endpoint wrapping proxy for cloud deploy
│   ├── dashboard_app.py        # Modal web endpoint wrapping Streamlit dashboard
│   └── agent_app.py            # Modal cron function running optimizer agent
│
├── db/
│   └── schema.sql              # all table definitions (source of truth)
│
├── tests/
│   ├── test_cache.py
│   ├── test_classifier.py
│   ├── test_router.py
│   ├── test_ledger.py
│   └── integration/
│       └── test_proxy_e2e.py
│
├── scripts/
│   └── seed_demo_traffic.py    # 200 synthetic requests for demo
│
├── docker-compose.yml
├── .env.example
├── requirements.txt
└── README.md
```

## Module responsibilities — what lives where

### `proxy/main.py`
- FastAPI app instantiation and `lifespan` context manager
- Single route: `POST /v1/chat/completions` (OpenAI-compatible response format)
- Extracts `messages`, `tag` (from body or `X-Tag` header)
- Reads `X-TokenOps-Route` header: `auto` enables classifier-based routing,
  default is passthrough (honours `req.model`)
- Reads `X-TokenOps-Cache` header: `skip` bypasses cache lookup
- Orchestrates: cache → classify → route → call LLM → store → log
- Returns OpenAI-spec JSON body (`id`, `object`, `created`, `model`, `choices`, `usage`)
  with TokenOps metadata in response headers (`X-TokenOps-Request-ID`,
  `X-TokenOps-Tier`, `X-TokenOps-Cost-USD`, `X-TokenOps-Cached`)
- Fire-and-forget tasks for cache write and ledger write
- Health check: `GET /health`
- Does NOT contain business logic — delegates to other proxy modules

### `proxy/cache.py`
- `ensure_collection()` — idempotent Qdrant collection setup
- `lookup(prompt: str) -> dict | None` — embed + ANN search, returns
  cached payload or None. Respects threshold from in-memory config.
- `store(prompt, response, model, tokens_in, tokens_out)` — embed + upsert
- Uses Modal embedder via `modal.Function.lookup()`
- 4-second timeout on Modal calls — returns None on timeout, never raises

### `proxy/classifier.py`
- `async classify(prompt: str) -> Literal["low", "mid", "high"]`
- Two-stage classification:
  1. **Word-count pre-classifier (no LLM call).** Word count
     ≤ `routing_rules.low_max_tokens` → `"low"`; word count
     ≥ `routing_rules.high_min_tokens` → `"high"`. Bands are tunable
     by the optimizer agent via `route_optimize.py`.
  2. **LLM-as-judge for the ambiguous middle band.** Calls
     `claude-haiku-4-5` via `langchain-openai` (OpenRouter) with
     `with_structured_output`, producing an internal
     `ComplexityResult(tier, reason)` Pydantic model. Only the bare
     tier string is exposed at the module boundary; `reason` is logged
     at DEBUG.
- Caps the prompt at 300 characters before sending to the LLM
- Soft-fails to `"mid"` on any classifier exception — the proxy continues
  serving via Sonnet rather than returning 502 on a soft error
- Target latency: ~0ms on the short-circuit paths, < 150ms on the LLM path

### `proxy/router.py`
- `select_model(tier: str) -> str` — reads from MODEL_MAP
- `compute_cost(model, tokens_in, tokens_out) -> float` — falls back to
  Sonnet-tier pricing for unknown passthrough models and logs a warning
- `counterfactual_cost(tokens_in, tokens_out) -> float` — what Opus
  would have cost, used for savings calculation in dashboard
- Pure functions (except logging), no network I/O, fully unit-testable

### `proxy/ledger.py`
- `init_db()` — creates asyncpg connection pool on startup
- `log_request(...)` — async insert into `requests` table
- `get_routing_rules() -> RoutingRules` — reads latest row
- All functions async. Connection pool stored as module-level singleton.

### `proxy/config.py`
- `Settings` class (Pydantic BaseSettings) — loads all env vars
  (`qdrant_url`, `qdrant_api_key`, `langfuse_*`, etc.)
- `RoutingRules` dataclass — in-memory cache of latest DB rules
- `reload_rules_loop()` — async background task, polls DB every 60s,
  swaps in-memory RoutingRules via `__dict__.update` (atomic in asyncio)
- Single `settings` instance imported by all other proxy modules

### `agent/graph.py`
- `OptimizerState` TypedDict — the full state schema
- Node functions: `observe_node`, `analyse_node`, `validate_node`, `apply_node`
- `build_graph()` — returns compiled LangGraph `StateGraph`
- `run_optimizer()` — entry point called by scheduler, returns summary dict
- Safety bounds defined as module-level constants (not config):
  `CACHE_THRESHOLD_RANGE = (0.85, 0.97)`
  `LOW_MAX_TOKENS_RANGE = (200, 500)`
  `HIGH_MIN_TOKENS_RANGE = (600, 1200)`

### `agent/tools/cache_tune.py`
- Input: current hit rate (float), current threshold (float)
- Logic: if hit_rate < 0.30 → lower threshold by 0.02; if > 0.70 → raise by 0.01
- Output: `{"action": "lower"|"raise"|"no_change", "proposed_threshold": float}`
- No LLM call — pure calculation

### `agent/tools/route_optimize.py`
- Input: per-tier quality scores and volume from last window
- Logic: if low-tier avg quality > 0.90 AND volume > 50 → relax `low_max_tokens` up
- Output: `{"action": str, "proposed_low_max": int, "proposed_high_min": int}`
- No LLM call — pure calculation

### `agent/tools/quality_sample.py`
- Input: N unscored request IDs from `requests` table
- Calls LLM-as-judge (Haiku) on each sampled response
- Writes `quality_score` back to `requests` table
- Returns: `{"sampled": int, "avg_score": float, "low_quality_count": int}`

### `host_app/main.py`
- Four endpoints, all POST, all call proxy at `PROXY_URL`
- `/sql-analyst` — NL to SQL question, tag: "sql-analyst"
- `/code-reviewer` — paste code, get review, tag: "code-reviewer"
- `/log-explainer` — paste error log, get explanation, tag: "log-explainer"
- `/doc-writer` — paste function, get docstring, tag: "doc-writer"
- Returns proxy response verbatim, adds `endpoint` field

### `dashboard/app.py`
- Panel 1 (top): total spend, savings, cache hit %, tier breakdown bar chart
- Panel 2: cost by tag — bar chart, model tier colour split
- Panel 3: agent decision log — table from `agent_decisions`, most recent first
- Panel 4: last 100 requests — sortable table
- Auto-refreshes every 10 seconds via `st.rerun()`
- Reads directly from Postgres — no API layer needed

### `modal_app/embedder.py`
- Single Modal function: `embed(texts: list[str]) -> list[list[float]]`
- Model: `BAAI/bge-small-en-v1.5`, normalized output
- GPU: T4 (cheapest Modal GPU, sufficient for this model)
- `min_containers=0` in dev, `min_containers=1` in production
- Deployed independently: `modal deploy modal_app/embedder.py`

### `modal_app/proxy_app.py`
- Modal web endpoint wrapping the FastAPI proxy for cloud deployment
- Targets Neon (Postgres), Qdrant Cloud, Langfuse as managed backends

### `modal_app/dashboard_app.py`
- Modal web endpoint wrapping the Streamlit dashboard

### `modal_app/agent_app.py`
- Modal cron function running the optimizer agent on schedule

## Database tables

All definitions in `db/schema.sql`. Modules must NOT define schema in code.

| Table | Written by | Read by |
|-------|-----------|---------|
| `routing_rules` | optimizer agent | proxy config hot-reload |
| `requests` | proxy ledger | agent observe node, dashboard |
| `agent_decisions` | optimizer agent | dashboard panel 3 |

## Data flow — request lifecycle

```
host_app endpoint
    │  POST /v1/chat/completions  {messages, model?, tag?}
    │  Headers: X-Tag, X-TokenOps-Route, X-TokenOps-Cache
    ▼
proxy/main.py
    │
    ├─ cache.lookup(prompt)                     # skipped if X-TokenOps-Cache: skip
    │       ├─ HIT  → fire-and-forget: ledger.log(cached=True)
    │       │         return OpenAI-spec JSON + X-TokenOps-* headers
    │       └─ MISS → continue
    │
    ├─ Routing decision:
    │       ├─ X-TokenOps-Route: auto  → classifier.classify(prompt) → tier
    │       │                            → router.select_model(tier) → model
    │       └─ default (passthrough)   → use req.model as-is, tier="passthrough"
    │              (if req.model is None → fall back to classifier)
    │
    ├─ LLM call (langchain-openai via OpenRouter)
    │
    ├─ asyncio.create_task(cache.store(...))    # non-blocking
    ├─ asyncio.create_task(ledger.log_request(...))  # non-blocking
    │
    └─ return OpenAI-spec JSON body + X-TokenOps-* headers
```

## Data flow — agent cycle (every 15 minutes)

```
scheduler.py → agent/graph.py:run_optimizer()
    │
    ├─ observe_node: SELECT aggregated stats FROM requests WHERE ts > now()-interval
    │
    ├─ analyse_node (LLM): decides which tools to call
    │       ├─ tools/cache_tune.py      → proposal dict
    │       ├─ tools/route_optimize.py  → proposal dict
    │       └─ tools/quality_sample.py  → writes quality_score to requests
    │
    ├─ validate_node: back-test each proposal against last 500 requests
    │       └─ reject if projected quality drop > 5%
    │
    └─ apply_node:
            ├─ INSERT accepted proposals → routing_rules
            └─ INSERT all decisions    → agent_decisions
```

## Naming conventions

- Files: `snake_case.py`
- Classes: `PascalCase`
- Functions and variables: `snake_case`
- Constants: `UPPER_SNAKE_CASE`
- Database columns: `snake_case`
- API tags (cost attribution): `kebab-case` (e.g. `sql-analyst`)

## Where NOT to put things

- No business logic in `main.py` — it orchestrates only
- No database calls in `router.py` or `classifier.py` — pure functions
- No LLM calls in `cache.py` — it calls Modal, not Anthropic directly
- No agent code in the `proxy/` directory — strict plane separation
- No Streamlit imports outside `dashboard/`
- No hardcoded API keys anywhere — always from `config.Settings`
