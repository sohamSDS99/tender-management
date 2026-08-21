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


@pytest.fixture
def client(db_session, monkeypatch, settings):
    from app.main import create_app

    monkeypatch.setattr("app.services.ingest.SessionLocal", db_session.info["factory"])
    monkeypatch.setattr("app.settings.config.get_settings", lambda: settings)
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    # No `with` block: the lifespan (migrations + scheduler) must not run in tests.
    return TestClient(app)
