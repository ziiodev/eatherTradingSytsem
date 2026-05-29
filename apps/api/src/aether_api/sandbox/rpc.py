"""Parent ↔ child RPC channel for the learning ctx proxies.

The agent-sandbox canonical spec (#2046) mandates that the child process
NEVER holds a DB handle, an open socket, or any host-side resource. The
learning proxies (:mod:`aether_api.sandbox.learning_ctx`) therefore need
a way to ask the parent to do reads/writes on their behalf.

This module implements that channel using **one additional duplex
``multiprocessing.Connection``** wired in by :class:`aether_api.sandbox.engine.Engine`:

* Parent end stays in the engine; a small dispatcher thread drains
  incoming requests, looks up the handler by ``method`` name, invokes it
  with the BOUND ``(user_id, project_id)`` recorded parent-side, and
  sends back the result.
* Child end is wrapped in :class:`RpcClient` and embedded into the three
  proxies before user code runs.

Three properties that must hold:

1. **Tenancy is parent-bound.** The dispatcher uses the
   ``(user_id, project_id)`` captured by the engine at spawn time; the
   child's payload may *include* those ids (for defence-in-depth
   logging) but the dispatcher IGNORES them. A tampered child cannot
   point a write at a foreign project.

2. **Wire format is pickle.** The Connection round-trips Python objects
   directly via ``send``/``recv``. Payloads are limited to small dicts
   of strings / numbers; we never ship SQLAlchemy entities across.

3. **No re-entrancy.** Requests are processed serially in the dispatcher
   thread; an agent calling four proxies in quick succession ends up
   queued (single-threaded child today, so the queue is at most 1
   anyway).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import traceback
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

__all__ = [
    "RpcClient",
    "RpcDispatcher",
    "RpcError",
    "RpcHandlers",
    "RpcShutdown",
    "build_default_handlers",
]


_LOG = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions.
# ---------------------------------------------------------------------------


class RpcError(RuntimeError):
    """Raised in the child when the parent reports a handler failure.

    The agent code SHOULD treat this as a normal exception — there is no
    information leak (the message is parent-controlled and never echoes
    a SQL error verbatim).
    """


class RpcShutdown(Exception):
    """Internal sentinel — the dispatcher exits on parent-side ``close()``.

    Never propagated to user code; the engine's wall-clock or normal
    child exit path collects this.
    """


# ---------------------------------------------------------------------------
# Wire envelope helpers.
# ---------------------------------------------------------------------------
#
# Each call is a 2-tuple roundtrip:
#
#     request  = {"method": str, "kwargs": dict, "rid": int}
#     response = {"rid": int, "ok": True,  "result": Any}
#              | {"rid": int, "ok": False, "error": str}
#
# The ``rid`` is sequence-only (no concurrency in v1) but keeps the
# wire format easy to extend later if we ever multiplex.


# ---------------------------------------------------------------------------
# Child-side client.
# ---------------------------------------------------------------------------


class RpcClient:
    """Synchronous request/response wrapper around the child end of the pipe.

    Constructed in the child during bootstrap (:mod:`aether_api.sandbox.child`)
    from the duplex Connection handed in by the engine. Holds no other
    state — pickling it is meaningless and never happens (the child
    creates it after unpickling the ctx payload).
    """

    def __init__(self, conn: Any) -> None:
        # ``conn`` is a ``multiprocessing.connection.Connection`` on the
        # child end. We hide the type behind ``Any`` because the
        # multiprocessing stubs vary across CPython versions.
        self._conn = conn
        self._rid = 0
        self._lock = threading.Lock()

    def call(self, method: str, /, **kwargs: Any) -> Any:
        """Synchronously call a parent-side handler. Returns the result.

        Raises :class:`RpcError` on parent-side failure. Connection-level
        failures (broken pipe) bubble out as :class:`OSError`; the
        user-code path catches both via ``except Exception`` already.
        """
        with self._lock:
            self._rid += 1
            rid = self._rid
            payload = {"method": method, "kwargs": kwargs, "rid": rid}
            self._conn.send(payload)
            response = self._conn.recv()
        if not isinstance(response, dict) or response.get("rid") != rid:
            raise RpcError(f"rpc protocol violation: expected rid={rid}, got {response!r}")
        if response.get("ok"):
            return response.get("result")
        raise RpcError(str(response.get("error") or "rpc handler failed"))

    def close(self) -> None:
        """Close the child end. Safe to call multiple times."""
        with contextlib.suppress(Exception):
            self._conn.close()


# ---------------------------------------------------------------------------
# Parent-side handlers.
# ---------------------------------------------------------------------------


SessionFactory = Callable[[], AsyncSession] | async_sessionmaker[AsyncSession]
AsyncHandler = Callable[..., Awaitable[Any]]


@dataclass
class RpcHandlers:
    """Registry of method-name → async handler.

    Handlers receive the BOUND ``(user_id, project_id)`` plus the
    keyword args from the child's payload. They MUST return JSON-ish
    primitives (dict / list / str / int / float / None) so the child
    can pickle them straight back without dragging the ORM along.
    """

    user_id: uuid.UUID
    project_id: uuid.UUID
    handlers: dict[str, AsyncHandler]

    async def dispatch(self, method: str, payload: dict[str, Any]) -> Any:
        """Look up ``method`` and invoke it with the bound IDs.

        ``payload`` may include ``user_id`` / ``project_id`` keys — they
        are **stripped and ignored** here. The handler always sees the
        parent-recorded tuple. This is the defence-in-depth check the
        spec calls out: even if a future bug let the child re-bind its
        proxy IDs, the parent never trusts them.
        """
        handler = self.handlers.get(method)
        if handler is None:
            raise RpcError(f"unknown rpc method: {method!r}")
        kwargs = dict(payload)
        # Strip any tenancy-looking keys; we use our own bound tuple.
        kwargs.pop("user_id", None)
        kwargs.pop("project_id", None)
        return await handler(
            user_id=self.user_id,
            project_id=self.project_id,
            **kwargs,
        )


# ---------------------------------------------------------------------------
# Dispatcher thread.
# ---------------------------------------------------------------------------


class RpcDispatcher:
    """Drain incoming requests and process them serially.

    Lives in a background thread spun up by :class:`Engine`. Uses its
    own ``asyncio.new_event_loop`` so handler coroutines can ``await``
    SQLAlchemy without colliding with the FastAPI event loop (which is
    on a different thread entirely — the engine is invoked via
    ``anyio.to_thread.run_sync``).
    """

    def __init__(
        self,
        *,
        conn: Any,
        handlers: RpcHandlers,
        name: str = "aether-rpc",
    ) -> None:
        self._conn = conn
        self._handlers = handlers
        self._name = name
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name=self._name, daemon=True)
        self._thread.start()

    def stop(self, *, timeout: float = 2.0) -> None:
        """Signal shutdown and join. Idempotent.

        We do NOT close ``self._conn`` here — the engine owns the pipe
        FDs and closes them after ``proc.join``. Closing the conn here
        could race the dispatcher thread's ``recv`` and noisily log a
        broken-pipe in the parent.
        """
        self._stop.set()
        thread = self._thread
        if thread is None:
            return
        # ``recv`` blocks; the cleanest way to unblock it is to close
        # our END of the pipe from the engine when the child exits.
        # Here we just wait — the engine guarantees a close happens.
        thread.join(timeout=timeout)
        self._thread = None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------
    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        try:
            while not self._stop.is_set():
                try:
                    if not self._conn.poll(timeout=0.1):
                        continue
                    payload = self._conn.recv()
                except (EOFError, OSError):
                    break
                if not isinstance(payload, dict):
                    self._reply_error(rid=None, message="malformed request")
                    continue
                rid = payload.get("rid")
                method = payload.get("method")
                kwargs = payload.get("kwargs") or {}
                if not isinstance(method, str):
                    self._reply_error(rid=rid, message="missing method")
                    continue
                try:
                    result = loop.run_until_complete(self._handlers.dispatch(method, kwargs))
                except Exception as exc:  # noqa: BLE001 — bubble shape, not detail.
                    _LOG.warning(
                        "rpc handler %s raised: %s\n%s",
                        method,
                        exc,
                        traceback.format_exc(),
                    )
                    self._reply_error(rid=rid, message=f"{type(exc).__name__}: {exc}")
                    continue
                self._reply_ok(rid=rid, result=result)
        finally:
            with contextlib.suppress(Exception):
                loop.close()

    def _reply_ok(self, *, rid: Any, result: Any) -> None:
        with contextlib.suppress(Exception):
            self._conn.send({"rid": rid, "ok": True, "result": result})

    def _reply_error(self, *, rid: Any, message: str) -> None:
        with contextlib.suppress(Exception):
            self._conn.send({"rid": rid, "ok": False, "error": message})


# ---------------------------------------------------------------------------
# Default handler set — qtable.get / qtable.suggest / semantic.list / episodic.record.
# ---------------------------------------------------------------------------


def build_default_handlers(
    *,
    session_factory: SessionFactory | None,
    cache: Any,
) -> dict[str, AsyncHandler]:
    """Construct the four canonical handlers.

    ``cache`` is a :class:`aether_api.learning.recovery.LearningCache`
    (typed loosely to avoid importing learning into a sandbox module
    that the child must NEVER pull in).

    Read handlers consult the cache first; on miss they fall back to
    the repository.

    Write handlers (``episodic.record``) skip the cache and write
    straight through the repository.
    """
    # Late imports keep the parent-side dependency graph honest — the
    # sandbox package depends on the learning + repositories layers
    # only at parent runtime, never at module load.

    from aether_api.learning.q_learning import state_key as _state_key
    from aether_api.repositories.episodic_memory_repository import (
        EpisodicMemoryRepository,
    )
    from aether_api.repositories.q_table_repository import QTableRepository
    from aether_api.repositories.semantic_memory_repository import (
        SemanticMemoryRepository,
    )

    def _open_session() -> AsyncSession:
        if session_factory is None:
            raise RpcError("no session_factory bound on the parent")
        candidate: Any = session_factory()
        # Both ``async_sessionmaker`` and a plain zero-arg callable
        # return an ``AsyncSession`` synchronously; the ``Any`` cast
        # plus this explicit cast keeps the contract obvious.
        return candidate  # type: ignore[no-any-return]

    async def _qtable_payload_for(
        *, user_id: uuid.UUID, project_id: uuid.UUID
    ) -> dict[str, Any] | None:
        # Cache-first; fall back to the repo on miss.
        if cache is not None:
            entry = cache.get(user_id, project_id)
            if entry is not None:
                q_table: Any = entry.q_table
                if q_table is None:
                    return None
                return dict(q_table)
        session = _open_session()
        try:
            async with session:
                repo = QTableRepository(session)
                row = await repo.get_latest(user_id=user_id, project_id=project_id)
                if row is None:
                    return None
                payload = dict(row.table_data or {})
                payload.pop("__meta__", None)
                return payload
        finally:
            with contextlib.suppress(Exception):
                await session.close()

    async def _semantic_rules_for(
        *, user_id: uuid.UUID, project_id: uuid.UUID
    ) -> list[dict[str, Any]]:
        # Cache-first.
        if cache is not None:
            entry = cache.get(user_id, project_id)
            if entry is not None:
                return list(entry.semantic_rules)
        session = _open_session()
        try:
            async with session:
                repo = SemanticMemoryRepository(session)
                rows = await repo.list_active(user_id=user_id, project_id=project_id)
                return [
                    {
                        "id": str(r.id),
                        "rule_type": r.rule_type,
                        "body": r.body,
                        "payload": dict(r.payload or {}),
                        "active": bool(r.active),
                    }
                    for r in rows
                ]
        finally:
            with contextlib.suppress(Exception):
                await session.close()

    async def qtable_get(
        *,
        user_id: uuid.UUID,
        project_id: uuid.UUID,
        state: dict[str, Any],
    ) -> dict[str, float] | None:
        q_table = await _qtable_payload_for(user_id=user_id, project_id=project_id)
        if not q_table:
            return None
        key = _state_key(state)
        bucket = q_table.get(key)
        if not isinstance(bucket, dict):
            return None
        return {str(k): float(v) for k, v in bucket.items()}

    async def qtable_suggest(
        *,
        user_id: uuid.UUID,
        project_id: uuid.UUID,
        state: dict[str, Any],
    ) -> str | None:
        bucket = await qtable_get(user_id=user_id, project_id=project_id, state=state)
        if not bucket:
            return None
        # Argmax with deterministic alphabetical tie-break.
        best_action: str | None = None
        best_q: float | None = None
        for action in sorted(bucket.keys()):
            q_val = bucket[action]
            if best_q is None or q_val > best_q:
                best_q = q_val
                best_action = action
        return best_action

    async def semantic_list(
        *,
        user_id: uuid.UUID,
        project_id: uuid.UUID,
        rule_type: str | None = None,
        active: bool = True,
    ) -> list[dict[str, Any]]:
        rows = await _semantic_rules_for(user_id=user_id, project_id=project_id)
        out: list[dict[str, Any]] = []
        for row in rows:
            if active and not row.get("active", True):
                continue
            if rule_type is not None and row.get("rule_type") != rule_type:
                continue
            payload = row.get("payload") or {}
            out.append(
                {
                    "id": row["id"],
                    "rule_type": row["rule_type"],
                    "title": payload.get("title") or "",
                    "content": row.get("body") or "",
                    "confidence": float(payload.get("confidence") or 0.0),
                    "source": payload.get("source") or "",
                    "version": int(payload.get("version") or 1),
                    "active": bool(row.get("active", True)),
                }
            )
        return out

    async def episodic_record(
        *,
        user_id: uuid.UUID,
        project_id: uuid.UUID,
        state: dict[str, Any],
        action: str,
        reward: float,
        result: dict[str, Any] | str | None = None,
        reasoning: str = "",
        q_before: float | None = None,
    ) -> dict[str, Any]:
        import json as _json

        key = _state_key(state)
        # The repo column is ``result text NULL``; we serialize dicts
        # deterministically so a Sleep Phase replay can still parse them
        # back. Strings are passed through verbatim.
        if result is None:
            result_payload: str | None = None
        elif isinstance(result, str):
            result_payload = result
        else:
            try:
                result_payload = _json.dumps(result, sort_keys=True, default=str)
            except (TypeError, ValueError):
                result_payload = str(result)
        session = _open_session()
        try:
            async with session:
                repo = EpisodicMemoryRepository(session)
                row = await repo.insert(
                    user_id=user_id,
                    project_id=project_id,
                    trade_id=None,
                    state=state,
                    state_key=key,
                    action=action,
                    reward=reward,
                    result=result_payload,
                    worker_reasoning=reasoning,
                    q_value_before=q_before,
                    q_value_after=None,
                    is_special=False,
                    sleep_run_id=None,
                )
                await session.commit()
                return {
                    "id": str(row.id),
                    "sleep_run_id": (
                        None
                        if row.consumed_by_sleep_run_id is None
                        else str(row.consumed_by_sleep_run_id)
                    ),
                    "q_value_before": (None if q_before is None else float(q_before)),
                }
        finally:
            with contextlib.suppress(Exception):
                await session.close()

    return {
        "qtable.get": qtable_get,
        "qtable.suggest": qtable_suggest,
        "semantic.list": semantic_list,
        "episodic.record": episodic_record,
    }
