"""Scheduled fetching (APScheduler, in-process, no broker)."""

from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.logging_config import log_ctx
from app.services.ingest import run_fetch
from app.settings import Settings

logger = logging.getLogger(__name__)

JOB_ID = "fetch-all-sources"
_scheduler: AsyncIOScheduler | None = None


async def _job() -> None:
    run_ids = await run_fetch(trigger="scheduled")
    log_ctx(logger, logging.INFO, "scheduled fetch dispatched", runs=len(run_ids))


def start_scheduler(settings: Settings) -> AsyncIOScheduler | None:
    """Idempotent: a uvicorn --reload restart replaces the job instead of adding one."""
    global _scheduler
    if not settings.enable_scheduler:
        log_ctx(logger, logging.INFO, "scheduler disabled")
        return None
    if _scheduler is not None:
        return _scheduler
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        _job,
        trigger="interval",
        hours=max(1, settings.fetch_interval_hours),
        id=JOB_ID,
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,
    )
    scheduler.start()
    _scheduler = scheduler
    log_ctx(logger, logging.INFO, "scheduler started", interval_hours=settings.fetch_interval_hours)
    return scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
