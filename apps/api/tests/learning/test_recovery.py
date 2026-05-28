"""Tests for :func:`aether_api.learning.recovery.warm_caches`.

The loader has two contracts these tests pin down:

1. **Full success path** — every project warms; ``WarmResult.succeeded``
   has one entry per project, the cache is populated, ``failed`` is
   empty.
2. **Partial failure path** — when one project's Q-Table fetch raises,
   that project lands in ``WarmResult.failed`` with a stringified
   :class:`RecoveryError`-derived message; the other project still
   warms, lands in ``WarmResult.succeeded``, and the cache is
   populated for the healthy project but NOT for the failing one.

Repository calls are stubbed via monkeypatch so the test does NOT need
a live Postgres — the seam is at the repo method level (``get_latest``,
``list_by_project``, ``list_active``). This isolates the loader's
control flow from the data layer.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from aether_api.learning.recovery import (
    LearningCache,
    warm_caches,
)
from aether_api.repositories.episodic_memory_repository import (
    EpisodicMemoryRepository,
)
from aether_api.repositories.q_table_repository import QTableRepository
from aether_api.repositories.semantic_memory_repository import (
    SemanticMemoryRepository,
)

# ---------------------------------------------------------------------------
# Lightweight ORM-row stand-ins. Mimic the attributes the loader reads.
# ---------------------------------------------------------------------------


@dataclass
class _FakeQTable:
    table_data: dict[str, Any]


@dataclass
class _FakeEpisode:
    id: uuid.UUID
    state_key: str
    action: str
    reward: Decimal
    next_state_key: str | None
    order_id: uuid.UUID | None
    created_at: datetime
    meta_data: dict[str, Any]


@dataclass
class _FakeRule:
    id: uuid.UUID
    rule_type: str
    body: str
    payload: dict[str, Any]
    active: bool
    created_at: datetime


# ---------------------------------------------------------------------------
# A minimal "session" — the loader only uses it to instantiate repos,
# which we monkey-patch wholesale.
# ---------------------------------------------------------------------------


class _NullSession:
    """Stand-in async session — the loader never touches it directly."""

    async def __aenter__(self) -> _NullSession:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def close(self) -> None:
        return None


def _session_factory() -> _NullSession:
    return _NullSession()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def project_pairs() -> list[tuple[uuid.UUID, uuid.UUID]]:
    """Two ``(user_id, project_id)`` pairs — one healthy, one failing."""
    uid = uuid.uuid4()
    return [(uid, uuid.uuid4()), (uid, uuid.uuid4())]


# ---------------------------------------------------------------------------
# Stubbed repo factories.
# ---------------------------------------------------------------------------


def _install_healthy_repo_stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every repo call succeed with a deterministic payload."""

    async def fake_get_latest(self: Any, **_: Any) -> _FakeQTable:
        return _FakeQTable(
            table_data={
                "sk:happy": {"buy": 0.42, "sell": -0.1},
                "__meta__": {"source": "unit-test"},
            }
        )

    async def fake_list_by_project(self: Any, **_: Any) -> list[_FakeEpisode]:
        return [
            _FakeEpisode(
                id=uuid.uuid4(),
                state_key="sk:happy",
                action="buy",
                reward=Decimal("0.5"),
                next_state_key=None,
                order_id=None,
                created_at=datetime.now(tz=UTC).replace(tzinfo=None),
                meta_data={"is_special": False},
            )
        ]

    async def fake_list_active(self: Any, **_: Any) -> list[_FakeRule]:
        return [
            _FakeRule(
                id=uuid.uuid4(),
                rule_type="entry",
                body="Buy on green doji",
                payload={"title": "doji", "confidence": 0.8, "source": "test"},
                active=True,
                created_at=datetime.now(tz=UTC).replace(tzinfo=None),
            )
        ]

    monkeypatch.setattr(QTableRepository, "get_latest", fake_get_latest)
    monkeypatch.setattr(
        EpisodicMemoryRepository, "list_by_project", fake_list_by_project
    )
    monkeypatch.setattr(SemanticMemoryRepository, "list_active", fake_list_active)


def _install_failing_q_repo_for(
    monkeypatch: pytest.MonkeyPatch,
    failing_project_id: uuid.UUID,
) -> None:
    """Patch ``QTableRepository.get_latest`` to raise for ONE project_id only.

    Episodic and semantic repos stay healthy — so we prove the loader's
    failure isolation kicks in even when only one of the three repo
    calls is broken for a project.
    """

    async def fake_get_latest(
        self: Any, *, user_id: uuid.UUID, project_id: uuid.UUID
    ) -> _FakeQTable | None:
        if project_id == failing_project_id:
            raise RuntimeError(
                f"injected q-table fetch failure for {project_id}"
            )
        return _FakeQTable(table_data={"sk:ok": {"buy": 0.1}})

    async def fake_list_by_project(self: Any, **_: Any) -> list[_FakeEpisode]:
        return []

    async def fake_list_active(self: Any, **_: Any) -> list[_FakeRule]:
        return []

    monkeypatch.setattr(QTableRepository, "get_latest", fake_get_latest)
    monkeypatch.setattr(
        EpisodicMemoryRepository, "list_by_project", fake_list_by_project
    )
    monkeypatch.setattr(SemanticMemoryRepository, "list_active", fake_list_active)


# ---------------------------------------------------------------------------
# Full-success path
# ---------------------------------------------------------------------------


async def test_warm_caches_full_success_two_projects(
    monkeypatch: pytest.MonkeyPatch,
    project_pairs: list[tuple[uuid.UUID, uuid.UUID]],
) -> None:
    _install_healthy_repo_stubs(monkeypatch)
    cache = LearningCache()

    result = await warm_caches(_session_factory, cache, project_pairs)

    # Both projects warmed; no failures.
    assert len(result.succeeded) == 2
    assert result.failed == {}
    for uid, pid in project_pairs:
        entry = cache.get(uid, pid)
        assert entry is not None
        # ``__meta__`` is stripped before reaching the cache so worker
        # code can't accidentally read seeder-internal state.
        assert entry.q_table == {"sk:happy": {"buy": 0.42, "sell": -0.1}}
        assert len(entry.episodic_window) == 1
        assert entry.episodic_window[0]["state_key"] == "sk:happy"
        assert len(entry.semantic_rules) == 1
        assert entry.semantic_rules[0]["rule_type"] == "entry"
        # ``fetched_at`` is set to monotonic-now — must be in the past.
        assert entry.fetched_at <= time.monotonic()


# ---------------------------------------------------------------------------
# Partial-failure path
# ---------------------------------------------------------------------------


async def test_warm_caches_one_project_fails_others_still_warm(
    monkeypatch: pytest.MonkeyPatch,
    project_pairs: list[tuple[uuid.UUID, uuid.UUID]],
) -> None:
    healthy_uid, healthy_pid = project_pairs[0]
    failing_uid, failing_pid = project_pairs[1]
    _install_failing_q_repo_for(monkeypatch, failing_pid)

    cache = LearningCache()
    result = await warm_caches(_session_factory, cache, project_pairs)

    # Healthy project landed in succeeded + cache; failing project did NOT.
    assert healthy_pid in result.succeeded
    assert failing_pid not in result.succeeded
    assert failing_pid in result.failed
    assert healthy_pid not in result.failed
    assert "injected q-table fetch failure" in result.failed[failing_pid]

    # Cache reflects the split.
    assert cache.get(healthy_uid, healthy_pid) is not None
    assert cache.get(failing_uid, failing_pid) is None


async def test_warm_caches_empty_input_returns_empty_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty pair list must NOT touch the repos and returns an empty result."""
    # No stubs — if the loader accidentally hit a real session it would blow up.
    cache = LearningCache()
    result = await warm_caches(_session_factory, cache, [])
    assert result.succeeded == {}
    assert result.failed == {}
    assert result.total == 0


async def test_warm_caches_all_projects_failing(
    monkeypatch: pytest.MonkeyPatch,
    project_pairs: list[tuple[uuid.UUID, uuid.UUID]],
) -> None:
    """When every project's Q-Table fetch fails, every project lands in
    ``failed`` and ``succeeded`` stays empty."""

    async def always_fail(self: Any, **_: Any) -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr(QTableRepository, "get_latest", always_fail)

    cache = LearningCache()
    result = await warm_caches(_session_factory, cache, project_pairs)

    assert result.succeeded == {}
    assert len(result.failed) == len(project_pairs)
    for _, pid in project_pairs:
        assert pid in result.failed
