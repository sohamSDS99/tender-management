"""Alembic environment. The database URL always comes from app settings."""

from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from app.db import Base
from app.models import (  # noqa: F401  (register tables)
    AppSetting,
    FetchRun,
    SlackNotification,
    Tender,
)
from app.settings import get_settings

config = context.config
if config.config_file_name is not None:
    # disable_existing_loggers defaults to True, which would switch off every
    # logger the application has already created. init_db() runs this migration
    # in-process at startup, so that default silently killed all app and uvicorn
    # logging for the rest of the process's life. See app.db.init_db, which also
    # restores the root handler afterwards.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

config.set_main_option("sqlalchemy.url", get_settings().database_url)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
