"""Fetch orchestration + upserts.

* one FetchRun row per source per run, so one failing source cannot fail the run
* upsert on (source, source_notice_id); first_seen_at is never touched
* content_hash decides created / updated / unchanged
* every stored notice is scored by app.services.relevance
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.base import ConnectorError, NormalizedTender
from app.connectors.registry import build_connector, enabled_sources
from app.db import SessionLocal
from app.logging_config import log_ctx
from app.models import FetchRun, Tender, utcnow
from app.services.relevance import RelevanceEngine, get_engine
from app.settings import Settings, get_settings

logger = logging.getLogger(__name__)

_running: set[str] = set()
_lock = asyncio.Lock()

#: Strong references to in-flight background sweeps.
#:
#: ``asyncio.create_task`` leaves the event loop holding only a *weak* reference
#: to the task, so a sweep whose Task object nobody keeps can be garbage
#: collected part-way through - and a full sweep spends about thirteen minutes
#: inside one ``await``, which is a long time to be collectable. The symptom is
#: not an error: the FetchRun rows simply stay at ``running`` until
#: ``reap_interrupted_runs`` closes them out an hour later, so the sweep looks
#: like it is still going and the dashboard's Fetch button stays disabled behind
#: the single-flight guard. Holding the reference until the task completes is
#: the documented remedy.
_background_tasks: set[asyncio.Task] = set()

SCORED_FIELDS = (
    "relevance_score",
    "relevance_category",
    "fit_status",
    "deployment_fit",
    "relevance_reasons",
    "disqualifiers",
    "review_flags",
    "topic_relevance_score",
    "product_fit_score",
    "procurement_intent_score",
    "is_actionable",
)

COPIED_FIELDS = (
    "source_url",
    "reference_number",
    "title",
    "description",
    "buyer_name",
    "buyer_country",
    "delivery_location",
    "publication_date",
    "deadline",
    "source_updated_at",
    "source_timezone",
    "status",
    "procurement_stage",
    "notice_type",
    "estimated_value",
    "currency",
    "classification_codes",
    "document_urls",
    "language",
    "raw_payload",
)


@dataclass
class UpsertStats:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    failed: int = 0

    @property
    def received(self) -> int:
        return self.created + self.updated + self.unchanged + self.failed


def window(days_back: int | None = None, settings: Settings | None = None) -> tuple[datetime, datetime]:
    """Overlapping lookback window (always >= FETCH_MIN_LOOKBACK_HOURS)."""
    settings = settings or get_settings()
    days = days_back if days_back is not None else settings.fetch_lookback_days
    span = max(timedelta(days=max(0, days)), timedelta(hours=settings.fetch_min_lookback_hours))
    now = datetime.now(UTC).replace(tzinfo=None)
    return now - span, now


def upsert_tender(
    db: Session,
    tender: NormalizedTender,
    engine: RelevanceEngine | None = None,
    now: datetime | None = None,
) -> str:
    """Insert or update one notice. Returns 'created' | 'updated' | 'unchanged'."""
    engine = engine or get_engine()
    now = now or utcnow()
    existing = db.execute(
        select(Tender).where(
            Tender.source == tender.source,
            Tender.source_notice_id == tender.source_notice_id,
        )
    ).scalar_one_or_none()

    content_hash = tender.content_hash
    if existing is None:
        row = Tender(
            source=tender.source,
            source_notice_id=tender.source_notice_id,
            content_hash=content_hash,
            first_seen_at=now,
            last_seen_at=now,
        )
        _copy_fields(tender, row)
        _apply_score(row, engine, now)
        db.add(row)
        db.commit()
        return "created"

    existing.last_seen_at = now
    if existing.content_hash == content_hash:
        db.commit()
        return "unchanged"

    _copy_fields(tender, existing)
    existing.content_hash = content_hash
    existing.updated_at = now
    _apply_score(existing, engine, now)
    db.commit()
    return "updated"


def _copy_fields(tender: NormalizedTender, row: Tender) -> None:
    for field in COPIED_FIELDS:
        setattr(row, field, getattr(tender, field))


def _apply_score(row: Tender, engine: RelevanceEngine, now: datetime | None = None) -> None:
    result = engine.score_obj(row, now=now)
    for field, value in result.as_dict().items():
        setattr(row, field, value)


def store_tenders(
    db: Session,
    tenders: Iterable[NormalizedTender],
    engine: RelevanceEngine | None = None,
) -> UpsertStats:
    """Upsert a batch. A malformed record only loses itself, not the batch."""
    engine = engine or get_engine()
    stats = UpsertStats()
    for tender in tenders:
        try:
            outcome = upsert_tender(db, tender, engine)
        except Exception:
            db.rollback()
            stats.failed += 1
            log_ctx(
                logger,
                logging.WARNING,
                "failed to store notice",
                source=tender.source,
                notice=tender.source_notice_id,
            )
            continue
        setattr(stats, outcome, getattr(stats, outcome) + 1)
    return stats


def rescore_all(db: Session, engine: RelevanceEngine | None = None) -> int:
    """Re-run the relevance engine over every stored notice."""
    engine = engine or get_engine(None)
    rows = db.execute(select(Tender)).scalars().all()
    now = utcnow()
    for row in rows:
        _apply_score(row, engine, now)
        row.updated_at = now
    db.commit()
    return len(rows)


# --- run orchestration -----------------------------------------------------


def _create_runs(
    sources: Sequence[str],
    date_from: datetime,
    date_to: datetime,
    trigger: str,
    batch_id: str | None = None,
) -> dict[str, int]:
    """Create one queued FetchRun row per source and return {source: run_id}."""
    db = SessionLocal()
    try:
        rows = [
            FetchRun(
                source=source,
                status="queued",
                started_at=utcnow(),
                window_from=date_from,
                window_to=date_to,
                trigger=trigger,
                batch_id=batch_id,
            )
            for source in sources
        ]
        db.add_all(rows)
        db.commit()
        return {row.source: row.id for row in rows}
    finally:
        db.close()


async def _execute(
    run_id: int,
    source: str,
    date_from: datetime,
    date_to: datetime,
    settings: Settings,
) -> int:
    """Run one connector and record the outcome. Never raises."""
    db = SessionLocal()
    run = db.get(FetchRun, run_id)
    if run is None:  # pragma: no cover - defensive
        db.close()
        return run_id
    run.status = "running"
    run.started_at = utcnow()
    db.commit()
    try:
        connector = build_connector(source, settings)
        reason = connector.unavailable_reason()
        if reason:
            run.status = "skipped"
            run.error_message = reason
            log_ctx(logger, logging.INFO, "source skipped", source=source, reason=reason)
        else:
            tenders = await connector.fetch(date_from, date_to)
            stats = store_tenders(db, tenders, get_engine())
            run.records_received = len(tenders)
            run.records_created = stats.created
            run.records_updated = stats.updated
            run.records_skipped = stats.failed
            run.status = "success" if stats.failed == 0 else "partial"
            log_ctx(
                logger,
                logging.INFO,
                "source fetch finished",
                source=source,
                received=len(tenders),
                created=stats.created,
                updated=stats.updated,
                unchanged=stats.unchanged,
                failed=stats.failed,
            )
    except asyncio.CancelledError:
        # The process is going away mid-fetch (a restart, a shutdown). Settle the
        # row before re-raising: CancelledError is a BaseException, so the
        # `except Exception` below never saw it, and the `finally` stamped
        # finished_at while status stayed at the "running" set on entry. That row
        # is self-contradictory, and operator._sweep_in_flight() reads it as a
        # live sweep - so the dashboard's Fetch button answered 409 for a full
        # STALE_RUN_MINUTES after any restart. Observed in production.
        run.status = "failed"
        run.error_message = (
            "Interrupted: the process running this fetch stopped before it finished. "
            "Ingested notices were committed as they arrived; re-run to pick up the rest."
        )
        log_ctx(logger, logging.WARNING, "source fetch interrupted", source=source)
        raise
    except ConnectorError as exc:
        run.status = "failed"
        run.error_message = str(exc)[:2000]
        log_ctx(
            logger, logging.ERROR, "source fetch failed", source=source, error=exc.message, status=exc.status
        )
    except Exception as exc:  # a bug in one connector must not kill the run
        run.status = "failed"
        run.error_message = f"{type(exc).__name__}: {exc}"[:2000]
        log_ctx(logger, logging.ERROR, "source fetch crashed", source=source, error=type(exc).__name__)
    finally:
        run.finished_at = utcnow()
        db.commit()
        db.close()
    return run_id


async def _run_sources(
    run_ids: dict[str, int],
    date_from: datetime,
    date_to: datetime,
    settings: Settings,
) -> list[int]:
    sources = list(run_ids)
    async with _lock:
        sources = [s for s in sources if s not in _running]
        _running.update(sources)
    try:
        results = await asyncio.gather(
            *(_execute(run_ids[s], s, date_from, date_to, settings) for s in sources),
            return_exceptions=True,
        )
    finally:
        async with _lock:
            _running.difference_update(sources)
    return [r for r in results if isinstance(r, int)]


def _plan(
    sources: Sequence[str] | None, days_back: int | None, settings: Settings
) -> tuple[list[str], list[str], datetime, datetime]:
    requested = list(sources) if sources else enabled_sources(settings)
    busy = [s for s in requested if s in _running]
    selected = [s for s in requested if s not in busy]
    date_from, date_to = window(days_back, settings)
    return selected, busy, date_from, date_to


async def run_fetch(
    sources: Sequence[str] | None = None,
    days_back: int | None = None,
    trigger: str = "manual",
    settings: Settings | None = None,
    batch_id: str | None = None,
) -> list[int]:
    """Fetch the given sources concurrently and wait for completion."""
    settings = settings or get_settings()
    selected, _busy, date_from, date_to = _plan(sources, days_back, settings)
    if not selected:
        return []
    run_ids = _create_runs(selected, date_from, date_to, trigger, batch_id)
    return await _run_sources(run_ids, date_from, date_to, settings)


async def start_fetch(
    sources: Sequence[str] | None = None,
    days_back: int | None = None,
    trigger: str = "manual",
    settings: Settings | None = None,
    batch_id: str | None = None,
) -> dict[str, object]:
    """Create the FetchRun rows, then keep fetching in the background."""
    settings = settings or get_settings()
    selected, busy, date_from, date_to = _plan(sources, days_back, settings)
    run_ids = _create_runs(selected, date_from, date_to, trigger, batch_id) if selected else {}
    if run_ids:
        task = asyncio.create_task(_run_sources(run_ids, date_from, date_to, settings))
        # See _background_tasks: without a strong reference here the sweep is
        # collectable mid-flight and dies without raising anything.
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
    return {
        "runs": [{"id": rid, "source": source, "status": "queued"} for source, rid in run_ids.items()],
        "run_ids": list(run_ids.values()),
        "skipped_sources": busy,
        "window_from": date_from,
        "window_to": date_to,
    }


def running_sources() -> set[str]:
    return set(_running)
