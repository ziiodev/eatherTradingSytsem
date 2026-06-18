"""Tests for :mod:`aether_api.services.chat.tools`.

Covers:

* Catalogue tenancy invariant: NO schema declares ``user_id`` /
  ``project_id`` / ``conversation_id``. Asserted at import time AND
  re-asserted here for belt-and-braces.
* Each tool returns tenant-scoped data (cross-tenant ctx → empty /
  ``None``).
* The dispatcher strips LLM-forged tenancy keys before invoking the
  callable; the underlying tool sees only the safe kwargs.
* Unknown tool name returns ``is_error=True`` with a helpful message.
* A tool raising an exception returns ``is_error=True`` without
  leaking a stack trace into ``content``.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed():
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


def _make_ctx(user_id, project_id):
    from aether_api.db.session import get_session_maker
    from aether_api.services.chat.context import ChatDispatchContext

    maker = get_session_maker()
    return ChatDispatchContext(
        user_id=user_id,
        pair_id=project_id,
        conversation_id=uuid.uuid4(),
        db_session_factory=maker,
        llm_client=object(),
    )


# ---------------------------------------------------------------------------
# Catalogue tenancy gate
# ---------------------------------------------------------------------------


def test_catalogue_has_expected_tools() -> None:
    from aether_api.services.chat.tools import TOOL_CATALOGUE

    assert set(TOOL_CATALOGUE.keys()) == {
        "get_project_status",
        "get_recent_trades",
        "get_sleep_reports",
        "get_qtable_summary",
        "get_semantic_rules",
    }


def test_no_tool_schema_exposes_tenancy() -> None:
    from aether_api.services.chat.tools import TOOL_CATALOGUE

    forbidden = {"user_id", "project_id", "conversation_id"}
    for name, spec in TOOL_CATALOGUE.items():
        props = (spec.schema or {}).get("properties", {})
        leaked = set(props.keys()) & forbidden
        assert not leaked, f"{name} schema leaks tenancy keys: {leaked}"


# ---------------------------------------------------------------------------
# Per-tool tenant scoping
# ---------------------------------------------------------------------------


async def test_tool_get_project_status_owned(app_client) -> None:
    from aether_api.services.chat.tools import tool_get_project_status

    user_a_id, _, proj_a_id, _ = await _seed()
    ctx = _make_ctx(user_a_id, proj_a_id)
    result = await tool_get_project_status(ctx)
    assert result["project"] is not None
    assert result["project"]["id"] == str(proj_a_id)
    assert result["project"]["name"] == "proj-a"
    assert result["equity"] is None  # v1: no MCP live-bus integration


async def test_tool_get_project_status_cross_tenant(app_client) -> None:
    from aether_api.services.chat.tools import tool_get_project_status

    user_a_id, _, _, proj_b_id = await _seed()
    ctx = _make_ctx(user_a_id, proj_b_id)
    result = await tool_get_project_status(ctx)
    assert result == {"project": None}


async def test_tool_get_recent_trades_empty(app_client) -> None:
    from aether_api.services.chat.tools import tool_get_recent_trades

    user_a_id, _, proj_a_id, _ = await _seed()
    ctx = _make_ctx(user_a_id, proj_a_id)
    result = await tool_get_recent_trades(ctx)
    assert result["total"] == 0
    assert result["trades"] == []
    assert result["since_hours"] == 24
    assert result["limit"] == 20


async def test_tool_get_recent_trades_cross_tenant(app_client) -> None:
    from aether_api.services.chat.tools import tool_get_recent_trades

    user_a_id, _, _, proj_b_id = await _seed()
    ctx = _make_ctx(user_a_id, proj_b_id)
    result = await tool_get_recent_trades(ctx)
    assert result["total"] == 0
    assert result["trades"] == []


async def test_tool_get_sleep_reports_empty(app_client) -> None:
    from aether_api.services.chat.tools import tool_get_sleep_reports

    user_a_id, _, proj_a_id, _ = await _seed()
    ctx = _make_ctx(user_a_id, proj_a_id)
    result = await tool_get_sleep_reports(ctx, limit=3)
    assert result["reports"] == []


async def test_tool_get_qtable_summary_none(app_client) -> None:
    from aether_api.services.chat.tools import tool_get_qtable_summary

    user_a_id, _, proj_a_id, _ = await _seed()
    ctx = _make_ctx(user_a_id, proj_a_id)
    result = await tool_get_qtable_summary(ctx)
    assert result == {"q_table": None}


async def test_tool_get_qtable_summary_present(app_client) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.q_table_repository import QTableRepository
    from aether_api.services.chat.tools import tool_get_qtable_summary

    user_a_id, _, proj_a_id, _ = await _seed()
    maker = get_session_maker()
    async with maker() as session:
        await QTableRepository(session).insert_version(
            user_id=user_a_id,
            project_id=proj_a_id,
            version=2,
            table_data={"S0": {"buy": 0.5}, "S1": {"sell": 0.3, "buy": 0.1}},
            learning_rate=0.1,
            discount_factor=0.9,
            episode_count=42,
        )
        await session.commit()

    ctx = _make_ctx(user_a_id, proj_a_id)
    result = await tool_get_qtable_summary(ctx)
    assert result["q_table"] is not None
    assert result["q_table"]["version"] == 2
    assert result["q_table"]["episode_count"] == 42
    # Action distribution: 'buy' appears in S0 + S1 = 2; 'sell' in S1 = 1.
    assert result["q_table"]["action_distribution"] == {"buy": 2, "sell": 1}


async def test_tool_get_semantic_rules_empty(app_client) -> None:
    from aether_api.services.chat.tools import tool_get_semantic_rules

    user_a_id, _, proj_a_id, _ = await _seed()
    ctx = _make_ctx(user_a_id, proj_a_id)
    result = await tool_get_semantic_rules(ctx)
    assert result["rules"] == []


async def test_tool_get_semantic_rules_inactive_returns_empty(app_client) -> None:
    from aether_api.services.chat.tools import tool_get_semantic_rules

    user_a_id, _, proj_a_id, _ = await _seed()
    ctx = _make_ctx(user_a_id, proj_a_id)
    result = await tool_get_semantic_rules(ctx, active=False)
    assert result["rules"] == []
    assert "note" in result


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


async def test_dispatcher_unknown_tool(app_client) -> None:
    from aether_api.services.chat.tools import dispatch_tool

    user_a_id, _, proj_a_id, _ = await _seed()
    ctx = _make_ctx(user_a_id, proj_a_id)
    result = await dispatch_tool(
        ctx,
        tool_use_id="tu_1",
        tool_name="get_universe_state",
        input={},
    )
    assert result["is_error"] is True
    assert result["tool_use_id"] == "tu_1"
    assert "unknown tool" in result["content"].lower()


async def test_dispatcher_strips_tenancy_keys(app_client, monkeypatch) -> None:
    """LLM-forged tenancy keys MUST NOT reach the underlying callable."""
    from aether_api.services.chat.tools import (
        TOOL_CATALOGUE,
        ToolSpec,
        dispatch_tool,
    )

    user_a_id, user_b_id, proj_a_id, _ = await _seed()
    ctx = _make_ctx(user_a_id, proj_a_id)

    seen_kwargs: dict[str, Any] = {}

    async def fake_callable(ctx_arg, **kwargs):
        seen_kwargs.update(kwargs)
        return {"ok": True, "got_kwargs": list(kwargs.keys())}

    fake_spec = ToolSpec(
        name="get_project_status",
        description="x",
        schema={"type": "object", "properties": {}, "additionalProperties": False},
        callable=fake_callable,
    )
    monkeypatch.setitem(TOOL_CATALOGUE, "get_project_status", fake_spec)

    # The LLM forges every tenancy key.
    result = await dispatch_tool(
        ctx,
        tool_use_id="tu_42",
        tool_name="get_project_status",
        input={
            "user_id": str(user_b_id),  # cross-tenant attempt
            "project_id": str(uuid.uuid4()),
            "conversation_id": str(uuid.uuid4()),
            "since_hours": 12,
        },
    )

    assert result["is_error"] is False
    # The callable saw `since_hours` but never the forged tenancy keys.
    assert "user_id" not in seen_kwargs
    assert "project_id" not in seen_kwargs
    assert "conversation_id" not in seen_kwargs
    assert seen_kwargs.get("since_hours") == 12


async def test_dispatcher_tool_exception_returns_is_error(app_client, monkeypatch) -> None:
    from aether_api.services.chat.tools import (
        TOOL_CATALOGUE,
        ToolSpec,
        dispatch_tool,
    )

    user_a_id, _, proj_a_id, _ = await _seed()
    ctx = _make_ctx(user_a_id, proj_a_id)

    async def boom(ctx_arg, **_kwargs):
        raise RuntimeError("super-secret stack trace that should NOT leak")

    fake_spec = ToolSpec(
        name="get_project_status",
        description="x",
        schema={"type": "object", "properties": {}, "additionalProperties": False},
        callable=boom,
    )
    monkeypatch.setitem(TOOL_CATALOGUE, "get_project_status", fake_spec)

    result = await dispatch_tool(
        ctx,
        tool_use_id="tu_99",
        tool_name="get_project_status",
        input={},
    )
    assert result["is_error"] is True
    # No stack trace, no secret message leaked to the LLM.
    assert "super-secret" not in result["content"]
    assert "RuntimeError" in result["content"]


async def test_dispatcher_happy_path_through_real_tool(app_client) -> None:
    """End-to-end dispatch into the real `get_project_status` callable."""
    from aether_api.services.chat.tools import dispatch_tool

    user_a_id, _, proj_a_id, _ = await _seed()
    ctx = _make_ctx(user_a_id, proj_a_id)
    result = await dispatch_tool(
        ctx,
        tool_use_id="tu_real",
        tool_name="get_project_status",
        input={},
    )
    assert result["is_error"] is False
    assert result["tool_use_id"] == "tu_real"
    assert result["content"]["project"] is not None
    assert result["content"]["project"]["id"] == str(proj_a_id)
