"""The window an operator sweep actually searches.

This exists because of a measured defect, not a hypothetical one. The dashboard's
"Fetch new tenders" button posted an empty body, so `days_back` arrived as None
and `ingest.window()` fell back to its floor - a 72-hour overlap. That is the
*same* window the twice-daily cron sweep already covers, whose entire purpose is
to keep up with the present. By the time a human clicks the button, that window
holds nothing unseen, so the sweep reported success and created almost nothing.

Measured on 2026-08-24 against the same five connectors at the same instant:

    72 hours   ted 5   find_a_tender 18   contracts_finder 0   world_bank 11
    30 days    ted 39  find_a_tender 56   contracts_finder 8   world_bank 16

34 notices against 119. The button was not broken; it was digging in a hole the
scheduler had already emptied.

Since D26 that operator is a *signed-in* one - the fixture is `client`, not
`anon_client`, because an anonymous caller no longer reaches these endpoints at
all. Nothing else about the window changed.

A human clicking Fetch means "go and look harder", which is a different question
from the one the schedule asks. So an operator sweep gets its own, deliberately
deeper default - and the 72-hour floor inside the frozen `window()` still applies
underneath it, so nothing here can make a sweep *shallower* than the schedule's.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import FetchRun, utcnow
from app.services import ingest


@pytest.fixture
def no_connectors(monkeypatch):
    """Let the window and the run rows be real; never touch a public service.

    `_run_sources` is the seam: everything above it - planning the window,
    creating one FetchRun per source, handing out the batch id - runs for real,
    and the eight outbound connectors do not.
    """
    seen: list[dict] = []

    async def fake_run_sources(run_ids, date_from, date_to, settings):
        seen.append({"run_ids": run_ids, "date_from": date_from, "date_to": date_to})
        return list(run_ids.values())

    monkeypatch.setattr(ingest, "_run_sources", fake_run_sources)
    return seen


def window_days(body: dict) -> float:
    start = datetime.fromisoformat(body["window_from"].rstrip("Z"))
    end = datetime.fromisoformat(body["window_to"].rstrip("Z"))
    return (end - start).total_seconds() / 86400.0


# --- the defect this module exists for -------------------------------------


def test_an_operator_sweep_looks_deeper_than_the_scheduled_one(client, settings, no_connectors):
    """An empty body must not mean "repeat the sweep that already ran"."""
    response = client.post("/api/fetch", json={})
    assert response.status_code == 202
    body = response.json()
    assert body["days_back"] == settings.operator_fetch_days_back
    assert window_days(body) == pytest.approx(settings.operator_fetch_days_back, abs=0.01)


def test_no_body_at_all_behaves_the_same_as_an_empty_one(client, settings, no_connectors):
    """The dashboard sends `{}`; curl users send nothing. Both are an operator."""
    response = client.post("/api/fetch")
    assert response.status_code == 202
    assert response.json()["days_back"] == settings.operator_fetch_days_back


def test_the_default_is_deeper_than_the_schedules_overlap(settings):
    """If these ever cross, the button is back to searching an emptied window."""
    assert settings.operator_fetch_days_back * 24 > settings.fetch_min_lookback_hours


def test_an_explicit_window_still_wins(client, no_connectors):
    response = client.post("/api/fetch", json={"days_back": 7})
    assert response.status_code == 202
    body = response.json()
    assert body["days_back"] == 7
    assert window_days(body) == pytest.approx(7, abs=0.01)


def test_the_frozen_seventy_two_hour_floor_still_applies(client, settings, no_connectors):
    """Asking for nothing must not produce a window shallower than the schedule's.

    `ingest.window()` enforces FETCH_MIN_LOOKBACK_HOURS and its semantics are
    frozen; this pins that the route cannot route around it.
    """
    response = client.post("/api/fetch", json={"days_back": 0})
    assert response.status_code == 202
    assert window_days(response.json()) == pytest.approx(settings.fetch_min_lookback_hours / 24, abs=0.01)


def test_the_window_reaches_the_sources_that_will_be_queried(client, settings, no_connectors):
    """The reported window must be the one the connectors are actually handed.

    Reporting one window and searching another is exactly the class of bug that
    let this go unnoticed - the response looked right either way.
    """
    client.post("/api/fetch", json={"days_back": 12})
    assert len(no_connectors) == 1
    handed = no_connectors[0]
    assert (handed["date_to"] - handed["date_from"]).total_seconds() / 86400.0 == pytest.approx(12, abs=0.01)


def test_the_dashboard_is_told_the_depth_rather_than_keeping_its_own_copy(client, settings):
    """One source of truth for the window, like the score bands in /api/stats.

    The frontend needs the number to state it at the point of action. If it kept
    its own constant instead, the two would drift and the button could quietly go
    back to searching the window the scheduler has already emptied.
    """
    body = client.get("/api/automation").json()
    assert body["operator_fetch_days_back"] == settings.operator_fetch_days_back


# --- provenance: an operator sweep is a sweep, and groups like one ----------


def test_an_operator_sweep_is_grouped_by_a_batch_id(client, db_session, no_connectors):
    """Without one, `automation.last_batch()` falls back to grouping on window_to.

    That fallback exists for rows written before the column did (D8), not as the
    normal path. An operator sweep that carries no batch id also reports
    `sent_in_last_batch: 0` regardless of what happened.
    """
    response = client.post("/api/fetch", json={})
    batch_id = response.json()["batch_id"]
    assert batch_id

    rows = db_session.execute(select(FetchRun)).scalars().all()
    assert rows, "the sweep created no run rows at all"
    assert {row.batch_id for row in rows} == {batch_id}
    assert {row.trigger for row in rows} == {"manual"}


def test_two_sweeps_do_not_share_a_batch_id(cron_client, no_connectors):
    """Each sweep gets its own id, or `last_batch()` would merge two sweeps.

    Uses the trusted client on purpose: an *operator* second sweep this soon is
    refused by the cooldown (429), which is the guard working. Only a caller
    holding CRON_SECRET controls its own timing - and since D26 that is also the
    only caller who reaches the endpoint without signing in.
    """
    first = cron_client.post("/api/fetch", json={}).json()["batch_id"]
    second = cron_client.post("/api/fetch", json={"sources": ["ted"]}).json()["batch_id"]
    assert first and second and first != second


def test_a_second_operator_sweep_is_still_refused_by_the_cooldown(client, no_connectors):
    """Widening the window must not have widened what a held-down button can do.

    A 30-day sweep is more expensive than a 3-day one, so the single-flight and
    cooldown guards matter more now, not less.
    """
    assert client.post("/api/fetch", json={}).status_code == 202
    refused = client.post("/api/fetch", json={})
    assert refused.status_code in (409, 429)
    assert "Retry-After" in refused.headers or refused.status_code == 409


# --- the background task must survive long enough to finish ----------------


async def test_the_background_sweep_is_held_by_a_strong_reference(settings, monkeypatch, db_session):
    """A sweep that the garbage collector can eat is a sweep that stops silently.

    `asyncio.create_task` leaves the loop holding only a *weak* reference, so a
    task whose result nobody keeps can be collected mid-flight - and a 14-minute
    PNCP fetch is a long time to be collectable. The symptom would be a run stuck
    at `running` until the reaper closes it out an hour later, which is precisely
    what one row in this database already looks like.
    """
    monkeypatch.setattr(ingest, "SessionLocal", db_session.info["factory"])
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_run_sources(run_ids, date_from, date_to, settings):
        started.set()
        await release.wait()
        return list(run_ids.values())

    monkeypatch.setattr(ingest, "_run_sources", slow_run_sources)

    await ingest.start_fetch(["ted"], 3, "manual", settings, batch_id="deadbeef")
    await asyncio.wait_for(started.wait(), timeout=2)

    assert ingest._background_tasks, "nothing holds the sweep; the collector may take it"

    release.set()
    await asyncio.gather(*tuple(ingest._background_tasks))
    assert not ingest._background_tasks, "the registry leaks a task per sweep"


# --- an interrupted sweep must not block the button for an hour -------------


async def test_a_cancelled_run_settles_as_failed_instead_of_claiming_to_run(
    settings, monkeypatch, db_session
):
    """A restart mid-sweep left a row saying `running` *and* carrying a finish time.

    `asyncio.CancelledError` inherits from BaseException, so `_execute`'s
    `except Exception` never saw it: the coroutine died, the `finally` stamped
    `finished_at`, and `status` stayed at the `running` it was set to on entry.
    That row is self-contradictory, and `_sweep_in_flight()` reads it as a live
    sweep - so every operator fetch was refused with 409 for a full
    STALE_RUN_MINUTES afterwards. Observed for real: a frontend rebuild restarted
    the API mid-sweep and blocked the button for the next hour.
    """
    from app.connectors.base import TenderConnector

    class Hanging(TenderConnector):
        source_name = "ted"

        async def fetch(self, date_from, date_to):
            raise asyncio.CancelledError()

    monkeypatch.setattr(ingest, "SessionLocal", db_session.info["factory"])
    monkeypatch.setattr(
        ingest, "build_connector", lambda source, s=None, transport=None, **kw: Hanging(settings)
    )
    run_ids = ingest._create_runs(["ted"], utcnow(), utcnow(), "manual", "batch-cancel")

    with pytest.raises(asyncio.CancelledError):
        await ingest._execute(run_ids["ted"], "ted", utcnow(), utcnow(), settings)

    row = db_session.get(FetchRun, run_ids["ted"])
    db_session.refresh(row)
    assert row.status == "failed", "an interrupted run must not still claim to be running"
    assert row.finished_at is not None
    assert "interrupted" in (row.error_message or "").lower()


def test_a_finished_row_never_counts_as_a_sweep_in_flight(db_session, settings):
    """Belt and braces: `finished_at` is the authority, not `status`.

    Even if some future path leaves a contradictory row behind, a run that has
    recorded a finish time is over, and must not hold the Fetch button shut.
    """
    from app.services import operator

    now = utcnow()
    db_session.add(
        FetchRun(
            source="pncp",
            status="running",
            trigger="manual",
            started_at=now - timedelta(minutes=2),
            finished_at=now - timedelta(minutes=1),
        )
    )
    db_session.commit()

    # Asserted against the single-flight check itself, not through guard_fetch:
    # a run two minutes old also trips the separate cooldown, and that refusal is
    # correct. Testing through the whole guard would pass for the wrong reason.
    assert operator._sweep_in_flight(db_session, settings, now) is False

    # And with the cooldown out of the way, the button genuinely opens again.
    no_cooldown = settings.model_copy(update={"operator_fetch_cooldown_seconds": 0})
    operator.guard_fetch(db_session, no_cooldown, now)
