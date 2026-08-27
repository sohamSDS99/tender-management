"""Stored source credentials.

The dashboard is unauthenticated by design (D23), so the read path is closed
rather than gated: a credential can be written and never read back.
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from app.models import AppSetting
from app.services.credentials import (
    CREDENTIAL_FIELDS,
    credential_hint,
    set_credential,
    settings_with_stored_credentials,
    stored_credential,
)
from app.settings import Settings


def _settings(**kw) -> Settings:
    return Settings(_env_file=None, database_url="sqlite://", **kw)


@pytest.fixture
def api_path_client(db_session, monkeypatch, settings):
    """A client whose SAM connector uses the metered API rather than the extract.

    SAM is the only built-in that takes a credential, and on the default
    transport - the free bulk extract - it needs none, so it can no longer
    demonstrate what a stored key changes. Pinning this client to the API path
    keeps these tests about the credential mechanism instead of about SAM.
    """
    from tests.conftest import CRON_SECRET, _build_app

    api_settings = settings.model_copy(update={"sam_use_bulk_extract": False})
    app = _build_app(db_session, monkeypatch, api_settings)
    return TestClient(app, headers={"X-Cron-Secret": CRON_SECRET})


def test_a_stored_credential_beats_the_environment(db_session):
    set_credential(db_session, "sam", "STORED-KEY-1234")
    resolved = settings_with_stored_credentials(db_session, _settings(sam_gov_api_key="ENV-KEY"))
    assert resolved.sam_gov_api_key == "STORED-KEY-1234"


def test_the_environment_is_used_when_nothing_is_stored(db_session):
    resolved = settings_with_stored_credentials(db_session, _settings(sam_gov_api_key="ENV-KEY"))
    assert resolved.sam_gov_api_key == "ENV-KEY"


def test_the_overlay_leaves_every_other_setting_alone(db_session):
    set_credential(db_session, "sam", "STORED")
    base = _settings(sam_gov_api_key="ENV", log_level="WARNING", page_size=42)
    resolved = settings_with_stored_credentials(db_session, base)
    assert resolved.log_level == "WARNING"
    assert resolved.page_size == 42


def test_only_declared_sources_can_carry_a_credential(db_session):
    # A typo'd or invented source name must not silently create a row that
    # nothing will ever read.
    assert "ted" not in CREDENTIAL_FIELDS
    assert set_credential(db_session, "ted", "nope") is False
    assert stored_credential(db_session, "ted") is None


def test_the_hint_shows_only_the_last_four_characters(db_session):
    set_credential(db_session, "sam", "abcdefghijkl9876")
    assert credential_hint(db_session, "sam") == "…9876"


def test_a_short_credential_is_not_revealed_by_its_own_hint(db_session):
    set_credential(db_session, "sam", "abc")
    hint = credential_hint(db_session, "sam")
    assert "abc" not in (hint or "")


def test_clearing_a_credential_falls_back_to_the_environment(db_session):
    set_credential(db_session, "sam", "STORED")
    set_credential(db_session, "sam", "")
    assert stored_credential(db_session, "sam") is None
    assert (
        settings_with_stored_credentials(db_session, _settings(sam_gov_api_key="ENV")).sam_gov_api_key
        == "ENV"
    )


def test_writing_a_credential_never_logs_its_value(db_session, caplog):
    with caplog.at_level(logging.DEBUG):
        set_credential(db_session, "sam", "SUPER-SECRET-VALUE")
    assert "SUPER-SECRET-VALUE" not in caplog.text


def test_the_value_is_not_in_the_row_repr(db_session):
    set_credential(db_session, "sam", "SUPER-SECRET-VALUE")
    row = db_session.query(AppSetting).filter(AppSetting.key == "source.sam.credential").one()
    assert "SUPER-SECRET-VALUE" not in repr(row)


# --- endpoints -------------------------------------------------------------


def test_get_sources_never_returns_the_credential_value(client, db_session):
    set_credential(db_session, "sam", "SUPER-SECRET-VALUE")
    response = client.get("/api/sources")
    assert response.status_code == 200
    assert "SUPER-SECRET-VALUE" not in response.text
    sam = next(s for s in response.json() if s["name"] == "sam")
    assert sam["credential_configured"] is True
    assert sam["credential_hint"] == "…ALUE"


def test_put_stores_a_credential(client, db_session):
    response = client.put("/api/sources/sam/credential", json={"value": "NEW-KEY-ABCD"})
    assert response.status_code == 204
    assert stored_credential(db_session, "sam") == "NEW-KEY-ABCD"


def test_put_is_refused_for_a_source_that_takes_no_key(client):
    assert client.put("/api/sources/ted/credential", json={"value": "x"}).status_code == 404


def test_put_is_refused_when_operator_actions_are_off(db_session, monkeypatch, settings):
    """403 for the *reason given*, not 401 for having no account.

    Built through the shared helper so the sign-in gate sees the same settings
    the route does. Rolling a bare `create_app()` here overrode only
    `routes.settings_dep`, left `security.settings_dep` on the real environment,
    and the request died at the gate with a 401 - which would have passed a
    naive "it was refused" assertion while proving nothing about
    ALLOW_OPERATOR_ACTIONS.
    """
    from fastapi.testclient import TestClient

    from tests.conftest import _build_app, make_account

    closed = settings.model_copy(update={"allow_operator_actions": False})
    app = _build_app(db_session, monkeypatch, closed)
    _, token = make_account(db_session, closed)
    client = TestClient(app, cookies={closed.session_cookie_name: token})
    response = client.put("/api/sources/sam/credential", json={"value": "x"})
    assert response.status_code == 403
    assert "ALLOW_OPERATOR_ACTIONS" in response.json()["detail"]


def test_a_stored_key_clears_the_sources_unavailable_reason(api_path_client, db_session):
    """The point of setting a key here: the source stops reporting it is missing.

    unavailable_reason() is computed from the settings the connector is built
    with, so a listing built from raw Settings would keep saying the key is not
    set while showing its hint beside it.
    """
    before = next(s for s in api_path_client.get("/api/sources").json() if s["name"] == "sam")
    assert before["unavailable_reason"] is not None

    set_credential(db_session, "sam", "A-REAL-LOOKING-KEY")
    after = next(s for s in api_path_client.get("/api/sources").json() if s["name"] == "sam")
    assert after["unavailable_reason"] is None
    assert after["credential_configured"] is True


def test_a_stored_key_puts_the_source_back_into_the_sweep(db_session, monkeypatch):
    """The whole point: a key set from the dashboard has to reach the planner.

    _plan asked enabled_sources() with the raw Settings, and enabled_sources
    filters on unavailable_reason(), which reads the key off Settings. So a
    stored key made SAM.gov *look* healthy on the Sources page while every
    sweep silently skipped it — the display was fixed and the behaviour was not.
    """
    from app.services import ingest

    monkeypatch.setattr(ingest, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    # The API path, where a key is what decides availability. On the default
    # bulk-extract transport SAM is in the sweep with or without one.
    settings = Settings(
        _env_file=None,
        database_url="sqlite://",
        sam_gov_api_key="",
        sam_use_bulk_extract=False,
    )

    selected, _busy, _from, _to = ingest._plan(None, 7, settings)
    assert "sam" not in selected, "no key anywhere, so it stays out"

    set_credential(db_session, "sam", "A-STORED-KEY")
    selected, _busy, _from, _to = ingest._plan(None, 7, settings)
    assert "sam" in selected, "a stored key must put the source back in the sweep"


# --- secrets that are not source credentials -------------------------------


def test_a_stored_slack_token_beats_the_environment(db_session):
    from app.services.credentials import set_secret

    set_secret(db_session, "slack_bot_token", "xoxb-STORED")
    resolved = settings_with_stored_credentials(db_session, _settings(slack_bot_token="xoxb-ENV"))
    assert resolved.slack_bot_token == "xoxb-STORED"


def test_storing_a_token_and_channel_switches_the_transport_on(db_session):
    """The point: configuring from the dashboard has to change what the app does."""
    from app.services.credentials import set_secret

    base = _settings()
    assert base.slack_transport == "none"

    set_secret(db_session, "slack_bot_token", "xoxb-STORED")
    set_secret(db_session, "slack_channel_id", "C0123ABCDEF")
    assert settings_with_stored_credentials(db_session, base).slack_transport == "bot_token"


def test_only_declared_settings_can_be_stored(db_session):
    from app.services.credentials import set_secret, stored_secret

    # Otherwise any Settings field becomes writable from an unauthenticated page.
    assert set_secret(db_session, "database_url", "postgresql://evil") is False
    assert stored_secret(db_session, "database_url") is None


def test_clearing_a_secret_falls_back_to_the_environment(db_session):
    from app.services.credentials import set_secret

    set_secret(db_session, "slack_bot_token", "xoxb-STORED")
    set_secret(db_session, "slack_bot_token", "")
    assert (
        settings_with_stored_credentials(db_session, _settings(slack_bot_token="xoxb-ENV")).slack_bot_token
        == "xoxb-ENV"
    )


def test_a_secret_value_is_never_logged(db_session, caplog):
    import logging as _logging

    from app.services.credentials import set_secret

    with caplog.at_level(_logging.DEBUG):
        set_secret(db_session, "slack_bot_token", "xoxb-SUPER-SECRET")
    assert "xoxb-SUPER-SECRET" not in caplog.text
