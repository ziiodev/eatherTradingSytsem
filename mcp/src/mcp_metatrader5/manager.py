"""Run orchestration: queue compile/backtest/optimize jobs and drive a
:class:`WineRunnerProtocol` implementation.

The :class:`RunManager` is the **single owner** of the in-memory job queue.
Every tool that needs to invoke MetaEditor or terminal64 enqueues a job here
and is handed back a ``run_id`` immediately. A single asyncio worker drains
the queue serially — there is at most one Wine subprocess in flight per MCP
process, which matches the design's concurrency model (the Wine prefix is not
safe for parallel use).

This module is **pure orchestration**: it does not invoke Wine itself. The
actual subprocess work lives behind :class:`WineRunnerProtocol`, which Phase 3
will implement. Tests inject a fake runner.

Locking model
-------------
The cross-process advisory lock (``mcp.lock``) is taken at write sites inside
:mod:`mcp_metatrader5.workspace`; the manager intentionally holds **no** locks
of its own. The single-consumer queue plus the workspace flock together
guarantee that two MCP processes pointing at the same Wine prefix cannot
race on Wine.

Restart reconciliation
----------------------
SQLite ``status`` columns are persistent. If the previous process died mid-run
we will see rows in ``queued`` or ``running`` state on startup. Those jobs are
not in the in-memory queue and we have no way to resume them, so :meth:`start`
flips them to ``failed`` with ``error_code=run_interrupted`` so callers can
see what happened and re-submit if desired.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from .errors import ErrorCode, MT5MCPError, WorkspaceError
from .logging import get_logger
from .parsers.compile_log import parse_compile_log
from .parsers.html_report import parse_backtest_report
from .parsers.optimization_xml import parse_optimization_xml
from .state import RunRecord, StateStore
from .workspace import WorkspacePaths, new_run_id

_log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Outcome dataclasses (shared by RunManager and the future WineRunner)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CompileOutcome:
    """Result of running MetaEditor against a single ``.mq5`` source.

    ``log_path`` is the UTF-16 compile log emitted via ``/log:``. ``ex5_path``
    is set even on failure (the file may not exist) so callers can probe.
    """

    exit_code: int
    log_path: Path
    ex5_path: Path
    ok: bool
    timed_out: bool = False


@dataclass(frozen=True, slots=True)
class BacktestOutcome:
    """Result of a single Strategy-Tester backtest run."""

    exit_code: int
    log_path: Path
    report_path: Path
    ini_path: Path
    timed_out: bool = False


@dataclass(frozen=True, slots=True)
class OptimizeOutcome:
    """Result of a single Strategy-Tester optimization run."""

    exit_code: int
    log_path: Path
    report_path: Path  # Excel-XML SpreadsheetML cache
    ini_path: Path
    timed_out: bool = False


# ---------------------------------------------------------------------------
# WineRunnerProtocol
# ---------------------------------------------------------------------------


class WineRunnerProtocol(Protocol):
    """Contract that Phase 3's Wine subprocess driver must satisfy.

    Implementations are responsible for:
    - acquiring the workspace flock around their subprocess invocation,
    - enforcing their own timeouts (they own the subprocess, the manager does not),
    - writing terminal/compile logs and report files inside ``run_dir``,
    - returning a structured outcome — never raising on a non-zero exit code
      (raise only on infrastructure failures the manager should treat as ``failed``).
    """

    async def compile(
        self,
        *,
        ea_handle: str,
        ea_workspace_path: Path,
        run_dir: Path,
    ) -> CompileOutcome:
        ...

    async def backtest(
        self,
        *,
        ea_handle: str,
        run_dir: Path,
        ini_text: str,
    ) -> BacktestOutcome:
        ...

    async def optimize(
        self,
        *,
        ea_handle: str,
        run_dir: Path,
        ini_text: str,
    ) -> OptimizeOutcome:
        ...


# ---------------------------------------------------------------------------
# Job records (in-process — never persisted)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _Job:
    run_id: str
    ea_handle: str
    kind: str  # 'compile' | 'backtest' | 'optimize'
    payload: dict[str, Any]
    cancelled: bool = False


# ---------------------------------------------------------------------------
# RunManager
# ---------------------------------------------------------------------------


class RunManager:
    """Owns the asyncio queue + single consumer driving the Wine runner.

    Lifecycle::

        mgr = RunManager(state_store=..., workspace_paths=..., wine_runner=...)
        await mgr.start()       # spawns worker, reconciles orphan runs
        run_id = await mgr.submit_compile(ea_handle=...)
        ...
        await mgr.stop()        # drains in-flight job, joins worker

    The manager itself is **not** a context manager: its lifetime spans the
    server process. Tests can build many short-lived managers in tmp dirs.
    """

    def __init__(
        self,
        *,
        state_store: StateStore,
        workspace_paths: WorkspacePaths,
        wine_runner: WineRunnerProtocol,
        clock: Any | None = None,
    ) -> None:
        self._state = state_store
        self._paths = workspace_paths
        self._runner = wine_runner
        self._clock = clock or (lambda: datetime.now(UTC))
        self._queue: asyncio.Queue[_Job] = asyncio.Queue()
        self._jobs: dict[str, _Job] = {}
        self._worker: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        """Reconcile orphan runs, then spawn the worker task.

        Orphan reconciliation: any run in state with ``status`` in
        ``{queued, running}`` is flipped to ``failed`` with
        ``error_code=run_interrupted``. We have no way to resume those — they
        were lost when the previous process exited.
        """

        # Reconcile orphans first so callers don't see stale 'running' rows.
        for orphan in self._state.list_runs(status="queued") + self._state.list_runs(
            status="running"
        ):
            self._state.update_run(
                orphan.run_id,
                status="failed",
                finished_at=self._now_iso(),
                error_code=ErrorCode.RUN_INTERRUPTED.value,
                error_msg="run was interrupted by server restart",
            )
            _log.warning(
                "run_reconciled_as_interrupted",
                run_id=orphan.run_id,
                kind=orphan.kind,
            )

        if self._worker is None or self._worker.done():
            self._stopping.clear()
            self._worker = asyncio.create_task(self._consume(), name="run-manager-worker")

    async def stop(self) -> None:
        """Stop the worker, draining any in-flight job.

        Pending queued jobs are left in place (in the DB they remain ``queued``
        — the next start() will reconcile them as interrupted). Cancelling
        in-flight subprocesses is the runner's responsibility.
        """

        self._stopping.set()
        # Wake the worker if it's blocked on get().
        await self._queue.put(_SENTINEL)
        if self._worker is not None:
            try:
                await self._worker
            finally:
                self._worker = None

    # ------------------------------------------------------------------ submit

    async def submit_compile(self, *, ea_handle: str) -> str:
        """Enqueue a compile job. Returns the new run_id immediately."""
        return await self._enqueue(
            ea_handle=ea_handle,
            kind="compile",
            payload={},
            symbol=None,
            period=None,
            from_date=None,
            to_date=None,
        )

    async def submit_backtest(
        self,
        *,
        ea_handle: str,
        ini_text: str,
        symbol: str,
        period: str,
        from_date: str,
        to_date: str,
    ) -> str:
        """Enqueue a backtest job. Returns the new run_id immediately."""
        return await self._enqueue(
            ea_handle=ea_handle,
            kind="backtest",
            payload={"ini_text": ini_text},
            symbol=symbol,
            period=period,
            from_date=from_date,
            to_date=to_date,
        )

    async def submit_optimize(
        self,
        *,
        ea_handle: str,
        ini_text: str,
        symbol: str,
        period: str,
        from_date: str,
        to_date: str,
    ) -> str:
        """Enqueue an optimization job. Returns the new run_id immediately."""
        return await self._enqueue(
            ea_handle=ea_handle,
            kind="optimize",
            payload={"ini_text": ini_text},
            symbol=symbol,
            period=period,
            from_date=from_date,
            to_date=to_date,
        )

    # ------------------------------------------------------------------ read / cancel

    async def get_run(self, run_id: str) -> RunRecord | None:
        """Read-through to the state store."""
        return self._state.get_run(run_id)

    async def cancel(self, run_id: str) -> bool:
        """Cancel a queued run.

        - If the run is still in the in-memory queue, we mark its ``_Job`` as
          ``cancelled``; the worker will skip it and write status=cancelled.
        - If the run is already running, we DO NOT signal the runner — it
          owns the subprocess and its own timeouts. We return ``False`` so
          the caller knows the run will continue to completion.
        - If the run does not exist, returns ``False``.

        Currently there is no kill-running support — this matches the design.
        """

        job = self._jobs.get(run_id)
        if job is None:
            return False
        rec = self._state.get_run(run_id)
        if rec is None:
            return False
        if rec.status != "queued":
            return False
        job.cancelled = True
        self._state.update_run(
            run_id,
            status="cancelled",
            finished_at=self._now_iso(),
        )
        _log.info("run_cancelled", run_id=run_id)
        return True

    # ------------------------------------------------------------------ internals

    async def _enqueue(
        self,
        *,
        ea_handle: str,
        kind: str,
        payload: dict[str, Any],
        symbol: str | None,
        period: str | None,
        from_date: str | None,
        to_date: str | None,
    ) -> str:
        ea = self._state.get_ea(ea_handle)
        if ea is None:
            raise WorkspaceError(
                ErrorCode.EA_NOT_FOUND,
                f"ea_handle {ea_handle!r} is not registered",
            )

        run_id = new_run_id(now=self._clock() if callable(self._clock) else None)
        # Pre-create the run directory so the runner has somewhere to write.
        self._paths.run_dir(run_id).mkdir(parents=True, exist_ok=True)

        self._state.create_run(
            run_id=run_id,
            ea_id=ea_handle,
            kind=kind,
            symbol=symbol,
            period=period,
            from_date=from_date,
            to_date=to_date,
        )

        job = _Job(run_id=run_id, ea_handle=ea_handle, kind=kind, payload=payload)
        self._jobs[run_id] = job
        await self._queue.put(job)
        _log.info("run_submitted", run_id=run_id, kind=kind, ea=ea_handle)
        return run_id

    async def _consume(self) -> None:
        while True:
            job = await self._queue.get()
            if job is _SENTINEL:
                # Shutdown signal.
                if self._stopping.is_set():
                    return
                # Spurious wake; keep going.
                continue
            try:
                await self._run_one(job)
            except Exception as exc:  # defensive: never let the worker die
                _log.exception("run_worker_unhandled", run_id=job.run_id, error=str(exc))
            finally:
                self._jobs.pop(job.run_id, None)

    async def _run_one(self, job: _Job) -> None:
        if job.cancelled:
            _log.info("run_skipped_cancelled", run_id=job.run_id)
            return

        run_dir = self._paths.run_dir(job.run_id)
        run_dir.mkdir(parents=True, exist_ok=True)

        self._state.update_run(
            job.run_id,
            status="running",
            started_at=self._now_iso(),
        )
        _log.info("run_started", run_id=job.run_id, kind=job.kind)

        try:
            if job.kind == "compile":
                await self._do_compile(job, run_dir)
            elif job.kind == "backtest":
                await self._do_backtest(job, run_dir)
            elif job.kind == "optimize":
                await self._do_optimize(job, run_dir)
            else:  # pragma: no cover - guarded by submit_* signatures
                raise MT5MCPError(
                    ErrorCode.INTERNAL,
                    f"unknown job kind {job.kind!r}",
                )
        except MT5MCPError as exc:
            self._state.update_run(
                job.run_id,
                status="failed",
                finished_at=self._now_iso(),
                error_code=exc.code.value,
                error_msg=exc.message,
            )
            _log.error(
                "run_failed",
                run_id=job.run_id,
                kind=job.kind,
                error_code=exc.code.value,
                error=exc.message,
            )
        except Exception as exc:
            self._state.update_run(
                job.run_id,
                status="failed",
                finished_at=self._now_iso(),
                error_code=ErrorCode.INTERNAL.value,
                error_msg=str(exc),
            )
            _log.exception("run_failed_unhandled", run_id=job.run_id, kind=job.kind)

    async def _do_compile(self, job: _Job, run_dir: Path) -> None:
        ea = self._state.get_ea(job.ea_handle)
        assert ea is not None  # checked at submit
        outcome = await self._runner.compile(
            ea_handle=job.ea_handle,
            ea_workspace_path=Path(ea.source_path),
            run_dir=run_dir,
        )
        # Parse compile log if it exists.
        summary: dict[str, Any] = {
            "ok": outcome.ok,
            "exit_code": outcome.exit_code,
            "timed_out": outcome.timed_out,
        }
        artifacts: dict[str, Any] = {
            "log_path": str(outcome.log_path),
            "ex5_path": str(outcome.ex5_path),
        }
        if outcome.log_path.exists():
            try:
                parsed = parse_compile_log(outcome.log_path)
                summary["error_count"] = parsed.error_count
                summary["warning_count"] = parsed.warning_count
                summary["diagnostics"] = [
                    {
                        "severity": d.severity.value,
                        "code": d.code,
                        "file": d.file,
                        "line": d.line,
                        "column": d.column,
                        "message": d.message,
                    }
                    for d in parsed.diagnostics
                ]
            except MT5MCPError as exc:
                _log.warning(
                    "compile_log_parse_failed",
                    run_id=job.run_id,
                    error=exc.message,
                )

        status = "done" if outcome.ok else "failed"
        self._state.update_run(
            job.run_id,
            status=status,
            finished_at=self._now_iso(),
            artifacts=artifacts,
            summary=summary,
            error_code=None if outcome.ok else ErrorCode.COMPILE_FAILED.value,
            error_msg=None if outcome.ok else "compile reported errors",
        )
        _log.info("compile_finished", run_id=job.run_id, ok=outcome.ok)

    async def _do_backtest(self, job: _Job, run_dir: Path) -> None:
        ini_text = job.payload["ini_text"]
        outcome = await self._runner.backtest(
            ea_handle=job.ea_handle,
            run_dir=run_dir,
            ini_text=ini_text,
        )
        artifacts: dict[str, Any] = {
            "log_path": str(outcome.log_path),
            "report_path": str(outcome.report_path),
            "ini_path": str(outcome.ini_path),
        }
        summary: dict[str, Any] = {
            "exit_code": outcome.exit_code,
            "timed_out": outcome.timed_out,
        }

        if outcome.report_path.exists():
            try:
                report = parse_backtest_report(outcome.report_path)
                summary.update(
                    {
                        k: v for k, v in report.to_dict().items()
                        if v is not None and not isinstance(v, dict)
                    }
                )
                summary["inputs"] = report.inputs
            except MT5MCPError as exc:
                _log.warning(
                    "backtest_report_parse_failed",
                    run_id=job.run_id,
                    error=exc.message,
                )

        if outcome.exit_code != 0 or outcome.timed_out:
            self._state.update_run(
                job.run_id,
                status="failed",
                finished_at=self._now_iso(),
                artifacts=artifacts,
                summary=summary,
                error_code=(
                    ErrorCode.BACKTEST_TIMEOUT.value
                    if outcome.timed_out
                    else ErrorCode.BACKTEST_FAILED.value
                ),
                error_msg=(
                    "backtest timed out"
                    if outcome.timed_out
                    else f"terminal64 exited with code {outcome.exit_code}"
                ),
            )
            return

        self._state.update_run(
            job.run_id,
            status="done",
            finished_at=self._now_iso(),
            artifacts=artifacts,
            summary=summary,
        )
        _log.info("backtest_finished", run_id=job.run_id)

    async def _do_optimize(self, job: _Job, run_dir: Path) -> None:
        ini_text = job.payload["ini_text"]
        outcome = await self._runner.optimize(
            ea_handle=job.ea_handle,
            run_dir=run_dir,
            ini_text=ini_text,
        )
        artifacts: dict[str, Any] = {
            "log_path": str(outcome.log_path),
            "report_path": str(outcome.report_path),
            "ini_path": str(outcome.ini_path),
        }
        summary: dict[str, Any] = {
            "exit_code": outcome.exit_code,
            "timed_out": outcome.timed_out,
        }

        if outcome.report_path.exists():
            try:
                report = parse_optimization_xml(outcome.report_path)
                summary["pass_count"] = report.pass_count
                summary["columns"] = list(report.columns)
                best = report.best_pass(by="profit")
                if best is not None:
                    summary["best"] = {
                        "pass_index": best.pass_index,
                        "profit": best.profit,
                        "profit_factor": best.profit_factor,
                        "sharpe_ratio": best.sharpe_ratio,
                        "trades": best.trades,
                        "parameters": best.parameters,
                    }
            except MT5MCPError as exc:
                _log.warning(
                    "optimization_xml_parse_failed",
                    run_id=job.run_id,
                    error=exc.message,
                )

        if outcome.exit_code != 0 or outcome.timed_out:
            self._state.update_run(
                job.run_id,
                status="failed",
                finished_at=self._now_iso(),
                artifacts=artifacts,
                summary=summary,
                error_code=(
                    ErrorCode.OPTIMIZATION_TIMEOUT.value
                    if outcome.timed_out
                    else ErrorCode.OPTIMIZATION_FAILED.value
                ),
                error_msg=(
                    "optimization timed out"
                    if outcome.timed_out
                    else f"terminal64 exited with code {outcome.exit_code}"
                ),
            )
            return

        self._state.update_run(
            job.run_id,
            status="done",
            finished_at=self._now_iso(),
            artifacts=artifacts,
            summary=summary,
        )
        _log.info("optimize_finished", run_id=job.run_id)

    # ------------------------------------------------------------------ utils

    def _now_iso(self) -> str:
        return datetime.now(UTC).isoformat(timespec="microseconds")


# Sentinel used to wake the consumer so it can observe ``_stopping``.
_SENTINEL: _Job = _Job(run_id="__sentinel__", ea_handle="", kind="__sentinel__", payload={})


__all__ = [
    "BacktestOutcome",
    "CompileOutcome",
    "OptimizeOutcome",
    "RunManager",
    "WineRunnerProtocol",
]
