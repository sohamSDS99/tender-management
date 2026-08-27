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
    # SAM.gov meters Get Opportunities *per day*, by account role: a non-federal
    # account with no assigned role gets 10 requests a day, one with a role gets
    # 1000. This connector used to spend up to 80 in a single sweep - 20 pages
    # plus 60 per-notice description fetches - so the first sweep of any day
    # exhausted the free quota and every request after it, the next day's
    # included, came back HTTP 429 "Message throttled out" until the 00:00 UTC
    # reset. Observed in production: SAM had never once returned a 200 there.
    #
    # These default to what the free tier can actually afford - one search
    # request per sweep, no description fetches. Raise them only if the account
    # holds a role; SAM_MAX_PAGES=20 and SAM_MAX_DESCRIPTION_FETCHES=60 restore
    # the old depth.
    sam_max_pages: int = 1
    sam_max_description_fetches: int = 0
    # There is no paid tier that lifts the quota above - GSA grants rate
    # increases only to federal system accounts - but there is a way around it
    # entirely. SAM publishes every active opportunity as one CSV once a day,
    # with no API key, no login and no quota, and that file carries the notice
    # description inline. So the metered API is no longer the default: the
    # extract is both unlimited and richer, because the API makes you spend a
    # second request per notice to read a description at all.
    #
    # The API path is kept for the two things the extract cannot do: query a
    # past window (the file holds only what is active *now*), and see closed
    # notices. Set this false to go back to it.
    sam_use_bulk_extract: bool = True
    # The file was 242 MB in August 2026 and only grows. This is a guard against
    # an unbounded stream, not a tuning knob; it is deliberately far above the
    # real size, and the download is gzipped in transit.
    sam_extract_max_bytes: int = 400_000_000
    enable_canada_buys_open_feed: bool = True
    relevance_config_path: str = str(REPO_DIR / "config" / "relevance_profiles.yaml")
    run_migrations_on_startup: bool = True
    # A run is executed in-process, so it cannot survive a restart. Any run
    # still marked running/queued after this long is orphaned, not alive.
    # Comfortably above the ~13 minutes a full live sweep takes.
    stale_run_minutes: int = 60

    # --- notifications (Slack) ---
    # Two transports, and the one in force is derived rather than configured:
    # a bot token needs a channel to post to, an incoming webhook carries its
    # own. See slack_transport below and docs/DECISIONS.md (D22).
    enable_slack_notifications: bool = True
    # Secret. Never logged; redacted by app.settings.redact().
    slack_webhook_url: str = ""
    # Secret. Bot user OAuth token (xoxb-...) for chat.postMessage. Needs the
    # chat:write scope, plus chat:write.public to post to a public channel the
    # bot has not joined.
    slack_bot_token: str = ""
    # Channel ID (e.g. C0123ABCDEF), required by the bot-token transport. An ID
    # rather than a name: a channel can be renamed, and #name lookups are the
    # first thing to break when it is.
    slack_channel_id: str = ""
    # Posting identity, bot-token transport only. Without these a digest arrives
    # under whatever the Slack app's bot user is called, which is rarely what the
    # channel expects. Requires the chat:write.customize scope.
    slack_bot_username: str = "Tender Monitor"
    slack_bot_icon_emoji: str = ":satellite_antenna:"
    slack_min_score: int = 70
    # Display label, and the ledger key that makes an announcement at-most-once
    # (D6). Changing it lets already-announced tenders be announced again, which
    # is right for a genuinely new channel and wrong as a rename - see D22.
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

    # --- operator actions from the dashboard (D23) ---
    # The dashboard can start a sweep and re-score without holding CRON_SECRET.
    # The secret was never a confidentiality control here - reads are wide open
    # (D5) - it was cost control, so cost controls replace it. Set false to close
    # these to the browser again, which is what an internet-exposed deployment
    # must do.
    allow_operator_actions: bool = True
    # Minimum gap between operator-started sweeps. A full sweep queries eight
    # public services for ~13 minutes, so this is what stops a repeatedly clicked
    # button from hammering them.
    operator_fetch_cooldown_seconds: int = 300
    # How far back a sweep started from the dashboard looks, in days.
    #
    # Deliberately much deeper than FETCH_MIN_LOOKBACK_HOURS, because the two
    # sweeps answer different questions. The schedule's 72-hour overlap exists to
    # keep up with the present without missing a late amendment; a human pressing
    # "Fetch new tenders" is asking the opposite - go and look harder than the
    # schedule does. Handing the button the schedule's window meant it re-queried
    # a window the last cron run had already emptied, so it truthfully reported
    # success and created almost nothing. Measured at one instant on the same
    # five connectors: 34 notices returned over 72 hours, 119 over 30 days.
    operator_fetch_days_back: int = 30
    # Re-scoring spends no outbound request but rewrites every stored row.
    operator_rescore_cooldown_seconds: int = 120

    # --- accounts (D25, gated by D26) ---
    # The sign-in gate. True means every route except PUBLIC_PATHS in
    # app/security.py needs a session, which is what D26 decided.
    #
    # It is a switch rather than a constant for one reason: it is the way back in
    # if the gate itself misbehaves. Flipping it to false on the platform
    # restores an open API without a deploy, which beats being locked out of the
    # tool that manages the accounts. Default true - a security control that
    # defaults to off is not a control.
    require_sign_in: bool = True
    # --- accounts (D25) ---
    # Accounts gate nothing: reads were open before them and still are. These
    # values decide how long a signed-in browser stays signed in and how hard it
    # is to guess a password, not who may read a tender.
    session_cookie_name: str = "tm_session"
    # Set true wherever the dashboard is served over HTTPS. Left false by
    # default because the documented deployment is plain HTTP on a LAN, and a
    # Secure cookie over HTTP is simply never sent - the symptom is a sign-in
    # that appears to succeed and lands you signed out.
    session_cookie_secure: bool = False
    # Sliding: touched at most once an hour, and extended when it is.
    session_lifetime_days: int = 14
    invite_lifetime_days: int = 7
    password_min_length: int = 10
    # Per account, not per address: the API is behind a proxy, so every browser
    # on the network would otherwise share one bucket.
    login_max_failures: int = 8
    login_lockout_minutes: int = 15

    # --- outbound email (D27) ---
    # SMTP rather than a provider SDK: no new dependency, and it is the one
    # interface Resend, Google Workspace, Postmark and a company relay all
    # offer. Unset by default, in which case invitations simply are not emailed
    # and the dashboard says so.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    # Secret. Never logged; redacted by app.settings.redact().
    smtp_password: str = ""
    # The envelope sender. Required alongside smtp_host for mail to be
    # considered configured at all.
    smtp_from: str = ""
    smtp_from_name: str = "Tender Monitor"
    # 587 + STARTTLS is the common case; set smtp_use_ssl for implicit TLS on
    # 465. Both verify certificates - there is no switch to turn that off,
    # because a mail path that silently accepts any certificate is worse than
    # no mail path.
    smtp_use_starttls: bool = True
    smtp_use_ssl: bool = False
    # Bounded because this runs inside a request. A dropped SMTP connection
    # otherwise hangs until the OS gives up, and the administrator watches a
    # spinner with no idea whether the invitation exists.
    smtp_timeout_seconds: int = 15

    # --- public surfaces ---
    # Base URL of the dashboard. Slack entries deep-link to
    # {public_app_url}/?tender={id}, which Dashboard.tsx already reads.
    public_app_url: str = "http://localhost:8080"
    # Secret. Shared secret for POST /api/fetch and POST /api/tenders/rescore.
    # Empty means the write endpoints are refused outright (fail closed).
    cron_secret: str = ""
    # /docs and /openapi.json. Fine on a local host; turn off before the API
    # is ever reachable from the internet (README section 13).
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
    def slack_transport(self) -> str:
        """Which delivery path is in force: "bot_token", "webhook" or "none".

        Derived, not configured, so there is no such thing as a combination that
        says one thing and does another. A bot token wins when it is usable,
        because it is the revocable one: an incoming webhook URL *is* its own
        credential and cannot be rotated without re-issuing it.
        """
        if self.slack_bot_token and self.slack_channel_id:
            return "bot_token"
        if self.slack_webhook_url:
            return "webhook"
        return "none"

    @property
    def slack_configured(self) -> bool:
        return bool(self.enable_slack_notifications and self.slack_transport != "none")


SECRET_FIELDS = (
    "slack_webhook_url",
    # Must be redacted: notifier error text reaches the dashboard through
    # /api/automation, so an unredacted token would be readable in a browser.
    "slack_bot_token",
    "cron_secret",
    "sam_gov_api_key",
    "smtp_password",
    "database_url",
)


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
