"""Sandbox engine — parent-side orchestrator.

Public surface:

* :class:`Engine.run_agent` — spawn a child, pipe the ctx, wait for
  result, finalise the audit row, return :class:`EngineResult`.

Implementation notes:

* We use ``multiprocessing.get_context("spawn")`` — NOT ``fork``.
  ``fork`` would inherit all parent FDs (DB pool, request socket) into
  the child, defeating the entire boundary.
* We pass two raw pipe FDs into the child via ``Process(args=...)`` —
  one for inbound payload, one for outbound result. The child closes
  both at completion, so EOF on the read side is our "child is done"
  signal.
* Wall-clock deadline is parent-monitored: we ``proc.join(timeout)`` then
  ``proc.kill()`` (SIGKILL) on expiry. RLIMIT_CPU in the child catches
  the CPU-bound case; the wall-clock deadline catches blocking I/O.
* We never let an exception inside the engine bubble out without
  finalising the agent_runs row — operators rely on the audit trail to
  diagnose engine bugs themselves.
"""

from __future__ import annotations

import multiprocessing
import os
import pickle
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from aether_api.core.settings import get_settings
from aether_api.sandbox.ctx import AgentContext, McpEndpoint
from aether_api.sandbox.rpc import RpcDispatcher, RpcHandlers, build_default_handlers


def _learning_enabled_from_env() -> bool:
    """Return whether the sleep-learning loop is enabled for this process.

    Phase 11 of ``sdd/sleep-learning-loop`` promoted the underlying
    ``AETHER_LEARNING_ENABLED`` env var to a formal pydantic setting; the
    function name is kept for backwards compatibility with the existing
    callers in this module.
    """
    return get_settings().learning_enabled


def _operativa_enabled_from_env() -> bool:
    """Return whether the Operativa write proxy is enabled for this process.

    Phase 2 of ``sdd/project-operativa`` introduced the
    ``AETHER_OPERATIVA_PROXY_ENABLED`` env var (default ``true``) — when
    True the child binds a live :class:`OrdersProxy`; when False it
    binds :class:`NoopOrders` instead. Settings-backed so tests can
    monkeypatch + invalidate the cache the same way they do for
    ``learning_enabled``.
    """
    return get_settings().operativa_proxy_enabled


#: Hard wall-clock deadline. RLIMIT_CPU=10s catches CPU; the 15s
#: wall-clock catches sleeps / blocking I/O.
DEFAULT_WALL_CLOCK_SECONDS = 15.0

#: Tail-truncate captured streams before persistence. Mostly a safety
#: belt against a user filling the audit table with megabytes of
#: ``print`` spam.
STREAM_TAIL_LIMIT = 64 * 1024  # 64 KiB


@dataclass
class EngineResult:
    """What :meth:`Engine.run_agent` returns to the caller.

    Mirrors the ``agent_runs`` row shape; the router persists this and
    then echoes the run_id + a subset back to the HTTP client.
    """

    run_id: uuid.UUID
    status: str
    result: Any = None
    stdout: str | None = None
    stderr: str | None = None
    exit_code: int | None = None
    denial_reason: str | None = None
    resource_usage: dict[str, Any] = field(default_factory=dict)
    duration_seconds: float = 0.0


def _tail(blob: str | None, limit: int = STREAM_TAIL_LIMIT) -> str | None:
    if blob is None:
        return None
    if len(blob) <= limit:
        return blob
    return "...[truncated]...\n" + blob[-limit:]


def _build_ctx(
    *,
    agent_row: Any,
    project_row: Any,
    inputs: dict[str, Any],
    dry_run: bool,
    mode: str,
) -> AgentContext:
    """Lift the ORM rows into a plain :class:`AgentContext`.

    We do NOT pass the ORM objects themselves into the child — they
    carry session state pickle can't round-trip cleanly. Only the
    primitives the user code is allowed to see make it across.
    """
    # ``mcp_url`` shape per CHARTER is ``http://host:port`` — parse
    # naïvely; for v1 the port can also live in the dedicated column.
    host = project_row.mcp_url
    port: int | None = project_row.mcp_port
    if host.startswith("http://") or host.startswith("https://"):
        # Strip scheme; keep the netloc.
        host = host.split("://", 1)[1]
    if ":" in host and port is None:
        host_part, _, port_str = host.partition(":")
        host = host_part
        try:
            port = int(port_str.split("/", 1)[0])
        except ValueError:
            port = 0
    # Strip any trailing path.
    host = host.split("/", 1)[0]

    return AgentContext(
        user_id=str(project_row.user_id),
        project_id=str(project_row.id),
        agent_id=str(agent_row.id),
        symbol=project_row.symbol,
        timeframe=project_row.timeframe,
        mcp=McpEndpoint(
            url=project_row.mcp_url,
            host=host,
            port=int(port or 0),
        ),
        inputs=inputs,
        mode=mode,
        dry_run=dry_run,
        learning_enabled=_learning_enabled_from_env(),
        operativa_enabled=_operativa_enabled_from_env(),
    )


class Engine:
    """Parent-side runner. Stateless — one instance per call is fine."""

    def __init__(
        self,
        *,
        wall_clock_seconds: float = DEFAULT_WALL_CLOCK_SECONDS,
        rlimit_cpu_seconds: int | None = None,
        rlimit_as_bytes: int | None = None,
        rlimit_nofile: int | None = None,
        rlimit_fsize: int | None = None,
        session_factory: Any | None = None,
        learning_cache: Any | None = None,
        rpc_handlers: dict[str, Any] | None = None,
    ) -> None:
        self.wall_clock_seconds = wall_clock_seconds
        self._rlimit_env: dict[str, str] = {}
        if rlimit_cpu_seconds is not None:
            self._rlimit_env["AETHER_SANDBOX_RLIMIT_CPU"] = str(rlimit_cpu_seconds)
        if rlimit_as_bytes is not None:
            self._rlimit_env["AETHER_SANDBOX_RLIMIT_AS"] = str(rlimit_as_bytes)
        if rlimit_nofile is not None:
            self._rlimit_env["AETHER_SANDBOX_RLIMIT_NOFILE"] = str(rlimit_nofile)
        if rlimit_fsize is not None:
            self._rlimit_env["AETHER_SANDBOX_RLIMIT_FSIZE"] = str(rlimit_fsize)
        # Learning wiring — optional. Default-None keeps existing callers
        # working: the child boots with learning disabled and the engine
        # never spins an RPC dispatcher. Production callers (the agents
        # router) pass session_factory + learning_cache so the per-run
        # bound handlers can hit the DB and cache.
        self._session_factory = session_factory
        self._learning_cache = learning_cache
        self._rpc_handlers_override = rpc_handlers

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def run_agent(
        self,
        *,
        agent_row: Any,
        project_row: Any,
        inputs: dict[str, Any] | None = None,
        dry_run: bool = False,
        mode: str = "manual",
        run_id: uuid.UUID | None = None,
    ) -> EngineResult:
        """Execute ``agent_row.logica`` inside a fresh subprocess.

        ``run_id`` is supplied by the caller (typically the router, after
        it has written the ``status='running'`` row) so the result can be
        correlated back to the DB record. If absent, a fresh UUID is
        generated so the caller can use the engine in standalone mode
        (the test suite does this).
        """
        ctx = _build_ctx(
            agent_row=agent_row,
            project_row=project_row,
            inputs=inputs or {},
            dry_run=dry_run,
            mode=mode,
        )
        return self._spawn_and_collect(
            run_id=run_id or uuid.uuid4(),
            source=agent_row.logica,
            entrypoint=agent_row.entrypoint or _default_entrypoint(agent_row.type),
            ctx=ctx,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _spawn_and_collect(
        self,
        *,
        run_id: uuid.UUID,
        source: str,
        entrypoint: str,
        ctx: AgentContext,
    ) -> EngineResult:
        # Use ``multiprocessing.Pipe`` (Connection objects) rather than raw
        # ``os.pipe`` FDs. ``spawn`` does NOT inherit arbitrary FDs into the
        # child; Pipe objects know how to round-trip themselves across the
        # spawn boundary via the multiprocessing handle inheritance mechanism.
        ctx_module = multiprocessing.get_context("spawn")
        parent_to_child_r, parent_to_child_w = ctx_module.Pipe(duplex=False)
        child_to_parent_r, child_to_parent_w = ctx_module.Pipe(duplex=False)

        # Third pipe — duplex — carries learning RPC traffic in BOTH
        # directions. The parent's end is drained by an
        # :class:`RpcDispatcher` thread; the child wraps its end in an
        # :class:`RpcClient` and embeds that in the three proxies on
        # ``ctx``. When ``ctx.learning_enabled`` is False, the child end
        # is still passed (it's cheap and keeps the spawn signature
        # stable) but the child binds Noop proxies and never sends.
        rpc_parent, rpc_child = ctx_module.Pipe(duplex=True)

        # Apply the rlimit env hints for THIS spawn only. ``spawn`` snapshots
        # the parent env at process-start, so a temporary set/unset suffices.
        prev_env: dict[str, str | None] = {}
        for key, value in self._rlimit_env.items():
            prev_env[key] = os.environ.get(key)
            os.environ[key] = value

        proc = ctx_module.Process(
            target=_child_entrypoint,
            args=(parent_to_child_r, child_to_parent_w, rpc_child),
            name=f"aether-sandbox-{run_id}",
        )

        # If learning OR operativa is on, spin a dispatcher thread BEFORE
        # start() so the child can immediately call back without racing
        # the parent. The dispatcher is shared — both proxy families
        # multiplex over the single duplex pipe, routed by method name.
        dispatcher: RpcDispatcher | None = None
        if ctx.learning_enabled or ctx.operativa_enabled:
            try:
                user_uuid = uuid.UUID(ctx.user_id)
                project_uuid = uuid.UUID(ctx.project_id)
                agent_uuid: uuid.UUID | None
                try:
                    agent_uuid = uuid.UUID(ctx.agent_id) if ctx.agent_id else None
                except (TypeError, ValueError):
                    agent_uuid = None
            except (TypeError, ValueError):
                # Non-UUID tenant identifiers (e.g. a unit test with
                # strings) disable RPC outright — the child's Noop
                # variants will raise on writes and reads return None.
                ctx.learning_enabled = False
                ctx.operativa_enabled = False
            else:
                handlers_map = self._rpc_handlers_override or (
                    build_default_handlers(
                        session_factory=self._session_factory,
                        cache=self._learning_cache,
                    )
                )
                dispatcher = RpcDispatcher(
                    conn=rpc_parent,
                    handlers=RpcHandlers(
                        user_id=user_uuid,
                        project_id=project_uuid,
                        handlers=handlers_map,
                        agent_id=agent_uuid,
                    ),
                    name=f"aether-rpc-{run_id}",
                )
                dispatcher.start()

        start = time.monotonic()
        try:
            proc.start()
        finally:
            for key, prev in prev_env.items():
                if prev is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = prev

        # Close the child's ends in the parent so EOF detection works.
        parent_to_child_r.close()
        child_to_parent_w.close()
        rpc_child.close()

        def _cleanup_rpc() -> None:
            """Tear down the dispatcher thread + close the parent end.

            Called at every return path below so the dispatcher never
            outlives the child. Idempotent — safe to call when the
            dispatcher was never spun up (learning disabled).
            """
            import contextlib as _contextlib

            with _contextlib.suppress(Exception):
                rpc_parent.close()
            if dispatcher is not None:
                dispatcher.stop(timeout=2.0)

        # Ship the payload via Connection.send_bytes.
        try:
            parent_to_child_w.send_bytes(
                pickle.dumps(
                    {"source": source, "entrypoint": entrypoint, "ctx": ctx},
                    protocol=pickle.HIGHEST_PROTOCOL,
                )
            )
            parent_to_child_w.close()
        except Exception as exc:  # noqa: BLE001
            self._kill(proc)
            child_to_parent_r.close()
            _cleanup_rpc()
            return EngineResult(
                run_id=run_id,
                status="error",
                stderr=f"failed to pipe payload to child: {exc!r}",
                duration_seconds=time.monotonic() - start,
            )

        # Block on the child producing a result OR the wall-clock deadline.
        deadline = start + self.wall_clock_seconds
        blob: bytes | None = None
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if child_to_parent_r.poll(timeout=min(remaining, 0.5)):
                try:
                    blob = child_to_parent_r.recv_bytes()
                except EOFError:
                    blob = None
                break
            if not proc.is_alive():
                # Child exited without producing a result (signal kill, crash).
                # Try one more drain in case the message landed between
                # is_alive() and poll().
                if child_to_parent_r.poll(timeout=0.1):
                    try:
                        blob = child_to_parent_r.recv_bytes()
                    except EOFError:
                        blob = None
                break

        wall_seconds = time.monotonic() - start

        # If we got a result, the child either has exited or is about to.
        # Wait for it briefly so exitcode is populated; do NOT misreport as
        # timeout just because the child's bookkeeping hasn't caught up.
        if blob is not None:
            proc.join(timeout=2.0)
            child_to_parent_r.close()
        elif proc.is_alive():
            # Wall-clock expired with no result — SIGKILL.
            self._kill(proc)
            child_to_parent_r.close()
            _cleanup_rpc()
            return EngineResult(
                run_id=run_id,
                status="timeout",
                stderr=f"wall-clock deadline ({self.wall_clock_seconds:.1f}s) exceeded",
                exit_code=None,
                denial_reason="wall-clock",
                duration_seconds=wall_seconds,
                resource_usage={
                    "wall_seconds": wall_seconds,
                    "exit_signal": "SIGKILL",
                },
            )

        if not blob:
            # Child exited without producing a result.
            proc.join(timeout=2.0)
            child_to_parent_r.close()
            _cleanup_rpc()
            return _from_dead_child(run_id, proc, wall_seconds)

        try:
            payload = pickle.loads(blob)  # noqa: S301 — child is our own code
        except Exception as exc:  # noqa: BLE001
            _cleanup_rpc()
            return EngineResult(
                run_id=run_id,
                status="error",
                stderr=f"failed to unpickle child result: {exc!r}",
                exit_code=proc.exitcode,
                duration_seconds=wall_seconds,
            )

        _cleanup_rpc()

        return EngineResult(
            run_id=run_id,
            status=payload.get("status", "error"),
            result=payload.get("result"),
            stdout=_tail(payload.get("stdout")),
            stderr=_tail(payload.get("stderr") or payload.get("traceback")),
            exit_code=proc.exitcode,
            denial_reason=payload.get("denial_reason"),
            resource_usage={
                "wall_seconds": wall_seconds,
                "exit_signal": None,
            },
            duration_seconds=wall_seconds,
        )

    @staticmethod
    def _kill(proc: Any) -> None:
        """Best-effort SIGKILL + join. Never raises.

        Typed as ``Any`` because :func:`multiprocessing.get_context("spawn").Process`
        returns a ``SpawnProcess`` whose stubs don't match the bare
        ``multiprocessing.Process`` signature mypy sees in this scope.
        """
        try:
            if proc.is_alive():
                proc.kill()
                proc.join(timeout=2.0)
        except Exception:  # noqa: BLE001 — last-ditch cleanup
            pass


def _default_entrypoint(agent_type: str) -> str:
    """Same mapping as :mod:`aether_api.routers.agents._DEFAULT_ENTRYPOINTS`.

    Duplicated here to keep the sandbox module free of router imports —
    the layering is sandbox → core stdlib only; routers depend on
    sandbox, never the other way.
    """
    return {
        "worker": "on_tick",
        "investigator": "investigate",
        "auditor": "audit",
    }.get(agent_type, "main")


def _from_dead_child(
    run_id: uuid.UUID,
    proc: Any,
    wall_seconds: float,
) -> EngineResult:
    """Translate a child that exited without writing a result into a
    structured EngineResult.

    Negative exit codes are signal numbers (POSIX convention).
    ``-signal.SIGKILL`` typically means the kernel OOM-killed us (rlimit
    AS) or the parent itself killed (handled above). Map the common
    signals into the agent_runs status enum so the operator sees a
    sensible row.
    """
    exit_code = proc.exitcode
    # SIGKILL (-9) from a wall-clock kill is handled upstream. Anything
    # else negative is the kernel killing us — likely OOM or CPU rlimit.
    if exit_code is None:
        return EngineResult(
            run_id=run_id,
            status="error",
            stderr="child exited without producing a result and exit_code is None",
            duration_seconds=wall_seconds,
        )
    if exit_code < 0:
        sig = -exit_code
        # 9=SIGKILL (likely RLIMIT_AS), 24=SIGXCPU (RLIMIT_CPU),
        # 25=SIGXFSZ (RLIMIT_FSIZE).
        if sig == 24:
            status = "timeout"
            denial = "rlimit:cpu"
        elif sig == 25:
            status = "denied_file"
            denial = "rlimit:fsize"
        elif sig == 9:
            status = "oom"
            denial = "rlimit:as"
        else:
            status = "error"
            denial = f"signal:{sig}"
        return EngineResult(
            run_id=run_id,
            status=status,
            stderr=f"child killed by signal {sig}",
            exit_code=exit_code,
            denial_reason=denial,
            duration_seconds=wall_seconds,
            resource_usage={"wall_seconds": wall_seconds, "exit_signal": f"signal-{sig}"},
        )
    return EngineResult(
        run_id=run_id,
        status="error",
        stderr=f"child exited with code {exit_code} without producing a result",
        exit_code=exit_code,
        duration_seconds=wall_seconds,
    )


def _child_entrypoint(read_conn: Any, write_conn: Any, rpc_conn: Any) -> None:
    """Module-level shim — multiprocessing.spawn requires a picklable
    target. Re-exports :func:`aether_api.sandbox.child.child_main`.

    ``rpc_conn`` is the child-end of the duplex RPC pipe created by
    :meth:`Engine._spawn_and_collect`. Forwarded into ``child_main``
    where the learning bootstrap wraps it in an
    :class:`aether_api.sandbox.rpc.RpcClient`.
    """
    # Late import so the child re-imports the module via spawn fresh.
    from aether_api.sandbox.child import child_main

    child_main(read_conn, write_conn, rpc_conn)
