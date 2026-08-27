"""Invitation email (D27).

The interesting cases here are all failures. A mail transport that works is
easy; what this module has to guarantee is that a broken one costs nothing and
lies about nothing:

* the invitation is created and usable even when SMTP is refusing connections
* the response never says "sent" unless the server accepted the message
* the invitation body — which contains a live single-use token — never reaches
  the log, and neither does the SMTP password
"""

from __future__ import annotations

import logging
import smtplib

import pytest

from app.services import mailer
from tests.conftest import make_account

SMTP_SETTINGS = {
    "smtp_host": "smtp.example.test",
    "smtp_port": 587,
    "smtp_username": "apikey",
    "smtp_password": "not-a-real-smtp-password",
    "smtp_from": "tenders@example.test",
}


@pytest.fixture
def mail_settings(settings):
    return settings.model_copy(update=SMTP_SETTINGS)


class FakeSMTP:
    """Records what it was asked to send. Substituted for a real connection."""

    def __init__(self, *, fail_with: Exception | None = None) -> None:
        self.fail_with = fail_with
        self.messages: list = []
        self.logged_in_as: str | None = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def login(self, username, password):
        self.logged_in_as = username

    def send_message(self, message):
        if self.fail_with is not None:
            raise self.fail_with
        self.messages.append(message)


# --- configuration ----------------------------------------------------------


def test_no_transport_configured_is_skipped_not_failed(settings):
    """The product's default state is "no mail server", and that is not an error."""
    result = mailer.send(settings, to="a@b.test", subject="s", body="b")
    assert result.status == mailer.SKIPPED
    assert not result.ok
    assert "no mail server" in result.detail.lower()


def test_a_host_without_a_from_address_is_not_configured(settings):
    """Half a configuration would fail at send time with a worse message."""
    half = settings.model_copy(update={"smtp_host": "smtp.example.test", "smtp_from": ""})
    assert mailer.is_configured(half) is False


def test_an_unauthenticated_relay_still_counts_as_configured(settings):
    """A relay on a private network needs no username, and that is ordinary."""
    relay = settings.model_copy(
        update={"smtp_host": "relay.internal", "smtp_from": "t@x.test", "smtp_username": ""}
    )
    assert mailer.is_configured(relay) is True


def test_an_empty_recipient_is_skipped(mail_settings):
    assert mailer.send(mail_settings, to="  ", subject="s", body="b").status == mailer.SKIPPED


# --- sending ----------------------------------------------------------------


def test_a_sent_message_carries_the_right_envelope(mail_settings, monkeypatch):
    fake = FakeSMTP()
    monkeypatch.setattr(mailer, "_connect", lambda settings: fake)

    result = mailer.send(mail_settings, to="them@example.test", subject="Subject", body="Body")

    assert result.status == mailer.SENT
    assert result.ok
    message = fake.messages[0]
    assert message["To"] == "them@example.test"
    assert message["Subject"] == "Subject"
    assert "tenders@example.test" in message["From"]
    # A Message-ID from the sending domain rather than the container's random
    # hostname, which reads as spam.
    assert "@example.test>" in message["Message-ID"]
    assert message.get_content().strip() == "Body"
    assert fake.logged_in_as == "apikey"


@pytest.mark.parametrize(
    "failure",
    [
        smtplib.SMTPAuthenticationError(535, b"bad credentials"),
        smtplib.SMTPRecipientsRefused({"them@example.test": (550, b"no such user")}),
        smtplib.SMTPServerDisconnected("connection dropped"),
        OSError("connection refused"),
        TimeoutError("timed out"),
    ],
)
def test_every_transport_failure_is_a_value_not_an_exception(mail_settings, monkeypatch, failure):
    """The caller is inside a request. An exception here would be a 500.

    Every one of these is a real thing a mail server does, and none of them is
    the administrator's fault or worth losing their invitation over.
    """
    monkeypatch.setattr(mailer, "_connect", lambda settings: FakeSMTP(fail_with=failure))

    result = mailer.send(mail_settings, to="them@example.test", subject="s", body="b")

    assert result.status == mailer.FAILED
    assert not result.ok
    assert type(failure).__name__ in result.detail


def test_a_connection_failure_is_a_value_too(mail_settings, monkeypatch):
    """Failing to connect at all, as distinct from failing to send."""

    def refuse(settings):
        raise OSError("connection refused")

    monkeypatch.setattr(mailer, "_connect", refuse)
    assert mailer.send(mail_settings, to="a@b.test", subject="s", body="b").status == mailer.FAILED


# --- what must never reach the log -----------------------------------------


def test_a_failure_logs_the_recipient_and_nothing_else(mail_settings, monkeypatch, caplog):
    """The body holds a live single-use credential."""
    monkeypatch.setattr(
        mailer, "_connect", lambda settings: FakeSMTP(fail_with=smtplib.SMTPServerDisconnected("x"))
    )
    secret_token = "tOkEn-THAT-MUST-NOT-BE-LOGGED"

    with caplog.at_level(logging.DEBUG):
        mailer.send(
            mail_settings,
            to="them@example.test",
            subject="s",
            body=f"Set up your account: https://x.test/?invite={secret_token}",
        )

    # The structured context is on the record, not in caplog's rendered text -
    # caplog installs its own formatter, so asserting against `caplog.text`
    # alone would silently pass even if the token *were* being logged in the
    # context. Check both surfaces.
    contexts = [getattr(record, "context", {}) for record in caplog.records]
    assert any(c.get("to") == "them@example.test" for c in contexts), "the recipient is safe"

    everything = caplog.text + repr(contexts)
    assert secret_token not in everything
    assert "not-a-real-smtp-password" not in everything


def test_a_success_does_not_log_the_body_either(mail_settings, monkeypatch, caplog):
    monkeypatch.setattr(mailer, "_connect", lambda settings: FakeSMTP())
    with caplog.at_level(logging.DEBUG):
        mailer.send(mail_settings, to="them@example.test", subject="s", body="invite=SECRET123")
    contexts = [getattr(record, "context", {}) for record in caplog.records]
    assert "SECRET123" not in caplog.text + repr(contexts)


def test_the_smtp_password_is_scrubbed_from_anything_logged(mail_settings):
    """`redact` scrubs a string before it is logged or stored.

    Registering smtp_password in SECRET_FIELDS is what makes an SMTP stack trace
    safe to put in FetchRun.error_message or to surface through /api/automation,
    the same way the Slack bot token is handled.
    """
    from app.settings import redact

    leaky = "SMTPAuthenticationError: login failed for apikey/not-a-real-smtp-password"
    scrubbed = redact(leaky, mail_settings)
    assert "not-a-real-smtp-password" not in scrubbed
    assert "SMTPAuthenticationError" in scrubbed


# --- the endpoint -----------------------------------------------------------


def _admin_client(db_session, monkeypatch, settings):
    from fastapi.testclient import TestClient

    from tests.conftest import _build_app

    app = _build_app(db_session, monkeypatch, settings)
    _, token = make_account(db_session, settings)
    return TestClient(app, cookies={settings.session_cookie_name: token})


def test_the_invitation_survives_a_mail_server_that_is_down(db_session, monkeypatch, mail_settings):
    """The whole point of sending *after* committing.

    An administrator whose mail server is broken must still end up with a link
    they can paste — which is exactly the behaviour the product had before mail
    existed.
    """
    monkeypatch.setattr(
        mailer, "_connect", lambda settings: FakeSMTP(fail_with=OSError("connection refused"))
    )
    client = _admin_client(db_session, monkeypatch, mail_settings)

    response = client.post("/api/auth/invites", json={"email": "them@example.test"})

    assert response.status_code == 201
    body = response.json()
    assert body["delivery"]["status"] == "failed"
    assert body["token"], "the link must still be returned"
    assert body["url"].endswith(body["token"])

    # ...and it is a real invitation, not a husk.
    assert client.get("/api/auth/invites").json()[0]["status"] == "pending"


def test_the_response_does_not_claim_sent_until_it_is(db_session, monkeypatch, mail_settings):
    monkeypatch.setattr(mailer, "_connect", lambda settings: FakeSMTP())
    client = _admin_client(db_session, monkeypatch, mail_settings)

    body = client.post("/api/auth/invites", json={"email": "them@example.test"}).json()

    assert body["delivery"]["status"] == "sent"
    assert "them@example.test" in body["delivery"]["detail"]


def test_the_email_contains_the_working_link(db_session, monkeypatch, mail_settings):
    """A message that arrives without a usable link is worse than none."""
    fake = FakeSMTP()
    monkeypatch.setattr(mailer, "_connect", lambda settings: fake)
    client = _admin_client(db_session, monkeypatch, mail_settings)

    body = client.post("/api/auth/invites", json={"email": "them@example.test"}).json()

    sent = fake.messages[0].get_content()
    assert body["url"] in sent
    assert "expires" in sent.lower()


def test_an_invitation_with_no_address_is_skipped_not_failed(db_session, monkeypatch, mail_settings):
    """An open "anyone with the link" invite has nobody to email."""
    monkeypatch.setattr(mailer, "_connect", lambda settings: FakeSMTP())
    client = _admin_client(db_session, monkeypatch, mail_settings)

    body = client.post("/api/auth/invites", json={"role": "member"}).json()

    assert body["delivery"]["status"] == "skipped"
    assert "nobody to email" in body["delivery"]["detail"]


def test_with_no_mail_configured_the_endpoint_behaves_as_it_always_did(db_session, monkeypatch, settings):
    """D27 must not change the no-SMTP deployment at all, beyond one sentence."""
    client = _admin_client(db_session, monkeypatch, settings)

    body = client.post("/api/auth/invites", json={"email": "them@example.test"}).json()

    assert body["delivery"]["status"] == "skipped"
    assert body["token"] and body["url"]


def test_an_admin_email_says_so_and_a_member_email_does_not(db_session, monkeypatch, mail_settings):
    """The invitee should know what they are accepting before they accept it."""
    fake = FakeSMTP()
    monkeypatch.setattr(mailer, "_connect", lambda settings: fake)
    client = _admin_client(db_session, monkeypatch, mail_settings)

    client.post("/api/auth/invites", json={"email": "one@example.test", "role": "admin"})
    client.post("/api/auth/invites", json={"email": "two@example.test", "role": "member"})

    assert "administrator" in fake.messages[0].get_content().lower()
    assert "be a member" in fake.messages[1].get_content().lower()
