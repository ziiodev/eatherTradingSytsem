"""Tests for :mod:`aether_api.services.chat.context`.

Covers:

* ``ChatDispatchContext`` is frozen and carries the expected fields.
* ``build_project_snapshot`` returns the expected shape for an owned
  project, with sleep_run + Q-Table + active rules surfaced.
* Cross-tenant ``project_id`` returns a neutral snapshot (``project``
  is ``None``, counts are zero) so existence is not disclosed.
* ``build_system_prompt`` produces exactly two blocks; block 1 carries
  ``cache_control={"type":"ephemeral"}``; block 2 never does.
* The static block 1 text is byte-identical across calls (a hard
  precondition for prompt caching).
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.integration


async def _seed_two_users_two_projects():
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


# ---------------------------------------------------------------------------
# ChatDispatchContext
# ---------------------------------------------------------------------------


def test_chat_dispatch_context_is_frozen() -> None:
    from aether_api.services.chat.context import ChatDispatchContext

    ctx = ChatDispatchContext(
        user_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        db_session_factory=lambda: None,  # type: ignore[arg-type]
        llm_client=object(),
    )
    # dataclass(frozen=True) with slots=True raises FrozenInstanceError;
    # the dataclasses module subclasses AttributeError on older Pythons.
    from dataclasses import FrozenInstanceError

    with pytest.raises((FrozenInstanceError, AttributeError)):
        ctx.user_id = uuid.uuid4()  # type: ignore[misc]


def test_chat_dispatch_context_defaults() -> None:
    from aether_api.services.chat.context import (
        DEFAULT_MAX_TOOL_ROUNDTRIPS,
        ChatDispatchContext,
    )

    ctx = ChatDispatchContext(
        user_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        db_session_factory=lambda: None,  # type: ignore[arg-type]
        llm_client=object(),
    )
    assert ctx.max_tool_roundtrips == DEFAULT_MAX_TOOL_ROUNDTRIPS == 5
    assert ctx.meta == {}


# ---------------------------------------------------------------------------
# build_project_snapshot
# ---------------------------------------------------------------------------


async def test_build_project_snapshot_owned(app_client) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.services.chat.context import build_project_snapshot

    user_a_id, _, proj_a_id, _ = await _seed_two_users_two_projects()

    maker = get_session_maker()
    async with maker() as session:
        snap = await build_project_snapshot(
            session, user_id=user_a_id, project_id=proj_a_id
        )

    assert snap["project"] is not None
    assert snap["project"]["id"] == str(proj_a_id)
    assert snap["project"]["name"] == "proj-a"
    assert snap["project"]["symbol"] == "EURUSD"
    assert snap["latest_sleep_report"] is None
    assert snap["active_rules_count"] == 0
    assert snap["q_table_version"] is None
    assert "generated_at" in snap


async def test_build_project_snapshot_cross_tenant(app_client) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.services.chat.context import build_project_snapshot

    user_a_id, _, _, proj_b_id = await _seed_two_users_two_projects()

    maker = get_session_maker()
    async with maker() as session:
        snap = await build_project_snapshot(
            session, user_id=user_a_id, project_id=proj_b_id
        )

    assert snap["project"] is None
    assert snap["active_rules_count"] == 0
    assert snap["q_table_version"] is None
    assert "generated_at" in snap


async def test_build_project_snapshot_with_qtable_and_rule(app_client) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.q_table_repository import QTableRepository
    from aether_api.repositories.semantic_memory_repository import (
        SemanticMemoryRepository,
    )
    from aether_api.services.chat.context import build_project_snapshot

    user_a_id, _, proj_a_id, _ = await _seed_two_users_two_projects()

    maker = get_session_maker()
    async with maker() as session:
        await QTableRepository(session).insert_version(
            user_id=user_a_id,
            project_id=proj_a_id,
            version=1,
            table_data={"S0": {"buy": 0.5}},
            learning_rate=0.1,
            discount_factor=0.9,
        )
        await SemanticMemoryRepository(session).insert(
            user_id=user_a_id,
            project_id=proj_a_id,
            rule_type="risk",
            title="No trade NFP",
            content="Pause 30min around NFP",
            confidence=0.8,
            source="manual",
        )
        await session.commit()

    async with maker() as session:
        snap = await build_project_snapshot(
            session, user_id=user_a_id, project_id=proj_a_id
        )

    assert snap["q_table_version"] == 1
    assert snap["active_rules_count"] == 1


# ---------------------------------------------------------------------------
# build_system_prompt
# ---------------------------------------------------------------------------


def test_build_system_prompt_two_blocks_with_cache_control() -> None:
    from aether_api.services.chat.context import build_system_prompt

    snapshot = {"project": None, "active_rules_count": 0}
    blocks = build_system_prompt(snapshot)

    assert isinstance(blocks, list)
    assert len(blocks) == 2

    block1, block2 = blocks
    assert block1["type"] == "text"
    assert block1["cache_control"] == {"type": "ephemeral"}
    assert isinstance(block1["text"], str)
    assert len(block1["text"]) > 100  # not empty / not a placeholder

    assert block2["type"] == "text"
    assert "cache_control" not in block2
    # The dynamic block must contain the snapshot — we check for a key.
    assert "active_rules_count" in block2["text"]


def test_build_system_prompt_block1_stable_across_calls() -> None:
    """Prompt caching demands byte-identical block 1 across requests."""
    from aether_api.services.chat.context import build_system_prompt

    blocks1 = build_system_prompt({"project": None})
    blocks2 = build_system_prompt({"project": "different snapshot"})

    # Block 1 invariant — must NOT change even when block 2 changes.
    assert blocks1[0]["text"] == blocks2[0]["text"]
    assert blocks1[0]["cache_control"] == blocks2[0]["cache_control"]
    # Block 2 should differ — sanity that the test is meaningful.
    assert blocks1[1]["text"] != blocks2[1]["text"]


def test_build_system_prompt_snapshot_structure() -> None:
    """The system-prompt structure is a stable contract — snapshot it."""
    from aether_api.services.chat.context import build_system_prompt

    blocks = build_system_prompt({"project": None})

    # Block 1 — static, cached. Snapshot the keys + cache_control.
    block1 = blocks[0]
    assert set(block1.keys()) == {"type", "text", "cache_control"}
    assert block1["type"] == "text"
    assert block1["cache_control"] == {"type": "ephemeral"}

    # Block 2 — dynamic, NEVER cached.
    block2 = blocks[1]
    assert set(block2.keys()) == {"type", "text"}
    assert block2["type"] == "text"
