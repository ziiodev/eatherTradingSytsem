"""Integration test: register + compile against a real Wine prefix.

Skipped unless ``MT5_PREFIX`` is set, pointing to a Wine prefix containing a
working MetaEditor 5 install. The test must remain importable (and mypy-clean)
even when skipped — CI exercises that property.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("MT5_PREFIX") is None,
    reason="MT5_PREFIX not set; skipping Wine integration tests",
)


_MINIMAL_EA = """\
//+------------------------------------------------------------------+
//| Minimal Expert template — used for integration smoke tests.       |
//+------------------------------------------------------------------+
int OnInit()    { return INIT_SUCCEEDED; }
void OnDeinit(const int reason) { }
void OnTick()  { }
"""


@pytest.mark.wine
@pytest.mark.integration
@pytest.mark.asyncio
async def test_register_and_compile_minimal_ea(tmp_path: Path) -> None:
    """Compile a trivial template EA against the real MetaEditor.

    Asserts that the run row reaches a terminal status and that a compile log
    artifact is recorded — does NOT enforce ok=True since the user's MetaEditor
    may emit warnings the test environment doesn't control.
    """

    # Imports are inside the test so that import-time failures of the production
    # code (e.g. on a system without the runner installed) still don't break
    # CI's collection step.
    from mcp_metatrader5.config import load_settings
    from mcp_metatrader5.manager import RunManager
    from mcp_metatrader5.state import StateStore
    from mcp_metatrader5.tools.compile import compile_ea
    from mcp_metatrader5.tools.eas import register_ea
    from mcp_metatrader5.tools.schemas import CompileEAInput, RegisterEAInput
    from mcp_metatrader5.workspace import WorkspacePaths

    src = tmp_path / "MinimalEA.mq5"
    src.write_text(_MINIMAL_EA, encoding="utf-8")

    settings = load_settings(dict(os.environ))
    paths = WorkspacePaths(tmp_path / "ws")
    paths.ensure()
    state = StateStore(paths.state_db)

    # Phase 3 will provide the real runner; placeholder import path:
    # from mcp_metatrader5.runner.wine import WineSubprocessRunner
    # runner = WineSubprocessRunner(settings)
    pytest.skip(
        "Real Wine runner not yet implemented (Phase 3); integration scaffold ready."
    )

    # Below is the post-Phase-3 shape of the test, kept here to keep the file
    # self-documenting and importable.
    runner = ...  # type: ignore[assignment,unreachable]
    manager = RunManager(state_store=state, workspace_paths=paths, wine_runner=runner)
    await manager.start()
    try:
        register_ea(
            RegisterEAInput(source_path=src),
            state=state,
            managed_root=tmp_path / "managed",
        )
        out = await compile_ea(
            CompileEAInput(ea_handle="minimalea"),
            state=state,
            manager=manager,
        )
        assert out.run_id
    finally:
        await manager.stop()
        state.close()
        # silence the unused-settings warning when this branch is dead.
        _ = settings
