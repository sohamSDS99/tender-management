"""Application settings, loaded from environment / .env."""

from __future__ import annotations

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
    enable_scheduler: bool = True
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
