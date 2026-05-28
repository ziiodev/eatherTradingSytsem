"""Alembic environment — async-aware.

This file is invoked by ``alembic`` for every migration command. It must:

1. Read the database URL from the ``DATABASE_URL`` env var (never from
   ``alembic.ini`` — secrets stay out of the repo).
2. Run migrations via SQLAlchemy 2.0's async engine, using ``run_sync`` to
   bridge into Alembic's synchronous ``context.run_migrations()``.

When models land in Phase 3, set ``target_metadata = Base.metadata`` to
enable autogenerate as a diff aid (still hand-edit before commit).
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

# -----------------------------------------------------------------------------
# Alembic Config object — gives us access to alembic.ini values.
# -----------------------------------------------------------------------------
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Phase 3 introduced the ORM models. Import Base.metadata so future
# ``alembic revision --autogenerate`` runs can diff the live DB against
# the declared models. The 0001_init migration is hand-written and stays
# that way — autogenerate is a diff aid, never an unreviewed source of truth.
from aether_api.db.base import Base  # noqa: E402  — must come after sys.path is sane
from aether_api import models as _aether_models  # noqa: E402,F401  — registers tables on Base.metadata

target_metadata = Base.metadata


def _get_url() -> str:
    """Resolve the async DB URL from the environment.

    Migrations refuse to run if ``DATABASE_URL`` is not set — failing
    loudly here is far better than silently picking up a wrong default
    and mutating the wrong database.
    """
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL env var is not set. "
            "Example: postgresql+asyncpg://aether:dev_only_change_me@localhost:5432/aether"
        )
    return url


def run_migrations_offline() -> None:
    """Emit SQL to stdout — no DB connection.

    Useful for `alembic upgrade head --sql > out.sql` review and for the
    drift snapshot generation in 0001_init.sql.
    """
    context.configure(
        url=_get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    """Synchronous body of the online migration, invoked via ``run_sync``."""
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Connect via the async engine and dispatch migrations through run_sync."""
    connectable = create_async_engine(_get_url(), poolclass=pool.NullPool)

    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
