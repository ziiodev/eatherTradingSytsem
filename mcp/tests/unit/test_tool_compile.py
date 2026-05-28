"""Unit tests for tools/compile.py — compile_ea."""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_metatrader5.errors import ErrorCode, WorkspaceError
from mcp_metatrader5.manager import RunManager
from mcp_metatrader5.state import StateStore
from mcp_metatrader5.tools.compile import compile_ea
from mcp_metatrader5.tools.schemas import CompileEAInput
from mcp_metatrader5.workspace import WorkspacePaths

from .test_manager import FakeWineRunner


def _setup(tmp_path: Path) -> tuple[StateStore, WorkspacePaths, FakeWineRunner, RunManager]:
    state = StateStore(tmp_path / "state.sqlite")
    state.upsert_ea(ea_id="my-ea", ea_name="MyEA", source_path="x.mq5", sha256="0" * 64)
    paths = WorkspacePaths(tmp_path)
    paths.ensure()
    runner = FakeWineRunner()
    manager = RunManager(state_store=state, workspace_paths=paths, wine_runner=runner)
    return state, paths, runner, manager


@pytest.mark.asyncio
async def test_compile_ea_returns_run_id(tmp_path: Path) -> None:
    state, _paths, _runner, manager = _setup(tmp_path)
    await manager.start()
    try:
        out = await compile_ea(
            CompileEAInput(ea_handle="my-ea"),
            state=state,
            manager=manager,
        )
        assert out.status == "queued"
        assert out.run_id
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_compile_ea_unknown_handle_raises(tmp_path: Path) -> None:
    state, _paths, _runner, manager = _setup(tmp_path)
    await manager.start()
    try:
        with pytest.raises(WorkspaceError) as info:
            await compile_ea(
                CompileEAInput(ea_handle="nonexistent"),
                state=state,
                manager=manager,
            )
        assert info.value.code is ErrorCode.EA_NOT_FOUND
    finally:
        await manager.stop()
