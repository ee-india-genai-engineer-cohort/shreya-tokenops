# Claude — AI Coding Agent Instructions

This file gives you persistent context for the TokenOps project.
Read this before generating any code, answering any question, or
suggesting any architectural change.

---

## What this project is

TokenOps is a **LLM cost intelligence proxy** — a FastAPI service that sits
between application code and LLM providers (via OpenRouter). It caches
semantically similar prompts, routes requests to the cheapest capable model,
and tracks every cent by team and feature. A LangGraph optimizer agent runs
in the background, autonomously tuning routing and cache rules without human
intervention.

Full context is in `.kiro/steering/`:
- `product.md` — what and why
- `tech.md` — stack, rules, exact library pins
- `structure.md` — where every module lives and what it owns

**Read those files first if you haven't.** Do not invent structure or
dependencies not described there.

---

## The one rule that matters most

**The agent plane must never touch the hot path.**

The proxy handles every request synchronously. The optimizer agent runs
on a schedule (APScheduler) and communicates with the proxy exclusively
through the `routing_rules` Postgres table. If you find yourself writing
agent logic inside a request handler, stop — that is wrong.

---

## How to behave when coding

**Be explicit about types.** Every function signature needs type hints.
Use Pydantic v2 models for all structured data — no raw dicts as function
return types unless it's a simple internal tool payload.

**Async in the proxy, sync is fine in the agent.**
All `proxy/` handlers and DB calls must be `async def`. The agent graph
runs in a background thread (APScheduler calls it synchronously) — mixing
asyncio there adds complexity for no gain. Keep agent node functions sync.

**Fire-and-forget the non-critical writes.**
After serving a response, cache writes and ledger writes use
`asyncio.create_task()`. They must not add latency to the response.

**Never break the module boundaries.**
`router.py` and `classifier.py` are pure functions — no network, no DB.
If you need to add I/O there, you're in the wrong module.

**When in doubt, log it.**
Use Python `logging`, not `print()`. Every proxy log line should include
`request_id`, `tag`, `model`, `cached`, `latency_ms`.

---

## Things you should never do

- Add a dependency not listed in `tech.md` without flagging it first
- Define database tables in Python code — `db/schema.sql` is the only source
- Call `os.environ` directly — always go through `proxy/config.py` Settings
- Put agent code in `proxy/` or proxy logic in `agent/`
- Use `stream=True` on LLM calls (not supported in v1)
- Hardcode any API key, URL, or threshold as a Python literal
- Add `Any` type hints — be explicit

---

## Suggested approach for new features

1. Check `structure.md` to find where the new code belongs
2. Check `tech.md` to confirm the right library and pattern
3. Write the function signature with types first
4. Implement, keeping each module's stated responsibility narrow
5. Write or update the corresponding test in `tests/`
6. If a new env var is needed, add it to both `config.py` and `.env.example`

---

## Quick reference

```bash
# Start everything
docker-compose up -d
modal deploy modal_app/embedder.py
uvicorn proxy.main:app --port 8000 --reload
uvicorn host_app.main:app --port 8001 --reload
python agent/scheduler.py
streamlit run dashboard/app.py

# Run tests
pytest tests/ -v

# Seed demo data
python scripts/seed_demo_traffic.py
```

---

## When you're unsure about intent

Ask rather than assume. Specifically:

- If a new feature would require touching the hot path in a non-trivial way,
  flag the latency implication before implementing.
- If a proposed change conflicts with the agent safety bounds in `graph.py`,
  surface the conflict explicitly.
- If the right module for new code is ambiguous, describe the two options
  and ask.