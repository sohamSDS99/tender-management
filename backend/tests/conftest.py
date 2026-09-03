"""Shared test fixtures. No test touches a live API."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.settings import Settings

CRON_SECRET = "test-cron-secret-not-real"

FIXTURES = Path(__file__).parent / "fixtures"
CONFIG = Path(__file__).resolve().parents[2] / "config" / "relevance_profiles.yaml"


def fixture_json(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        database_url="sqlite://",
        sam_gov_api_key="",
        retry_backoff_seconds=0.0,
        max_retries=2,
        request_timeout_seconds=5,
        max_pages_per_source=5,
        pncp_max_pages=2,
        pncp_modalidades="6",
        relevance_config_path=str(CONFIG),
        enable_scheduler=False,
        cron_secret=CRON_SECRET,
        slack_webhook_url="",
        public_app_url="http://localhost:8080",
    )


@pytest.fixture
def keyed_settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"sam_gov_api_key": "test-key-not-real"})


@pytest.fixture
def highergov_settings(settings: Settings) -> Settings:
    """HigherGov needs two things, not one: a key *and* a saved search id."""
    return settings.model_copy(
        update={
            "highergov_api_key": "hg-key-not-real",
            "highergov_search_id": "OvSsysuZMmV1UnmB1s0hJ",
        }
    )


@pytest.fixture(autouse=True)
def _clear_derived_caches():
    """Both service caches are process-global; every test gets its own database.

    Their fingerprints describe the data, which is the right invalidation rule
    for one deployment and the wrong one for a suite where two fresh in-memory
    databases can look identical. Cleared between tests so no test can be handed
    the previous test's engine or learned model.
    """
    from app.services.feedback import reset_model_cache
    from app.services.matching_rules import reset_engine_cache

    reset_engine_cache()
    reset_model_cache()
    yield
    reset_engine_cache()
    reset_model_cache()


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool, future=True
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)
    session = factory()
    session.info["factory"] = factory
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _build_app(db_session, monkeypatch, settings):
    """A TestClient app wired to the in-memory session and the fixture settings.

    Both `settings_dep` callables are overridden: FastAPI resolves dependencies
    by identity, and routes.py / security.py each hold their own reference to
    get_settings, so patching the module attribute alone would not reach them.
    """
    from app.api.routes import settings_dep as routes_settings_dep
    from app.main import create_app
    from app.security import settings_dep as security_settings_dep

    monkeypatch.setattr("app.services.ingest.SessionLocal", db_session.info["factory"])
    monkeypatch.setattr("app.settings.config.get_settings", lambda: settings)
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[routes_settings_dep] = lambda: settings
    app.dependency_overrides[security_settings_dep] = lambda: settings
    return app


#: The password every test account uses.
TEST_PASSWORD = "a-long-enough-test-password"

#: Hashing is deliberately expensive (~60ms of scrypt), and almost every test in
#: the suite now needs an account. Hashing once for the whole session and reusing
#: the digest keeps the run at seconds rather than minutes; the value is
#: identical to what ``register`` would have produced, so nothing about the
#: verification path is faked.
_cached_hash: str | None = None


def admin_hash() -> str:
    global _cached_hash
    if _cached_hash is None:
        from app.services import accounts

        _cached_hash = accounts.hash_password(TEST_PASSWORD)
    return _cached_hash


def make_account(db_session, settings, *, email="operator@example.com", role="admin"):
    """Insert an account and mint a session for it. Returns (user, raw_token).

    Rows are written directly rather than through the register endpoint because
    registration is invite-only after the first account (D25) and most tests do
    not care; the session itself is minted by the real ``start_session``, so the
    cookie under test is a genuine one.
    """
    from app.models import User
    from app.services import accounts

    user = User(
        email=email,
        display_name=email.partition("@")[0],
        password_hash=admin_hash(),
        role=role,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    token, _ = accounts.start_session(db_session, user, user_agent="pytest", settings=settings)
    return user, token


@pytest.fixture
def client(db_session, monkeypatch, settings):
    """A **signed-in administrator**, which is what every route now requires (D26).

    Deliberately does *not* send ``X-Cron-Secret``. The secret bypasses the
    sign-in gate, so a client carrying it would sail past the very thing this
    suite should be exercising - the whole pre-existing body of tests would keep
    passing even if the gate refused every real human. Tests that specifically
    need the secret (to skip an operator cooldown) send the header themselves.
    """
    app = _build_app(db_session, monkeypatch, settings)
    _, token = make_account(db_session, settings)
    # No `with` block: the lifespan (migrations + scheduler) must not run in tests.
    return TestClient(app, cookies={settings.session_cookie_name: token})


@pytest.fixture
def cron_client(db_session, monkeypatch, settings):
    """A machine caller: the shared secret, no account. Bypasses the gate (D26)."""
    app = _build_app(db_session, monkeypatch, settings)
    return TestClient(app, headers={"X-Cron-Secret": CRON_SECRET})


@pytest.fixture
def open_settings(settings):
    """Settings with the gate switched off, for the REQUIRE_SIGN_IN=false path."""
    return settings.model_copy(update={"require_sign_in": False})


@pytest.fixture
def anon_client(db_session, monkeypatch, settings):
    """An unauthenticated caller: no shared secret, as the public internet would."""
    return TestClient(_build_app(db_session, monkeypatch, settings))


@pytest.fixture
def isolated_factory(db_session):
    """A session factory bound to this test's own in-memory database.

    Passed to start_scheduler so the trigger decision is read from here rather
    than from whatever database the process happens to point at. Without it the
    result depends on the developer's own data/tenders.db.
    """
    return db_session.info["factory"]


@pytest.fixture
def public_dns(monkeypatch):
    """Resolve every hostname to a public address.

    The URL guard resolves hosts for real, so without this any test using an
    example.* URL depends on the machine having DNS and on that name existing.
    That is the environment-dependence that made the scheduler tests fail on a
    developer's laptop; it does not get to come back.
    """
    import app.services.probe as probe

    monkeypatch.setattr(probe, "_resolve", lambda host: ["93.184.216.34"])
