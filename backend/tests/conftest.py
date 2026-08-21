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


@pytest.fixture
def client(db_session, monkeypatch, settings):
    """An *authorised operator*: the shared secret is sent on every request.

    The write endpoints are gated (see tests/test_security.py for the bare-client
    401/202 proof); sending the header here keeps every pre-existing test body
    unchanged while the endpoints themselves are no longer publicly callable.
    """
    app = _build_app(db_session, monkeypatch, settings)
    # No `with` block: the lifespan (migrations + scheduler) must not run in tests.
    return TestClient(app, headers={"X-Cron-Secret": CRON_SECRET})


@pytest.fixture
def anon_client(db_session, monkeypatch, settings):
    """An unauthenticated caller: no shared secret, as the public internet would."""
    return TestClient(_build_app(db_session, monkeypatch, settings))
