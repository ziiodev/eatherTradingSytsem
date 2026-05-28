"""Integration test: register + compile + backtest a 1-week window.

Skipped unless ``MT5_PREFIX`` is set. Importable + mypy-clean even when
skipped; this is enforced by CI.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("MT5_PREFIX") is None,
    reason="MT5_PREFIX not set; skipping Wine integration tests",
)


_TEMPLATE_EA = """\
//+------------------------------------------------------------------+
//| Template EA used for backtest integration smoke tests.            |
//+------------------------------------------------------------------+
input double Lots = 0.10;
int OnInit()    { return INIT_SUCCEEDED; }
void OnDeinit(const int reason) { }
void OnTick()  { }
"""


@pytest.mark.wine
@pytest.mark.integration
@pytest.mark.asyncio
async def test_one_week_backtest(tmp_path: Path) -> None:
    """Run a one-week backtest and assert the run row reaches a terminal state."""

    from mcp_metatrader5.config import load_settings
    from mcp_metatrader5.manager import RunManager
    from mcp_metatrader5.state import StateStore
    from mcp_metatrader5.tools.backtest import backtest_ea
    from mcp_metatrader5.tools.compile import compile_ea
    from mcp_metatrader5.tools.eas import register_ea
    from mcp_metatrader5.tools.schemas import (
        BacktestEAInput,
        CompileEAInput,
        RegisterEAInput,
    )
    from mcp_metatrader5.workspace import WorkspacePaths

    src = tmp_path / "TemplateEA.mq5"
    src.write_text(_TEMPLATE_EA, encoding="utf-8")

    settings = load_settings(dict(os.environ))
    paths = WorkspacePaths(tmp_path / "ws")
    paths.ensure()
    state = StateStore(paths.state_db)

    pytest.skip(
        "Real Wine runner not yet implemented (Phase 3); integration scaffold ready."
    )

    runner = ...  # type: ignore[assignment,unreachable]
    manager = RunManager(state_store=state, workspace_paths=paths, wine_runner=runner)
    await manager.start()
    try:
        register_ea(
            RegisterEAInput(source_path=src),
            state=state,
            managed_root=tmp_path / "managed",
        )
        await compile_ea(
            CompileEAInput(ea_handle="templateea"),
            state=state,
            manager=manager,
        )
        out = await backtest_ea(
            BacktestEAInput(
                ea_handle="templateea",
                symbol="EURUSD",
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
        assert out.run_id
    finally:
        await manager.stop()
        state.close()
        _ = settings
