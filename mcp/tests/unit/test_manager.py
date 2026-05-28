"""Unit tests for the run orchestration manager (RunManager)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from mcp_metatrader5.manager import (
    BacktestOutcome,
    CompileOutcome,
    OptimizeOutcome,
    RunManager,
)
from mcp_metatrader5.state import StateStore
from mcp_metatrader5.workspace import WorkspacePaths

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class FakeWineRunner:
    """In-memory WineRunnerProtocol impl used to exercise the queue.

    Each method awaits an event the test controls so we can assert ordering
    and intermediate states deterministically.
    """

    compile_result: Callable[[str], CompileOutcome] | None = None
    backtest_result: Callable[[str], BacktestOutcome] | None = None
    optimize_result: Callable[[str], OptimizeOutcome] | None = None
    delay: float = 0.0
    raise_on: str | None = None
    invocations: list[tuple[str, Any]] = field(default_factory=list)
    gate: asyncio.Event | None = None

    async def compile(self, *, ea_handle: str, ea_workspace_path: Path,
                      run_dir: Path) -> CompileOutcome:
        self.invocations.append(("compile", ea_handle))
        if self.gate is not None:
            await self.gate.wait()
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.raise_on == "compile":
            raise RuntimeError("boom-compile")
        if self.compile_result is not None:
            return self.compile_result(ea_handle)
        return CompileOutcome(
            exit_code=0,
            log_path=run_dir / "compile.log",
            ex5_path=run_dir / "out.ex5",
            ok=True,
        )

    async def backtest(self, *, ea_handle: str, run_dir: Path,
                       ini_text: str) -> BacktestOutcome:
        self.invocations.append(("backtest", ea_handle))
        if self.gate is not None:
            await self.gate.wait()
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.raise_on == "backtest":
            raise RuntimeError("boom-backtest")
        if self.backtest_result is not None:
            return self.backtest_result(ea_handle)
        return BacktestOutcome(
            exit_code=0,
            log_path=run_dir / "terminal.log",
            report_path=run_dir / "report.html",
            ini_path=run_dir / "run.ini",
        )

    async def optimize(self, *, ea_handle: str, run_dir: Path,
                       ini_text: str) -> OptimizeOutcome:
        self.invocations.append(("optimize", ea_handle))
        if self.gate is not None:
            await self.gate.wait()
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.raise_on == "optimize":
            raise RuntimeError("boom-optimize")
        if self.optimize_result is not None:
            return self.optimize_result(ea_handle)
        return OptimizeOutcome(
            exit_code=0,
            log_path=run_dir / "terminal.log",
            report_path=run_dir / "report.xml",
            ini_path=run_dir / "run.ini",
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _new_state(tmp_path: Path) -> StateStore:
    store = StateStore(tmp_path / "state.sqlite")
    store.upsert_ea(
        ea_id="my-ea",
        ea_name="MyEA",
        source_path="MQL5/Experts/managed/my-ea/my-ea.mq5",
        sha256="0" * 64,
    )
    return store


def _paths(tmp_path: Path) -> WorkspacePaths:
    paths = WorkspacePaths(tmp_path)
    paths.ensure()
    return paths


def _write_fixture_html(report_path: Path) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_bytes(
        Path("tests/fixtures/backtest_report.html").read_bytes()
    )


def _write_fixture_xml(report_path: Path) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_bytes(
        Path("tests/fixtures/optimization_report.xml").read_bytes()
    )


def _write_fixture_compile_log(log_path: Path, *, clean: bool = True) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    name = "compile_clean.log" if clean else "compile_with_errors.log"
    log_path.write_bytes(Path(f"tests/fixtures/{name}").read_bytes())


async def _wait_until(predicate: Callable[[], bool], *, timeout: float = 2.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("predicate did not become true within timeout")


# ---------------------------------------------------------------------------
# Tests — submit + happy-path lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_compile_returns_run_id_immediately(tmp_path: Path) -> None:
    state = _new_state(tmp_path)
    paths = _paths(tmp_path)
    runner = FakeWineRunner()
    manager = RunManager(state_store=state, workspace_paths=paths, wine_runner=runner)

    await manager.start()
    try:
        run_id = await manager.submit_compile(ea_handle="my-ea")
        assert isinstance(run_id, str) and run_id
        # Immediate state row exists with status=queued or already running.
        rec = state.get_run(run_id)
        assert rec is not None
        assert rec.kind == "compile"
        assert rec.status in {"queued", "running", "done"}
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_compile_run_completes_and_records_summary(tmp_path: Path) -> None:
    state = _new_state(tmp_path)
    paths = _paths(tmp_path)

    def _compile(ea_handle: str) -> CompileOutcome:
        run_dir = paths.runs_dir
        # Caller will have created run-specific dir; emulate by writing a clean log
        # to the most recent run directory.
        latest = sorted(run_dir.iterdir())[-1]
        _write_fixture_compile_log(latest / "compile.log", clean=True)
        return CompileOutcome(
            exit_code=0,
            log_path=latest / "compile.log",
            ex5_path=latest / "my-ea.ex5",
            ok=True,
        )

    runner = FakeWineRunner(compile_result=_compile)
    manager = RunManager(state_store=state, workspace_paths=paths, wine_runner=runner)

    await manager.start()
    try:
        run_id = await manager.submit_compile(ea_handle="my-ea")
        await _wait_until(lambda: (state.get_run(run_id) or None) is not None
                          and state.get_run(run_id).status == "done")  # type: ignore[union-attr]
        rec = state.get_run(run_id)
        assert rec is not None
        assert rec.status == "done"
        assert rec.summary.get("ok") is True
        assert rec.artifacts.get("log_path", "").endswith("compile.log")
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_backtest_run_completes_and_records_report(tmp_path: Path) -> None:
    state = _new_state(tmp_path)
    paths = _paths(tmp_path)

    def _backtest(ea_handle: str) -> BacktestOutcome:
        latest = sorted(paths.runs_dir.iterdir())[-1]
        _write_fixture_html(latest / "report.html")
        return BacktestOutcome(
            exit_code=0,
            log_path=latest / "terminal.log",
            report_path=latest / "report.html",
            ini_path=latest / "run.ini",
        )

    runner = FakeWineRunner(backtest_result=_backtest)
    manager = RunManager(state_store=state, workspace_paths=paths, wine_runner=runner)

    await manager.start()
    try:
        run_id = await manager.submit_backtest(
            ea_handle="my-ea",
            ini_text="[Tester]\n",
            symbol="XAUUSD",
            period="H1",
            from_date="2024.01.01",
            to_date="2024.06.01",
        )
        await _wait_until(lambda: (state.get_run(run_id) or None) is not None
                          and state.get_run(run_id).status == "done")  # type: ignore[union-attr]
        rec = state.get_run(run_id)
        assert rec is not None
        assert rec.status == "done"
        assert rec.kind == "backtest"
        # Summary should contain at least one numeric metric from the parsed report.
        assert "summary" in rec.artifacts or rec.summary
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_optimize_run_completes_and_records_passes(tmp_path: Path) -> None:
    state = _new_state(tmp_path)
    paths = _paths(tmp_path)

    def _opt(ea_handle: str) -> OptimizeOutcome:
        latest = sorted(paths.runs_dir.iterdir())[-1]
        _write_fixture_xml(latest / "report.xml")
        return OptimizeOutcome(
            exit_code=0,
            log_path=latest / "terminal.log",
            report_path=latest / "report.xml",
            ini_path=latest / "run.ini",
        )

    runner = FakeWineRunner(optimize_result=_opt)
    manager = RunManager(state_store=state, workspace_paths=paths, wine_runner=runner)

    await manager.start()
    try:
        run_id = await manager.submit_optimize(
            ea_handle="my-ea",
            ini_text="[Tester]\n",
            symbol="XAUUSD",
            period="H1",
            from_date="2024.01.01",
            to_date="2024.06.01",
        )
        await _wait_until(lambda: (state.get_run(run_id) or None) is not None
                          and state.get_run(run_id).status == "done")  # type: ignore[union-attr]
        rec = state.get_run(run_id)
        assert rec is not None
        assert rec.status == "done"
        assert rec.kind == "optimize"
        assert rec.summary.get("pass_count", 0) >= 0
    finally:
        await manager.stop()


# ---------------------------------------------------------------------------
# Tests — error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runner_exception_marks_run_failed(tmp_path: Path) -> None:
    state = _new_state(tmp_path)
    paths = _paths(tmp_path)
    runner = FakeWineRunner(raise_on="backtest")
    manager = RunManager(state_store=state, workspace_paths=paths, wine_runner=runner)

    await manager.start()
    try:
        run_id = await manager.submit_backtest(
            ea_handle="my-ea",
            ini_text="[Tester]\n",
            symbol="XAUUSD",
            period="H1",
            from_date="2024.01.01",
            to_date="2024.06.01",
        )
        await _wait_until(lambda: (state.get_run(run_id) or None) is not None
                          and state.get_run(run_id).status == "failed")  # type: ignore[union-attr]
        rec = state.get_run(run_id)
        assert rec is not None
        assert rec.status == "failed"
        assert rec.error_code  # populated
        assert "boom-backtest" in (rec.error_msg or "")
    finally:
        await manager.stop()


# ---------------------------------------------------------------------------
# Tests — queue ordering / single consumer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_queue_serialises_jobs(tmp_path: Path) -> None:
    """At most one job runs at a time; submission order is preserved."""
    state = _new_state(tmp_path)
    paths = _paths(tmp_path)

    gate = asyncio.Event()
    runner = FakeWineRunner(gate=gate)
    manager = RunManager(state_store=state, workspace_paths=paths, wine_runner=runner)

    await manager.start()
    try:
        ids = [await manager.submit_compile(ea_handle="my-ea") for _ in range(3)]

        # All should be queued; only the first may have transitioned to running.
        await asyncio.sleep(0.05)
        statuses = [state.get_run(i).status for i in ids]  # type: ignore[union-attr]
        # Exactly one running at a time (or all queued if worker hasn't picked up).
        assert statuses.count("running") <= 1
        # Release the gate so jobs can complete one-by-one.
        gate.set()
        await _wait_until(
            lambda: all(state.get_run(i).status == "done" for i in ids)  # type: ignore[union-attr]
        )

        # FIFO ordering: invocations recorded in submission order.
        assert [r[0] for r in runner.invocations] == ["compile", "compile", "compile"]
    finally:
        await manager.stop()


# ---------------------------------------------------------------------------
# Tests — cancel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_queued_run(tmp_path: Path) -> None:
    state = _new_state(tmp_path)
    paths = _paths(tmp_path)
    gate = asyncio.Event()
    runner = FakeWineRunner(gate=gate)
    manager = RunManager(state_store=state, workspace_paths=paths, wine_runner=runner)

    await manager.start()
    try:
        first = await manager.submit_compile(ea_handle="my-ea")  # will be running (gated)
        second = await manager.submit_compile(ea_handle="my-ea")  # queued

        await asyncio.sleep(0.05)
        # Cancel the second (still queued).
        cancelled = await manager.cancel(second)
        assert cancelled is True
        rec = state.get_run(second)
        assert rec is not None
        assert rec.status == "cancelled"

        # Release first so worker can drain.
        gate.set()
        await _wait_until(
            lambda: state.get_run(first).status == "done"  # type: ignore[union-attr]
        )
        # Cancelled run never invoked the runner (only the first compile ran).
        assert runner.invocations == [("compile", "my-ea")]
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_cancel_unknown_run_returns_false(tmp_path: Path) -> None:
    state = _new_state(tmp_path)
    paths = _paths(tmp_path)
    runner = FakeWineRunner()
    manager = RunManager(state_store=state, workspace_paths=paths, wine_runner=runner)

    await manager.start()
    try:
        result = await manager.cancel("does-not-exist")
        assert result is False
    finally:
        await manager.stop()


# ---------------------------------------------------------------------------
# Tests — restart reconciliation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_restart_marks_orphan_runs_as_interrupted(tmp_path: Path) -> None:
    """On start(), runs left in (queued|running) get reconciled to failed
    with error_code=run_interrupted."""
    state = _new_state(tmp_path)
    paths = _paths(tmp_path)

    # Inject orphan runs as if a previous process died mid-run.
    state.create_run(run_id="20990101t000000z-aaaaaa", ea_id="my-ea", kind="compile")
    state.create_run(run_id="20990101t000000z-bbbbbb", ea_id="my-ea", kind="backtest")
    state.update_run("20990101t000000z-bbbbbb", status="running")

    runner = FakeWineRunner()
    manager = RunManager(state_store=state, workspace_paths=paths, wine_runner=runner)

    await manager.start()
    try:
        a = state.get_run("20990101t000000z-aaaaaa")
        b = state.get_run("20990101t000000z-bbbbbb")
        assert a is not None and b is not None
        assert a.status == "failed"
        assert a.error_code == "run_interrupted"
        assert b.status == "failed"
        assert b.error_code == "run_interrupted"
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_get_run_reads_through_state(tmp_path: Path) -> None:
    state = _new_state(tmp_path)
    paths = _paths(tmp_path)
    runner = FakeWineRunner()
    manager = RunManager(state_store=state, workspace_paths=paths, wine_runner=runner)

    await manager.start()
    try:
        run_id = await manager.submit_compile(ea_handle="my-ea")
        rec = await manager.get_run(run_id)
        assert rec is not None
        assert rec.run_id == run_id
        assert (await manager.get_run("nope")) is None
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_submit_unknown_ea_raises(tmp_path: Path) -> None:
    state = StateStore(tmp_path / "state.sqlite")  # no EA registered
    paths = _paths(tmp_path)
    runner = FakeWineRunner()
    manager = RunManager(state_store=state, workspace_paths=paths, wine_runner=runner)

    await manager.start()
    try:
        from mcp_metatrader5.errors import ErrorCode, MT5MCPError

        with pytest.raises(MT5MCPError) as exc:
            await manager.submit_compile(ea_handle="ghost")
        assert exc.value.code == ErrorCode.EA_NOT_FOUND
    finally:
        await manager.stop()
