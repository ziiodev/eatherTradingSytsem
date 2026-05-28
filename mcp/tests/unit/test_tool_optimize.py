"""Unit tests for tools/optimize.py — optimize_ea + values-list translation."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from mcp_metatrader5.errors import ErrorCode, MT5MCPError, WorkspaceError
from mcp_metatrader5.manager import RunManager
from mcp_metatrader5.state import StateStore
from mcp_metatrader5.tools.optimize import _values_to_range, optimize_ea
from mcp_metatrader5.tools.schemas import OptimizeEAInput, ParameterRange
from mcp_metatrader5.workspace import WorkspacePaths

from .test_manager import FakeWineRunner


def _payload(**overrides: object) -> OptimizeEAInput:
    base: dict[str, object] = dict(
        ea_handle="my-ea",
        symbol="XAUUSD",
        timeframe="H1",
        date_from=date(2024, 1, 1),
        date_to=date(2024, 6, 1),
        deposit=10_000,
        currency="USD",
        leverage=100,
        model=2,
        spread=0,
        parameter_ranges={"period": ParameterRange(start=10, stop=30, step=5)},
        criterion=0,
        mode=1,
    )
    base.update(overrides)
    return OptimizeEAInput(**base)  # type: ignore[arg-type]


def _setup(tmp_path: Path) -> tuple[StateStore, RunManager]:
    state = StateStore(tmp_path / "state.sqlite")
    state.upsert_ea(ea_id="my-ea", ea_name="MyEA", source_path="x.mq5", sha256="0" * 64)
    paths = WorkspacePaths(tmp_path)
    paths.ensure()
    runner = FakeWineRunner()
    manager = RunManager(state_store=state, workspace_paths=paths, wine_runner=runner)
    return state, manager


@pytest.mark.asyncio
async def test_optimize_ea_with_range_form(tmp_path: Path) -> None:
    state, manager = _setup(tmp_path)
    await manager.start()
    try:
        out = await optimize_ea(_payload(), state=state, manager=manager)
        rec = state.get_run(out.run_id)
        assert rec is not None
        assert rec.kind == "optimize"
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_optimize_ea_with_uniform_values_list(tmp_path: Path) -> None:
    state, manager = _setup(tmp_path)
    await manager.start()
    try:
        out = await optimize_ea(
            _payload(parameter_ranges={"step": ParameterRange(values=[1.0, 2.0, 3.0])}),
            state=state,
            manager=manager,
        )
        assert out.status == "queued"
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_optimize_ea_with_non_uniform_values_rejected(tmp_path: Path) -> None:
    state, manager = _setup(tmp_path)
    await manager.start()
    try:
        with pytest.raises(MT5MCPError) as info:
            await optimize_ea(
                _payload(parameter_ranges={"x": ParameterRange(values=[1.0, 2.0, 4.0])}),
                state=state,
                manager=manager,
            )
        assert info.value.code is ErrorCode.INVALID_INPUT
        assert "uniform" in info.value.message
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_optimize_ea_unknown_handle_raises(tmp_path: Path) -> None:
    state, manager = _setup(tmp_path)
    await manager.start()
    try:
        with pytest.raises(WorkspaceError) as info:
            await optimize_ea(
                _payload(ea_handle="ghost"),
                state=state,
                manager=manager,
            )
        assert info.value.code is ErrorCode.EA_NOT_FOUND
    finally:
        await manager.stop()


def test_values_to_range_uniform_int() -> None:
    assert _values_to_range("p", [1, 2, 3, 4]) == ("1", "1", "4")


def test_values_to_range_uniform_float() -> None:
    assert _values_to_range("p", [0.1, 0.2, 0.3]) == ("0.1", "0.1", "0.3")


def test_values_to_range_singleton() -> None:
    assert _values_to_range("p", [42.0]) == ("42", "1", "42")


def test_values_to_range_non_uniform_raises() -> None:
    with pytest.raises(MT5MCPError) as info:
        _values_to_range("p", [1.0, 2.0, 4.0])
    assert info.value.code is ErrorCode.INVALID_INPUT
