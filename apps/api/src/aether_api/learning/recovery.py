"""In-process learning cache + container-boot recovery loader.

This module is the **read-path substrate** for the learning loop. Two
concerns live side-by-side:

* :class:`LearningCache` — a tiny in-process dict keyed by
  ``(user_id, pair_id)``. Holds the materialised Q-Table, the recent
  episodic window, and the active semantic rules for every "live" pair
  so the Worker / Orquestador hot path never hits Postgres for state it
  already loaded once.

* :func:`warm_caches` (and the underlying :class:`RecoveryLoader`) —
  the lifespan-time loader that pulls each project's persisted learning
  state back into the cache. Runs once per process boot after the sleep
  boot-sweep, before the optional auto-wake.

Single-process assumption
-------------------------

The cache is **in-process only**. We do NOT use Redis or any external
shared cache for v1. The deployment topology is single-process / single-
container per backend instance; horizontal scale-out is explicitly out
of scope. A second backend process would NOT see another's cache and
would have to warm independently — both paths are correct, just not
shared. If the topology ever changes, this is the swap point — add a
``LearningCacheBackend`` protocol and ship a Redis impl.

TTL semantics
-------------

``get`` returns ``None`` when an entry is older than
``LEARNING_CACHE_TTL_SECONDS`` (env, default 300 = 5 minutes). We do NOT
auto-evict stale entries on read — returning ``None`` lets the caller's
warm path repopulate the slot via :func:`warm_caches` or the Sleep Phase
write-through. Eviction-on-read would race with concurrent readers and
add lock contention for no real benefit at this scale.

Failure semantics
-----------------

Per-project warm failures are **isolated**: an exception while loading
one project's state does NOT abort the loader for the others. The
failing project's id appears in :attr:`WarmResult.failed` with the
stringified exception, and the caller (lifespan) is responsible for
flipping that project's status to ``maintenance`` and logging the
incident. Successful projects appear in :attr:`WarmResult.succeeded`.
"""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aether_api.learning.exceptions import RecoveryError
from aether_api.repositories.episodic_memory_repository import EpisodicMemoryRepository
from aether_api.repositories.q_table_repository import QTableRepository
from aether_api.repositories.semantic_memory_repository import SemanticMemoryRepository

__all__ = [
    "DEFAULT_EPISODIC_WINDOW_DAYS",
    "DEFAULT_EPISODIC_WINDOW_LIMIT",
    "DEFAULT_TTL_SECONDS",
    "LearningCache",
    "LearningCacheEntry",
    "RecoveryLoader",
    "WarmResult",
    "warm_caches",
]


# ---------------------------------------------------------------------------
# Defaults — overridable via env where the spec says so.
# ---------------------------------------------------------------------------

#: Cache entry lifetime (seconds). Override via ``LEARNING_CACHE_TTL_SECONDS``.
DEFAULT_TTL_SECONDS: int = 300

#: How many recent episodes to load into each project's cache.
#: Override via ``LEARNING_EPISODIC_WINDOW``.
DEFAULT_EPISODIC_WINDOW_LIMIT: int = 200

#: Lookback window for the episodic warm load (days). Hard-coded to 7d —
#: matches the canonical spec (#2069) and there is no env knob today.
DEFAULT_EPISODIC_WINDOW_DAYS: int = 7


def _ttl_seconds() -> int:
    """Read the cache TTL from env each call (cheap, test-friendly)."""
    raw = os.environ.get("LEARNING_CACHE_TTL_SECONDS")
    if raw is None:
        return DEFAULT_TTL_SECONDS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_TTL_SECONDS
    if value <= 0:
        return DEFAULT_TTL_SECONDS
    return value


def _episodic_window_limit() -> int:
    """Read the episodic window limit from env each call."""
    raw = os.environ.get("LEARNING_EPISODIC_WINDOW")
    if raw is None:
        return DEFAULT_EPISODIC_WINDOW_LIMIT
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_EPISODIC_WINDOW_LIMIT
    if value <= 0:
        return DEFAULT_EPISODIC_WINDOW_LIMIT
    return value


# ---------------------------------------------------------------------------
# Cache entry.
# ---------------------------------------------------------------------------


@dataclass
class LearningCacheEntry:
    """Materialised learning state for a single project.

    All three sub-collections are plain Python primitives — the cache
    NEVER hands out SQLAlchemy entities, so callers can mutate the
    payload without dragging a session along.

    ``q_table`` is ``None`` when the project has no Q-Table version
    yet (fresh project, never gone through a Sleep Phase). Callers
    SHOULD treat that as "no policy learned yet" and fall back to
    the project's default action.
    """

    q_table: dict[str, Any] | None
    episodic_window: list[dict[str, Any]]
    semantic_rules: list[dict[str, Any]]
    fetched_at: float


# ---------------------------------------------------------------------------
# Cache.
# ---------------------------------------------------------------------------


class LearningCache:
    """In-process cache keyed by ``(user_id, pair_id)``.

    The cache is plain-dict-simple on purpose — no LRU, no size cap.
    A process holds at most one entry per active project; entries are
    ~hundreds of KiB each, so even 100 projects fit comfortably in RAM.
    """

    def __init__(self) -> None:
        # The tuple key keeps per-tenant isolation explicit: even if two
        # users happen to share a ``pair_id`` (they can't, but the
        # type system doesn't know), their cache slots stay distinct.
        self._store: dict[tuple[uuid.UUID, uuid.UUID], LearningCacheEntry] = {}

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    def get(
        self, user_id: uuid.UUID, pair_id: uuid.UUID
    ) -> LearningCacheEntry | None:
        """Return the entry for ``(user_id, pair_id)`` if fresh, else ``None``.

        Stale entries (older than :func:`_ttl_seconds`) are NOT evicted
        here — we return ``None`` and let the caller's warm path
        repopulate. Eviction-on-read would race against concurrent
        readers; the warm path will overwrite the slot anyway.
        """
        entry = self._store.get((user_id, pair_id))
        if entry is None:
            return None
        if (time.monotonic() - entry.fetched_at) > _ttl_seconds():
            return None
        return entry

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------
    def set(
        self,
        user_id: uuid.UUID,
        pair_id: uuid.UUID,
        entry: LearningCacheEntry,
    ) -> None:
        """Insert / overwrite the entry for ``(user_id, pair_id)``."""
        self._store[(user_id, pair_id)] = entry

    def invalidate(self, user_id: uuid.UUID, pair_id: uuid.UUID) -> None:
        """Drop the entry for ``(user_id, pair_id)`` if present.

        Idempotent — invalidating a missing slot is a no-op (no
        exception). Called by the Sleep Phase write-through whenever
        a Q-Table promotion or semantic supersession fires for the
        project.
        """
        self._store.pop((user_id, pair_id), None)

    def clear(self) -> None:
        """Drop every entry. Mostly useful in tests; production callers
        should prefer :meth:`invalidate` per project."""
        self._store.clear()


# ---------------------------------------------------------------------------
# Warm result envelope.
# ---------------------------------------------------------------------------


@dataclass
class WarmResult:
    """Outcome of one :func:`warm_caches` invocation.

    ``succeeded`` and ``failed`` are mutually exclusive — a pair_id
    appears in exactly one of them.
    """

    succeeded: dict[uuid.UUID, LearningCacheEntry] = field(default_factory=dict)
    failed: dict[uuid.UUID, str] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return len(self.succeeded) + len(self.failed)


# ---------------------------------------------------------------------------
# Recovery loader.
# ---------------------------------------------------------------------------


SessionFactory = Callable[[], AsyncSession] | async_sessionmaker[AsyncSession]


class RecoveryLoader:
    """Pull persisted learning state for a set of projects into the cache.

    Stateless — the only state is the references passed in
    (``session_factory``, ``cache``). The class form exists so tests
    can subclass / monkey-patch individual repo calls in isolation;
    feature code SHOULD prefer the :func:`warm_caches` free function.
    """

    def __init__(
        self,
        session_factory: SessionFactory,
        cache: LearningCache,
    ) -> None:
        self._session_factory = session_factory
        self._cache = cache

    async def warm(
        self,
        pair_ids_with_owner: list[tuple[uuid.UUID, uuid.UUID]],
    ) -> WarmResult:
        """Warm one entry per ``(user_id, pair_id)`` pair.

        Per-project failures are caught and recorded — they NEVER
        abort the loop. The caller (lifespan) is responsible for
        translating ``WarmResult.failed`` into project status updates.
        """
        result = WarmResult()
        for user_id, pair_id in pair_ids_with_owner:
            try:
                entry = await self._warm_one(user_id, pair_id)
            except Exception as exc:  # noqa: BLE001 — per-project isolation.
                # Wrap into the canonical RecoveryError so tests can
                # introspect the message shape if needed, but record
                # the stringified form on WarmResult.failed.
                wrapped = RecoveryError(
                    f"warm failed for project {pair_id} (user {user_id}): {exc}"
                )
                result.failed[pair_id] = str(wrapped)
                continue
            self._cache.set(user_id, pair_id, entry)
            result.succeeded[pair_id] = entry
        return result

    async def _warm_one(
        self, user_id: uuid.UUID, pair_id: uuid.UUID
    ) -> LearningCacheEntry:
        """Materialise one cache entry from the three repositories.

        Each repo call uses its own short-lived session. The boot path
        is sequential per project — concurrency here would just dogpile
        the same connection pool and add little.
        """
        now = datetime.now(tz=UTC).replace(tzinfo=None)
        since = now - timedelta(days=DEFAULT_EPISODIC_WINDOW_DAYS)
        episodic_limit = _episodic_window_limit()

        q_payload: dict[str, Any] | None = None
        episodic_window: list[dict[str, Any]] = []
        semantic_rules: list[dict[str, Any]] = []

        # --- Q-Table (latest version) ----------------------------------
        async with self._open_session() as session:
            q_repo = QTableRepository(session)
            q_row = await q_repo.get_latest(user_id=user_id, project_id=pair_id)
            if q_row is not None:
                # Strip the metadata-stash key (defensive — Worker code
                # has no business reading __meta__).
                payload = dict(q_row.table_data or {})
                payload.pop("__meta__", None)
                q_payload = payload

        # --- Episodic window (recent 7d, capped) -----------------------
        async with self._open_session() as session:
            epi_repo = EpisodicMemoryRepository(session)
            episodes = await epi_repo.list_by_project(
                user_id=user_id,
                project_id=pair_id,
                since=since,
                until=now,
                limit=episodic_limit,
            )
            episodic_window = [_episode_to_dict(e) for e in episodes]

        # --- Semantic rules (all currently active) ---------------------
        async with self._open_session() as session:
            sem_repo = SemanticMemoryRepository(session)
            rules = await sem_repo.list_active(
                user_id=user_id, project_id=pair_id
            )
            semantic_rules = [_rule_to_dict(r) for r in rules]

        return LearningCacheEntry(
            q_table=q_payload,
            episodic_window=episodic_window,
            semantic_rules=semantic_rules,
            fetched_at=time.monotonic(),
        )

    # ------------------------------------------------------------------
    # Session helper — supports both `async_sessionmaker` and a plain
    # zero-arg callable returning an AsyncSession (the test path).
    # ------------------------------------------------------------------
    def _open_session(self) -> _SessionCtx:
        return _SessionCtx(self._session_factory)


class _SessionCtx:
    """Tiny ``async with`` wrapper around the session factory.

    Accepts either an ``async_sessionmaker`` (production path) or a
    zero-arg callable that returns an ``AsyncSession`` directly — the
    second form lets tests inject a hand-rolled session without
    instantiating the full SQLAlchemy machinery.
    """

    def __init__(self, factory: SessionFactory) -> None:
        self._factory = factory
        self._session: AsyncSession | None = None

    async def __aenter__(self) -> AsyncSession:
        candidate = self._factory()
        # ``async_sessionmaker.__call__`` returns an ``AsyncSession``
        # directly, but tests may pass an async-callable that needs to
        # be awaited.
        if isinstance(candidate, Awaitable):
            session: AsyncSession = await candidate
        else:
            session = candidate
        self._session = session
        # If the session supports the async-context-manager protocol
        # (the real one does), enter it so it auto-closes on exit.
        if hasattr(session, "__aenter__"):
            await session.__aenter__()
        return session

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object,
    ) -> None:
        session = self._session
        if session is None:
            return
        if hasattr(session, "__aexit__"):
            await session.__aexit__(exc_type, exc, tb)
        else:
            await session.close()


# ---------------------------------------------------------------------------
# Functional facade.
# ---------------------------------------------------------------------------


async def warm_caches(
    session_factory: SessionFactory,
    cache: LearningCache,
    pair_ids_with_owner: list[tuple[uuid.UUID, uuid.UUID]],
) -> WarmResult:
    """Warm ``cache`` for every ``(user_id, pair_id)`` pair.

    Thin functional facade over :class:`RecoveryLoader`. Same per-project
    isolation contract: one project failing does NOT prevent the others
    from warming. Returns the :class:`WarmResult` envelope; callers
    decide what to do with ``failed`` entries.
    """
    loader = RecoveryLoader(session_factory, cache)
    return await loader.warm(pair_ids_with_owner)


# ---------------------------------------------------------------------------
# Internal helpers — ORM → dict conversion. Kept private so the cache
# entry never carries SQLAlchemy state.
# ---------------------------------------------------------------------------


def _episode_to_dict(episode: Any) -> dict[str, Any]:
    """Project an :class:`EpisodicMemory` ORM row to a plain dict."""
    return {
        "id": str(episode.id),
        "state_key": episode.state_key,
        "action": episode.action,
        "reward": str(episode.reward),
        "next_state_key": episode.next_state_key,
        "order_id": str(episode.order_id) if episode.order_id else None,
        "created_at": episode.created_at.isoformat() if episode.created_at else None,
        "meta": dict(episode.meta_data or {}),
    }


def _rule_to_dict(rule: Any) -> dict[str, Any]:
    """Project a :class:`SemanticMemory` ORM row to a plain dict."""
    return {
        "id": str(rule.id),
        "rule_type": rule.rule_type,
        "body": rule.body,
        "payload": dict(rule.payload or {}),
        "active": bool(rule.active),
        "created_at": rule.created_at.isoformat() if rule.created_at else None,
    }
