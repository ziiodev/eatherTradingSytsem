"""Tests for :class:`SemanticMemoryRepository` — tenant-scoped via projects JOIN."""

from __future__ import annotations

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
# insert + list_active (happy path)
# ---------------------------------------------------------------------------


async def test_insert_and_list_active_happy_path(app_client) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.semantic_memory_repository import (
        SemanticMemoryRepository,
    )

    user_a_id, _, proj_a_id, _ = await _seed_two_users_with_projects()

    maker = get_session_maker()
    async with maker() as session:
        repo = SemanticMemoryRepository(session)
        rule = await repo.insert(
            user_id=user_a_id,
            project_id=proj_a_id,
            rule_type="risk",
            title="Don't trade NFP",
            content="Avoid entries 15min before/after non-farm payrolls.",
            confidence=0.85,
            source="sleep-run-2026-05-28",
        )
        await session.commit()
        assert rule.active is True
        assert rule.rule_type == "risk"
        assert rule.body == "Avoid entries 15min before/after non-farm payrolls."
        # Title + confidence + source live in payload.
        assert rule.payload["title"] == "Don't trade NFP"
        assert rule.payload["confidence"] == 0.85
        assert rule.payload["source"] == "sleep-run-2026-05-28"

    async with maker() as session:
        repo = SemanticMemoryRepository(session)
        rows = await repo.list_active(user_id=user_a_id, project_id=proj_a_id)
        assert len(rows) == 1
        assert rows[0].body.startswith("Avoid entries")


async def test_list_active_excludes_inactive(app_client) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.semantic_memory_repository import (
        SemanticMemoryRepository,
    )

    user_a_id, _, proj_a_id, _ = await _seed_two_users_with_projects()

    maker = get_session_maker()
    async with maker() as session:
        repo = SemanticMemoryRepository(session)
        active_rule = await repo.insert(
            user_id=user_a_id,
            project_id=proj_a_id,
            rule_type="risk",
            title="active",
            content="active rule body",
            confidence=1.0,
            source="src",
        )
        inactive_rule = await repo.insert(
            user_id=user_a_id,
            project_id=proj_a_id,
            rule_type="entry",
            title="inactive",
            content="inactive rule body",
            confidence=1.0,
            source="src",
        )
        # Manually deactivate the second one.
        inactive_rule.active = False
        await session.commit()
        active_id = active_rule.id

    async with maker() as session:
        repo = SemanticMemoryRepository(session)
        rows = await repo.list_active(user_id=user_a_id, project_id=proj_a_id)
        assert len(rows) == 1
        assert rows[0].id == active_id


async def test_list_active_rule_type_filter(app_client) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.semantic_memory_repository import (
        SemanticMemoryRepository,
    )

    user_a_id, _, proj_a_id, _ = await _seed_two_users_with_projects()

    maker = get_session_maker()
    async with maker() as session:
        repo = SemanticMemoryRepository(session)
        for rt in ("risk", "entry", "risk"):
            await repo.insert(
                user_id=user_a_id,
                project_id=proj_a_id,
                rule_type=rt,
                title=f"{rt} rule",
                content=f"body for {rt}",
                confidence=1.0,
                source="src",
            )
        await session.commit()

    async with maker() as session:
        repo = SemanticMemoryRepository(session)
        risk_rows = await repo.list_active(
            user_id=user_a_id, project_id=proj_a_id, rule_type="risk"
        )
        assert len(risk_rows) == 2
        assert {r.rule_type for r in risk_rows} == {"risk"}


# ---------------------------------------------------------------------------
# supersede
# ---------------------------------------------------------------------------


async def test_supersede_deactivates_old_and_links(app_client) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.semantic_memory_repository import (
        SemanticMemoryRepository,
    )

    user_a_id, _, proj_a_id, _ = await _seed_two_users_with_projects()

    maker = get_session_maker()
    async with maker() as session:
        repo = SemanticMemoryRepository(session)
        old = await repo.insert(
            user_id=user_a_id,
            project_id=proj_a_id,
            rule_type="risk",
            title="old",
            content="old body",
            confidence=0.6,
            source="src",
        )
        new = await repo.insert(
            user_id=user_a_id,
            project_id=proj_a_id,
            rule_type="risk",
            title="new",
            content="new body",
            confidence=0.9,
            source="src",
        )
        await session.commit()
        old_id, new_id = old.id, new.id

    async with maker() as session:
        repo = SemanticMemoryRepository(session)
        await repo.supersede(
            user_id=user_a_id,
            project_id=proj_a_id,
            rule_id=old_id,
            new_rule_id=new_id,
        )
        await session.commit()

    async with maker() as session:
        repo = SemanticMemoryRepository(session)
        rows = await repo.list_active(user_id=user_a_id, project_id=proj_a_id)
        # Only the new rule is active now.
        assert len(rows) == 1
        assert rows[0].id == new_id


# ---------------------------------------------------------------------------
# Cross-tenant probes
# ---------------------------------------------------------------------------


async def test_cross_tenant_list_returns_empty(app_client) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.semantic_memory_repository import (
        SemanticMemoryRepository,
    )

    user_a_id, user_b_id, proj_a_id, _ = await _seed_two_users_with_projects()

    maker = get_session_maker()
    async with maker() as session:
        repo = SemanticMemoryRepository(session)
        await repo.insert(
            user_id=user_a_id,
            project_id=proj_a_id,
            rule_type="risk",
            title="x",
            content="secret rule body",
            confidence=1.0,
            source="src",
        )
        await session.commit()

    async with maker() as session:
        repo = SemanticMemoryRepository(session)
        rows = await repo.list_active(user_id=user_b_id, project_id=proj_a_id)
        assert rows == []


async def test_cross_tenant_insert_raises(app_client) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.semantic_memory_repository import (
        SemanticMemoryRepository,
    )

    user_a_id, user_b_id, proj_a_id, _ = await _seed_two_users_with_projects()

    maker = get_session_maker()
    async with maker() as session:
        repo = SemanticMemoryRepository(session)
        with pytest.raises(PermissionError):
            await repo.insert(
                user_id=user_b_id,
                project_id=proj_a_id,
                rule_type="risk",
                title="x",
                content="x",
                confidence=1.0,
                source="src",
            )


async def test_cross_tenant_supersede_is_noop(app_client) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.semantic_memory_repository import (
        SemanticMemoryRepository,
    )

    user_a_id, user_b_id, proj_a_id, _ = await _seed_two_users_with_projects()

    maker = get_session_maker()
    async with maker() as session:
        repo = SemanticMemoryRepository(session)
        old = await repo.insert(
            user_id=user_a_id,
            project_id=proj_a_id,
            rule_type="risk",
            title="old",
            content="old body",
            confidence=0.6,
            source="src",
        )
        new = await repo.insert(
            user_id=user_a_id,
            project_id=proj_a_id,
            rule_type="risk",
            title="new",
            content="new body",
            confidence=0.9,
            source="src",
        )
        await session.commit()
        old_id, new_id = old.id, new.id

    async with maker() as session:
        repo = SemanticMemoryRepository(session)
        # User B tries to supersede A's rule — must NOT mutate state.
        await repo.supersede(
            user_id=user_b_id,
            project_id=proj_a_id,
            rule_id=old_id,
            new_rule_id=new_id,
        )
        await session.commit()

    async with maker() as session:
        repo = SemanticMemoryRepository(session)
        rows = await repo.list_active(user_id=user_a_id, project_id=proj_a_id)
        # Both rules still active — supersede was rejected silently.
        assert len(rows) == 2
