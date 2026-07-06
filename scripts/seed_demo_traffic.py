"""Send 200 demo requests through the host_app to seed the proxy's ledger.

Demonstrates:
  - tiered routing      (50% low / 30% mid / 20% high mix by endpoint)
  - semantic cache      (natural duplicates from the 30-prompt pool)
  - cost attribution    (each request is tagged by endpoint)
  - savings vs Opus     (counterfactual cost surfaced from the ledger)

Usage:
    docker-compose up -d
    psql $DATABASE_URL < db/schema.sql
    modal deploy modal_app/embedder.py
    uvicorn proxy.main:app --port 8000 &
    uvicorn host_app.main:app --port 8001 &
    python scripts/seed_demo_traffic.py
"""

import asyncio
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

import asyncpg
import httpx

# Allow `from host_app.prompts import PROMPTS` when invoked as a script
# from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from host_app.prompts import PROMPTS
from proxy.config import settings


HOST_APP_URL = "http://localhost:8001"
TOTAL_REQUESTS = 200
INTER_REQUEST_DELAY_SEC = 0.10

# Endpoint share of total traffic (shares sum to 1.00).
# 50% low (doc-writer + log-explainer), 30% mid (sql-analyst),
# 20% high (code-reviewer). Whether prompts actually classify into those
# tiers depends on the active routing_rules bands.
DISTRIBUTION: list[tuple[str, float]] = [
    ("doc-writer",    0.30),
    ("log-explainer", 0.20),
    ("sql-analyst",   0.30),
    ("code-reviewer", 0.20),
]


def _build_plan() -> list[tuple[str, str]]:
    """Construct the (endpoint, prompt) request list according to the
    distribution. Prompts are drawn directly from the pool — with 30
    prompts per endpoint and 200 total requests, natural duplicates occur
    organically and let the semantic cache prove itself without bias."""
    plan: list[tuple[str, str]] = []
    for endpoint, share in DISTRIBUTION:
        count = round(TOTAL_REQUESTS * share)
        for _ in range(count):
            plan.append((endpoint, random.choice(PROMPTS[endpoint])))

    while len(plan) < TOTAL_REQUESTS:
        endpoint = random.choice([e for e, _ in DISTRIBUTION])
        plan.append((endpoint, random.choice(PROMPTS[endpoint])))
    plan = plan[:TOTAL_REQUESTS]

    random.shuffle(plan)
    return plan


async def _send(
    client: httpx.AsyncClient, endpoint: str, text: str
) -> dict[str, object] | None:
    try:
        r = await client.post(
            f"{HOST_APP_URL}/{endpoint}",
            json={"input": text},
            timeout=90.0,
        )
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        return {"error": str(exc)}


async def _fetch_summary(start_ts: datetime) -> dict[str, float | int]:
    """Aggregate ledger rows written since the seed started. Uses the same
    requests table that the dashboard reads from, so the numbers match
    what the demo viewer will see."""
    conn = await asyncpg.connect(settings.database_url)
    try:
        row = await conn.fetchrow(
            """
            SELECT
                COUNT(*)::int                                              AS total,
                COALESCE(SUM(CASE WHEN cached THEN 1 ELSE 0 END), 0)::int  AS hits,
                COALESCE(SUM(cost_usd), 0.0)::float                        AS spent,
                COALESCE(SUM(counterfactual_cost_usd), 0.0)::float         AS opus_cost
            FROM requests
            WHERE ts >= $1
            """,
            start_ts,
        )
    finally:
        await conn.close()
    return dict(row) if row else {"total": 0, "hits": 0, "spent": 0.0, "opus_cost": 0.0}


async def main() -> None:
    plan = _build_plan()
    print(f"Seeding {len(plan)} requests through {HOST_APP_URL}")
    print(f"  endpoint distribution: " + ", ".join(f"{e} {s:.0%}" for e, s in DISTRIBUTION))
    print(f"  prompts per endpoint:  30")
    print(f"  delay between calls:   {INTER_REQUEST_DELAY_SEC}s")
    print()

    start_ts = datetime.now(timezone.utc)
    sent = 0
    errors = 0
    hits_reported = 0

    async with httpx.AsyncClient() as client:
        for i, (endpoint, text) in enumerate(plan, 1):
            result = await _send(client, endpoint, text)
            sent += 1
            if result is None or (isinstance(result, dict) and "error" in result):
                errors += 1
            elif result.get("cached"):
                hits_reported += 1
            if i % 20 == 0:
                pct = i / len(plan) * 100
                print(f"  [{i:3d}/{len(plan)}] {pct:5.1f}%  "
                      f"cache_hits={hits_reported}  errors={errors}")
            await asyncio.sleep(INTER_REQUEST_DELAY_SEC)

    summary = await _fetch_summary(start_ts)
    total = summary["total"]
    hits = summary["hits"]
    spent = float(summary["spent"])
    opus_cost = float(summary["opus_cost"])
    saved = opus_cost - spent
    hit_rate = (hits / total * 100) if total else 0.0
    saved_pct = (saved / opus_cost * 100) if opus_cost else 0.0

    bar = "=" * 60
    print()
    print(bar)
    print("  TokenOps demo seed complete")
    print(bar)
    print(f"  Requests sent:           {sent}")
    print(f"  HTTP errors:             {errors}")
    print(f"  Ledger rows written:     {total}")
    print(f"  Cache hits:              {hits} ({hit_rate:.1f}%)")
    print(f"  Spend (actual):          ${spent:.4f}")
    print(f"  Spend (Opus baseline):   ${opus_cost:.4f}")
    print(f"  Cost saved:              ${saved:.4f}  ({saved_pct:.1f}% vs Opus-only)")
    print(bar)


if __name__ == "__main__":
    asyncio.run(main())
