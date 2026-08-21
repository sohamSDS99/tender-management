"""Application settings, loaded from environment / .env."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_DIR / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- core ---
    database_url: str = "sqlite:///./data/tenders.db"
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:5173,http://localhost:4173"

    # --- fetching ---
    fetch_interval_hours: int = 6
    fetch_lookback_days: int = 3
    # Overlapping window: always look back at least this many hours so amendments
    # and late updates are re-observed even for a small FETCH_LOOKBACK_DAYS.
    fetch_min_lookback_hours: int = 72
    request_timeout_seconds: int = 30
    max_retries: int = 3
    retry_backoff_seconds: float = 1.0
    max_response_bytes: int = 30_000_000
    max_pages_per_source: int = 20
    # Sources without server-side keyword search (UK feeds, CanadaBuys CSV,
    # AusTender RSS, PNCP) are prefiltered on topical terms before storage.
    apply_keyword_prefilter: bool = True
    page_size: int = 100
    # Off by default so no process ever fetches unexpectedly. Exactly one
    # trigger owner may enable it; see docs/DECISIONS.md (D2).
    enable_scheduler: bool = False
    # Two runs a day, expressed in Dhaka local time. Asia/Dhaka is UTC+6 with
    # no DST; the offset is never computed by hand - see app/jobs/schedule.py.
    scheduler_timezone: str = "Asia/Dhaka"
    scheduler_hours_local: str = "0,12"
    # Both CanadaBuys and AusTender sit behind WAFs that reject non-browser-shaped
    # UA strings; this form identifies the tool and is accepted by every source.
    user_agent: str = "Mozilla/5.0 (compatible; tender-monitor/0.1)"

    # --- credentials (never logged) ---
    sam_gov_api_key: str = ""

    # --- per-source switches ---
    enable_ted: bool = True
    enable_sam: bool = True
    enable_find_a_tender: bool = True
    enable_contracts_finder: bool = True
    enable_world_bank: bool = True
    enable_canada_buys: bool = True
    enable_austender: bool = True
    enable_pncp: bool = True

    # --- source tuning ---
    # High-volume sources are queried with the keyword list below instead of
    # downloading every notice in the window.
    pncp_modalidades: str = "6,8,4,5"
    pncp_max_pages: int = 6
    enable_canada_buys_open_feed: bool = True
    relevance_config_path: str = str(REPO_DIR / "config" / "relevance_profiles.yaml")
    run_migrations_on_startup: bool = True
    # A run is executed in-process, so it cannot survive a restart. Any run
    # still marked running/queued after this long is orphaned, not alive.
    # Comfortably above the ~13 minutes a full live sweep takes.
    stale_run_minutes: int = 60

    # --- notifications (Slack incoming webhook) ---
    enable_slack_notifications: bool = True
    # Secret. Never logged; redacted by app.settings.redact().
    slack_webhook_url: str = ""
    slack_min_score: int = 70
    slack_channel_label: str = "#tenders"
    slack_max_items: int = 8
    slack_timeout_seconds: int = 15
    # A 'pending' claim older than this is treated as abandoned (process died
    # mid-post) and may be retried. Only a 'sent' claim suppresses forever.
    slack_claim_stale_minutes: int = 30
    # A tender stays eligible to be announced for this long after it was first
    # seen, so a Slack outage, a crash mid-post, or a digest that overflowed
    # the item cap is picked up by a later run instead of being lost. The
    # ledger - not this window - is what prevents a second announcement.
    # Matches the fetch window's 72h floor.
    slack_announce_lookback_hours: int = 72

    # --- public surfaces ---
    # Base URL of the dashboard. Slack entries deep-link to
    # {public_app_url}/?tender={id}, which Dashboard.tsx already reads.
    public_app_url: str = "http://localhost:8080"
    # Secret. Shared secret for POST /api/fetch and POST /api/tenders/rescore.
    # Empty means the write endpoints are refused outright (fail closed).
    cron_secret: str = ""
    # /docs and /openapi.json. Fine on a local host; turn off before the API
    # is ever reachable from the internet (README section 12).
    enable_api_docs: bool = True

    @model_validator(mode="after")
    def _resolve_relative_paths(self) -> Settings:
        """Make relative paths repo-relative, not cwd-relative.

        The documented .env uses ./data/tenders.db and ./config/..., which must
        work whether the app is started from the repo root or from backend/.
        """
        config_path = Path(self.relevance_config_path)
        if not config_path.is_absolute():
            object.__setattr__(self, "relevance_config_path", str((REPO_DIR / config_path).resolve()))
        prefix = "sqlite:///"
        if self.database_url.startswith(prefix):
            raw = self.database_url[len(prefix) :]
            if raw and raw != ":memory:" and not raw.startswith("/"):
                resolved = (REPO_DIR / raw).resolve()
                object.__setattr__(self, "database_url", f"{prefix}{resolved}")
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def pncp_modalidade_codes(self) -> list[int]:
        out: list[int] = []
        for chunk in self.pncp_modalidades.split(","):
            chunk = chunk.strip()
            if chunk.isdigit():
                out.append(int(chunk))
        return out

    def source_enabled(self, source_name: str) -> bool:
        return bool(getattr(self, f"enable_{source_name}", False))

    @property
    def scheduler_hour_list(self) -> list[int]:
        """Local-time hours for the scheduled fetch, in the scheduler timezone."""
        out: list[int] = []
        for chunk in self.scheduler_hours_local.split(","):
            chunk = chunk.strip()
            if chunk.isdigit() and 0 <= int(chunk) <= 23:
                out.append(int(chunk))
        return sorted(set(out))

    @property
    def app_base_url(self) -> str:
        return self.public_app_url.rstrip("/")

    @property
    def slack_configured(self) -> bool:
        return bool(self.enable_slack_notifications and self.slack_webhook_url)


SECRET_FIELDS = ("slack_webhook_url", "cron_secret", "sam_gov_api_key", "database_url")


def redact(text: str, settings: Settings | None = None) -> str:
    """Strip secret values out of any string before it is logged or persisted.

    The connectors already write `api_key=***`; this keeps that convention for
    the settings that arrived with deployment (webhook URL, cron secret, DSN
    password) so a stack trace or FetchRun.error_message can never leak one.
    """
    settings = settings or get_settings()
    out = text
    for field in SECRET_FIELDS:
        value = getattr(settings, field, "") or ""
        if field == "database_url":
            # Only the password inside a DSN is secret, not the whole URL.
            out = re.sub(r"(?<=://)([^:/@\s]+):([^@/\s]+)(?=@)", r"\1:***", out)
            continue
        if len(value) >= 8:
            out = out.replace(value, "***")
    return re.sub(r"(api_key|apikey|token)=[^&\s]+", r"\1=***", out, flags=re.IGNORECASE)


@lru_cache
def get_settings() -> Settings:
    return Settings()
