"""End-to-end flow: register → compile → backtest → read artifact.

Drives the tool functions directly (no MCP transport) against a fake
WineRunner that materialises canned outcome files from existing fixtures.
This exercises the full lifecycle minus the JSON-RPC wire layer.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest

from mcp_metatrader5.manager import (
    BacktestOutcome,
    CompileOutcome,
    OptimizeOutcome,
    RunManager,
)
from mcp_metatrader5.state import StateStore
from mcp_metatrader5.tools.backtest import backtest_ea
from mcp_metatrader5.tools.compile import compile_ea
from mcp_metatrader5.tools.eas import register_ea
from mcp_metatrader5.tools.runs import get_run, get_run_artifact
from mcp_metatrader5.tools.schemas import (
    BacktestEAInput,
    CompileEAInput,
    GetRunArtifactInput,
    GetRunInput,
    RegisterEAInput,
)
from mcp_metatrader5.workspace import WorkspacePaths

_FIXTURES = Path("tests/fixtures")


@dataclass
class FakeRunner:
    """Materialises fixture artifacts in the run dir to mimic Wine output."""

    async def compile(
        self, *, ea_handle: str, ea_workspace_path: Path, run_dir: Path
    ) -> CompileOutcome:
        log_path = run_dir / "compile.log"
        log_path.write_bytes((_FIXTURES / "compile_clean.log").read_bytes())
        ex5_path = run_dir / f"{ea_handle}.ex5"
        ex5_path.write_bytes(b"\x00")  # any non-empty bytes will do
        return CompileOutcome(
            exit_code=0, log_path=log_path, ex5_path=ex5_path, ok=True
        )

    async def backtest(
        self, *, ea_handle: str, run_dir: Path, ini_text: str
    ) -> BacktestOutcome:
        log_path = run_dir / "terminal.log"
        log_path.write_text("tester ok\n", encoding="utf-8")
        report = run_dir / "report.html"
        report.write_bytes((_FIXTURES / "backtest_report.html").read_bytes())
        ini_path = run_dir / "run.ini"
        ini_path.write_text(ini_text, encoding="ascii")
        return BacktestOutcome(
            exit_code=0,
            log_path=log_path,
            report_path=report,
            ini_path=ini_path,
        )

    async def optimize(
        self, *, ea_handle: str, run_dir: Path, ini_text: str
    ) -> OptimizeOutcome:  # pragma: no cover - exercised by other tests
        log_path = run_dir / "terminal.log"
        log_path.write_text("opt ok\n", encoding="utf-8")
        report = run_dir / "report.xml"
        report.write_bytes((_FIXTURES / "optimization_report.xml").read_bytes())
        ini_path = run_dir / "run.ini"
        ini_path.write_text(ini_text, encoding="ascii")
        return OptimizeOutcome(
            exit_code=0,
            log_path=log_path,
            report_path=report,
            ini_path=ini_path,
        )


async def _wait_done(state: StateStore, run_id: str, *, timeout: float = 3.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        rec = state.get_run(run_id)
        if rec is not None and rec.status in {"done", "failed", "cancelled"}:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"run {run_id} did not finish within {timeout}s")


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_full_register_compile_backtest_artifact(tmp_path: Path) -> None:
    paths = WorkspacePaths(tmp_path / "ws")
    paths.ensure()
    state = StateStore(paths.state_db)
    runner = FakeRunner()
    manager = RunManager(state_store=state, workspace_paths=paths, wine_runner=runner)
    managed_root = tmp_path / "managed"

    await manager.start()
    try:
        # 1) register
        src = tmp_path / "TemplateEA.mq5"
        src.write_text(
            "// fixture EA for e2e\nint OnInit() { return 0; }\nvoid OnTick() {}\n",
            encoding="utf-8",
        )
        reg = register_ea(
            RegisterEAInput(source_path=src),
            state=state,
            managed_root=managed_root,
        )
        assert reg.ea_handle == "templateea"

        # 2) compile
        compile_out = await compile_ea(
            CompileEAInput(ea_handle=reg.ea_handle),
            state=state,
            manager=manager,
        )
        await _wait_done(state, compile_out.run_id)
        compile_run = get_run(GetRunInput(run_id=compile_out.run_id), state=state)
        assert compile_run.run.status.value == "done"

        # 3) backtest
        bt_out = await backtest_ea(
            BacktestEAInput(
                ea_handle=reg.ea_handle,
                symbol="XAUUSD",
                timeframe="H1",
                date_from=date(2024, 1, 1),
                date_to=date(2024, 1, 7),
                deposit=10_000,
                currency="USD",
                leverage=100,
                model=2,
                spread=0,
            ),
            state=state,
            manager=manager,
        )
        await _wait_done(state, bt_out.run_id)
        bt_run = get_run(GetRunInput(run_id=bt_out.run_id), state=state)
        assert bt_run.run.status.value == "done"
        # parser populated *something* in summary
        assert bt_run.run.summary

        # 4) read the report artifact back through the public API
        art = get_run_artifact(
            GetRunArtifactInput(run_id=bt_out.run_id, artifact="report"),
            state=state,
            paths=paths,
        )
        assert art.encoding == "utf-8"
        assert art.mime_type == "text/html"
        assert "<" in art.content  # any HTML
    finally:
        await manager.stop()
        state.close()
