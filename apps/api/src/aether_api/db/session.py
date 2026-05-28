"""Async engine + session factory + ``get_session`` FastAPI dependency.

The engine is built lazily on first access so importing this module
(e.g. by Alembic's env.py for ``Base.metadata``) does not require the
runtime settings to be populated. Production callers reach ``engine`` /
``AsyncSessionLocal`` through their accessor functions which trigger
construction on demand.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from aether_api.core.settings import get_settings

_engine: AsyncEngine | None = None
_session_maker: async_sessionmaker[AsyncSession] | None = None


def _build_engine() -> AsyncEngine:
    s = get_settings()
    # pool_pre_ping=True: cheap SELECT 1 before handing a connection to
    # a request — avoids the "stale connection killed by Postgres idle
    # timeout" 500s that otherwise surface on the next request.
    return create_async_engine(
        str(s.database_url),
        pool_pre_ping=True,
        echo=False,
    )


def get_engine() -> AsyncEngine:
    """Return the singleton engine, building it on first call."""
    global _engine
    if _engine is None:
        _engine = _build_engine()
    return _engine


def get_session_maker() -> async_sessionmaker[AsyncSession]:
    global _session_maker
    if _session_maker is None:
        _session_maker = async_sessionmaker(
            get_engine(), expire_on_commit=False, class_=AsyncSession
        )
    return _session_maker


# Backwards-compatible module-level attributes (accessed by tests, etc.)
# These are properties-ish; the names exist but the heavy lifting happens
# in the getters above.


def __getattr__(name: str) -> object:
    """Module-level lazy attributes for ``engine`` and ``AsyncSessionLocal``."""
    if name == "engine":
        return get_engine()
    if name == "AsyncSessionLocal":
        return get_session_maker()
    raise AttributeError(f"module 'aether_api.db.session' has no attribute {name!r}")


async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield a request-scoped :class:`AsyncSession` to FastAPI handlers."""
    async with get_session_maker()() as session:
        yield session
