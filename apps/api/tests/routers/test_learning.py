"""End-to-end HTTP coverage for ``/api/pairs/{id}/q-tables`` + friends.

Phase 9 of sleep-learning-loop adds three GET-only routes for the
operator dashboard:

* ``GET /api/pairs/{project_id}/q-tables``                — paginated
* ``GET /api/pairs/{project_id}/q-tables/{version}``      — single
* ``GET /api/pairs/{project_id}/episodic-memory``         — paginated
* ``GET /api/pairs/{project_id}/semantic-memory``         — active rules

Every test asserts the canonical multi-tenancy invariants:

* 401 without a session (auth gate).
* 404 (NOT 403) when the project belongs to another tenant
  (existence non-disclosure per ``specs/multi-tenancy`` and
  ``multi-tenancy-delta`` #2068).
* The repository-layer JOIN through ``projects.user_id`` is the
  enforcement boundary — these tests verify the router does NOT
  accidentally bypass it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _login(client, *, email: str | None = None):
    """Seed a user and authenticate the httpx client.

    The unique-by-default email keeps tests independent — the autouse
    TRUNCATE fixture also wipes users between tests, but a random suffix
    is cheap insurance.
    """
    from aether_api.core.settings import get_settings
    from aether_api.db.session import get_session_maker

    from tests._helpers import seed_user

    get_settings.cache_clear()
    email = email or f"learning-{uuid.uuid4().hex[:8]}@example.com"
    password = "correct horse battery staple"
    maker = get_session_maker()
    async with maker() as session:
        user = await seed_user(session, email=email, password=password)
        await session.commit()
    resp = await client.post(
        "/api/auth/login", json={"email": email, "password": password}
    )
    assert resp.status_code == 200, resp.text
    return user


async def _seed_project(owner) -> uuid.UUID:
    """Create one project owned by ``owner`` and return its UUID."""
    from aether_api.db.session import get_session_maker

    from tests._helpers import seed_project

    maker = get_session_maker()
    async with maker() as session:
        project = await seed_project(session, owner=owner, name=f"p-{uuid.uuid4().hex[:8]}")
        await session.commit()
        return project.id


async def _seed_q_table_versions(
    *, user_id: uuid.UUID, project_id: uuid.UUID, n: int
) -> list[int]:
    """Append ``n`` Q-Table versions for the project (1..n)."""
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.q_table_repository import QTableRepository

    versions: list[int] = []
    maker = get_session_maker()
    async with maker() as session:
        repo = QTableRepository(session)
        for v in range(1, n + 1):
            row = await repo.insert_version(
                user_id=user_id,
                project_id=project_id,
                version=v,
                table_data={"state-A": {"buy": 0.1 + v * 0.01}},
                learning_rate=Decimal("0.15"),
                discount_factor=Decimal("0.92"),
                episode_count=v * 10,
            )
            versions.append(int(row.version))
        await session.commit()
    return versions


async def _seed_episodes(
    *,
    user_id: uuid.UUID,
    project_id: uuid.UUID,
    rows: list[dict],
) -> None:
    """Insert one episode per spec dict.

    Each dict supports: ``state_key`` (str), ``action`` (str),
    ``reward`` (float), ``created_at`` (datetime override).
    """
    from aether_api.db.session import get_session_maker
    from aether_api.models.episodic_memory import EpisodicMemory
    from aether_api.repositories.episodic_memory_repository import (
        EpisodicMemoryRepository,
    )
    from sqlalchemy import update

    maker = get_session_maker()
    async with maker() as session:
        repo = EpisodicMemoryRepository(session)
        inserted_ids: list[tuple[uuid.UUID, datetime]] = []
        for spec in rows:
            row = await repo.insert(
                user_id=user_id,
                project_id=project_id,
                trade_id=None,
                state=spec.get("state", {"k": "v"}),
                state_key=spec["state_key"],
                action=spec["action"],
                reward=spec["reward"],
                result=None,
                worker_reasoning=None,
                q_value_before=None,
                q_value_after=None,
                is_special=False,
            )
            if "created_at" in spec:
                inserted_ids.append((row.id, spec["created_at"]))
        # Back-date rows so time-window filters can be exercised
        # deterministically — the default server_default is NOW().
        for row_id, dt in inserted_ids:
            await session.execute(
                update(EpisodicMemory)
                .where(EpisodicMemory.id == row_id)
                .values(created_at=dt)
            )
        await session.commit()


async def _seed_semantic_rules(
    *,
    user_id: uuid.UUID,
    project_id: uuid.UUID,
    rules: list[dict],
) -> None:
    """Insert ``rules`` — each dict needs ``rule_type``, ``title``, ``content``."""
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.semantic_memory_repository import (
        SemanticMemoryRepository,
    )

    maker = get_session_maker()
    async with maker() as session:
        repo = SemanticMemoryRepository(session)
        for spec in rules:
            await repo.insert(
                user_id=user_id,
                project_id=project_id,
                rule_type=spec["rule_type"],
                title=spec.get("title", "t"),
                content=spec.get("content", "c"),
                confidence=spec.get("confidence", 0.5),
                source=spec.get("source", "test"),
            )
        await session.commit()


# ---------------------------------------------------------------------------
# Q-Tables — list
# ---------------------------------------------------------------------------
async def test_list_q_tables_requires_auth(app_client) -> None:
    resp = await app_client.get(f"/api/pairs/{uuid.uuid4()}/q-tables")
    assert resp.status_code == 401


async def test_list_q_tables_happy_path(app_client) -> None:
    user = await _login(app_client)
    project_id = await _seed_project(user)
    await _seed_q_table_versions(user_id=user.id, project_id=project_id, n=3)

    resp = await app_client.get(f"/api/pairs/{project_id}/q-tables")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 3
    assert len(body["items"]) == 3
    # Newest version first.
    versions = [item["version"] for item in body["items"]]
    assert versions == sorted(versions, reverse=True)
    assert versions == [3, 2, 1]


async def test_list_q_tables_pagination(app_client) -> None:
    user = await _login(app_client)
    project_id = await _seed_project(user)
    await _seed_q_table_versions(user_id=user.id, project_id=project_id, n=5)

    resp = await app_client.get(
        f"/api/pairs/{project_id}/q-tables?limit=2&offset=1"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # ``total`` reflects ALL versions for the project, not the page size.
    assert body["total"] == 5
    versions = [item["version"] for item in body["items"]]
    assert versions == [4, 3]


async def test_list_q_tables_cross_tenant_is_404(app_client) -> None:
    user_a = await _login(app_client, email="a@example.com")
    project_a = await _seed_project(user_a)
    await _seed_q_table_versions(
        user_id=user_a.id, project_id=project_a, n=2
    )

    # Log out, log in as B, try to read A's q-tables.
    app_client.cookies.clear()
    await _login(app_client, email="b@example.com")
    resp = await app_client.get(f"/api/pairs/{project_a}/q-tables")
    # MUST be 404, not 403 — existence is NOT disclosed.
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Q-Tables — single version
# ---------------------------------------------------------------------------
async def test_get_q_table_version_requires_auth(app_client) -> None:
    resp = await app_client.get(
        f"/api/pairs/{uuid.uuid4()}/q-tables/1"
    )
    assert resp.status_code == 401


async def test_get_q_table_version_happy_path(app_client) -> None:
    user = await _login(app_client)
    project_id = await _seed_project(user)
    await _seed_q_table_versions(user_id=user.id, project_id=project_id, n=2)

    resp = await app_client.get(f"/api/pairs/{project_id}/q-tables/2")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["version"] == 2
    # The full table_data JSONB is present in the detail response.
    assert "state-A" in body["table_data"]


async def test_get_q_table_version_not_found_is_404(app_client) -> None:
    user = await _login(app_client)
    project_id = await _seed_project(user)
    await _seed_q_table_versions(user_id=user.id, project_id=project_id, n=1)

    resp = await app_client.get(f"/api/pairs/{project_id}/q-tables/99")
    assert resp.status_code == 404


async def test_get_q_table_version_cross_tenant_is_404(app_client) -> None:
    user_a = await _login(app_client, email="a@example.com")
    project_a = await _seed_project(user_a)
    await _seed_q_table_versions(
        user_id=user_a.id, project_id=project_a, n=1
    )

    app_client.cookies.clear()
    await _login(app_client, email="b@example.com")
    resp = await app_client.get(f"/api/pairs/{project_a}/q-tables/1")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Episodic memory
# ---------------------------------------------------------------------------
async def test_list_episodic_memory_requires_auth(app_client) -> None:
    resp = await app_client.get(
        f"/api/pairs/{uuid.uuid4()}/episodic-memory"
    )
    assert resp.status_code == 401


async def test_list_episodic_memory_happy_path(app_client) -> None:
    user = await _login(app_client)
    project_id = await _seed_project(user)
    await _seed_episodes(
        user_id=user.id,
        project_id=project_id,
        rows=[
            {"state_key": "s1", "action": "buy", "reward": 1.5},
            {"state_key": "s2", "action": "sell", "reward": -0.3},
        ],
    )

    resp = await app_client.get(f"/api/pairs/{project_id}/episodic-memory")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 2
    state_keys = {item["state_key"] for item in body["items"]}
    assert state_keys == {"s1", "s2"}


async def test_list_episodic_memory_filters_by_state_key(app_client) -> None:
    user = await _login(app_client)
    project_id = await _seed_project(user)
    await _seed_episodes(
        user_id=user.id,
        project_id=project_id,
        rows=[
            {"state_key": "s1", "action": "buy", "reward": 1.0},
            {"state_key": "s1", "action": "sell", "reward": 0.5},
            {"state_key": "s2", "action": "buy", "reward": -0.2},
        ],
    )

    resp = await app_client.get(
        f"/api/pairs/{project_id}/episodic-memory?state_key=s1"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 2
    assert all(item["state_key"] == "s1" for item in body["items"])


async def test_list_episodic_memory_filters_by_time_window(app_client) -> None:
    user = await _login(app_client)
    project_id = await _seed_project(user)

    now = datetime.now(tz=UTC).replace(tzinfo=None)
    await _seed_episodes(
        user_id=user.id,
        project_id=project_id,
        rows=[
            {
                "state_key": "old",
                "action": "buy",
                "reward": 0.1,
                "created_at": now - timedelta(days=30),
            },
            {
                "state_key": "recent",
                "action": "sell",
                "reward": 0.2,
                "created_at": now - timedelta(hours=1),
            },
        ],
    )

    # Default window (last 7 days) excludes the 30-day-old row.
    resp = await app_client.get(f"/api/pairs/{project_id}/episodic-memory")
    assert resp.status_code == 200
    body = resp.json()
    state_keys = {item["state_key"] for item in body["items"]}
    assert state_keys == {"recent"}

    # Explicit wide ``since`` includes both.
    since = (now - timedelta(days=60)).isoformat() + "Z"
    resp = await app_client.get(
        f"/api/pairs/{project_id}/episodic-memory?since={since}"
    )
    assert resp.status_code == 200
    body = resp.json()
    state_keys = {item["state_key"] for item in body["items"]}
    assert state_keys == {"old", "recent"}


async def test_list_episodic_memory_pagination(app_client) -> None:
    user = await _login(app_client)
    project_id = await _seed_project(user)
    await _seed_episodes(
        user_id=user.id,
        project_id=project_id,
        rows=[
            {"state_key": f"s{i}", "action": "buy", "reward": float(i)}
            for i in range(5)
        ],
    )

    resp = await app_client.get(
        f"/api/pairs/{project_id}/episodic-memory?limit=2&offset=0"
    )
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 2


async def test_list_episodic_memory_cross_tenant_is_404(app_client) -> None:
    user_a = await _login(app_client, email="a@example.com")
    project_a = await _seed_project(user_a)
    await _seed_episodes(
        user_id=user_a.id,
        project_id=project_a,
        rows=[{"state_key": "s1", "action": "buy", "reward": 1.0}],
    )

    app_client.cookies.clear()
    await _login(app_client, email="b@example.com")
    resp = await app_client.get(
        f"/api/pairs/{project_a}/episodic-memory"
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Semantic memory
# ---------------------------------------------------------------------------
async def test_list_semantic_memory_requires_auth(app_client) -> None:
    resp = await app_client.get(
        f"/api/pairs/{uuid.uuid4()}/semantic-memory"
    )
    assert resp.status_code == 401


async def test_list_semantic_memory_happy_path(app_client) -> None:
    user = await _login(app_client)
    project_id = await _seed_project(user)
    await _seed_semantic_rules(
        user_id=user.id,
        project_id=project_id,
        rules=[
            {"rule_type": "entry", "title": "London open", "content": "..."},
            {"rule_type": "risk", "title": "Friday cap", "content": "..."},
        ],
    )

    resp = await app_client.get(
        f"/api/pairs/{project_id}/semantic-memory"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 2
    rule_types = {item["rule_type"] for item in body["items"]}
    assert rule_types == {"entry", "risk"}


async def test_list_semantic_memory_filters_by_rule_type(app_client) -> None:
    user = await _login(app_client)
    project_id = await _seed_project(user)
    await _seed_semantic_rules(
        user_id=user.id,
        project_id=project_id,
        rules=[
            {"rule_type": "entry", "title": "a", "content": "..."},
            {"rule_type": "entry", "title": "b", "content": "..."},
            {"rule_type": "risk", "title": "c", "content": "..."},
        ],
    )

    resp = await app_client.get(
        f"/api/pairs/{project_id}/semantic-memory?rule_type=entry"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 2
    assert all(item["rule_type"] == "entry" for item in body["items"])


async def test_list_semantic_memory_active_false_returns_empty(app_client) -> None:
    """``active=false`` is reserved for a future history surface."""
    user = await _login(app_client)
    project_id = await _seed_project(user)
    await _seed_semantic_rules(
        user_id=user.id,
        project_id=project_id,
        rules=[{"rule_type": "entry", "title": "a", "content": "..."}],
    )

    resp = await app_client.get(
        f"/api/pairs/{project_id}/semantic-memory?active=false"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 0


async def test_list_semantic_memory_cross_tenant_is_404(app_client) -> None:
    user_a = await _login(app_client, email="a@example.com")
    project_a = await _seed_project(user_a)
    await _seed_semantic_rules(
        user_id=user_a.id,
        project_id=project_a,
        rules=[{"rule_type": "entry", "title": "a", "content": "..."}],
    )

    app_client.cookies.clear()
    await _login(app_client, email="b@example.com")
    resp = await app_client.get(
        f"/api/pairs/{project_a}/semantic-memory"
    )
    assert resp.status_code == 404
