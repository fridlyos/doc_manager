"""Alembic environment.

Phase 1 provides the migration harness only; the first schema revision lands in
Phase 2. ``target_metadata`` stays ``None`` until the SQLAlchemy models exist.
The database URL is read from application settings so it is never committed.
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool

from doc_manager.core.config import get_settings

config = context.config

# Forward-only migrations against the configured database. psycopg 3 sync driver
# is used for Alembic even though the app runs async.
_url = str(get_settings().database_url).replace("+psycopg", "+psycopg")
config.set_main_option("sqlalchemy.url", _url)

target_metadata = None


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
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
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
