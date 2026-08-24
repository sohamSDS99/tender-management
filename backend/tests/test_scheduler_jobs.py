"""The in-process scheduler: two Dhaka cron triggers, and honest reporting."""

from __future__ import annotations

from zoneinfo import ZoneInfo

import pytest

from app.services.scheduler import (
    job_id,
    reschedule,
    scheduler_state,
    set_trigger,
    start_scheduler,
    stop_scheduler,
)
from app.settings import Settings


@pytest.fixture(autouse=True)
async def _always_stop():
    """Shut down inside the test's own event loop.

    AsyncIOScheduler.shutdown() touches the loop it was started on. A plain
    (sync) fixture tears down after pytest-asyncio has closed that loop, which
    raises "Event loop is closed" and - worse - leaves the module-level
    _scheduler set, so the next test sees another test's jobs.
    """
    yield
    stop_scheduler()


def enabled_settings(**overrides) -> Settings:
    base = {"_env_file": None, "enable_scheduler": True, "database_url": "sqlite://"}
    base.update(overrides)
    return Settings(**base)


async def test_two_cron_triggers_are_registered_in_dhaka_time() -> None:
    scheduler = start_scheduler(enabled_settings(), hours=[0, 12])
    assert scheduler is not None
    jobs = {job.id: job for job in scheduler.get_jobs()}
    assert set(jobs) == {job_id(0), job_id(12)}
    for job in jobs.values():
        assert str(job.trigger).startswith("cron["), f"{job.id} is not a cron trigger"
        assert job.next_run_time.tzinfo is not None
        assert job.next_run_time.tzinfo.key == "Asia/Dhaka"
    assert {job.next_run_time.hour for job in jobs.values()} == {0, 12}


async def test_the_utc_instants_are_eighteen_hundred_and_six_hundred() -> None:
    """The same pair the GitHub Actions cron expressions encode."""
    scheduler = start_scheduler(enabled_settings(), hours=[0, 12])
    assert scheduler is not None
    utc_hours = {job.next_run_time.astimezone(ZoneInfo("UTC")).hour for job in scheduler.get_jobs()}
    assert utc_hours == {18, 6}


async def test_disabled_by_default_and_reports_nothing_scheduled() -> None:
    assert start_scheduler(Settings(_env_file=None, database_url="sqlite://")) is None
    assert scheduler_state() == {"running": False, "jobs": []}


async def test_starting_twice_does_not_duplicate_jobs() -> None:
    """A uvicorn --reload restart must replace the jobs, not stack them."""
    first = start_scheduler(enabled_settings(), hours=[0, 12])
    second = start_scheduler(enabled_settings(), hours=[0, 12])
    assert first is second
    assert len(first.get_jobs()) == 2


async def test_state_reports_what_is_actually_registered() -> None:
    start_scheduler(enabled_settings(), hours=[0, 12])
    state = scheduler_state()
    assert state["running"] is True
    jobs = state["jobs"]
    assert len(jobs) == 2
    for job in jobs:
        assert job["next_run_at"] is not None
        # Everything crossing into the database or the API is naive UTC.
        assert job["next_run_at"].tzinfo is None
        assert job["next_run_at"].hour in (18, 6)


async def test_custom_hours_are_honoured() -> None:
    scheduler = start_scheduler(enabled_settings(), hours=[3, 15])
    assert {job.next_run_time.hour for job in scheduler.get_jobs()} == {3, 15}


# --- runtime rescheduling: the point is that no restart is needed ----------


async def test_rescheduling_moves_the_live_jobs() -> None:
    scheduler = start_scheduler(enabled_settings(), hours=[0, 12])
    assert {job.next_run_time.hour for job in scheduler.get_jobs()} == {0, 12}

    assert reschedule([7, 19], enabled_settings()) is True

    jobs = scheduler.get_jobs()
    assert {job.next_run_time.hour for job in jobs} == {7, 19}
    assert {job.id for job in jobs} == {job_id(7), job_id(19)}
    # The old jobs are gone, not merely shadowed.
    assert len(jobs) == 2


async def test_rescheduling_to_a_single_sweep_removes_the_other() -> None:
    scheduler = start_scheduler(enabled_settings(), hours=[0, 12])
    reschedule([6], enabled_settings())
    assert [job.id for job in scheduler.get_jobs()] == [job_id(6)]


async def test_rescheduling_to_more_sweeps_adds_them() -> None:
    scheduler = start_scheduler(enabled_settings(), hours=[0])
    reschedule([0, 6, 12, 18], enabled_settings())
    assert {job.next_run_time.hour for job in scheduler.get_jobs()} == {0, 6, 12, 18}


async def test_rescheduling_reports_false_when_nothing_runs_here() -> None:
    """Not an error: the value is persisted and whoever owns the schedule picks it up."""
    assert reschedule([7, 19], enabled_settings()) is False


async def test_the_utc_instants_follow_a_reschedule() -> None:
    start_scheduler(enabled_settings(), hours=[0, 12])
    reschedule([5], enabled_settings())
    state = scheduler_state()
    # 05:00 Asia/Dhaka is 23:00 UTC the previous day.
    assert [job["next_run_at"].hour for job in state["jobs"]] == [23]


# --- pausing and resuming from the dashboard (docs/DECISIONS.md D21) -------


async def test_pausing_stops_the_scheduler_so_the_next_sweep_does_not_happen() -> None:
    """The failure this guards against is a pause that only changes a label."""
    start_scheduler(enabled_settings(), hours=[0, 12])
    assert scheduler_state()["running"] is True

    assert set_trigger(False, [0, 12], enabled_settings()) is False
    state = scheduler_state()
    assert state["running"] is False
    assert state["jobs"] == []


async def test_resuming_starts_a_scheduler_in_a_process_that_had_none() -> None:
    """An operator switching sweeps on where ENABLE_SCHEDULER=false still gets one.

    A switch that stores "on" without starting anything is the exact failure
    scheduler_state() exists to expose - so it must genuinely start.
    """
    off = Settings(_env_file=None, database_url="sqlite://")
    assert start_scheduler(off, hours=[0, 12]) is None

    assert set_trigger(True, [0, 12], off) is True
    state = scheduler_state()
    assert state["running"] is True
    assert {job["id"] for job in state["jobs"]} == {job_id(0), job_id(12)}


async def test_resuming_applies_the_hours_in_force_not_the_ones_it_started_with() -> None:
    start_scheduler(enabled_settings(), hours=[0, 12])
    set_trigger(True, [7, 19], enabled_settings())
    assert {job["id"] for job in scheduler_state()["jobs"]} == {job_id(7), job_id(19)}


async def test_a_pause_then_a_resume_leaves_exactly_one_job_per_hour() -> None:
    """Toggling must not stack duplicate jobs for the same hour."""
    settings = enabled_settings()
    start_scheduler(settings, hours=[0, 12])
    for _ in range(3):
        set_trigger(False, [0, 12], settings)
        set_trigger(True, [0, 12], settings)
    assert {job["id"] for job in scheduler_state()["jobs"]} == {job_id(0), job_id(12)}


async def test_the_stored_decision_beats_the_environment_on_start() -> None:
    """A pause has to survive a restart, or it silently undoes itself."""
    assert start_scheduler(enabled_settings(), hours=[0, 12], enabled=False) is None
    assert scheduler_state()["running"] is False

    off = Settings(_env_file=None, database_url="sqlite://")
    assert start_scheduler(off, hours=[0, 12], enabled=True) is not None
    assert scheduler_state()["running"] is True


async def test_stopping_always_drops_the_reference() -> None:
    """Otherwise the dashboard reports a scheduler that can no longer fire."""
    start_scheduler(enabled_settings(), hours=[0, 12])
    stop_scheduler()
    stop_scheduler()  # idempotent
    assert scheduler_state() == {"running": False, "jobs": []}
