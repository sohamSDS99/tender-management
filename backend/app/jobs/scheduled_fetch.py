"""``python -m app.jobs.scheduled_fetch`` - one scheduled run, start to finish.

Window -> fetch -> score -> notify, in a single process that exits with a
meaningful code. This is the *authoritative* shape of a run: the GitHub Actions
workflow and the in-process APScheduler fallback both do exactly this and
nothing more, so there is only one code path to reason about.

Exit codes
----------
0   ingest finished (fully or partially) and notification is settled
1   total ingest failure - every source failed, or the run could not start
2   ingest is safe but the Slack notification failed, or its delivery could
    not be confirmed (degraded, never silent)

Scoring happens inside ``ingest.upsert_tender`` as each notice is stored, so
there is no separate scoring pass to get out of step with it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select

from app.db import SessionLocal, init_db
from app.jobs.schedule import next_run_local
from app.logging_config import configure_logging, log_ctx
from app.models import FetchRun, SlackNotification, Tender, utcnow
from app.services import automation, ingest, notifier
from app.settings import Settings, get_settings, redact

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_INGEST_FAILED = 1
EXIT_NOTIFY_DEGRADED = 2

TERMINAL_OK = ("success", "partial", "skipped")


@dataclass
class RunReport:
    batch_id: str
    trigger: str
    started_at: datetime
    finished_at: datetime | None = None
    runs: list[dict[str, Any]] = field(default_factory=list)
    seeded: int = 0
    notification: dict[str, Any] = field(default_factory=dict)
    exit_code: int = EXIT_OK

    @property
    def received(self) -> int:
        return sum(r["records_received"] for r in self.runs)

    @property
    def created(self) -> int:
        return sum(r["records_created"] for r in self.runs)

    @property
    def updated(self) -> int:
        return sum(r["records_updated"] for r in self.runs)

    @property
    def failed_sources(self) -> list[str]:
        return [r["source"] for r in self.runs if r["status"] == "failed"]

    def summary_line(self) -> str:
        if not self.runs and self.seeded is not None and self.trigger.endswith("replay"):
            return f"seed replay · {self.seeded} fixture tender(s) inserted"
        if not self.runs:
            return f"no source ran · {self.seeded} fixture tender(s) inserted"
        return (
            f"{len(self.runs)} source(s) · {self.received} notices seen · "
            f"{self.created} new · {self.updated} updated"
            + (f" · {len(self.failed_sources)} failed" if self.failed_sources else "")
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "trigger": self.trigger,
            "started_at": self.started_at.isoformat() + "Z",
            "finished_at": (self.finished_at.isoformat() + "Z") if self.finished_at else None,
            "totals": {
                "sources": len(self.runs),
                "received": self.received,
                "created": self.created,
                "updated": self.updated,
                "failed_sources": self.failed_sources,
            },
            "seeded": self.seeded,
            "runs": self.runs,
            "notification": self.notification,
            "exit_code": self.exit_code,
        }


def _seed_fixtures(reset: bool, settings: Settings) -> int:
    """Demo replay: load the 14 committed fixtures. Never invents a tender.

    ``reset`` deletes the SEED-* rows *and* their notification ledger entries
    first, so a replay produces genuinely new tenders and therefore a genuinely
    new Slack digest - without weakening idempotency for real notices.
    """
    from app.seed import load_fixtures

    db = SessionLocal()
    try:
        if reset:
            seed_ids = [
                row_id
                for (row_id,) in db.execute(
                    select(Tender.id).where(Tender.source_notice_id.like("SEED-%"))
                ).all()
            ]
            if seed_ids:
                db.execute(delete(SlackNotification).where(SlackNotification.tender_id.in_(seed_ids)))
                db.execute(delete(Tender).where(Tender.id.in_(seed_ids)))
                db.commit()
        tenders = load_fixtures()
        stats = ingest.store_tenders(db, tenders)
    finally:
        db.close()
    log_ctx(
        logger,
        logging.INFO,
        "seed fixtures loaded",
        created=stats.created,
        updated=stats.updated,
        unchanged=stats.unchanged,
    )
    return stats.created


def _collect_runs(run_ids: list[int]) -> list[dict[str, Any]]:
    db = SessionLocal()
    try:
        rows = db.execute(select(FetchRun).where(FetchRun.id.in_(run_ids))).scalars().all() if run_ids else []
        return [
            {
                "id": r.id,
                "source": r.source,
                "status": r.status,
                "trigger": r.trigger,
                "records_received": r.records_received,
                "records_created": r.records_created,
                "records_updated": r.records_updated,
                "records_skipped": r.records_skipped,
                "error_message": redact(r.error_message) if r.error_message else None,
                "window_from": r.window_from.isoformat() + "Z" if r.window_from else None,
                "window_to": r.window_to.isoformat() + "Z" if r.window_to else None,
            }
            for r in sorted(rows, key=lambda r: r.source)
        ]
    finally:
        db.close()


async def run_once(
    sources: list[str] | None = None,
    days_back: int | None = None,
    trigger: str = "cron",
    *,
    notify: bool = True,
    dry_run_notify: bool = False,
    seed: bool = False,
    seed_reset: bool = False,
    settings: Settings | None = None,
) -> RunReport:
    """Perform one complete scheduled run. Never raises for a source failure."""
    settings = settings or get_settings()
    batch_id = uuid.uuid4().hex[:16]
    # Captured before anything is written: every tender whose immutable
    # first_seen_at is at or after this instant was created by *this* run.
    started_at = utcnow()
    report = RunReport(batch_id=batch_id, trigger=trigger, started_at=started_at)

    log_ctx(
        logger,
        logging.INFO,
        "scheduled run starting",
        batch=batch_id,
        trigger=trigger,
        seed=seed,
        notify=notify,
    )

    # A run orphaned by an earlier crash would otherwise stay "running" for ever.
    db = SessionLocal()
    try:
        automation.reap_interrupted_runs(db, settings, started_at)
    finally:
        db.close()

    if seed:
        report.seeded = _seed_fixtures(seed_reset, settings)
    else:
        run_ids = await ingest.run_fetch(sources, days_back, trigger, settings, batch_id=batch_id)
        report.runs = _collect_runs(run_ids)

    total_failure = bool(report.runs) and all(r["status"] == "failed" for r in report.runs)
    if not seed and not report.runs:
        total_failure = True  # nothing ran at all: every source disabled or busy

    if notify:
        db = SessionLocal()
        try:
            outcome = await asyncio.to_thread(
                notifier.notify_new_tenders,
                db,
                since=started_at,
                batch_id=batch_id,
                trigger=trigger,
                settings=settings,
                run_summary=report.summary_line(),
                dry_run=dry_run_notify,
            )
        finally:
            db.close()
        report.notification = outcome.as_dict()
    else:
        report.notification = {"status": "skipped", "error": "--no-notify"}

    report.finished_at = utcnow()
    if total_failure:
        report.exit_code = EXIT_INGEST_FAILED
    elif report.notification.get("status") in ("failed", "unconfirmed"):
        report.exit_code = EXIT_NOTIFY_DEGRADED
    else:
        report.exit_code = EXIT_OK

    log_ctx(
        logger,
        logging.INFO if report.exit_code == EXIT_OK else logging.ERROR,
        "scheduled run finished",
        batch=batch_id,
        received=report.received,
        created=report.created,
        slack=report.notification.get("status"),
        exit=report.exit_code,
        next_run=next_run_local().strftime("%Y-%m-%d %H:%M %Z"),
    )
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.jobs.scheduled_fetch",
        description="Run one scheduled fetch: window -> fetch -> score -> notify.",
    )
    parser.add_argument("--sources", nargs="*", default=None, help="Source names; omit for all enabled")
    parser.add_argument(
        "--days-back",
        type=int,
        default=None,
        help="Lookback window in days; use to re-run a missed window (min 72h is enforced)",
    )
    parser.add_argument(
        "--trigger",
        default="cron",
        help="Value recorded on FetchRun.trigger (cron | manual | scheduled)",
    )
    parser.add_argument("--no-notify", action="store_true", help="Fetch without posting to Slack")
    parser.add_argument(
        "--dry-run-notify",
        action="store_true",
        help="Build the Slack payload and print it, but do not POST it (used by CI)",
    )
    parser.add_argument(
        "--seed",
        action="store_true",
        help="Demo replay: load the committed fixtures instead of calling any connector",
    )
    parser.add_argument(
        "--seed-reset",
        action="store_true",
        help="With --seed: delete SEED-* rows first so the replay posts a fresh digest",
    )
    parser.add_argument("--json", action="store_true", help="Print the run report as JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level)
    try:
        init_db()
    except Exception as exc:  # a database we cannot migrate is a hard stop
        logger.error("cannot prepare the database: %s", redact(str(exc), settings))
        return EXIT_INGEST_FAILED

    report = asyncio.run(
        run_once(
            sources=args.sources or None,
            days_back=args.days_back,
            trigger=args.trigger,
            notify=not args.no_notify,
            dry_run_notify=args.dry_run_notify,
            seed=args.seed or args.seed_reset,
            seed_reset=args.seed_reset,
            settings=settings,
        )
    )
    if args.json:
        print(json.dumps(report.as_dict(), indent=2))
    else:
        print(
            f"batch {report.batch_id} · {report.summary_line()} · "
            f"slack={report.notification.get('status')} · exit={report.exit_code}"
        )
    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
