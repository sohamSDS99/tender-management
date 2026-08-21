"""Slack digests for newly discovered tenders.

Contract
--------
* **Qualifying** = created by *this* run (never a re-observed update), scoring at
  or above ``SLACK_MIN_SCORE``, and ``is_actionable``.
* **At most once.** Every announcement is written to ``slack_notifications``
  before the HTTP POST and settled after it. The unique constraint on
  ``(tender_id, channel_label)`` is what makes a retried, delayed or
  double-fired run unable to post the same tender twice.
* **Never fatal.** A Slack outage loses a notification, never ingested data. The
  caller is told the run is degraded; nothing is rolled back.
* Links point at *our* dashboard first (``{PUBLIC_APP_URL}/?tender={id}``, the
  deep link Dashboard.tsx already reads) and the buyer's notice second.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.logging_config import log_ctx
from app.models import CLAIMED, FAILED, SENT, UNCONFIRMED, SlackNotification, Tender, utcnow
from app.services.dhaka import format_dhaka
from app.settings import Settings, get_settings, redact

logger = logging.getLogger(__name__)

# Colour-coding thresholds, shared with the dashboard's deadline urgency.
URGENT_HOURS = 72
SOON_DAYS = 14

# Slack rejects a message with more than 50 blocks. A digest spends
# 3 blocks per tender plus a divider between them, and 4 on the frame:
# 3 + 4n <= 50 -> n <= 11. SLACK_MAX_ITEMS is clamped to this, so a
# mis-set env var degrades to "+N more" instead of invalid_blocks.
MAX_ITEMS_HARD_CAP = 11

FIT_LABELS = {
    "high_fit": "Excellent fit",
    "good_fit": "Good fit",
    "possible_fit": "Possible fit",
    "manual_review": "Manual review",
    "not_fit": "Not fit",
}
DEPLOYMENT_LABELS = {
    "cloud_required": "Cloud required",
    "cloud_preferred": "Cloud preferred",
    "cloud_allowed": "Cloud allowed",
    "deployment_unspecified": "Deployment unspecified",
    "hybrid": "Hybrid (cloud or on-prem)",
    "mandatory_on_premises": "Mandatory on-premises",
    "offline_or_air_gapped": "Offline / air-gapped",
}
CURRENCY_SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£", "CAD": "CA$", "AUD": "A$", "BRL": "R$"}


@dataclass
class DigestOutcome:
    """What a notification attempt did. ``ok`` is False only on real failure."""

    status: str = "skipped"  # sent | heartbeat | skipped | disabled | failed
    candidates: int = 0
    posted: int = 0
    suppressed: int = 0
    response_code: int | None = None
    error: str | None = None
    tender_ids: list[int] = field(default_factory=list)
    # Populated on a dry run so CI can show what *would* have been posted.
    payload: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        return self.status not in ("failed", "unconfirmed")

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "candidates": self.candidates,
            "posted": self.posted,
            "suppressed": self.suppressed,
            "response_code": self.response_code,
            "error": self.error,
            "tender_ids": list(self.tender_ids),
            "payload": self.payload,
        }


# --- selection -------------------------------------------------------------


def qualifying_tenders(
    db: Session, since: datetime, settings: Settings | None = None, now: datetime | None = None
) -> list[Tender]:
    """Tenders this run *created* that clear the announcement bar.

    ``first_seen_at`` is written once, at insert, and never touched again by
    ``ingest.upsert_tender`` - so ``first_seen_at >= since`` is exactly "new in
    this run" and needs no change to the frozen ingest path.
    """
    settings = settings or get_settings()
    now = now or utcnow()
    stmt = (
        select(Tender)
        .where(
            Tender.first_seen_at >= since,
            Tender.relevance_score >= settings.slack_min_score,
            Tender.is_actionable.is_(True),
        )
        .order_by(Tender.relevance_score.desc(), Tender.id.asc())
    )
    return list(db.execute(stmt).scalars().all())


def _claimable(db: Session, tender_ids: list[int], channel: str, stale_before: datetime) -> set[int]:
    """Ids not already announced, and not claimed by a live sibling process.

    ``sent`` and ``unconfirmed`` suppress forever - the second because the
    message may already have been delivered. ``pending`` suppresses only while
    fresh, so a process killed mid-post does not silence a tender permanently.
    """
    if not tender_ids:
        return set()
    rows = (
        db.execute(
            select(SlackNotification).where(
                SlackNotification.tender_id.in_(tender_ids),
                SlackNotification.channel_label == channel,
            )
        )
        .scalars()
        .all()
    )
    blocked: set[int] = set()
    for row in rows:
        if row.status in (SENT, UNCONFIRMED):
            blocked.add(row.tender_id)
        elif row.status == CLAIMED and row.claimed_at >= stale_before:
            blocked.add(row.tender_id)
    return {i for i in tender_ids if i not in blocked}


def claim(
    db: Session,
    tenders: list[Tender],
    batch_id: str,
    trigger: str,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> tuple[list[Tender], int]:
    """Reserve the right to announce each tender. Returns (claimed, suppressed).

    Committed *before* the POST, so a crash mid-post leaves evidence instead of
    a silent gap, and a concurrent run loses the race instead of double-posting.
    """
    settings = settings or get_settings()
    now = now or utcnow()
    channel = settings.slack_channel_label
    stale_before = now - timedelta(minutes=settings.slack_claim_stale_minutes)
    eligible = _claimable(db, [t.id for t in tenders], channel, stale_before)

    claimed: list[Tender] = []
    for tender in tenders:
        if tender.id not in eligible:
            continue
        existing = db.execute(
            select(SlackNotification).where(
                SlackNotification.tender_id == tender.id,
                SlackNotification.channel_label == channel,
            )
        ).scalar_one_or_none()
        try:
            if existing is None:
                db.add(
                    SlackNotification(
                        tender_id=tender.id,
                        channel_label=channel,
                        run_batch_id=batch_id,
                        trigger=trigger,
                        status=CLAIMED,
                        claimed_at=now,
                        relevance_score_at_send=tender.relevance_score,
                    )
                )
            else:  # a stale pending / previously failed attempt: take it over
                existing.status = CLAIMED
                existing.claimed_at = now
                existing.run_batch_id = batch_id
                existing.trigger = trigger
                existing.error_message = None
                existing.relevance_score_at_send = tender.relevance_score
            db.commit()
        except IntegrityError:
            # Lost the race to a concurrent run; it owns the announcement.
            db.rollback()
            continue
        claimed.append(tender)
    return claimed, len(tenders) - len(claimed)


def settle(
    db: Session,
    tenders: list[Tender],
    channel: str,
    status: str,
    response_code: int | None = None,
    error: str | None = None,
    now: datetime | None = None,
    batch_id: str | None = None,
) -> None:
    """Record the outcome of the POST against the rows this run claimed.

    Scoped by ``run_batch_id`` when given: a run must never overwrite the
    outcome of a claim belonging to a concurrent run.
    """
    now = now or utcnow()
    if not tenders:
        return
    conditions = [
        SlackNotification.tender_id.in_([t.id for t in tenders]),
        SlackNotification.channel_label == channel,
    ]
    if batch_id is not None:
        conditions.append(SlackNotification.run_batch_id == batch_id)
    rows = db.execute(select(SlackNotification).where(*conditions)).scalars().all()
    for row in rows:
        row.status = status
        row.response_code = response_code
        row.error_message = (error or None) and error[:2000]
        if status == SENT:
            row.posted_at = now
    db.commit()


# --- presentation ----------------------------------------------------------


def deadline_urgency(deadline: datetime | None, now: datetime | None = None) -> tuple[str, str]:
    """(class, human label) for a deadline. Mirrors the dashboard's colour rules."""
    if deadline is None:
        return "none", "no deadline in feed"
    now = now or utcnow()
    delta = deadline - now
    if delta.total_seconds() <= 0:
        return "gone", "closed"
    hours = delta.total_seconds() / 3600
    if hours <= URGENT_HOURS:
        left = int(hours)
        return "urgent", f"{left}h left" if left >= 1 else "closes within the hour"
    days = int(delta.days)
    if days <= SOON_DAYS:
        return "soon", f"{days} day{'s' if days != 1 else ''} left"
    return "normal", f"{days} days left"


def format_value(amount: float | None, currency: str | None) -> str:
    if amount is None:
        return "not published"
    symbol = CURRENCY_SYMBOLS.get((currency or "").upper(), "")
    rendered = f"{amount:,.0f}"
    if symbol:
        return f"{symbol}{rendered}"
    return f"{rendered} {currency}".strip() if currency else rendered


def tender_permalink(tender_id: int, settings: Settings | None = None) -> str:
    """Deep link into our own dashboard - the ?tender= hook in Dashboard.tsx."""
    settings = settings or get_settings()
    return f"{settings.app_base_url}/?tender={tender_id}"


def digest_permalink(settings: Settings | None = None) -> str:
    """Dashboard pre-filtered to exactly what the digest announced."""
    settings = settings or get_settings()
    query = urlencode(
        {
            "minimum_score": settings.slack_min_score,
            "active_only": "true",
            "sort": "first_seen_desc",
        }
    )
    return f"{settings.app_base_url}/?{query}"


def _escape(text: str) -> str:
    """Slack mrkdwn requires these three entities escaped."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def safe_url(url: str | None) -> str | None:
    """Return ``url`` only if it is safe to place inside Slack's <url|text> syntax.

    Notice URLs arrive from eight external feeds. Slack's link syntax is
    delimited by ``<``, ``|`` and ``>``, so a URL containing any of them can
    close the link early and inject arbitrary mrkdwn - including a link whose
    visible text says one thing and whose target is another. A non-http(s)
    scheme is refused outright.
    """
    if not url:
        return None
    candidate = url.strip()
    if not candidate.lower().startswith(("http://", "https://")):
        return None
    if any(ch in candidate for ch in "<>|") or any(ch in candidate for ch in "\n\r\t"):
        return None
    return candidate


def _clip(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def tender_blocks(tender: Tender, settings: Settings, now: datetime | None = None) -> list[dict]:
    """The Block Kit fragment for one tender."""
    now = now or utcnow()
    fit = FIT_LABELS.get(tender.fit_status, tender.fit_status)
    deployment = DEPLOYMENT_LABELS.get(tender.deployment_fit, tender.deployment_fit)
    urgency, deadline_label = deadline_urgency(tender.deadline, now)
    marker = {"urgent": ":red_circle:", "soon": ":large_yellow_circle:", "gone": ":black_circle:"}.get(
        urgency, ":large_green_circle:"
    )
    deadline_text = (
        f"{format_dhaka(tender.deadline, with_time=False)} · {deadline_label}"
        if tender.deadline
        else deadline_label
    )
    title = _escape(_clip(tender.title or "(untitled notice)", 220))
    blocks: list[dict] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*<{tender_permalink(tender.id, settings)}|{title}>*\n"
                    f"`{tender.relevance_score}` · {fit} · {deployment}"
                ),
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Buyer*\n{_escape(_clip(tender.buyer_name or '—', 60))}"},
                {
                    "type": "mrkdwn",
                    "text": f"*Country*\n{_escape(_clip(tender.buyer_country or '—', 40))}",
                },
                {"type": "mrkdwn", "text": f"*Deadline*\n{marker} {deadline_text}"},
                {
                    "type": "mrkdwn",
                    "text": f"*Est. value*\n{format_value(tender.estimated_value, tender.currency)}",
                },
            ],
        },
    ]
    elements: list[dict] = []
    reasons = tender.relevance_reasons or []
    if reasons:
        elements.append({"type": "mrkdwn", "text": f":white_check_mark: {_escape(_clip(reasons[0], 200))}"})
    tail = f"`{_escape(tender.source)}`"
    notice_url = safe_url(tender.source_url)
    if notice_url:
        tail += f" · <{notice_url}|Original notice>"
    elements.append({"type": "mrkdwn", "text": tail})
    blocks.append({"type": "context", "elements": elements})
    return blocks


def build_digest(
    tenders: list[Tender],
    settings: Settings | None = None,
    *,
    total_candidates: int | None = None,
    trigger: str = "cron",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Block Kit payload announcing ``tenders``, capped at SLACK_MAX_ITEMS."""
    settings = settings or get_settings()
    now = now or utcnow()
    total = total_candidates if total_candidates is not None else len(tenders)
    shown = tenders[: max(1, min(settings.slack_max_items, MAX_ITEMS_HARD_CAP))]
    noun = "tender" if total == 1 else "tenders"

    blocks: list[dict] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"{total} new {noun} scoring {settings.slack_min_score}+"},
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        f"{format_dhaka(now)} (Dhaka) · trigger `{trigger}` · "
                        f"{settings.slack_channel_label}"
                    ),
                }
            ],
        },
        {"type": "divider"},
    ]
    for index, tender in enumerate(shown):
        blocks.extend(tender_blocks(tender, settings, now))
        if index < len(shown) - 1:
            blocks.append({"type": "divider"})

    remaining = total - len(shown)
    footer = f"<{digest_permalink(settings)}|Open the dashboard>"
    if remaining > 0:
        footer = f"*+{remaining} more* · <{digest_permalink(settings)}|see all {total} in the dashboard>"
    blocks.append({"type": "divider"})
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": footer}]})

    return {
        "text": f"{total} new {noun} scoring {settings.slack_min_score}+ on Tender Monitor",
        "blocks": blocks,
    }


def build_heartbeat(
    settings: Settings | None = None,
    *,
    run_summary: str = "",
    trigger: str = "cron",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Silence must never be ambiguous: say the run happened and found nothing."""
    settings = settings or get_settings()
    now = now or utcnow()
    detail = f" · {run_summary}" if run_summary else ""
    text = (
        f":white_check_mark: Ran at {format_dhaka(now)} (Dhaka) · trigger `{trigger}`{detail} · "
        f"nothing scored {settings.slack_min_score}+. No action needed."
    )
    return {
        "text": f"Tender Monitor ran, nothing scored {settings.slack_min_score}+",
        "blocks": [{"type": "context", "elements": [{"type": "mrkdwn", "text": text}]}],
    }


# --- delivery --------------------------------------------------------------


RETRYABLE_STATUS = (429, 500, 502, 503, 504)


def _retry_after(response: httpx.Response, fallback: float) -> float:
    """Honour Slack's Retry-After, as the connectors do for the tender APIs."""
    raw = response.headers.get("Retry-After")
    if raw:
        try:
            return max(0.0, min(60.0, float(raw)))
        except ValueError:
            pass
    return fallback


def post_webhook(
    payload: dict[str, Any],
    settings: Settings | None = None,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[int | None, str | None]:
    """POST to the incoming webhook. Returns (status_code, error). Never raises.

    Retries only when Slack *answered* with a retryable status (429 or 5xx): a
    reply proves the message was rejected, so re-sending cannot duplicate it. A
    transport error is never retried, because the request may already have been
    delivered and an incoming webhook has no idempotency key. A 4xx other than
    429 is not retried either: an invalid payload will not become valid.

    Returns ``(None, error)`` for the ambiguous transport case, which the caller
    records as UNCONFIRMED rather than FAILED.
    """
    settings = settings or get_settings()
    owns = client is None
    client = client or httpx.Client(timeout=settings.slack_timeout_seconds)
    attempts = max(1, settings.max_retries)
    last: tuple[int | None, str | None] = (None, "no attempt was made")
    try:
        for attempt in range(1, attempts + 1):
            try:
                response = client.post(settings.slack_webhook_url, json=payload)
            except Exception as exc:
                # Ambiguous: the request may already have reached Slack. An
                # incoming webhook has no idempotency key, so retrying could
                # post the digest twice. Stop and report it as unconfirmed.
                return None, redact(f"{type(exc).__name__}: {exc}", settings)

            if response.status_code < 400:
                return response.status_code, None

            # Slack replies with a bare reason string, e.g. "invalid_payload".
            last = (response.status_code, redact(response.text[:500], settings))
            if response.status_code not in RETRYABLE_STATUS or attempt == attempts:
                break
            delay = _retry_after(response, settings.retry_backoff_seconds * attempt)
            log_ctx(
                logger,
                logging.WARNING,
                "slack post retrying",
                status=response.status_code,
                attempt=attempt,
                delay=delay,
            )
            sleep(delay)
        return last
    finally:
        if owns:
            client.close()


def notify_new_tenders(
    db: Session,
    *,
    since: datetime,
    batch_id: str,
    trigger: str = "cron",
    settings: Settings | None = None,
    run_summary: str = "",
    now: datetime | None = None,
    client: httpx.Client | None = None,
    dry_run: bool = False,
    sleep: Callable[[float], None] = time.sleep,
) -> DigestOutcome:
    """Announce this run's new qualifying tenders. Exactly once, or not at all.

    ``dry_run`` builds the exact payload and returns it without POSTing and
    without claiming anything. Used by CI, where the database is ephemeral: with
    no durable ledger, a real post could not honour the at-most-once guarantee.
    """
    settings = settings or get_settings()
    now = now or utcnow()
    channel = settings.slack_channel_label

    if not settings.enable_slack_notifications:
        return DigestOutcome(status="disabled", error="ENABLE_SLACK_NOTIFICATIONS is false")
    if not settings.slack_webhook_url and not dry_run:
        return DigestOutcome(status="disabled", error="SLACK_WEBHOOK_URL is not set")

    candidates = qualifying_tenders(db, since, settings, now)

    if dry_run:
        payload = (
            build_digest(candidates, settings, total_candidates=len(candidates), trigger=trigger, now=now)
            if candidates
            else build_heartbeat(settings, run_summary=run_summary, trigger=trigger, now=now)
        )
        log_ctx(logger, logging.INFO, "slack dry run", candidates=len(candidates), posted=0)
        return DigestOutcome(
            status="dry_run",
            candidates=len(candidates),
            posted=0,
            tender_ids=[t.id for t in candidates],
            payload=payload,
        )

    claimed, suppressed = claim(db, candidates, batch_id, trigger, settings, now)

    if not claimed:
        payload = build_heartbeat(settings, run_summary=run_summary, trigger=trigger, now=now)
        code, error = post_webhook(payload, settings, client, sleep)
        outcome = DigestOutcome(
            status="heartbeat" if error is None else "failed",
            candidates=len(candidates),
            suppressed=suppressed,
            response_code=code,
            error=error,
        )
        log_ctx(
            logger,
            logging.INFO if error is None else logging.ERROR,
            "slack heartbeat posted" if error is None else "slack heartbeat failed",
            candidates=len(candidates),
            suppressed=suppressed,
            code=code,
        )
        return outcome

    payload = build_digest(claimed, settings, total_candidates=len(claimed), trigger=trigger, now=now)
    code, error = post_webhook(payload, settings, client, sleep)
    if error is None:
        settle(db, claimed, channel, SENT, code, None, now, batch_id=batch_id)
        log_ctx(
            logger,
            logging.INFO,
            "slack digest posted",
            posted=len(claimed),
            suppressed=suppressed,
            code=code,
        )
        return DigestOutcome(
            status="sent",
            candidates=len(candidates),
            posted=len(claimed),
            suppressed=suppressed,
            response_code=code,
            tender_ids=[t.id for t in claimed],
        )

    # A status code means Slack answered and rejected it: nothing was delivered,
    # so release the claims as 'failed' and let the next run retry. No status
    # code means the request left this process and may have arrived - retrying
    # would risk a duplicate, so it is recorded as unconfirmed, which blocks
    # re-announcement and shows up as degraded for a human to resolve.
    ambiguous = code is None
    settle(
        db,
        claimed,
        channel,
        UNCONFIRMED if ambiguous else FAILED,
        code,
        error,
        now,
        batch_id=batch_id,
    )
    log_ctx(
        logger,
        logging.ERROR,
        "slack digest unconfirmed" if ambiguous else "slack digest failed",
        attempted=len(claimed),
        code=code,
        error=error,
    )
    return DigestOutcome(
        status="unconfirmed" if ambiguous else "failed",
        candidates=len(candidates),
        posted=0,
        suppressed=suppressed,
        response_code=code,
        error=error,
        tender_ids=[t.id for t in claimed],
    )
