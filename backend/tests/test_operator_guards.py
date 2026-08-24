"""The cost controls that replaced the shared secret on the two write endpoints.

D23 removed `CRON_SECRET` as the gate on `POST /api/fetch` and
`POST /api/tenders/rescore` so the dashboard's own buttons can work. The secret
was standing in for "do not hammer eight public services", so these are the tests
that the replacement actually does that job. If they pass while the guards are
broken, a held-down button becomes eight concurrent sweeps.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException

from app.models import KEY_LAST_RESCORE_AT, AppSetting, FetchRun
from app.services import operator

NOW = datetime(2026, 8, 24, 12, 0, 0)


def add_run(db, *, status: str, started: datetime, source: str = "ted") -> FetchRun:
    run = FetchRun(source=source, status=status, trigger="manual", started_at=started)
    db.add(run)
    db.commit()
    return run


# --- single flight ---------------------------------------------------------


def test_a_running_sweep_blocks_another_with_409(db_session, settings) -> None:
    add_run(db_session, status="running", started=NOW - timedelta(minutes=2))
    with pytest.raises(HTTPException) as caught:
        operator.guard_fetch(db_session, settings, NOW)
    assert caught.value.status_code == 409
    assert "already running" in caught.value.detail


def test_a_queued_sweep_also_blocks(db_session, settings) -> None:
    add_run(db_session, status="queued", started=NOW - timedelta(seconds=5))
    with pytest.raises(HTTPException) as caught:
        operator.guard_fetch(db_session, settings, NOW)
    assert caught.value.status_code == 409


def test_a_run_orphaned_by_a_crash_does_not_block_for_ever(db_session, settings) -> None:
    """A row left at 'running' by a killed process must not disable the button.

    Without this, one crash mid-sweep means nobody can ever start another from the
    dashboard - the exact failure reap_interrupted_runs() exists to undo.
    """
    stale = NOW - timedelta(minutes=settings.stale_run_minutes + 5)
    add_run(db_session, status="running", started=stale)
    operator.guard_fetch(db_session, settings, NOW)  # does not raise


# --- cooldown -------------------------------------------------------------


def test_a_recent_sweep_is_refused_with_429_and_retry_after(db_session, settings) -> None:
    add_run(db_session, status="success", started=NOW - timedelta(seconds=60))
    with pytest.raises(HTTPException) as caught:
        operator.guard_fetch(db_session, settings, NOW)
    assert caught.value.status_code == 429
    # A 429 without Retry-After tells the caller to guess.
    assert int(caught.value.headers["Retry-After"]) > 0
    assert "eight public services" in caught.value.detail


def test_the_cooldown_expires(db_session, settings) -> None:
    elapsed = settings.operator_fetch_cooldown_seconds + 1
    add_run(db_session, status="success", started=NOW - timedelta(seconds=elapsed))
    operator.guard_fetch(db_session, settings, NOW)  # does not raise


def test_a_first_ever_sweep_is_allowed(db_session, settings) -> None:
    """No history must not read as 'ran just now'."""
    operator.guard_fetch(db_session, settings, NOW)


def test_a_zero_cooldown_disables_only_the_cooldown(db_session, settings) -> None:
    instant = settings.model_copy(update={"operator_fetch_cooldown_seconds": 0})
    add_run(db_session, status="success", started=NOW - timedelta(seconds=1))
    operator.guard_fetch(db_session, instant, NOW)  # cooldown off

    add_run(db_session, status="running", started=NOW, source="pncp")
    with pytest.raises(HTTPException) as caught:
        operator.guard_fetch(db_session, instant, NOW)
    assert caught.value.status_code == 409, "single-flight must survive a zero cooldown"


# --- the switch -----------------------------------------------------------


def test_operator_actions_can_be_switched_off(db_session, settings) -> None:
    closed = settings.model_copy(update={"allow_operator_actions": False})
    for guard in (operator.guard_fetch, operator.guard_rescore):
        with pytest.raises(HTTPException) as caught:
            guard(db_session, closed, NOW)
        assert caught.value.status_code == 403
        assert "ALLOW_OPERATOR_ACTIONS" in caught.value.detail


# --- rescore --------------------------------------------------------------


def test_rescore_is_allowed_when_it_has_never_run(db_session, settings) -> None:
    operator.guard_rescore(db_session, settings, NOW)


def test_a_recent_rescore_is_refused(db_session, settings) -> None:
    operator.mark_rescore(db_session, NOW - timedelta(seconds=10))
    with pytest.raises(HTTPException) as caught:
        operator.guard_rescore(db_session, settings, NOW)
    assert caught.value.status_code == 429
    assert int(caught.value.headers["Retry-After"]) > 0


def test_the_rescore_cooldown_expires(db_session, settings) -> None:
    elapsed = settings.operator_rescore_cooldown_seconds + 1
    operator.mark_rescore(db_session, NOW - timedelta(seconds=elapsed))
    operator.guard_rescore(db_session, settings, NOW)


def test_marking_twice_updates_rather_than_duplicating(db_session, settings) -> None:
    operator.mark_rescore(db_session, NOW - timedelta(hours=1))
    operator.mark_rescore(db_session, NOW)
    assert db_session.query(AppSetting).filter_by(key=KEY_LAST_RESCORE_AT).count() == 1


def test_an_unreadable_stamp_does_not_lock_the_action_out(db_session, settings) -> None:
    """A hand-edited row must fail open, not brick the button for ever."""
    db_session.add(AppSetting(key=KEY_LAST_RESCORE_AT, value="not-a-date"))
    db_session.commit()
    operator.guard_rescore(db_session, settings, NOW)


def test_a_sweep_and_a_rescore_have_independent_cooldowns(db_session, settings) -> None:
    """Re-scoring spends no outbound request, so a sweep must not block it."""
    add_run(db_session, status="success", started=NOW)
    operator.guard_rescore(db_session, settings, NOW)  # unaffected by the sweep
