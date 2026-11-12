"""Alarms and the email that carries them (D43). No test sends a real email."""

from __future__ import annotations

import httpx
import pytest

from app.services import mailer
from app.settings import Settings

# --- the mailer --------------------------------------------------------------


def _send(settings: Settings) -> bool:
    return mailer.send("subj", "text", "<p>html</p>", to=["a@x.test"], cc=["b@x.test"], settings=settings)


def test_the_suite_can_never_hold_a_real_brevo_key():
    # tests/__init__.py blanks it before .env is read; a real key there is fine.
    assert Settings().brevo_api_key == ""


def test_no_key_is_a_dry_run_that_never_touches_the_network(settings, monkeypatch, caplog):
    monkeypatch.setattr(mailer.httpx, "post", lambda *a, **k: pytest.fail("network used in dry run"))
    with caplog.at_level("WARNING"):
        assert _send(settings) is False
    assert "dry run" in caplog.text


@pytest.mark.parametrize("status, expected", [(201, True), (202, True), (400, False), (500, False)])
def test_only_a_2xx_counts_as_sent(settings, monkeypatch, status, expected):
    seen = {}

    def fake_post(url, **kwargs):
        seen.update(url=url, **kwargs)
        return httpx.Response(status, text="{}")

    monkeypatch.setattr(mailer.httpx, "post", fake_post)
    keyed = settings.model_copy(update={"brevo_api_key": "xkeysib-test-not-real"})
    assert _send(keyed) is expected
    assert seen["url"] == mailer.BREVO_URL
    assert seen["headers"]["api-key"] == "xkeysib-test-not-real"
    assert seen["json"]["to"] == [{"email": "a@x.test"}]
    assert seen["json"]["cc"] == [{"email": "b@x.test"}]
    assert seen["json"]["sender"]["email"] == keyed.alarm_from


@pytest.mark.parametrize("exc", [httpx.ConnectError("down"), httpx.ReadTimeout("slow"), RuntimeError("bug")])
def test_a_network_error_is_false_and_never_raises(settings, monkeypatch, exc):
    def boom(*a, **k):
        raise exc

    monkeypatch.setattr(mailer.httpx, "post", boom)
    keyed = settings.model_copy(update={"brevo_api_key": "xkeysib-test-not-real"})
    assert _send(keyed) is False


def test_alarm_settings_parse(settings):
    assert settings.alarm_to_list == ["tanjir@sdsmanager.com"]
    assert settings.alarm_cc_list == ["abir@sdsmanager.com", "soham@sdsmanager.com", "shafiq@sdsmanager.com"]
    assert settings.alarm_workday_numbers == {0, 1, 2, 3, 4}
    assert settings.alarm_work_window == (10, 18)


# --- the alarms table --------------------------------------------------------


def _alarm(check_key="sweep_missed", subject="", **kw):
    from app.models import Alarm

    return Alarm(check_key=check_key, subject=subject, title="t", detail="", **kw)


def test_only_one_open_alarm_per_problem(db_session):
    from sqlalchemy.exc import IntegrityError

    db_session.add(_alarm())
    db_session.commit()
    db_session.add(_alarm())
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_a_closed_alarm_does_not_block_the_next_one_and_subjects_are_separate(db_session):
    from app.models import Alarm, utcnow

    db_session.add(_alarm(resolved_at=utcnow()))
    db_session.add(_alarm())  # same problem, but the first is closed
    db_session.add(_alarm("source_failing", "pncp"))
    db_session.add(_alarm("source_failing", "ted"))
    db_session.commit()
    assert db_session.query(Alarm).count() == 4


# --- the office-hours clock ----------------------------------------------------

from datetime import datetime, timedelta  # noqa: E402

from app.services import alarms  # noqa: E402


def dhaka(day: int, hour: int, minute: int = 0) -> datetime:
    """A November 2026 Dhaka wall-clock time, as the naive UTC the database stores.

    10 Nov 2026 is a Tuesday; 6 Nov a Friday; 7-8 Nov the weekend.
    """
    return datetime(2026, 11, day, hour, minute) - timedelta(hours=6)


@pytest.mark.parametrize(
    "start, end, hours",
    [
        (dhaka(6, 16), dhaka(9, 16), 8),  # Fri 16:00 -> Mon 16:00
        (dhaka(10, 2), dhaka(10, 12), 2),  # Tue 02:00 -> Tue 12:00
        (dhaka(7, 0), dhaka(8, 23, 59), 0),  # Sat -> Sun
        (dhaka(10, 13, 15), dhaka(11, 13, 15), 8),  # Tue 13:15 -> Wed 13:15
        (dhaka(10, 12), dhaka(10, 11), 0),  # backwards
    ],
)
def test_working_hours_between(settings, start, end, hours):
    assert alarms.working_hours_between(start, end, settings) == pytest.approx(hours)


@pytest.mark.parametrize(
    "opened, fix_by, remind_at",
    [  # the table in docs/ALARMS-PLAN.md section 3
        (dhaka(10, 13, 15), dhaka(11, 13, 15), dhaka(10, 17, 15)),
        (dhaka(10, 2), dhaka(10, 18), dhaka(10, 14)),
        (dhaka(6, 16), dhaka(9, 16), dhaka(9, 12)),
        (dhaka(7, 12), dhaka(9, 18), dhaka(9, 14)),
    ],
)
def test_fix_by_and_reminder_times(settings, opened, fix_by, remind_at):
    assert alarms.add_working_hours(opened, 8, settings) == fix_by
    assert alarms.add_working_hours(opened, 4, settings) == remind_at


# --- evaluate: open, retry, remind, close --------------------------------------


@pytest.fixture
def outbox(monkeypatch):
    """Replaces mailer.send. Records every email; `outbox.accept` decides the answer."""

    class Outbox(list):
        accept = True

    box = Outbox()

    def fake_send(subject, text, html, *, to, cc, settings):
        box.append({"subject": subject, "text": text, "html": html, "to": to, "cc": cc})
        return box.accept

    monkeypatch.setattr(mailer, "send", fake_send)
    return box


@pytest.fixture
def on_settings(settings):
    """Sweeps switched on by environment default, at 00:00 and 12:00 Dhaka."""
    return settings.model_copy(update={"enable_scheduler": True})


_batch_counter = iter(range(10_000))


def sweep(db, started, statuses=None, received=5, finished=True):
    """One sweep: a FetchRun per source, sharing a batch id."""
    from app.models import FetchRun

    batch = f"b{next(_batch_counter)}"
    for source, status in (statuses or {"ted": "success", "pncp": "success"}).items():
        db.add(
            FetchRun(
                source=source,
                status=status if finished else "running",
                started_at=started,
                finished_at=started + timedelta(minutes=13) if finished else None,
                records_received=received if status != "failed" else 0,
                batch_id=batch,
                trigger="cron",
                error_message="ReadTimeout" if status == "failed" else None,
            )
        )
    db.commit()
    return batch


def open_alarms(db):
    from app.models import Alarm

    return db.query(Alarm).filter(Alarm.resolved_at.is_(None)).populate_existing().all()


def all_alarms(db):
    from app.models import Alarm

    return db.query(Alarm).order_by(Alarm.id).populate_existing().all()


def test_a_missed_sweep_opens_one_alarm_and_sends_one_email(db_session, on_settings, outbox):
    sweep(db_session, dhaka(10, 0))
    alarms.evaluate(db_session, on_settings, now=dhaka(10, 13, 14))
    assert outbox == [] and open_alarms(db_session) == []  # 74 minutes late: not yet

    alarms.evaluate(db_session, on_settings, now=dhaka(10, 13, 15))
    alarms.evaluate(db_session, on_settings, now=dhaka(10, 13, 25))  # a second tick

    [alarm] = open_alarms(db_session)
    assert alarm.check_key == alarms.SWEEP_MISSED
    assert len(outbox) == 1
    email = outbox[0]
    assert email["subject"] == "[ALARM] Tender Monitor — the 12:00 sweep did not run — owner: Tanjir"
    assert email["to"] == ["tanjir@sdsmanager.com"]
    assert email["cc"] == ["abir@sdsmanager.com", "soham@sdsmanager.com", "shafiq@sdsmanager.com"]
    assert "Tue 10 Nov 00:00 (Dhaka) — finished OK" in email["text"]
    assert "Fix by       Wed 11 Nov 13:15 (Dhaka)" in email["text"]
    assert "http" not in email["text"].replace(on_settings.app_base_url, "")  # no action links


def test_when_a_sweep_then_finishes_the_alarm_closes_with_one_fixed_email(db_session, on_settings, outbox):
    sweep(db_session, dhaka(10, 0))
    alarms.evaluate(db_session, on_settings, now=dhaka(10, 13, 15))
    sweep(db_session, dhaka(10, 15, 40))  # someone starts one by hand
    alarms.evaluate(db_session, on_settings, now=dhaka(10, 15, 55))
    alarms.evaluate(db_session, on_settings, now=dhaka(10, 16, 5))

    assert open_alarms(db_session) == []
    assert [e["subject"].split(" ")[0] for e in outbox] == ["[ALARM]", "[FIXED]"]
    fixed = outbox[1]
    assert fixed["subject"] == "[FIXED] Tender Monitor — the 12:00 sweep did not run — owner: Tanjir"
    assert fixed["cc"] == on_settings.alarm_cc_list
    assert "Open for     2 h 40 min (2.7 office hours, within one working day)" in fixed["text"]


def test_a_sweep_still_running_does_not_close_a_missed_alarm(db_session, on_settings, outbox):
    sweep(db_session, dhaka(10, 0))
    alarms.evaluate(db_session, on_settings, now=dhaka(10, 13, 15))
    sweep(db_session, dhaka(10, 13, 30), finished=False)  # stuck
    alarms.evaluate(db_session, on_settings, now=dhaka(10, 15))
    assert len(open_alarms(db_session)) == 1 and len(outbox) == 1


def test_one_source_alarms_on_its_third_failure_in_a_row_and_closes_on_one_success(
    db_session, settings, outbox
):
    now = dhaka(10, 12)
    for n in range(2):
        sweep(db_session, now - timedelta(days=2 - n), {"ted": "success", "pncp": "failed"})
    alarms.evaluate(db_session, settings, now=now)
    assert open_alarms(db_session) == []

    sweep(db_session, now, {"ted": "success", "pncp": "failed"})
    alarms.evaluate(db_session, settings, now=now + timedelta(minutes=15))
    [alarm] = open_alarms(db_session)
    assert (alarm.check_key, alarm.subject) == (alarms.SOURCE_FAILING, "pncp")
    assert outbox[-1]["subject"] == "[ALARM] Tender Monitor — pncp failed 3 sweeps in a row — owner: Tanjir"
    assert "ReadTimeout" in outbox[-1]["text"]

    sweep(db_session, now + timedelta(hours=12), {"ted": "success", "pncp": "success"})
    alarms.evaluate(db_session, settings, now=now + timedelta(hours=12, minutes=15))
    assert open_alarms(db_session) == []
    assert outbox[-1]["subject"].startswith("[FIXED] Tender Monitor — pncp failed 3 sweeps in a row")


def test_a_refused_email_is_left_unsent_and_the_next_tick_sends_it(db_session, on_settings, outbox):
    sweep(db_session, dhaka(10, 0))
    outbox.accept = False
    alarms.evaluate(db_session, on_settings, now=dhaka(10, 13, 15))
    [alarm] = open_alarms(db_session)
    assert alarm.emailed_at is None and len(outbox) == 1

    outbox.accept = True
    alarms.evaluate(db_session, on_settings, now=dhaka(10, 13, 25))
    [alarm] = open_alarms(db_session)
    assert alarm.emailed_at == dhaka(10, 13, 25) and len(outbox) == 2

    alarms.evaluate(db_session, on_settings, now=dhaka(10, 13, 35))
    assert len(outbox) == 2


def test_the_reminder_goes_to_everyone_after_four_office_hours_never_counting_the_weekend(
    db_session, settings, outbox
):
    friday_4pm = dhaka(6, 16)
    for n in range(3):
        sweep(db_session, friday_4pm - timedelta(hours=12 * (2 - n)), {"ted": "success", "pncp": "failed"})
    alarms.evaluate(db_session, settings, now=friday_4pm)
    assert len(outbox) == 1  # the alarm itself

    for now in (dhaka(6, 23), dhaka(7, 12), dhaka(8, 23, 59), dhaka(9, 11, 59)):
        alarms.evaluate(db_session, settings, now=now)
    assert len(outbox) == 1  # Fri 16-18 + Mon 10-11:59 is under 4 office hours

    alarms.evaluate(db_session, settings, now=dhaka(9, 12))
    alarms.evaluate(db_session, settings, now=dhaka(9, 14))
    assert len(outbox) == 2  # exactly one reminder
    reminder = outbox[1]
    assert reminder["subject"] == "[REMINDER] Tender Monitor — pncp failed 3 sweeps in a row — owner: Tanjir"
    assert reminder["to"] == settings.alarm_to_list + settings.alarm_cc_list
    assert reminder["cc"] == []


def test_paused_sweeps_silence_check_3_and_raise_check_7_after_one_working_day(
    db_session, on_settings, outbox
):
    from app.models import KEY_SCHEDULER_ENABLED, AppSetting

    sweep(db_session, dhaka(10, 0))
    db_session.add(AppSetting(key=KEY_SCHEDULER_ENABLED, value="false", updated_at=dhaka(10, 10)))
    db_session.commit()

    alarms.evaluate(db_session, on_settings, now=dhaka(10, 20))  # 12:00 missed, but paused
    alarms.evaluate(db_session, on_settings, now=dhaka(11, 10))  # exactly 8 office hours
    assert open_alarms(db_session) == [] and outbox == []

    alarms.evaluate(db_session, on_settings, now=dhaka(11, 10, 10))
    [alarm] = open_alarms(db_session)
    assert alarm.check_key == alarms.SWEEPS_PAUSED
    assert "switched off for more than one working day" in outbox[0]["subject"]

    row = db_session.get(AppSetting, KEY_SCHEDULER_ENABLED)
    row.value, row.updated_at = "true", dhaka(11, 11)
    db_session.commit()
    alarms.evaluate(db_session, on_settings, now=dhaka(11, 11, 5))
    assert open_alarms(db_session) == []
    assert outbox[-1]["subject"].startswith("[FIXED]")


def test_expect_sweeps_always_ignores_the_pause_switch(db_session, settings, outbox):
    always = settings.model_copy(update={"alarm_expect_sweeps": "always"})  # switch reads off
    sweep(db_session, dhaka(10, 0))
    alarms.evaluate(db_session, always, now=dhaka(10, 13, 15))
    assert [a.check_key for a in open_alarms(db_session)] == [alarms.SWEEP_MISSED]
    assert "ALARM_EXPECT_SWEEPS=always" in outbox[0]["text"]


def test_a_slot_from_before_sweeps_were_switched_on_was_never_owed(db_session, on_settings, outbox):
    from app.models import KEY_SCHEDULER_ENABLED, AppSetting

    db_session.add(AppSetting(key=KEY_SCHEDULER_ENABLED, value="true", updated_at=dhaka(10, 12, 30)))
    db_session.commit()
    alarms.evaluate(db_session, on_settings, now=dhaka(10, 14))
    assert outbox == []


def test_every_source_failing_alarms_and_closes_when_one_works(db_session, settings, outbox):
    sweep(db_session, dhaka(10, 12), {"ted": "failed", "pncp": "failed", "sam_gov": "failed"})
    alarms.evaluate(db_session, settings, now=dhaka(10, 12, 15))
    assert [a.check_key for a in open_alarms(db_session)] == [alarms.ALL_SOURCES_FAILED]
    assert (
        outbox[0]["subject"]
        == "[ALARM] Tender Monitor — every source failed in the last sweep — owner: Tanjir"
    )

    sweep(db_session, dhaka(11, 0), finished=False)  # in flight: undecided, left open
    alarms.evaluate(db_session, settings, now=dhaka(11, 0, 5))
    assert len(open_alarms(db_session)) == 1

    sweep(db_session, dhaka(11, 1), {"ted": "success", "pncp": "failed"})
    alarms.evaluate(db_session, settings, now=dhaka(11, 1, 20))
    assert open_alarms(db_session) == []
    assert outbox[-1]["subject"].startswith("[FIXED]")


def test_one_source_fetched_by_hand_failing_is_not_every_source(db_session, settings, outbox):
    sweep(db_session, dhaka(10, 12), {"austender": "failed"})
    alarms.evaluate(db_session, settings, now=dhaka(10, 12, 15))
    assert outbox == []


def test_an_undelivered_digest_alarms_and_the_next_delivered_one_closes_it(db_session, settings, outbox):
    from app.models import SlackNotification

    db_session.add(
        SlackNotification(tender_id=1, run_batch_id="d1", status="failed", claimed_at=dhaka(10, 12))
    )
    db_session.add(SlackNotification(tender_id=2, run_batch_id="d1", status="sent", claimed_at=dhaka(10, 12)))
    db_session.commit()
    alarms.evaluate(db_session, settings, now=dhaka(10, 12, 15))
    assert [a.check_key for a in open_alarms(db_session)] == [alarms.SLACK_UNDELIVERED]
    assert "1 failed" in outbox[0]["text"]

    db_session.add(SlackNotification(tender_id=3, run_batch_id="d2", status="sent", claimed_at=dhaka(11, 0)))
    db_session.commit()
    alarms.evaluate(db_session, settings, now=dhaka(11, 0, 15))
    assert open_alarms(db_session) == []


def test_nothing_received_in_three_days_alarms_once_the_system_is_old_enough(db_session, settings, outbox):
    now = dhaka(10, 12)
    sweep(db_session, now - timedelta(days=2), received=0)
    sweep(db_session, now - timedelta(days=1), received=0)
    alarms.evaluate(db_session, settings, now=now)
    assert outbox == []  # only two days of history: too young to judge

    sweep(db_session, now - timedelta(days=5), received=40)
    alarms.evaluate(db_session, settings, now=now)
    assert [a.check_key for a in open_alarms(db_session)] == [alarms.NO_DATA]

    sweep(db_session, now + timedelta(hours=1), received=1)
    alarms.evaluate(db_session, settings, now=now + timedelta(hours=2))
    assert open_alarms(db_session) == []


def test_a_recorded_crash_alarms_now_and_closes_when_a_later_sweep_finishes(db_session, settings, outbox):
    from app.models import utcnow

    alarms.record_crash(RuntimeError("database went away"), db_session.info["factory"], settings)
    [alarm] = open_alarms(db_session)
    assert alarm.check_key == alarms.SWEEP_CRASHED
    assert outbox[0]["subject"] == "[ALARM] Tender Monitor — the sweep crashed — owner: Tanjir"
    assert "RuntimeError: database went away" in outbox[0]["text"]

    alarms.record_crash(RuntimeError("again"), db_session.info["factory"], settings)  # second crash
    assert len(all_alarms(db_session)) == 1 and len(outbox) == 1

    sweep(db_session, utcnow() + timedelta(minutes=1))
    alarms.evaluate(db_session, settings, now=utcnow() + timedelta(minutes=20))
    assert open_alarms(db_session) == []
    assert outbox[-1]["subject"] == "[FIXED] Tender Monitor — the sweep crashed — owner: Tanjir"


def test_a_crash_with_no_database_still_sends_the_email(settings, outbox):
    def no_database():
        raise RuntimeError("connection refused")

    alarms.record_crash(RuntimeError("database down"), no_database, settings)
    assert outbox[0]["subject"] == "[ALARM] Tender Monitor — the sweep crashed — owner: Tanjir"
    assert "the database could not be read" in outbox[0]["text"]


def test_a_check_that_raises_is_skipped_and_the_rest_still_run(db_session, settings, outbox, monkeypatch):
    monkeypatch.setattr(
        alarms,
        "CHECKS",
        (("boom", lambda *a: 1 / 0),) + tuple(c for c in alarms.CHECKS if c[0] != "boom"),
    )
    sweep(db_session, dhaka(10, 12), {"ted": "failed", "pncp": "failed"})
    alarms.evaluate(db_session, settings, now=dhaka(10, 12, 15))
    assert [a.check_key for a in open_alarms(db_session)] == [alarms.ALL_SOURCES_FAILED]


def test_alarms_disabled_sends_nothing(db_session, settings, outbox):
    sweep(db_session, dhaka(10, 12), {"ted": "failed", "pncp": "failed"})
    alarms.evaluate(db_session, settings.model_copy(update={"alarms_enabled": False}), now=dhaka(10, 13))
    assert outbox == [] and all_alarms(db_session) == []


def test_two_evaluators_cannot_send_the_same_email_twice(db_session, on_settings, outbox):
    sweep(db_session, dhaka(10, 0))
    alarms.evaluate(db_session, on_settings, now=dhaka(10, 13, 15))
    [alarm] = open_alarms(db_session)
    # The other evaluator already stamped it: this one must not send.
    assert alarms._claim(db_session, alarm, "emailed_at", dhaka(10, 13, 16)) is False


# --- hooks: the sweep, the crash, the watchdog ---------------------------------

import asyncio  # noqa: E402

from app.jobs import scheduled_fetch  # noqa: E402
from app.services import scheduler  # noqa: E402


@pytest.fixture
def wired(db_session, settings, monkeypatch):
    """run_once, main() and the scheduler job all pointed at the test database."""
    factory = db_session.info["factory"]
    monkeypatch.setattr(scheduled_fetch, "SessionLocal", factory)
    monkeypatch.setattr(scheduler, "SessionLocal", factory)
    monkeypatch.setattr(scheduled_fetch, "get_settings", lambda: settings)
    monkeypatch.setattr(scheduler, "get_settings", lambda: settings)
    monkeypatch.setattr(scheduled_fetch, "init_db", lambda: None)
    monkeypatch.setattr(scheduled_fetch, "configure_logging", lambda level: None)
    return db_session


def fake_fetch(db_session, statuses):
    """Stands in for ingest.run_fetch: writes finished FetchRun rows, returns their ids."""
    from app.models import FetchRun, utcnow

    async def run_fetch(sources, days_back, trigger, settings, batch_id=None):
        rows = [
            FetchRun(
                source=s,
                status=st,
                started_at=utcnow(),
                finished_at=utcnow(),
                batch_id=batch_id,
                records_received=3,
            )
            for s, st in statuses.items()
        ]
        db_session.add_all(rows)
        db_session.commit()
        return [r.id for r in rows]

    return run_fetch


def test_the_end_of_a_sweep_runs_the_checks(wired, settings, outbox, monkeypatch):
    monkeypatch.setattr(
        scheduled_fetch.ingest, "run_fetch", fake_fetch(wired, {"ted": "failed", "pncp": "failed"})
    )
    report = asyncio.run(scheduled_fetch.run_once(notify=False, settings=settings))
    assert report.exit_code == scheduled_fetch.EXIT_INGEST_FAILED
    assert [a.check_key for a in open_alarms(wired)] == [alarms.ALL_SOURCES_FAILED]
    assert len(outbox) == 1


def test_a_crash_inside_run_once_opens_alarm_1_and_still_reaches_the_caller(
    wired, settings, outbox, monkeypatch
):
    async def explode(*a, **k):
        raise RuntimeError("database went away mid-run")

    monkeypatch.setattr(scheduled_fetch.ingest, "run_fetch", explode)

    with pytest.raises(RuntimeError, match="mid-run"):
        scheduled_fetch.main(["--no-notify"])
    [alarm] = open_alarms(wired)
    assert alarm.check_key == alarms.SWEEP_CRASHED
    assert outbox[0]["subject"] == "[ALARM] Tender Monitor — the sweep crashed — owner: Tanjir"

    with pytest.raises(RuntimeError, match="mid-run"):  # the in-process scheduler's job too
        asyncio.run(scheduler._job())
    assert len(open_alarms(wired)) == 1 and len(outbox) == 1  # still one problem, one email


def test_a_crash_alarm_that_itself_fails_still_lets_the_original_crash_through(
    wired, settings, outbox, monkeypatch
):
    async def explode(*a, **k):
        raise RuntimeError("the real crash")

    monkeypatch.setattr(scheduled_fetch.ingest, "run_fetch", explode)
    monkeypatch.setattr(alarms, "record_crash", lambda *a: 1 / 0)
    with pytest.raises(RuntimeError, match="the real crash"):
        scheduled_fetch.main(["--no-notify"])
    with pytest.raises(RuntimeError, match="the real crash"):
        asyncio.run(scheduler._job())


@pytest.mark.parametrize("broken", ["evaluate", "evaluate_safely", "send"])
def test_an_exception_in_alarm_code_never_changes_a_sweeps_exit_code(
    wired, settings, outbox, monkeypatch, broken
):
    statuses = {"ted": "success", "pncp": "failed", "austender": "failed"}
    monkeypatch.setattr(scheduled_fetch.ingest, "run_fetch", fake_fetch(wired, statuses))
    clean = asyncio.run(scheduled_fetch.run_once(notify=False, settings=settings)).exit_code

    def boom(*a, **k):
        raise RuntimeError("alarm bug")

    target = mailer if broken == "send" else alarms
    monkeypatch.setattr(target, broken, boom)
    for _ in range(3):  # a third pncp failure would open an alarm, and its email raises
        report = asyncio.run(scheduled_fetch.run_once(notify=False, settings=settings))
        assert report.exit_code == clean == scheduled_fetch.EXIT_OK


def test_the_watchdog_runs_on_its_own_scheduler_even_with_sweeps_paused(settings):
    async def lifecycle():
        assert scheduler.start_scheduler(settings, hours=[0, 12], enabled=False) is None  # paused
        watchdog = alarms.start_watchdog(settings)
        try:
            assert watchdog is not None and watchdog.running
            assert [j.id for j in watchdog.get_jobs()] == ["alarm-watchdog"]
            assert alarms.start_watchdog(settings) is watchdog  # idempotent
        finally:
            alarms.stop_watchdog()
        assert alarms._watchdog is None

    asyncio.run(lifecycle())


def test_the_watchdog_does_not_start_when_alarms_are_off(settings):
    async def lifecycle():
        assert alarms.start_watchdog(settings.model_copy(update={"alarms_enabled": False})) is None

    asyncio.run(lifecycle())


def test_the_watchdog_tick_evaluates(monkeypatch, settings):
    calls = []
    monkeypatch.setattr(alarms, "evaluate_safely", lambda factory, s: calls.append(s))
    monkeypatch.setattr(alarms, "get_settings", lambda: settings)
    asyncio.run(alarms._watchdog_tick())
    assert calls == [settings]


def test_lifespan_starts_and_stops_the_watchdog(monkeypatch, settings):
    from fastapi import FastAPI

    from app import main

    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(main, "init_db", lambda: None)
    monkeypatch.setattr(main.automation, "reap_interrupted_runs", lambda *a, **k: 0)
    monkeypatch.setattr(main, "SessionLocal", lambda: type("S", (), {"close": lambda self: None})())
    monkeypatch.setattr(main, "configure_logging", lambda level: None)

    async def lifecycle():
        async with main.lifespan(FastAPI()):
            assert alarms._watchdog is not None and alarms._watchdog.running
        assert alarms._watchdog is None

    asyncio.run(lifecycle())


def test_a_mailer_that_raises_leaves_the_alarm_unsent_for_the_next_tick(db_session, on_settings, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("bug in send")

    sweep(db_session, dhaka(10, 0))
    monkeypatch.setattr(mailer, "send", boom)
    alarms.evaluate(db_session, on_settings, now=dhaka(10, 13, 15))
    [alarm] = open_alarms(db_session)
    assert alarm.emailed_at is None


# --- GET /api/alarms -----------------------------------------------------------


def test_the_alarms_endpoint_is_behind_sign_in(anon_client):
    assert anon_client.get("/api/alarms").status_code == 401


def test_the_alarms_endpoint_reports_open_recent_and_the_fix_rate(client, db_session):
    from app.models import Alarm, utcnow

    now = utcnow()

    def add(key, opened, resolved=None, subject=""):
        db_session.add(
            Alarm(
                check_key=key, subject=subject, title=key, detail="d", opened_at=opened, resolved_at=resolved
            )
        )

    add("sweep_missed", now - timedelta(hours=1))  # open
    add("no_data", now - timedelta(days=3), now - timedelta(days=3) + timedelta(minutes=30))  # quick fix
    add("source_failing", now - timedelta(days=20), now - timedelta(days=10), "pncp")  # 10 days: too slow
    add("sweeps_paused", now - timedelta(days=60), now - timedelta(days=45))  # outside the 30 days
    db_session.commit()

    body = client.get("/api/alarms").json()
    assert [a["check_key"] for a in body["open"]] == ["sweep_missed"]
    assert body["open"][0]["opened_at"].endswith("Z")
    assert [a["check_key"] for a in body["recent"]] == ["no_data", "source_failing", "sweeps_paused"]
    assert body["last_30_days"] == {"closed": 2, "fixed_within_one_working_day": 1, "percent": 50.0}


def test_no_closed_alarms_is_no_rate_rather_than_a_perfect_one(client):
    body = client.get("/api/alarms").json()
    assert body == {
        "open": [],
        "recent": [],
        "last_30_days": {"closed": 0, "fixed_within_one_working_day": 0, "percent": None},
    }
