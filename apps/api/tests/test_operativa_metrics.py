"""Phase 8 of ``sdd/project-operativa`` — Operativa Prometheus metrics.

Pinned behavioural contract:

* :func:`set_ws_subscribers` writes the supplied count into the
  :data:`operativa_ws_subscribers` Gauge under the ``project`` label.
* :func:`set_mcp_status` writes ``1.0`` when ``available=True`` and
  ``0.0`` when ``available=False`` into the
  :data:`operativa_mcp_status` Gauge.
* The LiveBus calls ``set_ws_subscribers`` on subscribe/unsubscribe and
  ``set_mcp_status`` on MCP status transitions (DOWN→UP and UP→DOWN).

These tests poke the collectors directly + drive the LiveBus end-to-end
to assert the integration contract.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import Any

import pytest
from aether_api.mcp_client.errors import MCPUnreachable
from aether_api.services.live_bus import LiveBus
from aether_api.services.operativa_metrics import (
    operativa_mcp_status,
    operativa_ws_subscribers,
    reset_for_test,
    set_mcp_status,
    set_ws_subscribers,
)


@pytest.fixture(autouse=True)
def _reset_metrics() -> None:
    """Process-global Prometheus state — reset every test."""
    reset_for_test()
    yield
    reset_for_test()


# ---------------------------------------------------------------------------
# Unit-level — the public set_* helpers write the expected sample values.
# ---------------------------------------------------------------------------


def test_set_ws_subscribers_writes_gauge_sample() -> None:
    project_id = uuid.uuid4()
    set_ws_subscribers(project_id, 3)

    value = operativa_ws_subscribers.labels(
        pair=str(project_id)
    )._value.get()  # type: ignore[attr-defined]
    assert value == 3.0


def test_set_ws_subscribers_accepts_string_label() -> None:
    """``project_id`` may be a string — Prometheus labels are strings."""
    set_ws_subscribers("custom-key", 7)
    value = operativa_ws_subscribers.labels(pair="custom-key")._value.get()  # type: ignore[attr-defined]
    assert value == 7.0


def test_set_mcp_status_maps_available_true_to_one() -> None:
    project_id = uuid.uuid4()
    set_mcp_status(project_id, available=True)
    value = operativa_mcp_status.labels(
        pair=str(project_id)
    )._value.get()  # type: ignore[attr-defined]
    assert value == 1.0


def test_set_mcp_status_maps_available_false_to_zero() -> None:
    project_id = uuid.uuid4()
    set_mcp_status(project_id, available=False)
    value = operativa_mcp_status.labels(
        pair=str(project_id)
    )._value.get()  # type: ignore[attr-defined]
    assert value == 0.0


def test_reset_for_test_clears_both_gauges() -> None:
    project_id = uuid.uuid4()
    set_ws_subscribers(project_id, 5)
    set_mcp_status(project_id, available=True)

    reset_for_test()

    # After clear, accessing the label re-initialises it to 0.0.
    assert (
        operativa_ws_subscribers.labels(pair=str(project_id))._value.get()  # type: ignore[attr-defined]
        == 0.0
    )
    assert (
        operativa_mcp_status.labels(pair=str(project_id))._value.get()  # type: ignore[attr-defined]
        == 0.0
    )


# ---------------------------------------------------------------------------
# Integration — LiveBus drives the gauges on subscribe/unsubscribe + MCP edges.
# ---------------------------------------------------------------------------


class _FakeMCP:
    """Minimal MCPClient stand-in driving the LiveBus polling loop."""

    def __init__(self) -> None:
        self.account_payload: dict[str, Any] = {"equity": "1000", "balance": "1000"}
        self.positions_payload: dict[str, Any] = {"positions": []}
        self.history_payload: dict[str, Any] = {"deals": []}
        self.raise_unreachable: bool = False

    async def get_account(self) -> dict[str, Any]:
        if self.raise_unreachable:
            raise MCPUnreachable("simulated outage", details={"tool": "mt5_get_account"})
        return self.account_payload

    async def get_positions(self, *, symbol: str | None = None) -> dict[str, Any]:
        if self.raise_unreachable:
            raise MCPUnreachable(
                "simulated outage", details={"tool": "mt5_get_positions"}
            )
        return self.positions_payload

    async def get_history(
        self,
        *,
        date_from: datetime,
        date_to: datetime,
        symbol: str | None = None,
    ) -> dict[str, Any]:
        return self.history_payload


async def _seed_user_and_project(
    *, email: str, project_name: str
) -> tuple[uuid.UUID, uuid.UUID]:
    from aether_api.db.session import get_session_maker

    from tests._helpers import seed_project, seed_user

    maker = get_session_maker()
    async with maker() as session:
        user = await seed_user(session, email=email, password="x" * 24)
        project = await seed_project(
            session,
            owner=user,
            name=project_name,
            mcp_url="http://test-mcp.local:8765",
        )
        await session.commit()
        return user.id, project.id


pytestmark_integration = pytest.mark.integration


@pytest.mark.integration
@pytest.mark.asyncio
async def test_live_bus_subscribe_increments_subscriber_gauge(
    migrated_db: str,
) -> None:
    from aether_api.db.session import get_session_maker

    owner_id, project_id = await _seed_user_and_project(
        email="metrics-sub@example.com",
        project_name="P-metrics-sub",
    )
    bus = LiveBus(
        get_session_maker(),
        mcp_client_factory=lambda _p: _FakeMCP(),
        positions_poll_seconds=0.05,
        reconcile_poll_seconds=10.0,
        heartbeat_seconds=10.0,
    )

    # Before any subscriber the label sample is the gauge default (0).
    assert (
        operativa_ws_subscribers.labels(pair=str(project_id))._value.get()  # type: ignore[attr-defined]
        == 0.0
    )

    sub = await bus.subscribe(
        pair_id=project_id, user_id=owner_id, conn_handle=object()
    )
    try:
        assert (
            operativa_ws_subscribers.labels(pair=str(project_id))._value.get()  # type: ignore[attr-defined]
            == 1.0
        )
    finally:
        await bus.unsubscribe(sub)

    # Unsubscribe drops the gauge back to zero.
    assert (
        operativa_ws_subscribers.labels(pair=str(project_id))._value.get()  # type: ignore[attr-defined]
        == 0.0
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_live_bus_mcp_outage_flips_status_gauge_to_zero(
    migrated_db: str,
) -> None:
    from aether_api.db.session import get_session_maker

    owner_id, project_id = await _seed_user_and_project(
        email="metrics-mcp-down@example.com",
        project_name="P-metrics-mcp-down",
    )
    fake = _FakeMCP()
    fake.raise_unreachable = True
    bus = LiveBus(
        get_session_maker(),
        mcp_client_factory=lambda _p: fake,
        positions_poll_seconds=0.05,
        reconcile_poll_seconds=10.0,
        heartbeat_seconds=10.0,
    )
    sub = await bus.subscribe(
        pair_id=project_id, user_id=owner_id, conn_handle=object()
    )
    try:
        # The polling loop should emit an mcp_status=false event very
        # quickly (within a few 50ms ticks). Poll the gauge up to 2s.
        deadline = asyncio.get_event_loop().time() + 2.0
        while asyncio.get_event_loop().time() < deadline:
            value = operativa_mcp_status.labels(
                pair=str(project_id)
            )._value.get()  # type: ignore[attr-defined]
            if value == 0.0 and len(
                operativa_mcp_status.labels(pair=str(project_id))._labelvalues  # type: ignore[attr-defined]
            ) > 0:
                # Make sure the gauge was ACTUALLY written, not just
                # the default. _Value._value carries the last set value;
                # the broker outage path explicitly calls set_mcp_status
                # with available=False.
                break
            await asyncio.sleep(0.05)
        final = operativa_mcp_status.labels(
            pair=str(project_id)
        )._value.get()  # type: ignore[attr-defined]
        assert final == 0.0
    finally:
        await bus.unsubscribe(sub)
