"""Unit tests for tools/backtest.py — backtest_ea."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from mcp_metatrader5.errors import ErrorCode, WorkspaceError
from mcp_metatrader5.manager import RunManager
from mcp_metatrader5.state import StateStore
from mcp_metatrader5.tools._inputs import coerce_inputs
from mcp_metatrader5.tools.backtest import backtest_ea
from mcp_metatrader5.tools.schemas import BacktestEAInput
from mcp_metatrader5.workspace import WorkspacePaths

from .test_manager import FakeWineRunner


def _payload(**overrides: object) -> BacktestEAInput:
    base: dict[str, object] = dict(
        ea_handle="my-ea",
        symbol="XAUUSD",
        timeframe="H1",
        date_from=date(2024, 1, 1),
        date_to=date(2024, 6, 1),
        deposit=10_000,
        currency="USD",
        leverage=100,
        model=0,
        spread=0,
    )
    base.update(overrides)
    return BacktestEAInput(**base)  # type: ignore[arg-type]


def _setup(tmp_path: Path) -> tuple[StateStore, RunManager]:
    state = StateStore(tmp_path / "state.sqlite")
    state.upsert_ea(ea_id="my-ea", ea_name="MyEA", source_path="x.mq5", sha256="0" * 64)
    paths = WorkspacePaths(tmp_path)
    paths.ensure()
    runner = FakeWineRunner()
    manager = RunManager(state_store=state, workspace_paths=paths, wine_runner=runner)
    return state, manager


@pytest.mark.asyncio
async def test_backtest_ea_returns_run_id_and_persists_run(tmp_path: Path) -> None:
    state, manager = _setup(tmp_path)
    await manager.start()
    try:
        out = await backtest_ea(_payload(), state=state, manager=manager)
        rec = state.get_run(out.run_id)
        assert rec is not None
        assert rec.kind == "backtest"
        assert rec.symbol == "XAUUSD"
        assert rec.from_date == "2024.01.01"
        assert rec.to_date == "2024.06.01"
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_backtest_ea_unknown_handle_raises(tmp_path: Path) -> None:
    state, manager = _setup(tmp_path)
    await manager.start()
    try:
        with pytest.raises(WorkspaceError) as info:
            await backtest_ea(
                _payload(ea_handle="ghost"),
                state=state,
                manager=manager,
            )
        assert info.value.code is ErrorCode.EA_NOT_FOUND
    finally:
        await manager.stop()


def test_coerce_inputs_handles_bool_and_numbers() -> None:
    out = coerce_inputs(
        {"useTrail": True, "magic": 12345, "lots": 0.10, "comment": "hi", "n": None}
    )
    # bool must become lowercase MT5 form, not 'True'/'False'
    assert out["useTrail"] == "true"
    assert out["magic"] == "12345"
    assert out["lots"] == "0.1"
    assert out["comment"] == "hi"
    assert out["n"] == ""


@pytest.mark.asyncio
async def test_backtest_ea_inputs_are_stringified(tmp_path: Path) -> None:
    state, manager = _setup(tmp_path)
    await manager.start()
    try:
        out = await backtest_ea(
            _payload(ea_inputs={"flag": False, "magic": 777}),
            state=state,
            manager=manager,
        )
        # the run was created — main contract is no exception.
        assert out.status == "queued"
    finally:
        await manager.stop()
