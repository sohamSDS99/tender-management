"""Slack digest behaviour: who qualifies, what the payload says, and at-most-once.

No test reaches the network. httpx.MockTransport stands in for Slack, which
lets every assertion be made about the exact bytes that would have been sent.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import httpx
import pytest

from app.models import CLAIMED, FAILED, SENT, UNCONFIRMED, SlackNotification, Tender
from app.services import notifier
from app.settings import Settings

NOW = datetime(2026, 8, 21, 6, 0, 0)  # naive UTC, = 12:00 Dhaka
RUN_START = NOW - timedelta(minutes=5)
WEBHOOK = "https://hooks.slack.com/services/T000/B000/xxxxxxxxxxxxxxxxxxxxxxxx"


@pytest.fixture
def slack_settings() -> Settings:
    return Settings(
        _env_file=None,
        database_url="sqlite://",
        slack_webhook_url=WEBHOOK,
        slack_min_score=70,
        slack_channel_label="#tenders",
        slack_max_items=8,
        public_app_url="http://localhost:8080/",
        enable_slack_notifications=True,
        # Retries are exercised deliberately below; never sleep for real.
        retry_backoff_seconds=0.0,
        max_retries=3,
    )


class Recorder:
    """Captures every request and replies like Slack does."""

    def __init__(self, status: int = 200, body: str = "ok") -> None:
        self.status = status
        self.body = body
        self.requests: list[dict] = []

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(json.loads(request.content.decode()))
            return httpx.Response(self.status, text=self.body)

        return httpx.MockTransport(handler)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=self.transport())

    @property
    def payloads(self) -> list[dict]:
        return self.requests


def make_tender(
    db,
    *,
    notice: str,
    score: int = 90,
    actionable: bool = True,
    first_seen: datetime = NOW,
    deadline: datetime | None = None,
    fit: str = "high_fit",
    title: str = "Cloud SDS management platform",
) -> Tender:
    tender = Tender(
        source="ted",
        source_notice_id=notice,
        source_url=f"https://ted.europa.eu/notice/{notice}",
        title=title,
        buyer_name="Federal Chemicals Agency",
        buyer_country="DEU",
        deadline=deadline if deadline is not None else NOW + timedelta(days=30),
        estimated_value=850000.0,
        currency="EUR",
        content_hash=f"hash-{notice}",
        first_seen_at=first_seen,
        last_seen_at=first_seen,
        relevance_score=score,
        relevance_category="sds_management",
        relevance_reasons=["Title matches SDS management: 'safety data sheets'"],
        topic_relevance_score=96,
        product_fit_score=98,
        procurement_intent_score=92,
        fit_status=fit,
        deployment_fit="cloud_required",
        is_actionable=actionable,
    )
    db.add(tender)
    db.commit()
    return tender


# --- selection -------------------------------------------------------------


def test_only_newly_created_tenders_qualify(db_session, slack_settings) -> None:
    """A re-observed update must never be announced a second time."""
    fresh = make_tender(db_session, notice="NEW-1", first_seen=NOW)
    make_tender(db_session, notice="OLD-1", first_seen=RUN_START - timedelta(days=2))
    qualifying = notifier.qualifying_tenders(db_session, RUN_START, slack_settings, NOW)
    assert [t.source_notice_id for t in qualifying] == [fresh.source_notice_id]


def test_score_below_the_threshold_does_not_qualify(db_session, slack_settings) -> None:
    make_tender(db_session, notice="LOW-1", score=69)
    make_tender(db_session, notice="AT-BAR", score=70)
    qualifying = notifier.qualifying_tenders(db_session, RUN_START, slack_settings, NOW)
    assert [t.source_notice_id for t in qualifying] == ["AT-BAR"], "the bar is >=, not >"


def test_non_actionable_tenders_do_not_qualify(db_session, slack_settings) -> None:
    make_tender(db_session, notice="AWARDED", score=95, actionable=False)
    assert notifier.qualifying_tenders(db_session, RUN_START, slack_settings, NOW) == []


def test_qualifying_is_ordered_by_score_descending(db_session, slack_settings) -> None:
    for notice, score in (("A", 72), ("B", 97), ("C", 85)):
        make_tender(db_session, notice=notice, score=score)
    scores = [
        t.relevance_score for t in notifier.qualifying_tenders(db_session, RUN_START, slack_settings, NOW)
    ]
    assert scores == [97, 85, 72]


# --- payload ---------------------------------------------------------------


def test_digest_links_to_our_dashboard_first_and_the_notice_second(db_session, slack_settings) -> None:
    tender = make_tender(db_session, notice="LINK-1")
    payload = notifier.build_digest([tender], slack_settings, now=NOW)
    blob = json.dumps(payload)
    assert f"http://localhost:8080/?tender={tender.id}" in blob, "must deep-link into our own dashboard"
    assert "https://ted.europa.eu/notice/LINK-1" in blob, "the original notice is the secondary link"
    first_section = payload["blocks"][3]["text"]["text"]
    assert first_section.startswith(f"*<http://localhost:8080/?tender={tender.id}|")


def test_digest_carries_every_field_the_brief_requires(db_session, slack_settings) -> None:
    tender = make_tender(db_session, notice="FIELDS-1", deadline=NOW + timedelta(days=8))
    blob = json.dumps(notifier.build_digest([tender], slack_settings, now=NOW))
    for expected in (
        "90",  # score
        "Excellent fit",  # fit status
        "Cloud required",  # deployment fit
        "Federal Chemicals Agency",  # buyer
        "DEU",  # country
        "8 days left",  # deadline urgency
        "\\u20ac850,000",  # estimated value
        "Title matches SDS management",  # top relevance reason
    ):
        assert expected in blob, f"digest is missing {expected!r}"


def test_deadline_urgency_thresholds() -> None:
    assert notifier.deadline_urgency(NOW + timedelta(hours=71), NOW)[0] == "urgent"
    assert notifier.deadline_urgency(NOW + timedelta(hours=73), NOW)[0] == "soon"
    assert notifier.deadline_urgency(NOW + timedelta(days=14), NOW)[0] == "soon"
    assert notifier.deadline_urgency(NOW + timedelta(days=15), NOW)[0] == "normal"
    assert notifier.deadline_urgency(NOW - timedelta(hours=1), NOW)[0] == "gone"
    assert notifier.deadline_urgency(None, NOW) == ("none", "no deadline in feed")


def test_digest_caps_items_and_offers_the_rest_as_a_filtered_link(db_session, slack_settings) -> None:
    tenders = [make_tender(db_session, notice=f"CAP-{i}", score=70 + i) for i in range(12)]
    payload = notifier.build_digest(tenders, slack_settings, now=NOW)
    assert len(payload["blocks"]) <= 50, "Slack rejects more than 50 blocks"
    footer = payload["blocks"][-1]["elements"][0]["text"]
    assert "+4 more" in footer, footer
    assert "minimum_score=70" in footer, "the +N link must land on the same filter"


def test_digest_never_exceeds_slack_block_limit_whatever_the_config(db_session, slack_settings) -> None:
    generous = slack_settings.model_copy(update={"slack_max_items": 500})
    tenders = [make_tender(db_session, notice=f"MANY-{i}") for i in range(60)]
    payload = notifier.build_digest(tenders, generous, now=NOW)
    assert len(payload["blocks"]) <= 50


def test_mrkdwn_special_characters_are_escaped(db_session, slack_settings) -> None:
    tender = make_tender(db_session, notice="ESC-1", title='Chemicals <script> & "safety" > all')
    section = notifier.build_digest([tender], slack_settings, now=NOW)["blocks"][3]["text"]["text"]
    assert "&lt;script&gt;" in section and "&amp;" in section
    assert "<script>" not in section


def test_heartbeat_says_the_run_happened(slack_settings) -> None:
    payload = notifier.build_heartbeat(slack_settings, run_summary="8 source(s) · 412 notices seen", now=NOW)
    text = payload["blocks"][0]["elements"][0]["text"]
    assert "412 notices seen" in text
    assert "nothing scored 70+" in text
    assert "12:00" in text, "the heartbeat is stamped in Dhaka local time"


# --- delivery and idempotency ---------------------------------------------


def test_digest_is_posted_and_ledgered(db_session, slack_settings) -> None:
    tender = make_tender(db_session, notice="SEND-1")
    recorder = Recorder()
    outcome = notifier.notify_new_tenders(
        db_session,
        since=RUN_START,
        batch_id="batch-1",
        settings=slack_settings,
        now=NOW,
        client=recorder.client(),
    )
    assert outcome.status == "sent"
    assert outcome.posted == 1
    assert len(recorder.payloads) == 1
    row = db_session.query(SlackNotification).one()
    assert (row.tender_id, row.status, row.response_code) == (tender.id, SENT, 200)
    assert row.run_batch_id == "batch-1"
    assert row.posted_at is not None


def test_a_double_fired_run_posts_no_duplicate_entry(db_session, slack_settings) -> None:
    """The acceptance case: re-running the same sweep immediately must not re-post."""
    make_tender(db_session, notice="DUP-1")
    recorder = Recorder()

    first = notifier.notify_new_tenders(
        db_session,
        since=RUN_START,
        batch_id="batch-1",
        settings=slack_settings,
        now=NOW,
        client=recorder.client(),
    )
    second = notifier.notify_new_tenders(
        db_session,
        since=RUN_START,
        batch_id="batch-2",
        settings=slack_settings,
        now=NOW + timedelta(seconds=30),
        client=recorder.client(),
    )

    assert first.status == "sent" and first.posted == 1
    assert second.status == "heartbeat", "the second run must fall through to a heartbeat"
    assert second.posted == 0
    assert second.candidates == 0, "an announced tender is no longer a candidate"
    assert db_session.query(SlackNotification).count() == 1, "no second ledger row"

    # Two messages went out, but only the first contains the tender.
    assert len(recorder.payloads) == 2
    tender_mentions = [p for p in recorder.payloads if "DUP-1" in json.dumps(p) or "tender=" in json.dumps(p)]
    assert len(tender_mentions) == 1, "the tender was announced twice"


def test_a_later_run_never_re_announces_an_old_tender(db_session, slack_settings) -> None:
    make_tender(db_session, notice="OLD-SENT")
    recorder = Recorder()
    notifier.notify_new_tenders(
        db_session,
        since=RUN_START,
        batch_id="b1",
        settings=slack_settings,
        now=NOW,
        client=recorder.client(),
    )
    # A much later run, with a window wide enough to see the same row again.
    later = notifier.notify_new_tenders(
        db_session,
        since=RUN_START,
        batch_id="b2",
        settings=slack_settings,
        now=NOW + timedelta(days=7),
        client=recorder.client(),
    )
    assert later.posted == 0
    assert db_session.query(SlackNotification).count() == 1


def test_a_failed_post_is_retryable_rather_than_permanently_silent(db_session, slack_settings) -> None:
    make_tender(db_session, notice="RETRY-1")
    failing = Recorder(status=500, body="server_error")
    outcome = notifier.notify_new_tenders(
        db_session,
        since=RUN_START,
        batch_id="b1",
        settings=slack_settings,
        now=NOW,
        client=failing.client(),
    )
    assert outcome.status == "failed"
    assert outcome.ok is False
    row = db_session.query(SlackNotification).one()
    assert row.status == FAILED
    assert row.response_code == 500

    # The next run takes the claim over and delivers it.
    ok = Recorder()
    retry = notifier.notify_new_tenders(
        db_session,
        since=RUN_START,
        batch_id="b2",
        settings=slack_settings,
        now=NOW + timedelta(minutes=1),
        client=ok.client(),
    )
    assert retry.status == "sent" and retry.posted == 1
    assert db_session.query(SlackNotification).count() == 1
    assert db_session.query(SlackNotification).one().status == SENT


def test_a_fresh_claim_by_a_sibling_process_blocks_a_second_post(db_session, slack_settings) -> None:
    """Concurrency guard: a live claim suppresses; a stale one is taken over."""
    tender = make_tender(db_session, notice="RACE-1")
    db_session.add(
        SlackNotification(
            tender_id=tender.id,
            channel_label="#tenders",
            run_batch_id="sibling",
            status=CLAIMED,
            claimed_at=NOW,
        )
    )
    db_session.commit()

    recorder = Recorder()
    blocked = notifier.notify_new_tenders(
        db_session,
        since=RUN_START,
        batch_id="mine",
        settings=slack_settings,
        now=NOW,
        client=recorder.client(),
    )
    assert blocked.posted == 0 and blocked.status == "heartbeat"

    # Once the claim is older than SLACK_CLAIM_STALE_MINUTES it is abandoned.
    stale_now = NOW + timedelta(minutes=slack_settings.slack_claim_stale_minutes + 1)
    recovered = notifier.notify_new_tenders(
        db_session,
        since=RUN_START,
        batch_id="mine",
        settings=slack_settings,
        now=stale_now,
        client=Recorder().client(),
    )
    assert recovered.status == "sent" and recovered.posted == 1


def test_network_failure_is_reported_not_raised(db_session, slack_settings) -> None:
    """A transport error is ambiguous, so it is 'unconfirmed', not 'failed'."""
    make_tender(db_session, notice="NET-1")

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("name or service not known")

    client = httpx.Client(transport=httpx.MockTransport(boom))
    outcome = notifier.notify_new_tenders(
        db_session, since=RUN_START, batch_id="b1", settings=slack_settings, now=NOW, client=client
    )
    assert outcome.status == "unconfirmed"
    assert outcome.ok is False
    assert outcome.response_code is None
    assert "ConnectError" in (outcome.error or "")


def test_the_webhook_url_is_never_echoed_in_an_error(db_session, slack_settings) -> None:
    make_tender(db_session, notice="LEAK-1")
    leaky = Recorder(status=403, body=f"forbidden for {WEBHOOK}")
    outcome = notifier.notify_new_tenders(
        db_session,
        since=RUN_START,
        batch_id="b1",
        settings=slack_settings,
        now=NOW,
        client=leaky.client(),
    )
    assert outcome.status == "failed"
    assert WEBHOOK not in (outcome.error or "")
    assert "***" in (outcome.error or "")
    stored = db_session.query(SlackNotification).one().error_message or ""
    assert WEBHOOK not in stored


def test_notifications_are_disabled_without_a_webhook(db_session, slack_settings) -> None:
    make_tender(db_session, notice="NOHOOK-1")
    outcome = notifier.notify_new_tenders(
        db_session,
        since=RUN_START,
        batch_id="b1",
        settings=slack_settings.model_copy(update={"slack_webhook_url": ""}),
        now=NOW,
    )
    assert outcome.status == "disabled"
    assert db_session.query(SlackNotification).count() == 0


def test_a_dry_run_builds_the_payload_without_claiming_or_posting(db_session, slack_settings) -> None:
    make_tender(db_session, notice="DRY-1")
    recorder = Recorder()
    outcome = notifier.notify_new_tenders(
        db_session,
        since=RUN_START,
        batch_id="b1",
        settings=slack_settings,
        now=NOW,
        client=recorder.client(),
        dry_run=True,
    )
    assert outcome.status == "dry_run"
    assert outcome.payload is not None and outcome.payload["blocks"]
    assert recorder.payloads == [], "a dry run must not send anything"
    assert db_session.query(SlackNotification).count() == 0, "a dry run must not claim"


# --- webhook retry behaviour ----------------------------------------------


class CountingRecorder(Recorder):
    """Fails a set number of times, then succeeds."""

    def __init__(self, failures: int, status: int = 503) -> None:
        super().__init__()
        self.failures = failures
        self.fail_status = status
        self.calls = 0

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            self.calls += 1
            self.requests.append(json.loads(request.content.decode()))
            if self.calls <= self.failures:
                return httpx.Response(self.fail_status, text="server_error")
            return httpx.Response(200, text="ok")

        return httpx.MockTransport(handler)


def test_a_transient_slack_failure_is_retried_and_succeeds(db_session, slack_settings) -> None:
    """A momentary blip must not cost a digest that then waits 12 hours."""
    make_tender(db_session, notice="RETRY-503")
    recorder = CountingRecorder(failures=2)
    delays: list[float] = []
    outcome = notifier.notify_new_tenders(
        db_session,
        since=RUN_START,
        batch_id="b1",
        settings=slack_settings,
        now=NOW,
        client=recorder.client(),
        sleep=delays.append,
    )
    assert outcome.status == "sent"
    assert recorder.calls == 3, "should have retried twice before succeeding"
    assert len(delays) == 2, "each retry must be preceded by a backoff"
    assert db_session.query(SlackNotification).one().status == SENT


def test_retries_are_bounded(db_session, slack_settings) -> None:
    make_tender(db_session, notice="RETRY-BOUND")
    recorder = CountingRecorder(failures=99)
    delays: list[float] = []
    outcome = notifier.notify_new_tenders(
        db_session,
        since=RUN_START,
        batch_id="b1",
        settings=slack_settings,
        now=NOW,
        client=recorder.client(),
        sleep=delays.append,
    )
    assert outcome.status == "failed"
    assert recorder.calls == slack_settings.max_retries, "must not retry forever"


def test_a_rate_limit_honours_retry_after(slack_settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="rate_limited", headers={"Retry-After": "7"})

    delays: list[float] = []
    code, error = notifier.post_webhook(
        {"text": "x"},
        slack_settings,
        httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=delays.append,
    )
    assert code == 429
    assert delays and all(d == 7.0 for d in delays), delays


def test_an_invalid_payload_is_not_retried(slack_settings) -> None:
    """A 4xx that is not a rate limit will not become valid on a second try."""
    recorder = CountingRecorder(failures=99, status=400)
    delays: list[float] = []
    code, _error = notifier.post_webhook(
        {"text": "x"}, slack_settings, recorder.client(), sleep=delays.append
    )
    assert code == 400
    assert recorder.calls == 1
    assert delays == []


# --- feed-supplied URLs are untrusted -------------------------------------


@pytest.mark.parametrize(
    "hostile",
    [
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "https://evil.test/x|Click%20here",
        "https://evil.test/x>bad",
        "https://evil.test/a<b",
        "  ftp://evil.test/x  ",
        "https://evil.test/\nsecond-line",
    ],
)
def test_a_hostile_notice_url_never_reaches_a_slack_link(db_session, slack_settings, hostile) -> None:
    """Slack link syntax is <url|text>; a URL carrying those delimiters could
    otherwise close the link early and inject arbitrary mrkdwn."""
    tender = make_tender(db_session, notice="HOSTILE-URL")
    tender.source_url = hostile
    db_session.commit()
    blob = json.dumps(notifier.build_digest([tender], slack_settings, now=NOW))
    assert "Original notice" not in blob, "an unsafe URL must not become a link"
    assert "javascript:" not in blob
    assert "data:text/html" not in blob


def test_a_normal_notice_url_still_becomes_a_link(db_session, slack_settings) -> None:
    tender = make_tender(db_session, notice="GOOD-URL")
    blob = json.dumps(notifier.build_digest([tender], slack_settings, now=NOW))
    assert "<https://ted.europa.eu/notice/GOOD-URL|Original notice>" in blob


def test_safe_url_accepts_only_http_schemes() -> None:
    assert notifier.safe_url("https://x.test/a") == "https://x.test/a"
    assert notifier.safe_url("http://x.test/a") == "http://x.test/a"
    assert notifier.safe_url(None) is None
    assert notifier.safe_url("") is None
    assert notifier.safe_url("javascript:alert(1)") is None
    assert notifier.safe_url("//x.test/a") is None


def test_hostile_text_fields_are_escaped(db_session, slack_settings) -> None:
    tender = make_tender(db_session, notice="HOSTILE-TEXT")
    tender.buyer_country = "<http://evil.test|DEU>"
    tender.source = "ted<|>"
    db_session.commit()
    blob = json.dumps(notifier.build_digest([tender], slack_settings, now=NOW))
    assert "<http://evil.test|DEU>" not in blob
    assert "&lt;http://evil.test|DEU&gt;" in blob


# --- the ambiguous case: the POST left, but no reply came back ------------
#
# Slack's incoming webhooks have no idempotency key, so a request that may
# already have been delivered must never be re-sent. These tests pin that:
# retry only when Slack *answered*, and never re-announce something whose
# delivery is unknown.


def test_a_lost_response_is_delivered_at_most_once(db_session, slack_settings) -> None:
    """The exact scenario that would otherwise post the same digest three times."""
    make_tender(db_session, notice="LOST-1")
    delivered: list[int] = []

    def lost(request: httpx.Request) -> httpx.Response:
        delivered.append(1)  # Slack got it...
        raise httpx.ReadTimeout("connection reset after the request was sent")

    outcome = notifier.notify_new_tenders(
        db_session,
        since=RUN_START,
        batch_id="b1",
        settings=slack_settings,
        now=NOW,
        client=httpx.Client(transport=httpx.MockTransport(lost)),
        sleep=lambda _d: None,
    )
    assert outcome.status == "unconfirmed"
    assert len(delivered) == 1, "an ambiguous transport error must not be retried"
    assert db_session.query(SlackNotification).one().status == UNCONFIRMED

    # And a later run must not re-announce it.
    resent = Recorder()
    later = notifier.notify_new_tenders(
        db_session,
        since=RUN_START,
        batch_id="b2",
        settings=slack_settings,
        now=NOW + timedelta(hours=12),
        client=resent.client(),
    )
    assert later.posted == 0
    assert later.status == "heartbeat"
    assert db_session.query(SlackNotification).count() == 1
    assert db_session.query(SlackNotification).one().status == UNCONFIRMED


def test_a_rejected_post_is_still_retried_because_nothing_was_delivered(db_session, slack_settings) -> None:
    """A status code proves Slack answered, so re-sending cannot duplicate."""
    make_tender(db_session, notice="REJECTED-1")
    recorder = Recorder(status=500, body="server_error")
    outcome = notifier.notify_new_tenders(
        db_session,
        since=RUN_START,
        batch_id="b1",
        settings=slack_settings,
        now=NOW,
        client=recorder.client(),
        sleep=lambda _d: None,
    )
    assert outcome.status == "failed"
    assert db_session.query(SlackNotification).one().status == FAILED

    ok = Recorder()
    retry = notifier.notify_new_tenders(
        db_session,
        since=RUN_START,
        batch_id="b2",
        settings=slack_settings,
        now=NOW + timedelta(minutes=1),
        client=ok.client(),
    )
    assert retry.status == "sent" and retry.posted == 1


def test_a_transport_error_is_never_retried_within_a_run(slack_settings) -> None:
    attempts: list[int] = []

    def boom(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        raise httpx.ConnectError("refused")

    delays: list[float] = []
    code, error = notifier.post_webhook(
        {"text": "x"},
        slack_settings,
        httpx.Client(transport=httpx.MockTransport(boom)),
        sleep=delays.append,
    )
    assert code is None
    assert "ConnectError" in (error or "")
    assert len(attempts) == 1, "a possibly-delivered request must not be re-sent"
    assert delays == []


# --- the item cap ---------------------------------------------------------


def test_the_item_cap_defers_the_remainder_instead_of_silently_marking_it_sent(
    db_session, slack_settings
) -> None:
    """The cap is a rate limit, not a silent drop.

    Only the tenders a message actually names are claimed, so the overflow keeps
    no ledger row and the next run announces it. The earlier implementation
    claimed every candidate and marked them all sent, which meant anything past
    the cap appeared in no message that was ever posted and was never announced
    again.
    """
    capped = slack_settings.model_copy(update={"slack_max_items": 3})
    for i in range(10):
        make_tender(db_session, notice=f"CAP-{i:02d}", score=90 - i)

    first = Recorder()
    run1 = notifier.notify_new_tenders(
        db_session, since=RUN_START, batch_id="b1", settings=capped, now=NOW, client=first.client()
    )
    assert run1.posted == 3, "only the cap is announced"
    assert run1.deferred == 7, "the rest is deferred, not dropped"
    assert db_session.query(SlackNotification).count() == 3, "no ledger row for a deferred tender"

    payload = first.payloads[0]
    named = [b for b in payload["blocks"] if b["type"] == "section" and "text" in b]
    assert len(named) == 3
    footer = payload["blocks"][-1]["elements"][0]["text"]
    assert "+7 more" in footer and "see all 10" in footer

    # The next run picks up the next three, and never repeats the first three.
    second = Recorder()
    run2 = notifier.notify_new_tenders(
        db_session,
        since=RUN_START,
        batch_id="b2",
        settings=capped,
        now=NOW + timedelta(hours=12),
        client=second.client(),
    )
    assert run2.posted == 3 and run2.deferred == 4
    announced_first = set(run1.tender_ids)
    announced_second = set(run2.tender_ids)
    assert not (announced_first & announced_second), "a tender was announced twice"

    # Drain it: every qualifying tender is named in exactly one message.
    seen = announced_first | announced_second
    now = NOW + timedelta(hours=24)
    for _ in range(5):
        rec = Recorder()
        out = notifier.notify_new_tenders(
            db_session, since=RUN_START, batch_id="drain", settings=capped, now=now, client=rec.client()
        )
        assert not (seen & set(out.tender_ids)), "a tender was announced twice"
        seen |= set(out.tender_ids)
        now += timedelta(hours=12)
        if out.posted == 0:
            break
    assert len(seen) == 10, f"every qualifying tender should be named exactly once, got {len(seen)}"


def test_a_rejected_digest_is_retried_by_the_next_run(db_session, slack_settings) -> None:
    """The guarantee the docs promised but the old window made unreachable.

    Selection used to be `first_seen_at >= this run's start`, so a tender created
    by an earlier run could never be a candidate again - a Slack outage meant it
    was announced by no run, ever.
    """
    make_tender(db_session, notice="OUTAGE-1", first_seen=NOW)

    failed = notifier.notify_new_tenders(
        db_session,
        since=NOW,
        batch_id="b1",
        settings=slack_settings,
        now=NOW,
        client=Recorder(status=500, body="server_error").client(),
        sleep=lambda _d: None,
    )
    assert failed.status == "failed"
    assert db_session.query(SlackNotification).one().status == FAILED

    # Twelve hours later, exactly as the scheduler would: a fresh `since`.
    later = NOW + timedelta(hours=12)
    recovered = Recorder()
    run2 = notifier.notify_new_tenders(
        db_session,
        since=later,
        batch_id="b2",
        settings=slack_settings,
        now=later,
        client=recovered.client(),
    )
    assert run2.status == "sent", "the failed digest must be retried by a later run"
    assert run2.posted == 1
    assert "OUTAGE-1" in json.dumps(recovered.payloads[0])
    assert db_session.query(SlackNotification).one().status == SENT


def test_a_tender_older_than_the_lookback_is_no_longer_announced(db_session, slack_settings) -> None:
    """The window still bounds the backlog, so a long outage cannot flood later."""
    hours = slack_settings.slack_announce_lookback_hours
    make_tender(db_session, notice="ANCIENT-1", first_seen=NOW - timedelta(hours=hours + 1))
    make_tender(db_session, notice="RECENT-1", first_seen=NOW - timedelta(hours=hours - 1))
    announceable = notifier.announceable_tenders(db_session, slack_settings, NOW)
    assert [t.source_notice_id for t in announceable] == ["RECENT-1"]


def test_a_re_observed_notice_is_never_re_announced(db_session, slack_settings) -> None:
    """first_seen_at is immutable, so an update cannot re-enter the candidate set."""
    tender = make_tender(db_session, notice="AMENDED-1", first_seen=NOW)
    recorder = Recorder()
    notifier.notify_new_tenders(
        db_session, since=NOW, batch_id="b1", settings=slack_settings, now=NOW, client=recorder.client()
    )
    # The source amends the notice: last_seen_at and content move, first_seen_at does not.
    tender.last_seen_at = NOW + timedelta(hours=6)
    tender.title = "Amended title"
    db_session.commit()
    again = notifier.notify_new_tenders(
        db_session,
        since=NOW + timedelta(hours=12),
        batch_id="b2",
        settings=slack_settings,
        now=NOW + timedelta(hours=12),
        client=Recorder().client(),
    )
    assert again.posted == 0
    assert again.candidates == 0


# --- the bot-token transport (docs/DECISIONS.md D22) -----------------------


BOT_TOKEN = "xoxb-test-not-a-real-token-0000"


def bot_settings(slack_settings):
    """Same settings, delivering through chat.postMessage instead."""
    return slack_settings.model_copy(
        update={"slack_bot_token": BOT_TOKEN, "slack_channel_id": "C0TEST0001", "slack_webhook_url": ""}
    )


def web_api(body: dict, status: int = 200, capture: list | None = None) -> httpx.Client:
    """A Slack Web API stand-in. Records the request for assertions."""

    def handler(request: httpx.Request) -> httpx.Response:
        if capture is not None:
            capture.append(request)
        return httpx.Response(status, json=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_a_200_with_ok_false_is_a_failure_not_a_delivery(slack_settings) -> None:
    """The whole point of this transport having its own reader.

    chat.postMessage answers 200 for most failures. Trusting the status code
    would write SENT to the ledger, and the unique constraint (D6) means the
    tender could then never be announced again - a silent permanent loss.
    """
    code, error = notifier.post_chat_message(
        {"text": "x"},
        bot_settings(slack_settings),
        web_api({"ok": False, "error": "channel_not_found"}),
        lambda _: None,
    )
    assert code == 200
    assert error is not None
    assert "channel_not_found" in error


def test_a_missing_scope_names_the_scope_it_needs(slack_settings) -> None:
    _code, error = notifier.post_chat_message(
        {"text": "x"},
        bot_settings(slack_settings),
        web_api({"ok": False, "error": "missing_scope", "needed": "chat:write", "provided": "channels:read"}),
        lambda _: None,
    )
    assert "missing_scope" in error
    assert "chat:write" in error


def test_ok_true_is_a_delivery(slack_settings) -> None:
    code, error = notifier.post_chat_message(
        {"text": "x"},
        bot_settings(slack_settings),
        web_api({"ok": True, "ts": "1700000000.000100"}),
        lambda _: None,
    )
    assert (code, error) == (200, None)


def test_the_channel_and_bearer_token_are_sent(slack_settings) -> None:
    captured: list[httpx.Request] = []
    notifier.post_chat_message(
        {"text": "x", "blocks": []},
        bot_settings(slack_settings),
        web_api({"ok": True}, capture=captured),
        lambda _: None,
    )
    request = captured[0]
    assert str(request.url) == notifier.SLACK_POST_MESSAGE_URL
    assert request.headers["authorization"] == f"Bearer {BOT_TOKEN}"
    assert json.loads(request.content.decode())["channel"] == "C0TEST0001"


def test_a_non_json_body_is_not_treated_as_success(slack_settings) -> None:
    """An HTML error page from a proxy in front of Slack must not read as sent."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>gateway</html>")

    code, error = notifier.post_chat_message(
        {"text": "x"},
        bot_settings(slack_settings),
        httpx.Client(transport=httpx.MockTransport(handler)),
        lambda _: None,
    )
    assert code == 200
    assert "non-JSON" in error


def test_a_config_fault_is_not_retried(slack_settings) -> None:
    """channel_not_found will not become found. Retrying only delays the report."""
    attempts: list[httpx.Request] = []
    notifier.post_chat_message(
        {"text": "x"},
        bot_settings(slack_settings),
        web_api({"ok": False, "error": "channel_not_found"}, capture=attempts),
        lambda _: None,
    )
    assert len(attempts) == 1


def test_a_rate_limit_in_the_body_is_retried(slack_settings) -> None:
    """Slack reports throttling both as a 429 and as ok:false ratelimited."""
    attempts: list[httpx.Request] = []
    notifier.post_chat_message(
        {"text": "x"},
        bot_settings(slack_settings),
        web_api({"ok": False, "error": "ratelimited"}, capture=attempts),
        lambda _: None,
    )
    assert len(attempts) == 3  # max_retries


def test_a_transport_error_is_unconfirmed_never_retried(slack_settings) -> None:
    """No idempotency key here either, so a retry could post the digest twice."""
    attempts: list[httpx.Request] = []

    def boom(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        raise httpx.ConnectError("connection reset")

    code, error = notifier.post_chat_message(
        {"text": "x"},
        bot_settings(slack_settings),
        httpx.Client(transport=httpx.MockTransport(boom)),
        lambda _: None,
    )
    assert code is None, "None is what the caller records as UNCONFIRMED"
    assert len(attempts) == 1
    assert "ConnectError" in error


def test_the_bot_token_never_appears_in_an_error(slack_settings) -> None:
    """Notifier errors reach the dashboard through /api/automation."""
    settings = bot_settings(slack_settings)

    def leaky(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text=f"upstream rejected token {BOT_TOKEN}")

    _code, error = notifier.post_chat_message(
        {"text": "x"}, settings, httpx.Client(transport=httpx.MockTransport(leaky)), lambda _: None
    )
    assert BOT_TOKEN not in error
    assert "***" in error


def test_post_digest_picks_the_transport_from_the_settings(slack_settings) -> None:
    captured: list[httpx.Request] = []
    notifier.post_digest(
        {"text": "x"}, bot_settings(slack_settings), web_api({"ok": True}, capture=captured), lambda _: None
    )
    assert str(captured[0].url) == notifier.SLACK_POST_MESSAGE_URL

    captured.clear()
    notifier.post_digest(
        {"text": "x"}, slack_settings, web_api({"ok": True}, capture=captured), lambda _: None
    )
    assert str(captured[0].url) == WEBHOOK


def test_a_full_digest_delivers_through_the_bot_token(db_session, slack_settings) -> None:
    """End to end: the ledger settles as sent, exactly as with the webhook."""
    make_tender(db_session, notice="BOT-1")
    captured: list[httpx.Request] = []
    outcome = notifier.notify_new_tenders(
        db_session,
        since=RUN_START,
        batch_id="b-bot",
        settings=bot_settings(slack_settings),
        now=NOW,
        client=web_api({"ok": True, "ts": "1.1"}, capture=captured),
        sleep=lambda _: None,
    )
    assert outcome.status == "sent", outcome.error
    assert json.loads(captured[0].content.decode())["channel"] == "C0TEST0001"
    assert db_session.query(SlackNotification).filter_by(status="sent").count() == 1


def test_an_ok_false_digest_is_not_recorded_as_sent(db_session, slack_settings) -> None:
    """The ledger must stay open so a later run can retry the announcement."""
    make_tender(db_session, notice="BOT-2")
    outcome = notifier.notify_new_tenders(
        db_session,
        since=RUN_START,
        batch_id="b-bot-fail",
        settings=bot_settings(slack_settings),
        now=NOW,
        client=web_api({"ok": False, "error": "not_in_channel"}),
        sleep=lambda _: None,
    )
    assert outcome.status != "sent"
    assert db_session.query(SlackNotification).filter_by(status="sent").count() == 0


def test_no_transport_at_all_is_reported_as_disabled(db_session, slack_settings) -> None:
    make_tender(db_session, notice="BOT-3")
    outcome = notifier.notify_new_tenders(
        db_session,
        since=RUN_START,
        batch_id="b-none",
        settings=slack_settings.model_copy(
            update={"slack_webhook_url": "", "slack_bot_token": "", "slack_channel_id": ""}
        ),
        now=NOW,
    )
    assert outcome.status == "disabled"
    assert "SLACK_BOT_TOKEN" in outcome.error
    assert db_session.query(SlackNotification).count() == 0


def test_a_bot_token_without_a_channel_is_not_a_transport(slack_settings) -> None:
    """Half-configured must fail closed, not post somewhere unintended."""
    half = slack_settings.model_copy(update={"slack_bot_token": BOT_TOKEN, "slack_webhook_url": ""})
    assert half.slack_transport == "none"
    assert half.slack_configured is False
