"""Small helpers shared across test modules.

Lives outside ``conftest.py`` so it can be imported explicitly — pytest
treats conftest as collection-only, importing helpers from it works
but is confusing for readers.
"""

from __future__ import annotations

from datetime import UTC, datetime

from aether_api.auth.passwords import hash_password
from aether_api.models.account import Account
from aether_api.models.agent import Agent
from aether_api.models.exchange import Exchange
from aether_api.models.pair import Pair
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


async def seed_exchange(
    session: AsyncSession,
    *,
    owner: User,
    name: str = "exchange-fixture",
    code: str = "ICM",
    kind: str = "broker",
) -> Exchange:
    exchange = Exchange(
        user_id=owner.id,
        name=name,
        code=code,
        kind=kind,
    )
    session.add(exchange)
    await session.flush()
    return exchange


async def seed_account(
    session: AsyncSession,
    *,
    owner: User,
    exchange: Exchange,
    name: str = "account-fixture",
) -> Account:
    account = Account(
        user_id=owner.id,
        exchange_id=exchange.id,
        name=name,
        broker_name="ICMarkets",
        account_currency="USD",
        account_type="demo",
    )
    session.add(account)
    await session.flush()
    return account


async def seed_project(
    session: AsyncSession,
    *,
    owner: User,
    name: str = "project-fixture",
    symbol: str = "EURUSD",
    timeframe: str = "H1",
    mcp_url: str = "http://localhost:8081",
    account: Account | None = None,
) -> Pair:
    """Seed a Pair (formerly ``projects``) plus its Exchange→Account parents.

    Kept under the historical name ``seed_project`` so existing test
    modules continue to work; the returned object is a :class:`Pair`.
    Pass an explicit ``account`` to attach the pair to an existing
    account; otherwise a fresh Exchange + Account are seeded for ``owner``.
    """
    if account is None:
        # ``code`` is UNIQUE per (user_id) — seeding several pairs for the
        # same owner must not collide, so append a short random suffix.
        import uuid as _uuid

        exchange = await seed_exchange(
            session,
            owner=owner,
            code=f"EX-{_uuid.uuid4().hex[:12]}",
            name=f"exchange-for-{name}",
        )
        account = await seed_account(session, owner=owner, exchange=exchange)

    pair = Pair(
        user_id=owner.id,
        account_id=account.id,
        name=name,
        symbol=symbol,
        timeframe=timeframe,
        mcp_url=mcp_url,
        status="active",
    )
    session.add(pair)
    await session.flush()
    return pair


def utcnow() -> datetime:
    return datetime.now(tz=UTC)
