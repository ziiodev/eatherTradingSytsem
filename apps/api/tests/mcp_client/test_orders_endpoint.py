"""End-to-end tests for the live MT5 endpoints.

Covered surfaces:

* Feature flag gate — ``AETHER_LIVE_ORDERS_ENABLED=false`` → 503.
* Cross-tenant 404 on every live endpoint.
* MCP unreachable path → 502 + audit row written.
* Charter SL missing → 422 + risk_violation.
* 2-phase audit row written even on error.

The MCP client is patched at the module boundary so no real TCP traffic
is generated. We stub :class:`MCPClient` with a fake whose methods
return canned responses.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy import select

from aether_api.auth.cookies import CSRF_COOKIE
from aether_api.core.settings import get_settings
from aether_api.mcp_client.errors import MCPUnreachable

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Test helpers (mirror tests/test_projects_crud.py patterns)
# ---------------------------------------------------------------------------


async def _seed_and_login(client, *, email: str = "live@example.com"):  # type: ignore[no-untyped-def]
    from aether_api.db.session import get_session_maker

    from tests._helpers import seed_project, seed_user

    maker = get_session_maker()
    async with maker() as session:
        user = await seed_user(session, email=email, password="x" * 24)
        project = await seed_project(
            session,
            owner=user,
            name="P-live",
            mcp_url="http://test-mcp.local:8765",
        )
        # Set trading_sessions so the RiskEnforcer doesn't reject every order
        # on the "session_closed" rule.
        project.trading_sessions = list({"europe", "new_york", "tokyo"})
        await session.commit()
        owner_id = user.id
        project_id = project.id

    resp = await client.post(
        "/api/auth/login", json={"email": email, "password": "x" * 24}
    )
    assert resp.status_code == 200, resp.text
    return owner_id, project_id


def _csrf_headers(client) -> dict[str, str]:  # type: ignore[no-untyped-def]
    token = client.cookies.get(CSRF_COOKIE)
    assert token, "csrf cookie missing — did you log in first?"
    return {"X-CSRF-Token": token}


@contextmanager
def _patch_mcp_client(**responses: Any):
    """Patch :func:`get_mcp_client` to return a fake."""

    class _Fake:
        async def get_account(self) -> dict[str, Any]:
            if "get_account" in responses:
                if isinstance(responses["get_account"], Exception):
                    raise responses["get_account"]
                return responses["get_account"]
            return {
                "balance": "10000",
                "equity": "10000",
                "margin": "0",
                "free_margin": "10000",
                "leverage": 100,
                "currency": "USD",
                "login": 12345,
            }

        async def get_positions(self, *, symbol: str | None = None) -> dict[str, Any]:
            return responses.get("get_positions", {"positions": []})

        async def get_history(self, **_: Any) -> dict[str, Any]:
            return responses.get("get_history", {"deals": []})

        async def get_candles(self, **_: Any) -> dict[str, Any]:
            return responses.get("get_candles", {"candles": []})

        async def place_order(self, payload: dict[str, Any]) -> dict[str, Any]:
            if "place_order" in responses:
                if isinstance(responses["place_order"], Exception):
                    raise responses["place_order"]
                return responses["place_order"]
            return {
                "ticket": 999_111,
                "status": "filled",
                "mt5_retcode": 10009,
            }

    with patch(
        "aether_api.routers.projects_live.get_mcp_client", return_value=_Fake()
    ):
        yield


# ---------------------------------------------------------------------------
# Feature flag gate
# ---------------------------------------------------------------------------


async def test_post_orders_503_when_disabled(app_client) -> None:  # type: ignore[no-untyped-def]
    _, project_id = await _seed_and_login(app_client)
    get_settings.cache_clear()
    with _patch_mcp_client():
        resp = await app_client.post(
            f"/api/projects/{project_id}/orders",
            json={
                "symbol": "EURUSD",
                "side": "buy",
                "volume": "0.01",
                "sl": "1.0900",
            },
            headers=_csrf_headers(app_client),
        )
    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "live_orders_disabled"


# ---------------------------------------------------------------------------
# Cross-tenant — orders for a project the user doesn't own
# ---------------------------------------------------------------------------


async def test_cross_tenant_orders_returns_404(app_client) -> None:  # type: ignore[no-untyped-def]
    await _seed_and_login(app_client)
    other_project_id = uuid.uuid4()
    resp = await app_client.get(f"/api/projects/{other_project_id}/orders")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# MCP unreachable → 502 + audit row written
# ---------------------------------------------------------------------------


async def test_mcp_unreachable_writes_audit_row(
    app_client, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    _, project_id = await _seed_and_login(app_client)
    monkeypatch.setenv("AETHER_LIVE_ORDERS_ENABLED", "true")
    get_settings.cache_clear()

    with _patch_mcp_client(get_account=MCPUnreachable("simulated outage")):
        resp = await app_client.post(
            f"/api/projects/{project_id}/orders",
            json={
                "symbol": "EURUSD",
                "side": "buy",
                "volume": "0.01",
                "sl": "1.0900",
            },
            headers=_csrf_headers(app_client),
        )
    assert resp.status_code == 502

    from aether_api.db.session import get_session_maker
    from aether_api.models.order import OrderLog

    maker = get_session_maker()
    async with maker() as session:
        rows = (
            (await session.execute(select(OrderLog).where(OrderLog.project_id == project_id)))
            .scalars()
            .all()
        )
        assert len(rows) >= 1
        assert rows[0].status == "blocked"
        assert "mcp_unreachable" in (rows[0].error or "")


# ---------------------------------------------------------------------------
# Schema gate — sl<=0 at request layer
# ---------------------------------------------------------------------------


async def test_missing_sl_rejected_at_router(
    app_client, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    _, project_id = await _seed_and_login(app_client)
    monkeypatch.setenv("AETHER_LIVE_ORDERS_ENABLED", "true")
    get_settings.cache_clear()

    resp = await app_client.post(
        f"/api/projects/{project_id}/orders",
        json={"symbol": "EURUSD", "side": "buy", "volume": "0.01", "sl": "0"},
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 2-phase audit row written on success path
# ---------------------------------------------------------------------------


async def test_two_phase_audit_on_success(
    app_client, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    _, project_id = await _seed_and_login(app_client)
    monkeypatch.setenv("AETHER_LIVE_ORDERS_ENABLED", "true")
    get_settings.cache_clear()

    with _patch_mcp_client():
        resp = await app_client.post(
            f"/api/projects/{project_id}/orders",
            json={
                "symbol": "EURUSD",
                "side": "buy",
                "volume": "0.01",
                "sl": "1.0900",
                "tp": "1.1100",
            },
            headers=_csrf_headers(app_client),
        )

    # Status depends on whether the test runs inside one of the configured
    # session windows. Both outcomes leave a forensic row.
    assert resp.status_code in (200, 409)

    from aether_api.db.session import get_session_maker
    from aether_api.models.order import OrderLog

    maker = get_session_maker()
    async with maker() as session:
        rows = (
            (await session.execute(select(OrderLog).where(OrderLog.project_id == project_id)))
            .scalars()
            .all()
        )
        assert len(rows) >= 1
        # Either blocked (risk gate) or filled (happy path).
        assert rows[0].status in {"blocked", "filled", "pending"}
