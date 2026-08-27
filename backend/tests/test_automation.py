"""GET /api/automation - what the dashboard shows instead of a fetch button."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.models import SENT, FetchRun, SlackNotification, Tender, utcnow
from app.services import automation, scheduler


def add_run(db, *, source, status, batch="batch-a", started=None, **kwargs) -> FetchRun:
    run = FetchRun(
        source=source,
        status=status,
        batch_id=batch,
        trigger="cron",
        started_at=started or datetime(2026, 8, 21, 6, 0),
        finished_at=(started or datetime(2026, 8, 21, 6, 0)) + timedelta(minutes=2),
        window_from=datetime(2026, 8, 18, 6, 0),
        window_to=started or datetime(2026, 8, 21, 6, 0),
        **kwargs,
    )
    db.add(run)
    db.commit()
    return run


def test_reports_the_dhaka_schedule_and_the_derived_cron(db_session, settings) -> None:
    status = automation.automation_status(db_session, settings)
    assert status["timezone"] == "Asia/Dhaka"
    assert status["run_hours_local"] == [0, 12]
    assert status["cron_utc"] == ["0 18 * * *", "0 6 * * *"]
    assert status["observes_dst"] is False


def test_next_run_is_stored_as_naive_utc_with_a_local_label(db_session, settings) -> None:
    status = automation.automation_status(db_session, settings)
    assert status["next_run_at"].tzinfo is None
    assert status["next_run_at"].hour in (18, 6)
    assert status["next_run_local_label"]


def test_no_history_yet_is_reported_as_no_last_run(db_session, settings) -> None:
    assert automation.automation_status(db_session, settings)["last_run"] is None


def test_last_run_aggregates_the_whole_batch(db_session, settings) -> None:
    add_run(db_session, source="ted", status="success", records_received=412, records_created=18)
    add_run(db_session, source="pncp", status="partial", records_received=96, records_created=6)
    last = automation.automation_status(db_session, settings)["last_run"]
    assert last["sources_total"] == 2
    assert last["records_received"] == 508
    assert last["records_created"] == 24
    assert last["trigger"] == "cron"


def test_a_partly_failed_batch_reports_partial_and_names_the_failure(db_session, settings) -> None:
    """Some-but-not-all failing is "partial" - the same word a single source uses.

    Reporting "failed" here would tell the reader the sweep did not work, when in
    fact ted's notices were stored. The failure still has to be named, though:
    "partial" without the reason is not actionable.
    """
    add_run(db_session, source="ted", status="success")
    add_run(db_session, source="sam", status="failed", error_message="boom")
    last = automation.automation_status(db_session, settings)["last_run"]
    assert last["status"] == "partial"
    assert last["sources_failed"] == 1
    assert last["errors"] == [{"source": "sam", "message": "boom"}]


def test_a_newer_batch_replaces_an_older_one(db_session, settings) -> None:
    add_run(db_session, source="ted", status="failed", batch="old", started=datetime(2026, 8, 20, 6, 0))
    add_run(db_session, source="ted", status="success", batch="new", started=datetime(2026, 8, 21, 6, 0))
    last = automation.automation_status(db_session, settings)["last_run"]
    assert (last["batch_id"], last["status"]) == ("new", "success")


def test_runs_predating_the_batch_column_still_group(db_session, settings) -> None:
    """Rows written before batch_id existed group by their shared window."""
    shared = datetime(2026, 8, 21, 6, 0)
    for source in ("ted", "find_a_tender"):
        run = FetchRun(
            source=source,
            status="success",
            trigger="cron",
            batch_id=None,
            started_at=shared,
            finished_at=shared,
            window_from=shared - timedelta(days=3),
            window_to=shared,
        )
        db_session.add(run)
    db_session.commit()
    last = automation.automation_status(db_session, settings)["last_run"]
    assert last["sources_total"] == 2
    assert last["batch_id"] is None


def test_slack_is_unconfigured_without_a_webhook(db_session, settings) -> None:
    slack = automation.automation_status(db_session, settings)["slack"]
    assert slack["status"] == "unconfigured"
    assert "SLACK_WEBHOOK_URL" in slack["detail"]


def test_slack_is_ok_once_a_digest_has_been_delivered(db_session, settings) -> None:
    configured = settings.model_copy(update={"slack_webhook_url": "https://hooks.slack.com/services/a/b/c"})
    tender = Tender(source="ted", source_notice_id="A", content_hash="h", title="t")
    db_session.add(tender)
    db_session.commit()
    add_run(db_session, source="ted", status="success", batch="batch-a")
    db_session.add(
        SlackNotification(
            tender_id=tender.id,
            channel_label=configured.slack_channel_label,
            run_batch_id="batch-a",
            status=SENT,
            claimed_at=datetime(2026, 8, 21, 6, 1),
        )
    )
    db_session.commit()
    slack = automation.automation_status(db_session, configured)["slack"]
    assert slack["status"] == "ok"
    assert slack["sent_total"] == 1
    assert slack["sent_in_last_batch"] == 1


def test_a_failed_delivery_surfaces_as_degraded(db_session, settings) -> None:
    """A Slack outage must be visible, never swallowed."""
    configured = settings.model_copy(update={"slack_webhook_url": "https://hooks.slack.com/services/a/b/c"})
    tender = Tender(source="ted", source_notice_id="B", content_hash="h", title="t")
    db_session.add(tender)
    db_session.commit()
    add_run(db_session, source="ted", status="success", batch="batch-a")
    db_session.add(
        SlackNotification(
            tender_id=tender.id,
            channel_label=configured.slack_channel_label,
            run_batch_id="batch-a",
            status="failed",
            error_message="500 server_error",
            claimed_at=datetime(2026, 8, 21, 6, 1),
        )
    )
    db_session.commit()
    slack = automation.automation_status(db_session, configured)["slack"]
    assert slack["status"] == "degraded"
    assert "server_error" in slack["detail"]


def test_the_endpoint_serialises_and_reports_the_scheduler_state(client) -> None:
    body = client.get("/api/automation").json()
    assert body["cron_utc"] == ["0 18 * * *", "0 6 * * *"]
    assert body["timezone"] == "Asia/Dhaka"
    assert body["scheduler_in_process"] is False
    assert body["next_run_at"].endswith("Z"), "datetimes are emitted as explicit UTC"
    assert body["slack"]["status"] == "unconfigured"


def test_health_names_the_database_engine(client) -> None:
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["database"]["ok"] is True
    assert body["database"]["dialect"] == "sqlite"


# --- interrupted runs -----------------------------------------------------


def test_an_orphaned_run_is_closed_out(db_session, settings) -> None:
    """A run cannot survive the process dying; it must not stay 'running'."""
    stale = datetime(2026, 8, 21, 6, 0)
    now = stale + timedelta(minutes=settings.stale_run_minutes + 5)
    run = FetchRun(source="ted", status="running", trigger="cron", started_at=stale, finished_at=None)
    db_session.add(run)
    db_session.commit()

    assert automation.reap_interrupted_runs(db_session, settings, now) == 1
    db_session.refresh(run)
    assert run.status == "failed"
    assert run.finished_at == now
    assert "Interrupted" in (run.error_message or "")


def test_a_queued_run_is_reaped_too(db_session, settings) -> None:
    stale = datetime(2026, 8, 21, 6, 0)
    db_session.add(FetchRun(source="sam", status="queued", trigger="cron", started_at=stale))
    db_session.commit()
    now = stale + timedelta(minutes=settings.stale_run_minutes + 1)
    assert automation.reap_interrupted_runs(db_session, settings, now) == 1


def test_a_run_still_within_the_window_is_left_alone(db_session, settings) -> None:
    """An operator's CLI run must not be killed because the API restarted."""
    started = datetime(2026, 8, 21, 6, 0)
    run = FetchRun(source="ted", status="running", trigger="cron", started_at=started)
    db_session.add(run)
    db_session.commit()
    # A full live sweep takes ~13 minutes; the default window is 60.
    assert automation.reap_interrupted_runs(db_session, settings, started + timedelta(minutes=13)) == 0
    db_session.refresh(run)
    assert run.status == "running"


def test_finished_runs_are_never_touched(db_session, settings) -> None:
    started = datetime(2026, 8, 21, 6, 0)
    for status in ("success", "partial", "failed", "skipped"):
        db_session.add(
            FetchRun(
                source=status,
                status=status,
                trigger="cron",
                started_at=started,
                finished_at=started + timedelta(minutes=1),
            )
        )
    db_session.commit()
    assert automation.reap_interrupted_runs(db_session, settings, started + timedelta(hours=5)) == 0


def test_reaping_makes_the_dashboard_stop_reporting_running(db_session, settings) -> None:
    stale = datetime(2026, 8, 21, 6, 0)
    add_run(db_session, source="ted", status="success", batch="b1", started=stale)
    db_session.add(
        FetchRun(
            source="pncp", status="running", trigger="cron", batch_id="b1", started_at=stale, window_to=stale
        )
    )
    db_session.commit()

    assert automation.automation_status(db_session, settings)["last_run"]["status"] == "running"
    automation.reap_interrupted_runs(db_session, settings, stale + timedelta(hours=2))
    after = automation.automation_status(db_session, settings)["last_run"]
    # The point of reaping: the dashboard stops claiming a run is still going.
    assert after["status"] != "running"
    # ted succeeded, so the batch is partial rather than a wholesale failure.
    assert after["status"] == "partial"
    assert after["sources_failed"] == 1


def test_an_old_failure_is_not_reported_as_current_degradation(db_session, settings) -> None:
    """A single old failure must not show the system as degraded for ever."""
    configured = settings.model_copy(update={"slack_webhook_url": "https://hooks.slack.com/services/a/b/c"})
    tender = Tender(source="ted", source_notice_id="OLD", content_hash="h", title="t")
    db_session.add(tender)
    db_session.commit()
    long_ago = datetime(2026, 1, 1, 0, 0)
    db_session.add(
        SlackNotification(
            tender_id=tender.id,
            channel_label=configured.slack_channel_label,
            run_batch_id="ancient",
            status="failed",
            error_message="500 server_error",
            claimed_at=long_ago,
        )
    )
    db_session.commit()

    # No batch at all: the failure is far outside the retry window.
    state = automation.slack_state(db_session, configured, None)
    assert state["status"] == "ok", "an ancient failure is not a current problem"

    # A failure inside the window still surfaces.
    fresh = Tender(source="ted", source_notice_id="NEW", content_hash="h2", title="t")
    db_session.add(fresh)
    db_session.commit()
    db_session.add(
        SlackNotification(
            tender_id=fresh.id,
            channel_label=configured.slack_channel_label,
            run_batch_id="recent",
            status="failed",
            error_message="503 unavailable",
            claimed_at=utcnow(),
        )
    )
    db_session.commit()
    assert automation.slack_state(db_session, configured, None)["status"] == "degraded"


# --- batch status semantics ----------------------------------------------
# One failing connector must not make the whole sweep read as "failed": the
# per-source FetchRun design exists precisely so one source can fail harmlessly.


def test_one_failing_source_among_many_is_partial_not_failed(db_session, settings) -> None:
    """The live 2026-08-21 sweep: pncp timed out, 269 notices still landed."""
    add_run(db_session, source="ted", status="success", records_created=10)
    add_run(db_session, source="canada_buys", status="success", records_created=225)
    add_run(db_session, source="pncp", status="failed", error_message="ReadTimeout")
    last = automation.automation_status(db_session, settings)["last_run"]
    assert last["status"] == "partial", "a sweep that stored 235 notices is not a failure"
    assert last["sources_failed"] == 1
    assert last["records_created"] == 235


def test_every_source_failing_is_reported_as_failed(db_session, settings) -> None:
    add_run(db_session, source="ted", status="failed", error_message="boom")
    add_run(db_session, source="pncp", status="failed", error_message="boom")
    assert automation.automation_status(db_session, settings)["last_run"]["status"] == "failed"


def test_a_failure_alongside_in_flight_sources_still_shows_running(db_session, settings) -> None:
    add_run(db_session, source="ted", status="failed", error_message="boom")
    add_run(db_session, source="pncp", status="running")
    assert automation.automation_status(db_session, settings)["last_run"]["status"] == "running"


def test_a_skipped_source_does_not_degrade_a_clean_sweep(db_session, settings) -> None:
    """SAM.gov with no API key is skipped, which is expected, not a problem."""
    add_run(db_session, source="ted", status="success", records_created=10)
    add_run(db_session, source="sam", status="skipped", error_message="SAM_GOV_API_KEY not set")
    last = automation.automation_status(db_session, settings)["last_run"]
    assert last["status"] == "skipped"
    assert last["sources_failed"] == 0


# --- the operator-editable schedule (docs/DECISIONS.md D14) ----------------


def test_schedule_endpoint_changes_the_times_and_reports_the_new_cron(client) -> None:
    response = client.put("/api/automation/schedule", json={"hours_local": [7, 19]})
    assert response.status_code == 200
    body = response.json()
    assert body["hours_local"] == [7, 19]
    assert body["timezone"] == "Asia/Dhaka"
    # 07:00 Dhaka is 01:00 UTC, 19:00 Dhaka is 13:00 UTC.
    assert body["cron_utc"] == ["0 1 * * *", "0 13 * * *"]
    assert body["next_run_local_label"]


def test_the_new_schedule_is_what_the_dashboard_then_reports(client) -> None:
    client.put("/api/automation/schedule", json={"hours_local": [5]})
    status = client.get("/api/automation").json()
    assert status["run_hours_local"] == [5]
    assert status["run_hours_are_custom"] is True
    assert status["cron_utc"] == ["0 23 * * *"]  # 05:00 Dhaka = 23:00 UTC previous day


def test_the_default_schedule_is_not_reported_as_custom(client) -> None:
    status = client.get("/api/automation").json()
    assert status["run_hours_local"] == [0, 12]
    assert status["run_hours_are_custom"] is False
    assert (status["run_hours_min"], status["run_hours_max"]) == (1, 6)


def test_a_nonsense_schedule_is_refused_with_a_message_a_person_can_act_on(client) -> None:
    response = client.put("/api/automation/schedule", json={"hours_local": [25]})
    assert response.status_code == 422
    assert "between 0 and 23" in response.json()["detail"]


def test_too_many_sweeps_a_day_are_refused(client) -> None:
    response = client.put("/api/automation/schedule", json={"hours_local": [0, 2, 4, 6, 8, 10, 12]})
    assert response.status_code == 422
    assert "at most 6" in response.json()["detail"]


def test_an_empty_schedule_is_refused_rather_than_silently_disabling_sweeps(client) -> None:
    response = client.put("/api/automation/schedule", json={"hours_local": []})
    assert response.status_code == 422
    assert "at least one" in response.json()["detail"]


def test_a_refused_change_leaves_the_previous_schedule_running(client) -> None:
    client.put("/api/automation/schedule", json={"hours_local": [9, 21]})
    client.put("/api/automation/schedule", json={"hours_local": [99]})
    assert client.get("/api/automation").json()["run_hours_local"] == [9, 21]


def test_the_schedule_endpoint_needs_no_shared_secret(client) -> None:
    """A member of staff setting the time in the dashboard *is* the authorisation.

    Still true, and still nothing to do with CRON_SECRET. What changed in D26 is
    only *who counts as a member of staff*: they must now be signed in, so this
    uses the signed-in client. The contrast the test draws is unchanged - what
    limits each endpoint is a cost, not a credential: choosing a time is free and
    unlimited, while starting a sweep spends outbound requests and so carries a
    cooldown. Proven by starting one and being refused the second immediately.
    """
    assert client.put("/api/automation/schedule", json={"hours_local": [8, 20]}).status_code == 200
    assert client.put("/api/automation/schedule", json={"hours_local": [9, 21]}).status_code == 200

    assert client.post("/api/fetch").status_code == 202
    assert client.post("/api/fetch").status_code in (409, 429)

    # Re-scoring has its own, separate cooldown: it rewrites every row but spends
    # no outbound request, so a sweep must not gate it and vice versa.
    assert client.post("/api/tenders/rescore").status_code == 200
    assert client.post("/api/tenders/rescore").status_code == 429


# --- pausing and resuming the sweep (docs/DECISIONS.md D21) ----------------


@pytest.fixture(autouse=True)
def _no_scheduler_leaks():
    """Drop any scheduler a test in this file started.

    The trigger endpoint genuinely starts APScheduler, and TestClient closes the
    event loop it bound to when the request ends - so the module-level reference
    would survive into the next test and report another test's jobs.
    stop_scheduler() clears it whether or not the shutdown itself can run.
    """
    yield
    scheduler.stop_scheduler()


def test_pausing_switches_the_sweep_off_and_says_what_that_means(client) -> None:
    response = client.put("/api/automation/trigger", json={"enabled": False})
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is False
    assert body["scheduler_running"] is False
    assert body["next_run_local_label"] is None
    assert "no digest" in body["detail"].lower() or "paused" in body["detail"].lower()


def test_resuming_starts_the_scheduler_and_names_the_next_sweep(client) -> None:
    body = client.put("/api/automation/trigger", json={"enabled": True}).json()
    assert body["enabled"] is True
    assert body["scheduler_running"] is True
    assert body["next_run_local_label"]
    assert "Asia/Dhaka" in body["detail"]


def test_the_dashboard_then_reports_the_paused_state(client) -> None:
    client.put("/api/automation/trigger", json={"enabled": False})
    status = client.get("/api/automation").json()
    assert status["scheduler_in_process"] is False
    assert status["trigger_is_custom"] is True
    assert status["trigger_default"] is False
    assert status["trigger_changed_at"].endswith("Z")


def test_pausing_does_not_read_as_the_broken_scheduler_state(client) -> None:
    """`in_process and not running` is the dashboard's "switched on but dead" alarm.

    A deliberate pause must not trip it, or every pause looks like a fault - which
    is why the reported intent is the decision in force, not ENABLE_SCHEDULER.
    """
    client.put("/api/automation/trigger", json={"enabled": False})
    status = client.get("/api/automation").json()
    assert not (status["scheduler_in_process"] and not status["scheduler_running"])


def test_resuming_is_reported_as_on_and_running(client) -> None:
    client.put("/api/automation/trigger", json={"enabled": True})
    status = client.get("/api/automation").json()
    assert status["scheduler_in_process"] is True
    assert status["scheduler_running"] is True
    assert status["trigger_is_custom"] is True


def test_the_default_trigger_state_is_not_reported_as_custom(client) -> None:
    status = client.get("/api/automation").json()
    assert status["trigger_is_custom"] is False
    assert status["trigger_changed_at"] is None


def test_pausing_leaves_the_chosen_sweep_times_alone(client) -> None:
    """Resuming has to restore what the operator picked, not the env default."""
    client.put("/api/automation/schedule", json={"hours_local": [7, 19]})
    client.put("/api/automation/trigger", json={"enabled": False})
    assert client.get("/api/automation").json()["run_hours_local"] == [7, 19]

    body = client.put("/api/automation/trigger", json={"enabled": True}).json()
    assert body["scheduler_running"] is True
    status = client.get("/api/automation").json()
    assert status["run_hours_local"] == [7, 19]
    assert status["cron_utc"] == ["0 1 * * *", "0 13 * * *"]
    assert {job["id"] for job in status["scheduler_jobs"]} == {
        "scheduled-fetch-07",
        "scheduled-fetch-19",
    }


def test_a_nonsense_trigger_state_is_refused(client) -> None:
    assert client.put("/api/automation/trigger", json={"enabled": "maybe"}).status_code == 422
    assert client.put("/api/automation/trigger", json={}).status_code == 422


def test_the_trigger_endpoint_needs_no_shared_secret(client) -> None:
    """Same authorisation as the schedule: the person in the dashboard (D19/D21).

    Pausing spends no outbound requests and rewrites no rows, so it carries no
    cooldown at all - unlike a sweep, which does (D23). Toggling repeatedly is
    therefore always allowed.
    """
    assert client.put("/api/automation/trigger", json={"enabled": False}).status_code == 200
    assert client.put("/api/automation/trigger", json={"enabled": True}).status_code == 200
    assert client.put("/api/automation/trigger", json={"enabled": False}).status_code == 200


def test_setting_times_while_paused_says_so_rather_than_blaming_another_process(client) -> None:
    """ "No scheduler runs here" reads as "Actions owns the trigger", which is wrong."""
    client.put("/api/automation/trigger", json={"enabled": False})
    body = client.put("/api/automation/schedule", json={"hours_local": [6, 18]}).json()
    assert body["applied_to_running_scheduler"] is False
    assert "paused" in body["detail"]
    assert "no scheduler runs" not in body["detail"].lower()
