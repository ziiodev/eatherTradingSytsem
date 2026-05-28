"""Unit tests for :class:`aether_api.learning.recovery.LearningCache`.

The cache is in-process only (no Redis) and keyed by
``(user_id, project_id)``. These tests cover the five contracted
behaviours:

1. ``get`` miss — empty cache returns ``None``.
2. ``set`` then ``get`` — round-trip recovers the same entry.
3. ``invalidate`` — drops the slot; ``get`` returns ``None``.
4. TTL — entries older than ``LEARNING_CACHE_TTL_SECONDS`` return
   ``None`` from ``get`` (the entry is NOT auto-evicted; warm path
   repopulates).
5. Key isolation — two users with different ``user_id`` cannot see each
   other's slots even if they share a ``project_id`` (defence in depth
   against the type system letting a bare UUID leak).
"""

from __future__ import annotations

import time
import uuid

import pytest
from aether_api.learning.recovery import (
    LearningCache,
    LearningCacheEntry,
)


def _make_entry(*, q: dict[str, object] | None = None) -> LearningCacheEntry:
    return LearningCacheEntry(
        q_table=q if q is not None else {"sk:foo": {"buy": 0.5}},
        episodic_window=[{"id": "e1"}],
        semantic_rules=[{"id": "r1", "body": "rule"}],
        fetched_at=time.monotonic(),
    )


# ---------------------------------------------------------------------------
# Miss / hit / invalidate
# ---------------------------------------------------------------------------


def test_get_returns_none_on_empty_cache() -> None:
    cache = LearningCache()
    assert cache.get(uuid.uuid4(), uuid.uuid4()) is None


def test_set_then_get_round_trips_entry() -> None:
    cache = LearningCache()
    uid = uuid.uuid4()
    pid = uuid.uuid4()
    entry = _make_entry()

    cache.set(uid, pid, entry)

    fetched = cache.get(uid, pid)
    assert fetched is entry
    assert fetched.q_table == {"sk:foo": {"buy": 0.5}}
    assert fetched.episodic_window == [{"id": "e1"}]
    assert fetched.semantic_rules == [{"id": "r1", "body": "rule"}]


def test_invalidate_drops_the_slot() -> None:
    cache = LearningCache()
    uid = uuid.uuid4()
    pid = uuid.uuid4()

    cache.set(uid, pid, _make_entry())
    assert cache.get(uid, pid) is not None

    cache.invalidate(uid, pid)
    assert cache.get(uid, pid) is None


def test_invalidate_missing_slot_is_no_op() -> None:
    cache = LearningCache()
    # No exception.
    cache.invalidate(uuid.uuid4(), uuid.uuid4())


def test_clear_drops_every_entry() -> None:
    cache = LearningCache()
    uid_a, pid_a = uuid.uuid4(), uuid.uuid4()
    uid_b, pid_b = uuid.uuid4(), uuid.uuid4()
    cache.set(uid_a, pid_a, _make_entry())
    cache.set(uid_b, pid_b, _make_entry())

    cache.clear()
    assert cache.get(uid_a, pid_a) is None
    assert cache.get(uid_b, pid_b) is None


# ---------------------------------------------------------------------------
# TTL
# ---------------------------------------------------------------------------


def test_stale_entry_returns_none_on_get(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An entry older than the configured TTL must return ``None``."""
    monkeypatch.setenv("LEARNING_CACHE_TTL_SECONDS", "1")
    cache = LearningCache()
    uid = uuid.uuid4()
    pid = uuid.uuid4()
    # Forge an entry whose ``fetched_at`` is 10 seconds in the past.
    entry = LearningCacheEntry(
        q_table={},
        episodic_window=[],
        semantic_rules=[],
        fetched_at=time.monotonic() - 10.0,
    )
    cache.set(uid, pid, entry)

    assert cache.get(uid, pid) is None


def test_stale_entry_is_not_auto_evicted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stale ``get`` returns ``None`` but the slot stays — the warm path
    will overwrite it, not the read path."""
    monkeypatch.setenv("LEARNING_CACHE_TTL_SECONDS", "1")
    cache = LearningCache()
    uid = uuid.uuid4()
    pid = uuid.uuid4()
    entry = LearningCacheEntry(
        q_table={},
        episodic_window=[],
        semantic_rules=[],
        fetched_at=time.monotonic() - 10.0,
    )
    cache.set(uid, pid, entry)

    assert cache.get(uid, pid) is None
    # Internal store still has the slot.
    assert (uid, pid) in cache._store  # noqa: SLF001 — whitebox check


def test_fresh_entry_is_returned_under_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LEARNING_CACHE_TTL_SECONDS", "300")
    cache = LearningCache()
    uid = uuid.uuid4()
    pid = uuid.uuid4()
    entry = _make_entry()
    cache.set(uid, pid, entry)
    assert cache.get(uid, pid) is entry


def test_invalid_ttl_env_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Garbage in ``LEARNING_CACHE_TTL_SECONDS`` must not break the cache.

    A misconfigured env should silently revert to the documented default
    rather than raise at every ``get`` call.
    """
    monkeypatch.setenv("LEARNING_CACHE_TTL_SECONDS", "not-a-number")
    cache = LearningCache()
    uid = uuid.uuid4()
    pid = uuid.uuid4()
    cache.set(uid, pid, _make_entry())
    # Fresh entry must be readable despite the bad env.
    assert cache.get(uid, pid) is not None


# ---------------------------------------------------------------------------
# Multi-tenant key isolation
# ---------------------------------------------------------------------------


def test_two_users_with_same_project_id_do_not_collide() -> None:
    """Defence in depth: the key includes ``user_id`` so even if two users
    were ever handed the same ``project_id`` (they aren't — UUIDs are
    globally unique) the cache would still keep them apart."""
    cache = LearningCache()
    shared_pid = uuid.uuid4()
    uid_a = uuid.uuid4()
    uid_b = uuid.uuid4()
    entry_a = _make_entry(q={"sk:a": {"buy": 0.1}})
    entry_b = _make_entry(q={"sk:b": {"buy": 0.9}})

    cache.set(uid_a, shared_pid, entry_a)
    cache.set(uid_b, shared_pid, entry_b)

    assert cache.get(uid_a, shared_pid) is entry_a
    assert cache.get(uid_b, shared_pid) is entry_b


def test_invalidate_only_affects_one_user(
) -> None:
    cache = LearningCache()
    shared_pid = uuid.uuid4()
    uid_a = uuid.uuid4()
    uid_b = uuid.uuid4()
    cache.set(uid_a, shared_pid, _make_entry())
    cache.set(uid_b, shared_pid, _make_entry())

    cache.invalidate(uid_a, shared_pid)

    assert cache.get(uid_a, shared_pid) is None
    assert cache.get(uid_b, shared_pid) is not None
