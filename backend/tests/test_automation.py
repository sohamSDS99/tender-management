"""GET /api/automation - what the dashboard shows instead of a fetch button."""

from __future__ import annotations

from datetime import datetime, timedelta

from app.models import SENT, FetchRun, SlackNotification, Tender
from app.services import automation


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


def test_batch_status_is_the_worst_source_status(db_session, settings) -> None:
    add_run(db_session, source="ted", status="success")
    add_run(db_session, source="sam", status="failed", error_message="boom")
    last = automation.automation_status(db_session, settings)["last_run"]
    assert last["status"] == "failed"
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
