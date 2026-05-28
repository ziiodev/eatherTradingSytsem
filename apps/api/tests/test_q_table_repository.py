"""Tests for :class:`QTableRepository` — tenant-scoped via JOIN on projects.

All reads/writes MUST be filtered by ``user_id`` (spec multi-tenancy-delta
#2068). Cross-tenant access returns None / empty — the router maps that
to a 404 to avoid existence disclosure.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

pytestmark = pytest.mark.integration


async def _seed_two_users_with_projects():
    """Seed user A + project A, user B + project B. Returns ids."""
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
# insert_version + get_version (happy path)
# ---------------------------------------------------------------------------


async def test_insert_version_and_get_version_happy_path(app_client) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.q_table_repository import QTableRepository

    user_a_id, _, proj_a_id, _ = await _seed_two_users_with_projects()

    maker = get_session_maker()
    async with maker() as session:
        repo = QTableRepository(session)
        row = await repo.insert_version(
            user_id=user_a_id,
            project_id=proj_a_id,
            version=1,
            table_data={"sk:foo": {"buy": 0.5}},
            learning_rate=0.150,
            discount_factor=0.920,
            metadata={"source": "unit-test"},
        )
        await session.commit()
        assert row is not None
        assert row.version == 1
        # State Q-values present; metadata is stashed under __meta__
        # because Phase 1 model has no dedicated metadata column.
        assert row.table_data["sk:foo"] == {"buy": 0.5}
        assert row.table_data["__meta__"] == {"source": "unit-test"}
        row_id = row.id

    # Round-trip get_version.
    async with maker() as session:
        repo = QTableRepository(session)
        fetched = await repo.get_version(
            user_id=user_a_id, project_id=proj_a_id, version=1
        )
        assert fetched is not None
        assert fetched.id == row_id
        assert fetched.table_data["sk:foo"] == {"buy": 0.5}


async def test_get_latest_returns_highest_version(app_client) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.q_table_repository import QTableRepository

    user_a_id, _, proj_a_id, _ = await _seed_two_users_with_projects()

    maker = get_session_maker()
    async with maker() as session:
        repo = QTableRepository(session)
        await repo.insert_version(
            user_id=user_a_id,
            project_id=proj_a_id,
            version=1,
            table_data={},
            learning_rate=0.150,
            discount_factor=0.920,
            metadata={},
        )
        await repo.insert_version(
            user_id=user_a_id,
            project_id=proj_a_id,
            version=2,
            table_data={"sk:x": {"buy": 1.0}},
            learning_rate=0.150,
            discount_factor=0.920,
            metadata={},
        )
        await session.commit()

    async with maker() as session:
        repo = QTableRepository(session)
        latest = await repo.get_latest(user_id=user_a_id, project_id=proj_a_id)
        assert latest is not None
        assert latest.version == 2
        assert latest.table_data == {"sk:x": {"buy": 1.0}}


async def test_list_versions_returns_versions_desc(app_client) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.q_table_repository import QTableRepository

    user_a_id, _, proj_a_id, _ = await _seed_two_users_with_projects()

    maker = get_session_maker()
    async with maker() as session:
        repo = QTableRepository(session)
        for v in (1, 2, 3):
            await repo.insert_version(
                user_id=user_a_id,
                project_id=proj_a_id,
                version=v,
                table_data={},
                learning_rate=0.150,
                discount_factor=0.920,
                metadata={},
            )
        await session.commit()

    async with maker() as session:
        repo = QTableRepository(session)
        rows = await repo.list_versions(
            user_id=user_a_id, project_id=proj_a_id, limit=10, offset=0
        )
        assert [r.version for r in rows] == [3, 2, 1]


# ---------------------------------------------------------------------------
# Cross-tenant probe
# ---------------------------------------------------------------------------


async def test_cross_tenant_get_latest_returns_none(app_client) -> None:
    """User B tries to read User A's project Q-Table — sees nothing."""
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.q_table_repository import QTableRepository

    user_a_id, user_b_id, proj_a_id, _ = await _seed_two_users_with_projects()

    maker = get_session_maker()
    async with maker() as session:
        repo = QTableRepository(session)
        await repo.insert_version(
            user_id=user_a_id,
            project_id=proj_a_id,
            version=1,
            table_data={"sk:secret": {"buy": 9.99}},
            learning_rate=0.150,
            discount_factor=0.920,
            metadata={},
        )
        await session.commit()

    async with maker() as session:
        repo = QTableRepository(session)
        # User B asks for User A's project — must return None.
        latest = await repo.get_latest(user_id=user_b_id, project_id=proj_a_id)
        assert latest is None
        version = await repo.get_version(
            user_id=user_b_id, project_id=proj_a_id, version=1
        )
        assert version is None
        rows = await repo.list_versions(
            user_id=user_b_id, project_id=proj_a_id, limit=10, offset=0
        )
        assert rows == []


async def test_cross_tenant_insert_raises(app_client) -> None:
    """User B tries to insert a Q-Table version on User A's project — refused."""
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.q_table_repository import QTableRepository

    user_a_id, user_b_id, proj_a_id, _ = await _seed_two_users_with_projects()

    maker = get_session_maker()
    async with maker() as session:
        repo = QTableRepository(session)
        with pytest.raises(PermissionError):
            await repo.insert_version(
                user_id=user_b_id,
                project_id=proj_a_id,
                version=1,
                table_data={},
                learning_rate=Decimal("0.150"),
                discount_factor=Decimal("0.920"),
                metadata={},
            )
