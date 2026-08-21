"""Application logging must survive the startup migration.

Regression test for a silent, load-bearing defect: alembic's env.py calls
``logging.config.fileConfig``, whose ``disable_existing_loggers`` default is
True. Because ``init_db()`` runs the migration *in-process* at startup, that
default switched off every logger the app had already created and re-pointed the
root logger at alembic's own stderr handler at WARNING.

The effect was that everything after startup logged nothing at all - the
scheduler starting, a sweep finishing, a Slack delivery failing. The API kept
working, so nothing looked broken; there was simply no evidence to diagnose a
missed run from, which is the one thing the runbook depends on.
"""

from __future__ import annotations

import logging

import pytest

from app.logging_config import configure_logging


def test_app_logging_survives_init_db(tmp_path, monkeypatch, capsys) -> None:
    """A log emitted after init_db() must still reach a handler.

    Asserted through stdout rather than caplog: configure_logging deliberately
    replaces the root handlers, which removes pytest's capture handler too, so
    caplog would report nothing even when logging works perfectly.
    """
    from app import db as db_module

    db_path = tmp_path / "startup.db"
    monkeypatch.setattr(db_module.settings, "database_url", f"sqlite:///{db_path}")
    monkeypatch.setattr(db_module.settings, "run_migrations_on_startup", True)

    configure_logging("INFO")
    logger = logging.getLogger("app.services.scheduler")

    db_module.init_db()

    assert logger.disabled is False, (
        "alembic's fileConfig disabled the app's loggers; env.py must pass " "disable_existing_loggers=False"
    )
    root = logging.getLogger()
    assert root.level <= logging.INFO, (
        f"root logger left at {logging.getLevelName(root.level)} after the migration; "
        "init_db must restore the app's logging configuration"
    )
    assert root.handlers, "root logger has no handlers after the migration"

    logger.info("post-migration probe")
    assert "post-migration probe" in capsys.readouterr().out


@pytest.fixture(autouse=True)
def _restore_logging():
    """These tests reconfigure global logging; hand it back as it was."""
    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    yield
    root.handlers, root.level = saved_handlers, saved_level


def test_configure_logging_is_idempotent() -> None:
    """Calling it twice must not stack duplicate handlers."""
    configure_logging("INFO")
    first = len(logging.getLogger().handlers)
    configure_logging("INFO")
    assert len(logging.getLogger().handlers) == first == 1
