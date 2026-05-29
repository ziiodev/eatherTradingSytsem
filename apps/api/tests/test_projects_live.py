"""End-to-end tests for the Operativa surface on ``projects_live``.

Covered surfaces (Phase 3 of ``project-operativa``):

* ``GET /api/projects/{id}/operativa/account-summary``
    - Happy path with MCP reachable: 200 + full payload.
    - MCP outage: 200 + ``mcp_status='unavailable'``; DB-side P&L still
      computed.
    - Cross-tenant: 404 (no existence leak).

* ``GET /api/projects/{id}/operativa/orders``
    - Filter combinations: date range, symbol, side, result win/loss,
      magic, status, pagination.
    - Metrics: ``profit_factor == 'Infinity'`` when no losses; ``avg_rr``
      is ``None`` when no valid R denominator.
    - Cross-tenant: 404.

These tests stub :func:`aether_api.routers.projects_live.get_mcp_client`
so no real TCP traffic flows. The DB is the real Postgres provided by
``conftest.py`` so the tenancy JOIN + SQL filters are exercised
end-to-end.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import pytest
from aether_api.mcp_client.errors import MCPUnreachable

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _seed_user_and_project(
    *,
    email: str = "operativa@example.com",
    password: str = "x" * 24,
    project_name: str = "P-operativa",
):
    """Seed a user + active project. Returns ``(user_id, project_id)``."""
    from aether_api.db.session import get_session_maker

    from tests._helpers import seed_project, seed_user

    maker = get_session_maker()
    async with maker() as session:
        user = await seed_user(session, email=email, password=password)
        project = await seed_project(
            session,
            owner=user,
            name=project_name,
            mcp_url="http://test-mcp.local:8765",
        )
        await session.commit()
        return user.id, project.id


async def _login(client, *, email: str, password: str) -> None:
    resp = await client.post(
        "/api/auth/login", json={"email": email, "password": password}
    )
    assert resp.status_code == 200, resp.text


async def _seed_and_login(
    client,
    *,
    email: str = "operativa@example.com",
    password: str = "x" * 24,
    project_name: str = "P-operativa",
) -> tuple[uuid.UUID, uuid.UUID]:
    owner_id, project_id = await _seed_user_and_project(
        email=email, password=password, project_name=project_name
    )
    await _login(client, email=email, password=password)
    return owner_id, project_id


async def _insert_order(
    *,
    user_id: uuid.UUID,
    project_id: uuid.UUID,
    symbol: str = "EURUSD",
    side: str = "buy",
    volume: str = "0.1",
    sl: str = "1.0900",
    tp: str | None = "1.1100",
    status: str = "closed",
    magic: int | None = 1000,
    open_time: datetime | None = None,
    close_time: datetime | None = None,
    open_price: str | None = "1.1000",
    close_price: str | None = "1.1050",
    profit_net: str | None = "50.00",
    profit_gross: str | None = "52.00",
    commission: str | None = "-1.00",
    swap: str | None = "-1.00",
) -> uuid.UUID:
    """Insert an order row directly via the ORM. Returns the new id."""
    from aether_api.db.session import get_session_maker
    from aether_api.models.order import Order

    maker = get_session_maker()
    async with maker() as session:
        order = Order(
            project_id=project_id,
            user_id=user_id,
            symbol=symbol,
            side=side,
            volume=Decimal(volume),
            sl=Decimal(sl),
            tp=Decimal(tp) if tp is not None else None,
            status=status,
            magic=magic,
            open_time=open_time,
            close_time=close_time,
            open_price=Decimal(open_price) if open_price is not None else None,
            close_price=Decimal(close_price) if close_price is not None else None,
            profit_net=Decimal(profit_net) if profit_net is not None else None,
            profit_gross=Decimal(profit_gross) if profit_gross is not None else None,
            commission=Decimal(commission) if commission is not None else None,
            swap=Decimal(swap) if swap is not None else None,
        )
        session.add(order)
        await session.commit()
        await session.refresh(order)
        return uuid.UUID(str(order.id))


@contextmanager
def _patch_mcp_client(**responses: Any):
    """Patch the router's :func:`get_mcp_client` with a fake."""

    class _Fake:
        async def get_account(self) -> dict[str, Any]:
            v = responses.get("get_account")
            if isinstance(v, Exception):
                raise v
            if v is not None:
                return dict(v)
            return {
                "balance": "10000",
                "equity": "10250",
                "margin": "150",
                "free_margin": "10100",
                "leverage": 100,
                "currency": "USD",
                "login": 12345,
            }

    with patch(
        "aether_api.routers.projects_live.get_mcp_client", return_value=_Fake()
    ):
        yield


# ===========================================================================
# /operativa/account-summary
# ===========================================================================


async def test_account_summary_happy_path_mcp_up(app_client) -> None:
    user_id, project_id = await _seed_and_login(app_client)
    now = datetime.now(tz=UTC)
    # Three closed trades: today, 5d ago, 20d ago.
    await _insert_order(
        user_id=user_id,
        project_id=project_id,
        open_time=now - timedelta(hours=2),
        close_time=now - timedelta(hours=1),
        profit_net="100.00",
        symbol="EURUSD",
    )
    await _insert_order(
        user_id=user_id,
        project_id=project_id,
        open_time=now - timedelta(days=5, hours=2),
        close_time=now - timedelta(days=5),
        profit_net="-30.00",
        side="sell",
        symbol="GBPUSD",
    )
    await _insert_order(
        user_id=user_id,
        project_id=project_id,
        open_time=now - timedelta(days=20, hours=2),
        close_time=now - timedelta(days=20),
        profit_net="200.00",
        symbol="USDJPY",
    )

    with _patch_mcp_client():
        resp = await app_client.get(
            f"/api/projects/{project_id}/operativa/account-summary"
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mcp_status"] == "available"
    assert body["equity"] == "10250"
    assert body["balance"] == "10000"
    assert body["margin_used"] == "150"
    assert body["margin_free"] == "10100"
    # balance > equity? No, equity > balance → drawdown should clamp to 0.
    assert body["current_drawdown"] == "0"
    # Day window contains only the +100 trade.
    assert Decimal(body["pnl_day"]) == Decimal("100.00")
    # Week window contains +100 and -30 → 70.
    assert Decimal(body["pnl_week"]) == Decimal("70.00")
    # Month window contains all three → 270.
    assert Decimal(body["pnl_month"]) == Decimal("270.00")
    # source_at parseable as ISO.
    datetime.fromisoformat(body["source_at"].replace("Z", "+00:00"))


async def test_account_summary_mcp_unreachable_returns_200(app_client) -> None:
    user_id, project_id = await _seed_and_login(
        app_client, email="mcp_down@example.com", project_name="P-down"
    )
    now = datetime.now(tz=UTC)
    await _insert_order(
        user_id=user_id,
        project_id=project_id,
        open_time=now - timedelta(hours=2),
        close_time=now - timedelta(hours=1),
        profit_net="42.00",
    )

    with _patch_mcp_client(get_account=MCPUnreachable("simulated outage")):
        resp = await app_client.get(
            f"/api/projects/{project_id}/operativa/account-summary"
        )
    # CRITICAL: 200, not 502, even though MCP is dead.
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mcp_status"] == "unavailable"
    assert body["equity"] is None
    assert body["balance"] is None
    assert body["margin_used"] is None
    assert body["margin_free"] is None
    assert body["current_drawdown"] is None
    # DB-side P&L still computes.
    assert Decimal(body["pnl_day"]) == Decimal("42.00")
    assert Decimal(body["pnl_week"]) == Decimal("42.00")
    assert Decimal(body["pnl_month"]) == Decimal("42.00")


async def test_account_summary_cross_tenant_returns_404(app_client) -> None:
    # User A owns a project; user B logs in and tries to read it.
    user_a_id, project_a_id = await _seed_user_and_project(
        email="a-acct@example.com", project_name="A-acct"
    )
    await _seed_and_login(
        app_client, email="b-acct@example.com", project_name="B-acct"
    )

    resp = await app_client.get(
        f"/api/projects/{project_a_id}/operativa/account-summary"
    )
    assert resp.status_code == 404


async def test_account_summary_drawdown_when_equity_below_balance(app_client) -> None:
    _, project_id = await _seed_and_login(
        app_client, email="dd@example.com", project_name="P-dd"
    )
    underwater = {
        "balance": "10000",
        "equity": "9800",
        "margin": "120",
        "free_margin": "9700",
    }
    with _patch_mcp_client(get_account=underwater):
        resp = await app_client.get(
            f"/api/projects/{project_id}/operativa/account-summary"
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["mcp_status"] == "available"
    # balance (10000) - equity (9800) = 200 drawdown.
    assert Decimal(body["current_drawdown"]) == Decimal("200")


# ===========================================================================
# /operativa/orders
# ===========================================================================


async def test_orders_default_window_returns_all_recent(app_client) -> None:
    user_id, project_id = await _seed_and_login(
        app_client, email="ord-all@example.com", project_name="P-ord-all"
    )
    now = datetime.now(tz=UTC)
    await _insert_order(
        user_id=user_id, project_id=project_id, open_time=now - timedelta(days=1),
        close_time=now - timedelta(hours=23), profit_net="10.00", symbol="EURUSD",
    )
    await _insert_order(
        user_id=user_id, project_id=project_id, open_time=now - timedelta(days=2),
        close_time=now - timedelta(days=1, hours=23), profit_net="-5.00", side="sell",
        symbol="GBPUSD",
    )

    resp = await app_client.get(f"/api/projects/{project_id}/operativa/orders")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 2
    assert len(body["items"]) == 2
    assert body["metrics"]["trades_total"] == 2


async def test_orders_filter_by_symbol(app_client) -> None:
    user_id, project_id = await _seed_and_login(
        app_client, email="ord-sym@example.com", project_name="P-ord-sym"
    )
    now = datetime.now(tz=UTC)
    await _insert_order(
        user_id=user_id, project_id=project_id, open_time=now - timedelta(hours=2),
        close_time=now - timedelta(hours=1), symbol="EURUSD", profit_net="10",
    )
    await _insert_order(
        user_id=user_id, project_id=project_id, open_time=now - timedelta(hours=4),
        close_time=now - timedelta(hours=3), symbol="GBPUSD", profit_net="20",
    )

    resp = await app_client.get(
        f"/api/projects/{project_id}/operativa/orders",
        params={"symbol": "EURUSD"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["symbol"] == "EURUSD"


async def test_orders_filter_by_side_and_result(app_client) -> None:
    user_id, project_id = await _seed_and_login(
        app_client, email="ord-side@example.com", project_name="P-ord-side"
    )
    now = datetime.now(tz=UTC)
    # buy winner
    await _insert_order(
        user_id=user_id, project_id=project_id, open_time=now - timedelta(hours=5),
        close_time=now - timedelta(hours=4), side="buy", profit_net="50",
    )
    # buy loser
    await _insert_order(
        user_id=user_id, project_id=project_id, open_time=now - timedelta(hours=4),
        close_time=now - timedelta(hours=3), side="buy", profit_net="-20",
    )
    # sell winner
    await _insert_order(
        user_id=user_id, project_id=project_id, open_time=now - timedelta(hours=3),
        close_time=now - timedelta(hours=2), side="sell", profit_net="15",
    )

    # buy + win → only the +50 row.
    resp = await app_client.get(
        f"/api/projects/{project_id}/operativa/orders",
        params={"side": "buy", "result": "win"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["side"] == "buy"
    assert Decimal(body["items"][0]["profit_net"]) > 0

    # sell only → one sell row.
    resp2 = await app_client.get(
        f"/api/projects/{project_id}/operativa/orders",
        params={"side": "sell"},
    )
    assert resp2.status_code == 200
    assert resp2.json()["total"] == 1

    # losses only → one losing row.
    resp3 = await app_client.get(
        f"/api/projects/{project_id}/operativa/orders",
        params={"result": "loss"},
    )
    assert resp3.status_code == 200
    body3 = resp3.json()
    assert body3["total"] == 1
    assert Decimal(body3["items"][0]["profit_net"]) < 0


async def test_orders_filter_by_magic_and_status(app_client) -> None:
    user_id, project_id = await _seed_and_login(
        app_client, email="ord-magic@example.com", project_name="P-ord-magic"
    )
    now = datetime.now(tz=UTC)
    await _insert_order(
        user_id=user_id, project_id=project_id, magic=42, status="closed",
        open_time=now - timedelta(hours=3), close_time=now - timedelta(hours=2),
        profit_net="10",
    )
    await _insert_order(
        user_id=user_id, project_id=project_id, magic=99, status="closed",
        open_time=now - timedelta(hours=2), close_time=now - timedelta(hours=1),
        profit_net="20",
    )
    await _insert_order(
        user_id=user_id, project_id=project_id, magic=42, status="filled",
        open_time=now - timedelta(hours=1),
        close_time=None, profit_net=None, close_price=None,
    )

    # magic=42 → two rows (closed + filled).
    resp = await app_client.get(
        f"/api/projects/{project_id}/operativa/orders", params={"magic": 42}
    )
    assert resp.status_code == 200
    assert resp.json()["total"] == 2

    # magic=42 + status=closed → one row.
    resp2 = await app_client.get(
        f"/api/projects/{project_id}/operativa/orders",
        params={"magic": 42, "status": "closed"},
    )
    assert resp2.status_code == 200
    assert resp2.json()["total"] == 1


async def test_orders_date_range_filter(app_client) -> None:
    user_id, project_id = await _seed_and_login(
        app_client, email="ord-date@example.com", project_name="P-ord-date"
    )
    now = datetime.now(tz=UTC)
    # one trade 2 days ago, one 10 days ago.
    await _insert_order(
        user_id=user_id, project_id=project_id,
        open_time=now - timedelta(days=2), close_time=now - timedelta(days=2),
        profit_net="10",
    )
    await _insert_order(
        user_id=user_id, project_id=project_id,
        open_time=now - timedelta(days=10), close_time=now - timedelta(days=10),
        profit_net="20",
    )

    # narrow to last 5 days only.
    from_iso = (now - timedelta(days=5)).isoformat()
    resp = await app_client.get(
        f"/api/projects/{project_id}/operativa/orders",
        params={"from": from_iso},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1


async def test_orders_pagination(app_client) -> None:
    user_id, project_id = await _seed_and_login(
        app_client, email="ord-page@example.com", project_name="P-ord-page"
    )
    now = datetime.now(tz=UTC)
    for i in range(5):
        await _insert_order(
            user_id=user_id, project_id=project_id,
            open_time=now - timedelta(hours=10 - i),
            close_time=now - timedelta(hours=9 - i),
            profit_net=f"{i+1}.00",
            symbol="EURUSD",
        )

    resp = await app_client.get(
        f"/api/projects/{project_id}/operativa/orders",
        params={"limit": 2, "offset": 0},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 5
    assert len(body["items"]) == 2

    resp2 = await app_client.get(
        f"/api/projects/{project_id}/operativa/orders",
        params={"limit": 2, "offset": 4},
    )
    assert resp2.status_code == 200
    assert len(resp2.json()["items"]) == 1


async def test_orders_metrics_profit_factor_infinity_when_no_losses(
    app_client,
) -> None:
    user_id, project_id = await _seed_and_login(
        app_client, email="ord-pf@example.com", project_name="P-ord-pf"
    )
    now = datetime.now(tz=UTC)
    for amount in ("10", "20", "5"):
        await _insert_order(
            user_id=user_id, project_id=project_id,
            open_time=now - timedelta(hours=4), close_time=now - timedelta(hours=3),
            profit_net=amount,
        )

    resp = await app_client.get(f"/api/projects/{project_id}/operativa/orders")
    assert resp.status_code == 200
    metrics = resp.json()["metrics"]
    # JSON literal string — NOT Python float inf (which fails JSON).
    assert metrics["profit_factor"] == "Infinity"
    assert metrics["win_rate"] == 1.0
    assert metrics["trades_total"] == 3


async def test_orders_metrics_avg_rr_none_when_no_valid_r(app_client) -> None:
    user_id, project_id = await _seed_and_login(
        app_client, email="ord-rr@example.com", project_name="P-ord-rr"
    )
    now = datetime.now(tz=UTC)
    # open_price == sl → zero denominator → excluded from avg_rr.
    await _insert_order(
        user_id=user_id, project_id=project_id,
        open_time=now - timedelta(hours=3), close_time=now - timedelta(hours=2),
        open_price="1.0900", sl="1.0900", close_price="1.0950", profit_net="10",
    )
    resp = await app_client.get(f"/api/projects/{project_id}/operativa/orders")
    assert resp.status_code == 200
    metrics = resp.json()["metrics"]
    assert metrics["avg_rr"] is None
    assert metrics["trades_total"] == 1


async def test_orders_cross_tenant_returns_404(app_client) -> None:
    user_a_id, project_a_id = await _seed_user_and_project(
        email="a-ord@example.com", project_name="A-ord"
    )
    # Seed a trade under user A's project so we can verify B can't see it.
    now = datetime.now(tz=UTC)
    await _insert_order(
        user_id=user_a_id, project_id=project_a_id,
        open_time=now - timedelta(hours=2), close_time=now - timedelta(hours=1),
        profit_net="999",
    )
    await _seed_and_login(
        app_client, email="b-ord@example.com", project_name="B-ord"
    )

    resp = await app_client.get(
        f"/api/projects/{project_a_id}/operativa/orders"
    )
    # MUST be 404 — existence is NOT disclosed.
    assert resp.status_code == 404


async def test_orders_invalid_date_range_returns_400(app_client) -> None:
    _, project_id = await _seed_and_login(
        app_client, email="ord-bad@example.com", project_name="P-ord-bad"
    )
    now = datetime.now(tz=UTC)
    resp = await app_client.get(
        f"/api/projects/{project_id}/operativa/orders",
        params={
            "from": now.isoformat(),
            "to": (now - timedelta(days=1)).isoformat(),
        },
    )
    assert resp.status_code == 400


# ===========================================================================
# Cross-tenant audit hook — Phase 8 (multi-tenancy delta #2122).
# ===========================================================================


async def test_account_summary_cross_tenant_emits_audit(
    app_client, monkeypatch
) -> None:
    """A cross-tenant /operativa/account-summary GET MUST emit one
    ``log_cross_tenant_attempt`` line — same contract as the WS route."""
    from aether_api.learning import audit as audit_module
    from aether_api.learning.audit import reset_for_test
    from aether_api.routers import projects_live as router_module

    reset_for_test()

    # User A owns a project; user B is logged in and tries to read it.
    _, project_a_id = await _seed_user_and_project(
        email="ct-acct-a@example.com", project_name="A-ct-acct"
    )
    await _seed_and_login(
        app_client, email="ct-acct-b@example.com", project_name="B-ct-acct"
    )

    audit_calls: list[dict[str, Any]] = []
    original = audit_module.log_cross_tenant_attempt

    async def _spy(**kwargs: Any) -> bool:
        audit_calls.append(kwargs)
        return await original(**kwargs)

    monkeypatch.setattr(
        router_module, "log_cross_tenant_attempt", _spy, raising=True
    )

    resp = await app_client.get(
        f"/api/projects/{project_a_id}/operativa/account-summary"
    )
    # Existence MUST NOT leak — still 404, identical to "no such project".
    assert resp.status_code == 404
    # The audit hook fired exactly once.
    assert len(audit_calls) == 1, audit_calls
    call = audit_calls[0]
    assert str(call["target_project_id"]) == str(project_a_id)
    assert call["table_name"] == "projects"
    assert call["operation"] == "operativa_account_summary"


async def test_orders_cross_tenant_emits_audit(app_client, monkeypatch) -> None:
    """A cross-tenant /operativa/orders GET MUST emit one
    ``log_cross_tenant_attempt`` line."""
    from aether_api.learning import audit as audit_module
    from aether_api.learning.audit import reset_for_test
    from aether_api.routers import projects_live as router_module

    reset_for_test()

    _, project_a_id = await _seed_user_and_project(
        email="ct-ord-a@example.com", project_name="A-ct-ord"
    )
    await _seed_and_login(
        app_client, email="ct-ord-b@example.com", project_name="B-ct-ord"
    )

    audit_calls: list[dict[str, Any]] = []
    original = audit_module.log_cross_tenant_attempt

    async def _spy(**kwargs: Any) -> bool:
        audit_calls.append(kwargs)
        return await original(**kwargs)

    monkeypatch.setattr(
        router_module, "log_cross_tenant_attempt", _spy, raising=True
    )

    resp = await app_client.get(
        f"/api/projects/{project_a_id}/operativa/orders"
    )
    assert resp.status_code == 404
    assert len(audit_calls) == 1, audit_calls
    call = audit_calls[0]
    assert str(call["target_project_id"]) == str(project_a_id)
    assert call["table_name"] == "projects"
    assert call["operation"] == "operativa_orders_list"
