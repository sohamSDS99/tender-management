"""Stored source credentials.

The dashboard is unauthenticated by design (D23), so the read path is closed
rather than gated: a credential can be written and never read back.
"""

from __future__ import annotations

import logging

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
    assert settings_with_stored_credentials(db_session, _settings(sam_gov_api_key="ENV")).sam_gov_api_key == "ENV"


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
    from fastapi.testclient import TestClient

    from app.api.routes import settings_dep as routes_settings_dep
    from app.db import get_db
    from app.main import create_app

    closed = settings.model_copy(update={"allow_operator_actions": False})
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[routes_settings_dep] = lambda: closed
    response = TestClient(app).put("/api/sources/sam/credential", json={"value": "x"})
    assert response.status_code == 403
    assert "ALLOW_OPERATOR_ACTIONS" in response.json()["detail"]
