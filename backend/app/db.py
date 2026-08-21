"""Database engine / session management."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.logging_config import configure_logging
from app.settings import BACKEND_DIR, get_settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


def _engine_kwargs(url: str) -> dict:
    if url.startswith("sqlite"):
        # Ensure the sqlite directory exists (sqlite:///./data/tenders.db).
        path = url.split("///", 1)[-1]
        if path and path != ":memory:":
            Path(path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        return {"connect_args": {"check_same_thread": False}}
    return {"pool_pre_ping": True}


settings = get_settings()
engine = create_engine(settings.database_url, future=True, **_engine_kwargs(settings.database_url))
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Bring the schema up to date.

    Alembic is the source of truth. If it cannot run (e.g. a bare checkout with no
    alembic.ini) we fall back to create_all so the app still boots.
    """
    from app import models  # noqa: F401  (register mappers)

    if settings.run_migrations_on_startup:
        try:
            from alembic import command
            from alembic.config import Config

            cfg = Config(str(BACKEND_DIR / "alembic.ini"))
            cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
            cfg.set_main_option("sqlalchemy.url", settings.database_url)
            command.upgrade(cfg, "head")
            # alembic.ini owns a [logger_root] of its own, so running the upgrade
            # in-process leaves the root logger pointing at alembic's stderr
            # handler at WARNING. Without this, every later app log line - the
            # scheduler starting, a sweep finishing, a Slack failure - is thrown
            # away, and the runbook has nothing to diagnose from.
            configure_logging(settings.log_level)
            return
        except Exception:  # pragma: no cover - defensive boot path
            logger.warning("alembic upgrade failed, falling back to create_all", exc_info=True)
    Base.metadata.create_all(engine)
