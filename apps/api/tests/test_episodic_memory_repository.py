"""Tests for :class:`EpisodicMemoryRepository` — tenant-scoped via projects JOIN."""

from __future__ import annotations

from decimal import Decimal

import pytest

pytestmark = pytest.mark.integration


async def _seed_two_users_with_projects():
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
# insert + list_by_project (happy path)
# ---------------------------------------------------------------------------


async def test_insert_and_list_happy_path(app_client) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.episodic_memory_repository import (
        EpisodicMemoryRepository,
    )

    user_a_id, _, proj_a_id, _ = await _seed_two_users_with_projects()

    maker = get_session_maker()
    async with maker() as session:
        repo = EpisodicMemoryRepository(session)
        ep = await repo.insert(
            user_id=user_a_id,
            project_id=proj_a_id,
            trade_id=None,
            state={"rsi": 35, "trend": "up"},
            state_key="rsi35:up",
            action="buy",
            reward=Decimal("1.50"),
            result="win",
            worker_reasoning="rsi bounce",
            q_value_before=Decimal("0.10"),
            q_value_after=Decimal("0.25"),
            is_special=False,
            sleep_run_id=None,
        )
        await session.commit()
        assert ep is not None
        assert ep.state_key == "rsi35:up"
        assert ep.action == "buy"
        # The structured extras live in meta_data JSONB.
        assert ep.meta_data["result"] == "win"
        assert ep.meta_data["worker_reasoning"] == "rsi bounce"

    async with maker() as session:
        repo = EpisodicMemoryRepository(session)
        rows = await repo.list_by_project(
            user_id=user_a_id,
            project_id=proj_a_id,
            since=None,
            until=None,
            limit=10,
            offset=0,
        )
        assert len(rows) == 1
        assert rows[0].state_key == "rsi35:up"


async def test_list_by_project_state_key_filter(app_client) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.episodic_memory_repository import (
        EpisodicMemoryRepository,
    )

    user_a_id, _, proj_a_id, _ = await _seed_two_users_with_projects()

    maker = get_session_maker()
    async with maker() as session:
        repo = EpisodicMemoryRepository(session)
        for sk, a in [
            ("rsi35:up", "buy"),
            ("rsi35:up", "buy"),
            ("rsi70:down", "sell"),
        ]:
            await repo.insert(
                user_id=user_a_id,
                project_id=proj_a_id,
                trade_id=None,
                state={},
                state_key=sk,
                action=a,
                reward=Decimal("0.0"),
                result="flat",
                worker_reasoning="x",
                q_value_before=Decimal("0.0"),
                q_value_after=Decimal("0.0"),
                is_special=False,
                sleep_run_id=None,
            )
        await session.commit()

    async with maker() as session:
        repo = EpisodicMemoryRepository(session)
        rows = await repo.list_by_project(
            user_id=user_a_id,
            project_id=proj_a_id,
            since=None,
            until=None,
            state_key="rsi35:up",
            limit=10,
            offset=0,
        )
        assert len(rows) == 2
        assert {r.state_key for r in rows} == {"rsi35:up"}


# ---------------------------------------------------------------------------
# top_k_states
# ---------------------------------------------------------------------------


async def test_top_k_states_orders_by_count_desc(app_client) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.episodic_memory_repository import (
        EpisodicMemoryRepository,
    )

    user_a_id, _, proj_a_id, _ = await _seed_two_users_with_projects()

    maker = get_session_maker()
    async with maker() as session:
        repo = EpisodicMemoryRepository(session)
        plan = [("sk:a", 4), ("sk:b", 2), ("sk:c", 5)]
        for sk, n in plan:
            for _ in range(n):
                await repo.insert(
                    user_id=user_a_id,
                    project_id=proj_a_id,
                    trade_id=None,
                    state={},
                    state_key=sk,
                    action="buy",
                    reward=Decimal("0.0"),
                    result="flat",
                    worker_reasoning="x",
                    q_value_before=Decimal("0.0"),
                    q_value_after=Decimal("0.0"),
                    is_special=False,
                    sleep_run_id=None,
                )
        await session.commit()

    async with maker() as session:
        repo = EpisodicMemoryRepository(session)
        top = await repo.top_k_states(user_id=user_a_id, project_id=proj_a_id, k=2)
        # Two highest counts, in DESC order.
        assert top == [("sk:c", 5), ("sk:a", 4)]


# ---------------------------------------------------------------------------
# Cross-tenant probes
# ---------------------------------------------------------------------------


async def test_cross_tenant_returns_empty_or_none(app_client) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.episodic_memory_repository import (
        EpisodicMemoryRepository,
    )

    user_a_id, user_b_id, proj_a_id, _ = await _seed_two_users_with_projects()

    maker = get_session_maker()
    async with maker() as session:
        repo = EpisodicMemoryRepository(session)
        await repo.insert(
            user_id=user_a_id,
            project_id=proj_a_id,
            trade_id=None,
            state={},
            state_key="sk:secret",
            action="buy",
            reward=Decimal("1.0"),
            result="win",
            worker_reasoning="x",
            q_value_before=Decimal("0.0"),
            q_value_after=Decimal("0.0"),
            is_special=False,
            sleep_run_id=None,
        )
        await session.commit()

    async with maker() as session:
        repo = EpisodicMemoryRepository(session)
        rows = await repo.list_by_project(
            user_id=user_b_id,
            project_id=proj_a_id,
            since=None,
            until=None,
            limit=10,
            offset=0,
        )
        assert rows == []
        top = await repo.top_k_states(
            user_id=user_b_id, project_id=proj_a_id, k=10
        )
        assert top == []


async def test_cross_tenant_insert_raises(app_client) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.episodic_memory_repository import (
        EpisodicMemoryRepository,
    )

    user_a_id, user_b_id, proj_a_id, _ = await _seed_two_users_with_projects()

    maker = get_session_maker()
    async with maker() as session:
        repo = EpisodicMemoryRepository(session)
        with pytest.raises(PermissionError):
            await repo.insert(
                user_id=user_b_id,
                project_id=proj_a_id,
                trade_id=None,
                state={},
                state_key="sk:x",
                action="buy",
                reward=Decimal("0.0"),
                result="flat",
                worker_reasoning="x",
                q_value_before=Decimal("0.0"),
                q_value_after=Decimal("0.0"),
                is_special=False,
                sleep_run_id=None,
            )
