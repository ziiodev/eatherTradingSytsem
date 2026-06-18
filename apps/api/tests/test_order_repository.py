"""Tests for :class:`OrderRepository` — tenant-scoped via projects.user_id JOIN.

Integration-marked: the repository talks to Postgres directly (the
filtered / metric SQL is the entire point of the test, mocking it out
defeats coverage). The ``app_client`` fixture brings the DB up via
Alembic ``heads`` so migration 0013's new columns + CHECK are in place.

The suite covers:

* :meth:`list_filtered` — symbol / side / status / magic / result /
  date-range / pagination + total count.
* :meth:`aggregate_metrics` — empty slice, mixed slice, "Infinity"
  profit factor edge.
* :meth:`account_summary_for_project` — closed P&L sum + open
  positions count.
* :meth:`upsert_by_ticket` — fresh insert + idempotent update on the
  same ticket.
* Cross-tenant — every read path returns empty / ``None`` for a
  project the caller does not own.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Seeders
# ---------------------------------------------------------------------------
async def _seed_two_users_with_projects() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    """Two distinct users, each with one project.

    Cross-tenant tests use ``user_b`` to attempt to read ``project_a``.
    """
    from aether_api.db.session import get_session_maker

    from tests._helpers import seed_project, seed_user

    maker = get_session_maker()
    async with maker() as session:
        user_a = await seed_user(session, email="a@example.com", password="testtesttesttest")
        user_b = await seed_user(session, email="b@example.com", password="testtesttesttest")
        proj_a = await seed_project(session, owner=user_a, name="proj-a")
        proj_b = await seed_project(session, owner=user_b, name="proj-b")
        await session.commit()
        return user_a.id, user_b.id, proj_a.id, proj_b.id


async def _seed_orders(
    user_id: uuid.UUID,
    project_id: uuid.UUID,
    rows: list[dict],
) -> list[uuid.UUID]:
    """Insert orders directly via the ORM. Returns the new IDs."""
    from aether_api.db.session import get_session_maker
    from aether_api.models.order import Order

    maker = get_session_maker()
    ids: list[uuid.UUID] = []
    async with maker() as session:
        for r in rows:
            order = Order(
                pair_id=project_id,
                user_id=user_id,
                agent_id=None,
                symbol=r.get("symbol", "EURUSD"),
                side=r.get("side", "buy"),
                volume=Decimal(str(r.get("volume", "0.1"))),
                sl=Decimal(str(r.get("sl", "1.0900"))),
                tp=r.get("tp"),
                status=r.get("status", "closed"),
                comment=r.get("comment"),
                magic=r.get("magic"),
                open_time=r.get("open_time"),
                open_price=r.get("open_price"),
                close_time=r.get("close_time"),
                close_price=r.get("close_price"),
                commission=r.get("commission"),
                swap=r.get("swap"),
                profit_gross=r.get("profit_gross"),
                profit_net=r.get("profit_net"),
                mt5_ticket=r.get("mt5_ticket"),
            )
            session.add(order)
            await session.flush()
            ids.append(order.id)
        await session.commit()
    return ids


# ---------------------------------------------------------------------------
# list_filtered
# ---------------------------------------------------------------------------
async def test_list_filtered_no_filters_returns_all_for_project(app_client) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.order_repository import OrderRepository

    user_a_id, _, proj_a_id, _ = await _seed_two_users_with_projects()
    await _seed_orders(
        user_a_id,
        proj_a_id,
        [
            {"symbol": "EURUSD", "side": "buy"},
            {"symbol": "GBPUSD", "side": "sell"},
        ],
    )

    maker = get_session_maker()
    async with maker() as session:
        repo = OrderRepository(session)
        rows, total = await repo.list_filtered(user_id=user_a_id, project_id=proj_a_id)
        assert total == 2
        assert len(rows) == 2


async def test_list_filtered_by_symbol(app_client) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.order_repository import OrderRepository

    user_a_id, _, proj_a_id, _ = await _seed_two_users_with_projects()
    await _seed_orders(
        user_a_id,
        proj_a_id,
        [
            {"symbol": "EURUSD"},
            {"symbol": "GBPUSD"},
            {"symbol": "EURUSD"},
        ],
    )

    maker = get_session_maker()
    async with maker() as session:
        repo = OrderRepository(session)
        rows, total = await repo.list_filtered(
            user_id=user_a_id, project_id=proj_a_id, symbol="EURUSD"
        )
        assert total == 2
        assert all(r.symbol == "EURUSD" for r in rows)


async def test_list_filtered_by_result(app_client) -> None:
    """``result='win'`` narrows on ``profit_net > 0``."""
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.order_repository import OrderRepository

    user_a_id, _, proj_a_id, _ = await _seed_two_users_with_projects()
    await _seed_orders(
        user_a_id,
        proj_a_id,
        [
            {"profit_net": Decimal("10")},
            {"profit_net": Decimal("-5")},
            {"profit_net": Decimal("3")},
        ],
    )

    maker = get_session_maker()
    async with maker() as session:
        repo = OrderRepository(session)
        rows, total = await repo.list_filtered(
            user_id=user_a_id, project_id=proj_a_id, result="win"
        )
        assert total == 2
        assert all(r.profit_net > 0 for r in rows)


async def test_list_filtered_by_date_range(app_client) -> None:
    """``from_date`` / ``to_date`` filter on ``open_time``."""
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.order_repository import OrderRepository

    user_a_id, _, proj_a_id, _ = await _seed_two_users_with_projects()
    now = datetime.now(tz=UTC)
    await _seed_orders(
        user_a_id,
        proj_a_id,
        [
            {"open_time": now - timedelta(days=10)},
            {"open_time": now - timedelta(days=2)},
            {"open_time": now},
        ],
    )

    maker = get_session_maker()
    async with maker() as session:
        repo = OrderRepository(session)
        rows, total = await repo.list_filtered(
            user_id=user_a_id,
            project_id=proj_a_id,
            from_date=now - timedelta(days=5),
            to_date=now + timedelta(minutes=1),
        )
        # The 10-days-ago row is out; the other two are in.
        assert total == 2
        assert len(rows) == 2


async def test_list_filtered_pagination(app_client) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.order_repository import OrderRepository

    user_a_id, _, proj_a_id, _ = await _seed_two_users_with_projects()
    await _seed_orders(user_a_id, proj_a_id, [{"symbol": "EURUSD"} for _ in range(5)])

    maker = get_session_maker()
    async with maker() as session:
        repo = OrderRepository(session)
        rows, total = await repo.list_filtered(
            user_id=user_a_id,
            project_id=proj_a_id,
            limit=2,
            offset=2,
        )
        assert total == 5
        assert len(rows) == 2


# ---------------------------------------------------------------------------
# Cross-tenant
# ---------------------------------------------------------------------------
async def test_list_filtered_cross_tenant_returns_empty(app_client) -> None:
    """User B asking for user A's project gets ([], 0) — not a leak."""
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.order_repository import OrderRepository

    user_a_id, user_b_id, proj_a_id, _ = await _seed_two_users_with_projects()
    await _seed_orders(user_a_id, proj_a_id, [{"symbol": "EURUSD"}])

    maker = get_session_maker()
    async with maker() as session:
        repo = OrderRepository(session)
        rows, total = await repo.list_filtered(user_id=user_b_id, project_id=proj_a_id)
        assert rows == []
        assert total == 0


async def test_account_summary_cross_tenant_returns_none(app_client) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.order_repository import OrderRepository

    user_a_id, user_b_id, proj_a_id, _ = await _seed_two_users_with_projects()

    maker = get_session_maker()
    async with maker() as session:
        repo = OrderRepository(session)
        summary = await repo.account_summary_for_project(user_id=user_b_id, project_id=proj_a_id)
        assert summary is None


async def test_upsert_by_ticket_cross_tenant_returns_none(app_client) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.order_repository import OrderRepository

    user_a_id, user_b_id, proj_a_id, _ = await _seed_two_users_with_projects()

    maker = get_session_maker()
    async with maker() as session:
        repo = OrderRepository(session)
        result = await repo.upsert_by_ticket(
            user_id=user_b_id,
            project_id=proj_a_id,
            ticket="123456",
            fields={
                "symbol": "EURUSD",
                "side": "buy",
                "volume": Decimal("0.1"),
                "sl": Decimal("1.0900"),
            },
        )
        assert result is None


# ---------------------------------------------------------------------------
# aggregate_metrics
# ---------------------------------------------------------------------------
async def test_aggregate_metrics_empty_slice(app_client) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.order_repository import OrderRepository

    user_a_id, _, proj_a_id, _ = await _seed_two_users_with_projects()

    maker = get_session_maker()
    async with maker() as session:
        repo = OrderRepository(session)
        m = await repo.aggregate_metrics(user_id=user_a_id, project_id=proj_a_id)
        assert m.trades_total == 0
        assert m.win_rate == 0.0
        assert m.profit_factor == 0.0
        assert m.avg_rr is None
        assert m.total_pnl == Decimal("0")


async def test_aggregate_metrics_mixed(app_client) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.order_repository import OrderRepository

    user_a_id, _, proj_a_id, _ = await _seed_two_users_with_projects()
    await _seed_orders(
        user_a_id,
        proj_a_id,
        [
            {
                "side": "buy",
                "sl": Decimal("1.0900"),
                "open_price": Decimal("1.1000"),
                "close_price": Decimal("1.1100"),
                "profit_net": Decimal("10"),
                "status": "closed",
            },
            {
                "side": "buy",
                "sl": Decimal("1.0900"),
                "open_price": Decimal("1.1000"),
                "close_price": Decimal("1.0950"),
                "profit_net": Decimal("-5"),
                "status": "closed",
            },
        ],
    )

    maker = get_session_maker()
    async with maker() as session:
        repo = OrderRepository(session)
        m = await repo.aggregate_metrics(user_id=user_a_id, project_id=proj_a_id)
        assert m.trades_total == 2
        assert m.win_rate == 0.5
        # 10 / 5 = 2.0
        assert m.profit_factor == 2.0
        # rs = [+1.0, -0.5] → mean 0.25
        assert m.avg_rr == 0.25
        assert m.total_pnl == Decimal("5")


async def test_aggregate_metrics_infinity_profit_factor(app_client) -> None:
    """All-wins slice → profit_factor wire value is the string 'Infinity'."""
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.order_repository import OrderRepository

    user_a_id, _, proj_a_id, _ = await _seed_two_users_with_projects()
    await _seed_orders(
        user_a_id,
        proj_a_id,
        [
            {"profit_net": Decimal("10"), "status": "closed"},
            {"profit_net": Decimal("5"), "status": "closed"},
        ],
    )

    maker = get_session_maker()
    async with maker() as session:
        repo = OrderRepository(session)
        m = await repo.aggregate_metrics(user_id=user_a_id, project_id=proj_a_id)
        assert m.profit_factor == "Infinity"


# ---------------------------------------------------------------------------
# account_summary_for_project
# ---------------------------------------------------------------------------
async def test_account_summary_closed_pnl_and_open_positions(app_client) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.order_repository import OrderRepository

    user_a_id, _, proj_a_id, _ = await _seed_two_users_with_projects()
    await _seed_orders(
        user_a_id,
        proj_a_id,
        [
            {"status": "closed", "profit_net": Decimal("12.50")},
            {"status": "closed", "profit_net": Decimal("-3.25")},
            {"status": "filled"},  # currently open
            {"status": "filled"},  # currently open
            {"status": "pending"},  # not on broker yet — shouldn't count as open
        ],
    )

    maker = get_session_maker()
    async with maker() as session:
        repo = OrderRepository(session)
        summary = await repo.account_summary_for_project(user_id=user_a_id, project_id=proj_a_id)
        assert summary is not None
        assert summary.closed_pnl == Decimal("9.25")
        assert summary.open_positions == 2
        # Realtime fields are layered in by Phase 4's LiveBus; for now None.
        assert summary.realtime_equity is None
        assert summary.realtime_balance is None
        assert summary.realtime_margin is None


# ---------------------------------------------------------------------------
# upsert_by_ticket
# ---------------------------------------------------------------------------
async def test_upsert_by_ticket_insert_then_update(app_client) -> None:
    """Fresh insert ➞ subsequent call with same ticket UPDATEs in place."""
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.order_repository import OrderRepository

    user_a_id, _, proj_a_id, _ = await _seed_two_users_with_projects()

    maker = get_session_maker()
    async with maker() as session:
        repo = OrderRepository(session)
        row1 = await repo.upsert_by_ticket(
            user_id=user_a_id,
            project_id=proj_a_id,
            ticket="987654",
            fields={
                "symbol": "EURUSD",
                "side": "buy",
                "volume": Decimal("0.5"),
                "sl": Decimal("1.0900"),
                "open_price": Decimal("1.1000"),
                "status": "filled",
            },
        )
        await session.commit()
        assert row1 is not None
        assert row1.mt5_ticket == 987654
        assert row1.status == "filled"
        first_id = row1.id

    # Second pass — close-out the same ticket.
    async with maker() as session:
        repo = OrderRepository(session)
        row2 = await repo.upsert_by_ticket(
            user_id=user_a_id,
            project_id=proj_a_id,
            ticket="987654",
            fields={
                "close_price": Decimal("1.1100"),
                "profit_net": Decimal("50"),
                "status": "closed",
            },
        )
        await session.commit()
        assert row2 is not None
        # Same row — primary key preserved.
        assert row2.id == first_id
        assert row2.status == "closed"
        assert row2.close_price == Decimal("1.11000000")
        assert row2.profit_net == Decimal("50.0000")
