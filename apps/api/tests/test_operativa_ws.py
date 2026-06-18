"""Integration tests for the Operativa WebSocket surface.

Phase 4 of the ``project-operativa`` change. Covers:

* WS auth — owner cookie accepted (handshake + heartbeat ping).
* WS auth — cross-tenant cookie closed with 1008 + audit log emitted.
* WS auth — no cookie closed with 1008.
* LiveBus subscriber lifecycle (first-subscribe → task started;
  last-unsubscribe → task cancelled).
* MCP outage → ``mcp_status`` event with ``available=false``.
* **Critical**: reconciler does NOT overwrite Worker-authored fields
  on existing rows.

Tests stub the MCP client factory so no TCP traffic flows. The DB is
the real Postgres provided by ``conftest.py`` so the tenancy SELECT +
``upsert_by_ticket`` path are exercised end-to-end.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from aether_api.mcp_client.errors import MCPUnreachable
from aether_api.services.live_bus import (
    WORKER_AUTHORED_FIELDS,
    LiveBus,
    reconcile_history,
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeMCP:
    """In-process stand-in for :class:`MCPClient`.

    Controlled by attributes the tests flip between calls:

    * ``account_payload`` / ``positions_payload`` / ``history_payload``
      drive the three read methods.
    * ``raise_unreachable`` flips the next account/positions call to
      raise :class:`MCPUnreachable`.
    """

    def __init__(self) -> None:
        self.account_payload: dict[str, Any] = {"equity": "1000", "balance": "1000"}
        self.positions_payload: dict[str, Any] = {"positions": []}
        self.history_payload: dict[str, Any] = {"deals": []}
        self.raise_unreachable: bool = False
        self.calls_account: int = 0
        self.calls_positions: int = 0
        self.calls_history: int = 0

    async def get_account(self) -> dict[str, Any]:
        self.calls_account += 1
        if self.raise_unreachable:
            raise MCPUnreachable("simulated outage", details={"tool": "mt5_get_account"})
        return self.account_payload

    async def get_positions(self, *, symbol: str | None = None) -> dict[str, Any]:
        self.calls_positions += 1
        if self.raise_unreachable:
            raise MCPUnreachable("simulated outage", details={"tool": "mt5_get_positions"})
        return self.positions_payload

    async def get_history(
        self,
        *,
        date_from: datetime,
        date_to: datetime,
        symbol: str | None = None,
    ) -> dict[str, Any]:
        self.calls_history += 1
        return self.history_payload


async def _seed_user_and_project(
    *,
    email: str,
    password: str = "x" * 24,
    project_name: str,
) -> tuple[uuid.UUID, uuid.UUID]:
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


# ---------------------------------------------------------------------------
# LiveBus unit-ish tests (no WS layer, just the bus).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_live_bus_subscriber_lifecycle_starts_and_cancels_task(migrated_db: str) -> None:
    """Subscribe → task started; last unsubscribe → task cancelled."""
    from aether_api.db.session import get_session_maker

    owner_id, project_id = await _seed_user_and_project(
        email="bus-lifecycle@example.com",
        project_name="P-bus-lifecycle",
    )

    fake = _FakeMCP()
    bus = LiveBus(
        get_session_maker(),
        mcp_client_factory=lambda _project: fake,
        positions_poll_seconds=0.05,
        reconcile_poll_seconds=10.0,
        heartbeat_seconds=10.0,
    )

    baseline_tasks = {t for t in asyncio.all_tasks() if not t.done()}
    assert bus.subscriber_count(project_id) == 0
    assert not bus.has_task(project_id)

    sub = await bus.subscribe(pair_id=project_id, user_id=owner_id, conn_handle=object())
    assert bus.subscriber_count(project_id) == 1
    assert bus.has_task(project_id)

    # New asyncio task should exist for this project.
    extra_tasks = {t for t in asyncio.all_tasks() if not t.done()} - baseline_tasks
    assert any(t.get_name().startswith("live-bus-pair-") for t in extra_tasks)

    await bus.unsubscribe(sub)
    assert bus.subscriber_count(project_id) == 0
    assert not bus.has_task(project_id)

    # Loop must wind down — give the event loop one tick.
    await asyncio.sleep(0)
    final_tasks = {t for t in asyncio.all_tasks() if not t.done()}
    project_loop_tasks = {
        t for t in final_tasks if t.get_name().startswith("live-bus-pair-")
    }
    assert project_loop_tasks == set()


@pytest.mark.asyncio
async def test_live_bus_queue_overflow_drops_oldest(migrated_db: str) -> None:
    """Subscriber queue is bounded — old events are dropped on overflow."""
    from aether_api.db.session import get_session_maker
    from aether_api.services.live_bus import SUBSCRIBER_QUEUE_MAXSIZE, LiveEvent

    owner_id, project_id = await _seed_user_and_project(
        email="bus-overflow@example.com",
        project_name="P-bus-overflow",
    )
    fake = _FakeMCP()
    bus = LiveBus(
        get_session_maker(),
        mcp_client_factory=lambda _project: fake,
        positions_poll_seconds=10.0,
        reconcile_poll_seconds=10.0,
        heartbeat_seconds=10.0,
    )
    sub = await bus.subscribe(
        pair_id=project_id, user_id=owner_id, conn_handle=object()
    )
    try:
        # Pump (maxsize + 5) events through the broadcaster.
        for i in range(SUBSCRIBER_QUEUE_MAXSIZE + 5):
            bus._broadcast(  # noqa: SLF001 — internal in tests is fine
                project_id,
                LiveEvent(type="probe", payload={"seq": i}),
            )

        # Queue must be at capacity, never beyond.
        assert sub.queue.qsize() == SUBSCRIBER_QUEUE_MAXSIZE
        # First event still in the queue should be one of the LATER
        # events — i.e. the OLDEST ones were dropped.
        first = sub.queue.get_nowait()
        assert first.payload["seq"] >= 5
    finally:
        await bus.unsubscribe(sub)


@pytest.mark.asyncio
async def test_live_bus_mcp_outage_emits_status_event(migrated_db: str) -> None:
    """MCP raising MCPUnreachable → ``mcp_status`` event with available=false."""
    from aether_api.db.session import get_session_maker

    owner_id, project_id = await _seed_user_and_project(
        email="bus-mcp-down@example.com",
        project_name="P-bus-mcp-down",
    )
    fake = _FakeMCP()
    fake.raise_unreachable = True
    bus = LiveBus(
        get_session_maker(),
        mcp_client_factory=lambda _project: fake,
        positions_poll_seconds=0.05,
        reconcile_poll_seconds=10.0,
        heartbeat_seconds=10.0,
    )
    sub = await bus.subscribe(
        pair_id=project_id, user_id=owner_id, conn_handle=object()
    )
    try:
        # Wait up to 2s for an mcp_status=false event.
        deadline = asyncio.get_event_loop().time() + 2.0
        seen_status = False
        while asyncio.get_event_loop().time() < deadline:
            try:
                event = await asyncio.wait_for(sub.queue.get(), timeout=0.5)
            except TimeoutError:
                continue
            if event.type == "mcp_status" and event.payload.get("available") is False:
                seen_status = True
                assert event.payload.get("error_code") in {
                    "TIMEOUT",
                    "UNREACHABLE",
                    "UNAUTHENTICATED",
                }
                break
        assert seen_status, "Expected mcp_status=false event when MCP raises Unreachable"
    finally:
        await bus.unsubscribe(sub)


# ---------------------------------------------------------------------------
# Reconciler — CRITICAL: no-overwrite invariant on Worker fields.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconciler_does_not_overwrite_worker_authored_fields(
    migrated_db: str,
) -> None:
    """Reconciler MUST NEVER overwrite Worker-authored P&L / pricing fields.

    Spec: ``sdd/project-operativa/design`` (#2125) — the broker view
    lives in ``meta_data.broker_*``; the canonical Worker-authored
    columns (``profit_*``, ``commission``, ``swap``, ``open_price``,
    ``close_price``) stay untouched.
    """
    from aether_api.db.session import get_session_maker
    from aether_api.models.order import Order

    owner_id, project_id = await _seed_user_and_project(
        email="reconciler-no-overwrite@example.com",
        project_name="P-reconciler-no-overwrite",
    )
    maker = get_session_maker()

    # Seed an existing Worker-authored row.
    ticket_int = 7777777
    async with maker() as session:
        order = Order(
            pair_id=project_id,
            user_id=owner_id,
            symbol="EURUSD",
            side="buy",
            volume=Decimal("0.10"),
            sl=Decimal("1.0900"),
            tp=Decimal("1.1100"),
            mt5_ticket=ticket_int,
            status="closed",
            open_time=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
            open_price=Decimal("1.10000000"),
            close_time=datetime(2026, 1, 1, 14, 0, tzinfo=UTC),
            close_price=Decimal("1.10500000"),
            commission=Decimal("-0.5000"),
            swap=Decimal("0.0000"),
            profit_gross=Decimal("50.0000"),
            profit_net=Decimal("49.5000"),  # Worker's authoritative number
            meta_data={"worker_authored": True},
        )
        session.add(order)
        await session.commit()
        worker_order_id = order.id

    # Reconciler runs with a CONFLICTING broker view (different
    # profit/commission/prices) — none of these may overwrite the
    # Worker-authored columns.
    broker_deals: list[dict[str, Any]] = [
        {
            "ticket": ticket_int,
            "symbol": "EURUSD",
            "side": "buy",
            "volume": "0.10",
            "open_price": "9.99999999",  # WRONG — must NOT land in open_price
            "close_price": "8.88888888",  # WRONG — must NOT land in close_price
            "commission": "-99.9999",  # WRONG — must NOT land in commission
            "swap": "12.3456",  # WRONG — must NOT land in swap
            "profit_gross": "999.0000",  # WRONG — must NOT land in profit_gross
            "profit": "999.0000",
        }
    ]

    async with maker() as session:
        touched = await reconcile_history(
            session=session,
            user_id=owner_id,
            pair_id=project_id,
            deals=broker_deals,
        )
        await session.commit()

    assert touched == 1

    # Re-read the row and assert the Worker-authored fields are unchanged
    # while the broker view landed in ``meta_data.broker_*``.
    async with maker() as session:
        refreshed = await session.get(Order, worker_order_id)
        assert refreshed is not None
        # Critical no-overwrite assertions.
        assert refreshed.open_price == Decimal("1.10000000"), (
            "Reconciler overwrote Worker open_price!"
        )
        assert refreshed.close_price == Decimal("1.10500000"), (
            "Reconciler overwrote Worker close_price!"
        )
        assert refreshed.commission == Decimal("-0.5000"), (
            "Reconciler overwrote Worker commission!"
        )
        assert refreshed.swap == Decimal("0.0000"), (
            "Reconciler overwrote Worker swap!"
        )
        assert refreshed.profit_gross == Decimal("50.0000"), (
            "Reconciler overwrote Worker profit_gross!"
        )
        assert refreshed.profit_net == Decimal("49.5000"), (
            "Reconciler overwrote Worker profit_net!"
        )
        # Broker view present in meta_data.
        meta = refreshed.meta_data or {}
        assert meta.get("broker_profit_gross") == "999.0000"
        assert meta.get("broker_commission") == "-99.9999"
        assert meta.get("broker_swap") == "12.3456"
        assert "reconciled_at" in meta
        # Original meta_data is preserved.
        assert meta.get("worker_authored") is True

    # Defence-in-depth — assert the protected set hasn't shrunk in a
    # future refactor without an associated test update.
    assert {
        "profit_gross",
        "profit_net",
        "commission",
        "swap",
        "open_price",
        "close_price",
    } == WORKER_AUTHORED_FIELDS


@pytest.mark.asyncio
async def test_reconciler_creates_row_for_unknown_ticket(migrated_db: str) -> None:
    """Reconciler-discovered tickets are inserted with reconciler_authored=true."""
    from aether_api.db.session import get_session_maker
    from aether_api.models.order import Order
    from sqlalchemy import select

    owner_id, project_id = await _seed_user_and_project(
        email="reconciler-create@example.com",
        project_name="P-reconciler-create",
    )
    maker = get_session_maker()
    ticket_int = 8888888

    deals: list[dict[str, Any]] = [
        {
            "ticket": ticket_int,
            "symbol": "GBPUSD",
            "side": "sell",
            "volume": "0.05",
            "sl": "1.2500",
            "open_price": "1.2400",
            "close_price": "1.2380",
            "open_time": "2026-01-15T08:30:00+00:00",
            "close_time": "2026-01-15T09:30:00+00:00",
            "commission": "-0.25",
            "swap": "0.00",
            "profit": "10.00",
            "status": "closed",
        }
    ]
    async with maker() as session:
        touched = await reconcile_history(
            session=session,
            user_id=owner_id,
            pair_id=project_id,
            deals=deals,
        )
        await session.commit()
    assert touched == 1

    async with maker() as session:
        stmt = select(Order).where(
            Order.pair_id == project_id, Order.mt5_ticket == ticket_int
        )
        order = (await session.execute(stmt)).scalar_one_or_none()
        assert order is not None
        meta = order.meta_data or {}
        assert meta.get("reconciler_authored") is True
        assert "broker_profit_gross" in meta


# ---------------------------------------------------------------------------
# WebSocket handshake gates — TestClient WS support.
# ---------------------------------------------------------------------------


def _build_test_app() -> Any:
    """Build a fresh FastAPI app for synchronous TestClient WS tests.

    The fixture-based ``app_client`` is async-only; for the
    handshake-shape assertions we use the sync TestClient directly.
    The DB is the same migrated Postgres because settings + sessions
    are global.
    """
    from aether_api.core.settings import get_settings
    from aether_api.main import create_app

    get_settings.cache_clear()
    return create_app()


async def _dispose_engine() -> None:
    """Dispose the SQLAlchemy async engine so the next test boots fresh.

    The sync TestClient drives the FastAPI lifespan on an anyio-managed
    event loop that goes away when the ``with TestClient(app) as ...:``
    block exits. Connections in the pool stay bound to that dead loop,
    which crashes the NEXT pytest-asyncio test's session.execute(). We
    pre-empt by tearing the engine down.
    """
    import aether_api.db.session as db_session_module

    engine = db_session_module._engine  # noqa: SLF001 — test-only teardown
    if engine is not None:
        await engine.dispose()
        db_session_module._engine = None  # noqa: SLF001
        db_session_module._session_maker = None  # noqa: SLF001


@pytest.mark.asyncio
async def test_ws_no_cookie_closes_with_1008(migrated_db: str) -> None:
    """A WS upgrade without the access cookie is closed with code 1008."""
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    _, project_id = await _seed_user_and_project(
        email="ws-no-cookie@example.com",
        project_name="P-ws-no-cookie",
    )

    app = _build_test_app()
    try:
        with (
            TestClient(app, client=("127.0.0.1", 50000)) as client,
            pytest.raises(WebSocketDisconnect) as exc_info,
            client.websocket_connect(f"/api/pairs/{project_id}/operativa/ws"),
        ):
            pass

        assert exc_info.value.code == 1008
    finally:
        await _dispose_engine()


@pytest.mark.asyncio
async def test_ws_cross_tenant_closes_with_1008_and_audits(
    monkeypatch: pytest.MonkeyPatch, migrated_db: str
) -> None:
    """Cross-tenant WS upgrade is closed with 1008 + emits audit log."""
    from aether_api.learning.audit import reset_for_test
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    reset_for_test()

    owner_email = "ws-owner@example.com"
    owner_password = "x" * 24
    _, project_id = await _seed_user_and_project(
        email=owner_email, password=owner_password, project_name="P-ws-owner"
    )
    # Second tenant whose cookie will be presented against the owner's project.
    attacker_email = "ws-attacker@example.com"
    attacker_password = "y" * 24
    await _seed_user_and_project(
        email=attacker_email,
        password=attacker_password,
        project_name="P-ws-attacker",
    )

    # Spy on log_cross_tenant_attempt so we can assert it fired regardless
    # of caplog vs structlog routing.
    audit_calls: list[dict[str, Any]] = []

    import aether_api.routers.operativa_ws as ws_router_module
    from aether_api.learning import audit as audit_module

    original = audit_module.log_cross_tenant_attempt

    async def _spy(**kwargs: Any) -> bool:
        audit_calls.append(kwargs)
        return await original(**kwargs)

    monkeypatch.setattr(
        ws_router_module, "log_cross_tenant_attempt", _spy, raising=True
    )

    app = _build_test_app()
    try:
        with TestClient(app, client=("127.0.0.1", 50000)) as client:
            login = client.post(
                "/api/auth/login",
                json={"email": attacker_email, "password": attacker_password},
            )
            assert login.status_code == 200, login.text

            with (
                pytest.raises(WebSocketDisconnect) as exc_info,
                client.websocket_connect(f"/api/pairs/{project_id}/operativa/ws"),
            ):
                pass

            assert exc_info.value.code == 1008

        # Audit hook fires exactly once for the cross-tenant attempt.
        assert len(audit_calls) == 1, audit_calls
        call = audit_calls[0]
        assert str(call["target_project_id"]) == str(project_id)
        assert call["table_name"] == "pairs"
        assert call["operation"] == "operativa_ws_subscribe"
    finally:
        await _dispose_engine()


@pytest.mark.asyncio
async def test_ws_owner_handshake_accepts_and_sees_event(monkeypatch, migrated_db: str) -> None:
    """Owner cookie → accept; client receives at least one bus event."""
    from fastapi.testclient import TestClient

    email = "ws-owner-happy@example.com"
    password = "x" * 24
    _, project_id = await _seed_user_and_project(
        email=email, password=password, project_name="P-ws-owner-happy"
    )

    # Stub the MCP factory so the polling loop has deterministic data.
    fake = _FakeMCP()
    fake.account_payload = {"equity": "1234.56", "balance": "1000.00"}

    import aether_api.services.live_bus as live_bus_module

    monkeypatch.setattr(
        live_bus_module, "get_mcp_client", lambda project: fake, raising=True
    )

    app = _build_test_app()
    # Re-wire the bus to use short poll/heartbeat windows so the
    # first event lands quickly. We replace the app.state binding
    # directly — the WS handler picks it up via app.state.live_bus.
    from aether_api.db.session import get_session_maker

    short_bus = LiveBus(
        get_session_maker(),
        mcp_client_factory=lambda _project: fake,
        positions_poll_seconds=0.05,
        reconcile_poll_seconds=10.0,
        heartbeat_seconds=0.1,
    )
    app.state.live_bus = short_bus

    try:
        with TestClient(app, client=("127.0.0.1", 50000)) as client:
            login = client.post(
                "/api/auth/login", json={"email": email, "password": password}
            )
            assert login.status_code == 200, login.text

            with client.websocket_connect(
                f"/api/pairs/{project_id}/operativa/ws"
            ) as ws:
                # Receive at least one event (heartbeat or snapshot)
                # within a reasonable budget. WebSocketTestSession
                # blocks synchronously on receive_json — the bus is
                # configured with sub-second heartbeat so this resolves
                # promptly.
                frame = ws.receive_json()
                assert "type" in frame
                assert "payload" in frame
                assert "ts" in frame
    finally:
        await short_bus.shutdown()
        await _dispose_engine()
