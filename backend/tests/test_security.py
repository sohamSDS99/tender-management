"""What the API refuses, and what it merely limits. Two different things.

**D26 closed the front door.** Every route now needs a session except the five in
``security.PUBLIC_PATHS``, which reverses D5's "reads stay open" and D25's
"accounts gate nothing". Those older records are history, not instructions; the
tests below are the current contract.

What did *not* change is the second axis, and confusing the two is the easy
mistake here. Being signed in gets you through the door; it does not exempt you
from the cost controls behind it. D23's guards on the two expensive writes are
untouched - one sweep at a time, and a cooldown between operator runs - because
they were never about confidentiality. They were about not hammering eight
public services, and a signed-in operator can hammer them just as hard.

``CRON_SECRET`` is a *machine* identity: it passes the sign-in gate and skips the
cooldowns. No browser can hold it (D5), and with the secret unset - the default -
that door does not exist at all.
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
def test_a_wrong_secret_is_simply_not_a_machine(client, path) -> None:
    """A bad secret must not *downgrade* a caller who is otherwise allowed in.

    "No secret" and "wrong secret" have to behave identically, or a stray header
    could lock out a signed-in operator whose browser never had the secret to
    begin with. Both mean the same thing: you are a person, so the cost controls
    apply to you.
    """
    response = client.post(path, headers={CRON_HEADER: "not-the-secret"})
    assert response.status_code in (200, 202), response.text


@pytest.mark.parametrize("path", WRITE_ENDPOINTS)
def test_a_wrong_secret_does_not_open_the_door_on_its_own(anon_client, path) -> None:
    """The bypass is the *right* secret, not the presence of the header."""
    assert anon_client.post(path, headers={CRON_HEADER: "not-the-secret"}).status_code == 401


@pytest.mark.parametrize("path", WRITE_ENDPOINTS)
def test_a_signed_in_operator_needs_no_secret(client, path) -> None:
    """The point of D23, still standing: the dashboard button has to work.

    D26 added a door in front of it; it did not put the secret back.
    """
    response = client.post(path)
    assert response.status_code in (200, 202), response.text


def test_an_unset_secret_no_longer_disables_the_endpoint(db_session, monkeypatch, settings) -> None:
    """It used to 503. Now the guards carry it, so a deployment with no secret works.

    Failing closed made sense while the secret was the only control; with the
    cooldown and single-flight guards in place it only broke the dashboard.
    """
    from fastapi.testclient import TestClient

    from tests.conftest import _build_app, make_account

    no_secret = settings.model_copy(update={"cron_secret": ""})
    app = _build_app(db_session, monkeypatch, no_secret)
    _, token = make_account(db_session, no_secret)
    client = TestClient(app, cookies={no_secret.session_cookie_name: token})
    assert client.post("/api/fetch").status_code == 202


def test_operator_actions_can_be_switched_off_entirely(db_session, monkeypatch, settings) -> None:
    """An internet-exposed deployment needs a way to close this again."""
    from fastapi.testclient import TestClient

    from tests.conftest import _build_app, make_account

    closed = settings.model_copy(update={"allow_operator_actions": False})
    app = _build_app(db_session, monkeypatch, closed)
    _, token = make_account(db_session, closed)
    client = TestClient(app, cookies={closed.session_cookie_name: token})
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


# --- the sign-in gate (D26) ----------------------------------------------
#
# These four replace `test_read_endpoints_stay_open_by_design`, which asserted
# the exact opposite and was correct until D26. It was not deleted quietly: the
# reversal is the feature.


@pytest.mark.parametrize("path", ["/api/tenders", "/api/sources", "/api/stats", "/api/automation"])
def test_reads_now_require_a_session(anon_client, path) -> None:
    assert anon_client.get(path).status_code == 401


def test_health_stays_public_because_the_platform_probes_it(anon_client) -> None:
    """Railway's healthcheck hits /health over HTTP with no cookie.

    A 401 here does not fail loudly - it fails the *deployment*: the replica
    never becomes healthy, the release rolls back, and every application log line
    looks fine throughout. This is the one route that must never be gated.
    """
    assert anon_client.get("/health").status_code == 200


def test_the_doors_themselves_stay_public(anon_client) -> None:
    """Otherwise there is no way to sign in to the thing you must sign in to."""
    assert anon_client.get("/api/auth/session").status_code == 200
    assert anon_client.post("/api/auth/logout").status_code == 204
    # Wrong credentials, not a gate refusal: 401 with a message about the
    # details, which is the login form working rather than the gate closing.
    refused = anon_client.post("/api/auth/login", json={"email": "nobody@example.com", "password": "x" * 12})
    assert refused.status_code == 401
    assert "do not match" in refused.json()["detail"]


def test_a_route_added_later_is_private_without_anyone_remembering(db_session, monkeypatch, settings) -> None:
    """The gate is an app-level dependency, so new routes inherit it.

    This is the difference between a policy and a habit. If the gate were applied
    per-router, the next endpoint somebody adds would be public until they
    noticed - and nothing would tell them.

    Asserts the *behaviour*, not the registration. An earlier version of this
    test checked that "enforce_sign_in" appeared in `app.router.dependencies`,
    which would have passed just as happily if the function's first line were
    `return` - it proved the wiring existed, not that it did anything.
    """
    from fastapi.testclient import TestClient

    from tests.conftest import _build_app, make_account

    app = _build_app(db_session, monkeypatch, settings)

    # A route that did not exist when the gate was written. Nobody decorated it,
    # nobody added it to an allow-list, nobody thought about auth at all.
    @app.get("/api/invented-after-the-fact")
    def _invented() -> dict:  # pragma: no cover - the 401 means it never runs
        return {"secret": "should never be readable"}

    assert TestClient(app).get("/api/invented-after-the-fact").status_code == 401

    # ...and it is reachable once you are in, so the gate is refusing the caller
    # rather than the route simply being broken.
    _, token = make_account(db_session, settings)
    reader = TestClient(app, cookies={settings.session_cookie_name: token})
    assert reader.get("/api/invented-after-the-fact").json() == {"secret": "should never be readable"}


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


def test_the_docs_are_behind_the_gate_too(db_session, monkeypatch, settings) -> None:
    """Found by running the server, not by reading it — and it was a real hole.

    FastAPI registers /docs, /redoc and /openapi.json through Starlette's
    ``add_route``, while application-level ``dependencies`` only reach
    ``add_api_route``. So with ``docs_url="/docs"`` the gate silently skipped
    them: every other route answered 401 while ``/openapi.json`` handed an
    anonymous caller the complete list of paths, parameters and schema names.

    ``create_app`` now registers all three itself. This test is the reason it
    must keep doing so — reverting to ``docs_url=...`` reopens the hole and
    nothing else would notice.
    """
    from fastapi.testclient import TestClient

    from tests.conftest import _build_app, make_account

    with_docs = settings.model_copy(update={"enable_api_docs": True})
    # create_app() reads its settings through app.main's own binding, which the
    # fixture's patch of app.settings.config does not reach - so anything
    # decided at construction time (which docs routes exist at all) has to be
    # patched here or the test silently exercises the real environment.
    monkeypatch.setattr("app.main.get_settings", lambda: with_docs)
    app = _build_app(db_session, monkeypatch, with_docs)

    stranger = TestClient(app)
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert stranger.get(path).status_code == 401, path

    _, token = make_account(db_session, with_docs)
    reader = TestClient(app, cookies={with_docs.session_cookie_name: token})
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert reader.get(path).status_code == 200, path


def test_turning_the_docs_off_removes_them_rather_than_gating_them(db_session, monkeypatch, settings) -> None:
    """ENABLE_API_DOCS=false must still mean "not there", which is what prod uses."""
    from fastapi.testclient import TestClient

    from tests.conftest import _build_app, make_account

    no_docs = settings.model_copy(update={"enable_api_docs": False})
    monkeypatch.setattr("app.main.get_settings", lambda: no_docs)
    app = _build_app(db_session, monkeypatch, no_docs)
    _, token = make_account(db_session, no_docs)
    reader = TestClient(app, cookies={no_docs.session_cookie_name: token})
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert reader.get(path).status_code == 404, path
