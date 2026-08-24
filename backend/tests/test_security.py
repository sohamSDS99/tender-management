"""The API is reachable without a login. These are the limits on what that allows.

README section 12 warns the API has no authentication. The decision recorded in
docs/DECISIONS.md (D5) is that *reads* stay open - the notices are public
procurement data.

D23 changed what guards the two expensive writes. They are no longer gated on
``CRON_SECRET``, because the secret was never protecting anything confidential
here - it was protecting eight public services from being hammered. Cost controls
do that job now: one sweep at a time, and a cooldown between operator-initiated
runs. The secret still works and still bypasses those guards, for CI.
"""

from __future__ import annotations

import pytest

from app.security import BASE_HEADERS, CRON_HEADER
from tests.conftest import CRON_SECRET

WRITE_ENDPOINTS = ["/api/fetch", "/api/tenders/rescore"]


# --- the shared secret is a bypass, not a gate (D23) -----------------------


def test_fetch_accepts_the_shared_secret_and_returns_202(anon_client) -> None:
    response = anon_client.post("/api/fetch", headers={CRON_HEADER: CRON_SECRET})
    assert response.status_code == 202
    assert "run_ids" in response.json()


def test_rescore_accepts_the_shared_secret(anon_client) -> None:
    response = anon_client.post("/api/tenders/rescore", headers={CRON_HEADER: CRON_SECRET})
    assert response.status_code == 200
    assert "rescored" in response.json()


@pytest.mark.parametrize("path", WRITE_ENDPOINTS)
def test_a_wrong_secret_is_treated_as_an_operator_not_rejected(anon_client, path) -> None:
    """A bad secret is no longer a 401: it just means the guards apply.

    The browser never has the secret, so "no secret" and "wrong secret" have to
    behave the same way or the dashboard's own buttons would 401.
    """
    response = anon_client.post(path, headers={CRON_HEADER: "not-the-secret"})
    assert response.status_code != 401
    assert response.status_code < 500


@pytest.mark.parametrize("path", WRITE_ENDPOINTS)
def test_an_operator_with_no_secret_is_allowed_through(anon_client, path) -> None:
    """This is the whole point of D23 - the dashboard button has to work."""
    response = anon_client.post(path)
    assert response.status_code in (200, 202), response.text


def test_an_unset_secret_no_longer_disables_the_endpoint(db_session, monkeypatch, settings) -> None:
    """It used to 503. Now the guards carry it, so a deployment with no secret works.

    Failing closed made sense while the secret was the only control; with the
    cooldown and single-flight guards in place it only broke the dashboard.
    """
    from fastapi.testclient import TestClient

    from tests.conftest import _build_app

    no_secret = settings.model_copy(update={"cron_secret": ""})
    client = TestClient(_build_app(db_session, monkeypatch, no_secret))
    assert client.post("/api/fetch").status_code == 202


def test_operator_actions_can_be_switched_off_entirely(db_session, monkeypatch, settings) -> None:
    """An internet-exposed deployment needs a way to close this again."""
    from fastapi.testclient import TestClient

    from tests.conftest import _build_app

    closed = settings.model_copy(update={"allow_operator_actions": False})
    client = TestClient(_build_app(db_session, monkeypatch, closed))
    for path in WRITE_ENDPOINTS:
        response = client.post(path)
        assert response.status_code == 403, path
        assert "ALLOW_OPERATOR_ACTIONS" in response.json()["detail"]


def test_the_secret_still_works_when_operator_actions_are_off(db_session, monkeypatch, settings) -> None:
    """Closing the browser door must not break CI."""
    from fastapi.testclient import TestClient

    from tests.conftest import _build_app

    closed = settings.model_copy(update={"allow_operator_actions": False})
    client = TestClient(_build_app(db_session, monkeypatch, closed))
    assert client.post("/api/fetch", headers={CRON_HEADER: CRON_SECRET}).status_code == 202


def test_the_secret_is_not_disclosed_by_the_error_body(anon_client) -> None:
    body = anon_client.post("/api/fetch", headers={CRON_HEADER: "wrong"}).text
    assert CRON_SECRET not in body


# --- read access is deliberately open ------------------------------------


@pytest.mark.parametrize("path", ["/health", "/api/tenders", "/api/sources", "/api/stats", "/api/automation"])
def test_read_endpoints_stay_open_by_design(anon_client, path) -> None:
    assert anon_client.get(path).status_code == 200


# --- hardening headers ----------------------------------------------------


def test_every_response_carries_the_hardening_headers(anon_client) -> None:
    response = anon_client.get("/health")
    for header, value in BASE_HEADERS.items():
        assert response.headers.get(header) == value, f"{header} missing or wrong"


def test_error_responses_are_hardened_too(anon_client) -> None:
    response = anon_client.post("/api/tenders/9999999/nope")
    assert response.status_code == 404
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("X-Frame-Options") == "DENY"


def test_the_api_cannot_be_framed_or_rebased(anon_client) -> None:
    csp = anon_client.get("/api/stats").headers.get("Content-Security-Policy", "")
    assert "frame-ancestors 'none'" in csp
    assert "base-uri 'none'" in csp
    assert "default-src 'none'" in csp
