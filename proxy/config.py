"""Proxy configuration: env-loaded Settings, in-memory routing rules, hot reload.

Every env var in the codebase is declared here on the Settings class. Reading
os.environ anywhere else is forbidden (see CLAUDE.md). The reload_rules_loop
keeps current_rules in sync with the most recent routing_rules row, allowing
the agent plane to influence the data plane without sharing process state.
"""

import asyncio
import logging
from dataclasses import dataclass

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """All env vars consumed by the proxy and agent.

    Loaded once at import time. Field names are the lowercase form of the
    env var names listed in tech.md.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    openrouter_api_key: str
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    database_url: str
    qdrant_url: str
    qdrant_api_key: str | None = None
    modal_embedder_app: str
    proxy_url: str = "http://localhost:8000"
    agent_run_interval_minutes: int = 15
    rules_reload_interval_sec: int = 60
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_host: str = "https://us.cloud.langfuse.com"


settings = Settings()


@dataclass
class RoutingRules:
    """In-memory snapshot of the latest routing_rules row.

    The proxy hot path reads these attributes directly. reload_rules_loop
    mutates the singleton in place via __dict__.update for an atomic swap —
    asyncio is single-threaded and dict.update does not yield, so a reader
    cannot observe a half-updated state.

    id is carried alongside the rule values so the reload loop can detect
    new revisions and /health can expose the active rules_version.
    """

    id: int
    cache_threshold: float
    low_max_tokens: int
    high_min_tokens: int


# Module-level singleton. Bootstrapped from Postgres by main.py's lifespan
# before the first request is served; reload_rules_loop keeps it current
# thereafter. id=0 is a sentinel — any real row (SERIAL starts at 1)
# registers as newer and triggers the first swap.
current_rules: RoutingRules = RoutingRules(
    id=0,
    cache_threshold=0.92,
    low_max_tokens=300,
    high_min_tokens=800,
)


async def reload_rules_loop() -> None:
    """Poll routing_rules every RULES_RELOAD_INTERVAL_SEC and swap in place.

    Started as a background task by the FastAPI lifespan and cancelled on
    shutdown. Exceptions during a single iteration are logged and swallowed
    so a transient DB blip never kills the loop.
    """
    # Lazy import: proxy.ledger imports `settings` from this module, so a
    # top-level import here would deadlock at module load.
    from proxy import ledger

    while True:
        try:
            await asyncio.sleep(settings.rules_reload_interval_sec)
            new_rules = await ledger.get_latest_rules()
            if new_rules.id != current_rules.id:
                old_id = current_rules.id
                current_rules.__dict__.update(new_rules.__dict__)
                logger.info(
                    "routing_rules reloaded",
                    extra={
                        "old_id": old_id,
                        "new_id": new_rules.id,
                        "cache_threshold": new_rules.cache_threshold,
                        "low_max_tokens": new_rules.low_max_tokens,
                        "high_min_tokens": new_rules.high_min_tokens,
                    },
                )
        except asyncio.CancelledError:
            logger.info("reload_rules_loop cancelled")
            raise
        except Exception as exc:
            logger.warning(
                "reload_rules_loop iteration failed",
                extra={"error": str(exc)},
            )
