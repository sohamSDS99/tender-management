"""The API is reachable without a login. These are the limits on what that allows.

README section 12 warns the API has no authentication. The decision recorded in
docs/DECISIONS.md (D5) is that *reads* stay open - the notices are public
procurement data - while everything that writes, or spends an outbound request,
sits behind a shared secret.
"""

from __future__ import annotations

import pytest

from app.security import BASE_HEADERS, CRON_HEADER
from tests.conftest import CRON_SECRET

WRITE_ENDPOINTS = ["/api/fetch", "/api/tenders/rescore"]


# --- the shared-secret gate -----------------------------------------------


@pytest.mark.parametrize("path", WRITE_ENDPOINTS)
def test_write_endpoints_reject_an_anonymous_caller(anon_client, path) -> None:
    response = anon_client.post(path)
    assert response.status_code == 401, f"{path} is publicly callable"
    assert CRON_HEADER.lower() in response.headers.get("www-authenticate", "").lower()


@pytest.mark.parametrize("path", WRITE_ENDPOINTS)
def test_write_endpoints_reject_a_wrong_secret(anon_client, path) -> None:
    response = anon_client.post(path, headers={CRON_HEADER: "not-the-secret"})
    assert response.status_code == 401


def test_fetch_accepts_the_shared_secret_and_returns_202(anon_client) -> None:
    response = anon_client.post("/api/fetch", headers={CRON_HEADER: CRON_SECRET})
    assert response.status_code == 202
    assert "run_ids" in response.json()


def test_rescore_accepts_the_shared_secret(anon_client) -> None:
    response = anon_client.post("/api/tenders/rescore", headers={CRON_HEADER: CRON_SECRET})
    assert response.status_code == 200
    assert "rescored" in response.json()


def test_an_empty_header_is_not_a_valid_secret(anon_client) -> None:
    assert anon_client.post("/api/fetch", headers={CRON_HEADER: ""}).status_code == 401


def test_the_gate_fails_closed_when_no_secret_is_configured(db_session, monkeypatch, settings) -> None:
    """An unconfigured deployment refuses the endpoint rather than leaving it open."""
    from fastapi.testclient import TestClient

    from tests.conftest import _build_app

    open_settings = settings.model_copy(update={"cron_secret": ""})
    client = TestClient(_build_app(db_session, monkeypatch, open_settings))
    response = client.post("/api/fetch", headers={CRON_HEADER: "anything"})
    assert response.status_code == 503
    assert "CRON_SECRET" in response.json()["detail"]


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
    response = anon_client.post("/api/fetch")
    assert response.status_code == 401
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("X-Frame-Options") == "DENY"


def test_the_api_cannot_be_framed_or_rebased(anon_client) -> None:
    csp = anon_client.get("/api/stats").headers.get("Content-Security-Policy", "")
    assert "frame-ancestors 'none'" in csp
    assert "base-uri 'none'" in csp
    assert "default-src 'none'" in csp
