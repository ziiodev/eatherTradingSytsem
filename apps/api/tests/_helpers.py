"""Small helpers shared across test modules.

Lives outside ``conftest.py`` so it can be imported explicitly — pytest
treats conftest as collection-only, importing helpers from it works
but is confusing for readers.
"""

from __future__ import annotations

from datetime import UTC, datetime

from aether_api.auth.passwords import hash_password
from aether_api.models.agent import Agent
from aether_api.models.project import Project
from aether_api.models.user import User
from sqlalchemy.ext.asyncio import AsyncSession


async def seed_user(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    display_name: str | None = None,
    is_admin: bool = False,
) -> User:
    user = User(
        email=email.lower(),
        password_hash=hash_password(password),
        display_name=display_name,
        is_admin=is_admin,
    )
    session.add(user)
    await session.flush()
    return user


async def seed_agent(
    session: AsyncSession,
    *,
    owner: User,
    name: str = "agent-fixture",
    type: str = "worker",  # noqa: A002
    logica: str = "def on_tick(ctx):\n    return None\n",
) -> Agent:
    agent = Agent(
        user_id=owner.id,
        name=name,
        type=type,
        logica=logica,
        entrypoint="on_tick",
    )
    session.add(agent)
    await session.flush()
    return agent


async def seed_project(
    session: AsyncSession,
    *,
    owner: User,
    name: str = "project-fixture",
    symbol: str = "EURUSD",
    timeframe: str = "H1",
    mcp_url: str = "http://localhost:8081",
) -> Project:
    project = Project(
        user_id=owner.id,
        name=name,
        symbol=symbol,
        timeframe=timeframe,
        mcp_url=mcp_url,
        status="active",
    )
    session.add(project)
    await session.flush()
    return project


def utcnow() -> datetime:
    return datetime.now(tz=UTC)
