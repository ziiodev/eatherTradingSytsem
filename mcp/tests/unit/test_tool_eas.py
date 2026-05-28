"""Unit tests for tools/eas.py — register/list/get/remove EA."""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_metatrader5.errors import ErrorCode, WorkspaceError
from mcp_metatrader5.state import StateStore
from mcp_metatrader5.tools.eas import get_ea, list_eas, register_ea, remove_ea
from mcp_metatrader5.tools.schemas import RegisterEAInput


def _make_state(tmp_path: Path) -> StateStore:
    return StateStore(tmp_path / "state.sqlite")


def _write_mq5(path: Path, *, body: str = "// hello") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def test_register_ea_copies_source_and_persists_row(tmp_path: Path) -> None:
    state = _make_state(tmp_path)
    managed_root = tmp_path / "managed"
    src = _write_mq5(tmp_path / "src" / "MyEA.mq5")

    out = register_ea(
        RegisterEAInput(source_path=src, ea_name="MyEA"),
        state=state,
        managed_root=managed_root,
    )

    assert out.ea_handle == "myea"
    target = managed_root / "myea" / "myea.mq5"
    assert target.is_file()
    assert target.read_text() == "// hello"

    rec = state.get_ea("myea")
    assert rec is not None
    assert rec.ea_name == "MyEA"
    assert rec.source_path == str(target)
    assert len(rec.sha256) == 64


def test_register_ea_default_name_uses_basename(tmp_path: Path) -> None:
    state = _make_state(tmp_path)
    src = _write_mq5(tmp_path / "Hello World.mq5")

    out = register_ea(
        RegisterEAInput(source_path=src),
        state=state,
        managed_root=tmp_path / "managed",
    )
    assert out.ea_handle == "hello-world"


def test_register_ea_rejects_non_mq5(tmp_path: Path) -> None:
    state = _make_state(tmp_path)
    bad = tmp_path / "notes.txt"
    bad.write_text("nope")

    with pytest.raises(WorkspaceError) as info:
        register_ea(
            RegisterEAInput(source_path=bad),
            state=state,
            managed_root=tmp_path / "managed",
        )
    assert info.value.code is ErrorCode.EA_SOURCE_INVALID


def test_register_ea_rejects_missing_path(tmp_path: Path) -> None:
    state = _make_state(tmp_path)
    with pytest.raises(WorkspaceError) as info:
        register_ea(
            RegisterEAInput(source_path=tmp_path / "missing.mq5"),
            state=state,
            managed_root=tmp_path / "managed",
        )
    assert info.value.code is ErrorCode.EA_SOURCE_INVALID


def test_register_ea_directory_with_single_mq5(tmp_path: Path) -> None:
    state = _make_state(tmp_path)
    src_dir = tmp_path / "EAPack"
    _write_mq5(src_dir / "Strategy.mq5", body="// strategy")

    out = register_ea(
        RegisterEAInput(source_path=src_dir, ea_name="Strategy"),
        state=state,
        managed_root=tmp_path / "managed",
    )
    assert out.ea_handle == "strategy"


def test_register_ea_directory_multiple_mq5_rejected(tmp_path: Path) -> None:
    state = _make_state(tmp_path)
    src_dir = tmp_path / "MultiEA"
    _write_mq5(src_dir / "OneEA.mq5", body="// 1")
    _write_mq5(src_dir / "TwoEA.mq5", body="// 2")

    with pytest.raises(WorkspaceError) as info:
        register_ea(
            RegisterEAInput(source_path=src_dir),
            state=state,
            managed_root=tmp_path / "managed",
        )
    assert info.value.code is ErrorCode.EA_SOURCE_INVALID


def test_register_ea_rejects_duplicate_without_overwrite(tmp_path: Path) -> None:
    state = _make_state(tmp_path)
    src = _write_mq5(tmp_path / "Dup.mq5")
    register_ea(
        RegisterEAInput(source_path=src),
        state=state,
        managed_root=tmp_path / "managed",
    )
    with pytest.raises(WorkspaceError) as info:
        register_ea(
            RegisterEAInput(source_path=src),
            state=state,
            managed_root=tmp_path / "managed",
        )
    assert info.value.code is ErrorCode.EA_ALREADY_EXISTS


def test_register_ea_overwrite_replaces_existing(tmp_path: Path) -> None:
    state = _make_state(tmp_path)
    src = _write_mq5(tmp_path / "EA.mq5", body="// v1")
    register_ea(
        RegisterEAInput(source_path=src),
        state=state,
        managed_root=tmp_path / "managed",
    )
    src.write_text("// v2", encoding="utf-8")
    out = register_ea(
        RegisterEAInput(source_path=src, overwrite=True),
        state=state,
        managed_root=tmp_path / "managed",
    )
    rec = state.get_ea(out.ea_handle)
    assert rec is not None
    target = Path(rec.source_path)
    assert target.read_text() == "// v2"


def test_register_ea_overwrite_blocked_by_active_run(tmp_path: Path) -> None:
    state = _make_state(tmp_path)
    src = _write_mq5(tmp_path / "Busy.mq5")
    register_ea(
        RegisterEAInput(source_path=src),
        state=state,
        managed_root=tmp_path / "managed",
    )
    state.create_run(run_id="20990101t000000z-aaaaaa", ea_id="busy", kind="compile")

    with pytest.raises(WorkspaceError) as info:
        register_ea(
            RegisterEAInput(source_path=src, overwrite=True),
            state=state,
            managed_root=tmp_path / "managed",
        )
    assert info.value.code is ErrorCode.EA_ALREADY_EXISTS


def test_list_eas_orders_by_creation(tmp_path: Path) -> None:
    state = _make_state(tmp_path)
    a = _write_mq5(tmp_path / "Alpha.mq5")
    b = _write_mq5(tmp_path / "Beta.mq5")
    register_ea(
        RegisterEAInput(source_path=a),
        state=state,
        managed_root=tmp_path / "managed",
    )
    register_ea(
        RegisterEAInput(source_path=b),
        state=state,
        managed_root=tmp_path / "managed",
    )
    out = list_eas(state=state)
    assert [e.ea_handle for e in out.eas] == ["alpha", "beta"]


def test_get_ea_returns_detail(tmp_path: Path) -> None:
    state = _make_state(tmp_path)
    src = _write_mq5(tmp_path / "OneEA.mq5")
    register_ea(
        RegisterEAInput(source_path=src),
        state=state,
        managed_root=tmp_path / "managed",
    )
    out = get_ea("oneea", state=state)
    assert out.ea.ea_handle == "oneea"
    assert out.ea.workspace_path.endswith("oneea.mq5")


def test_get_ea_unknown_raises(tmp_path: Path) -> None:
    state = _make_state(tmp_path)
    with pytest.raises(WorkspaceError) as info:
        get_ea("ghost", state=state)
    assert info.value.code is ErrorCode.EA_NOT_FOUND


def test_remove_ea_clears_state_and_optionally_files(tmp_path: Path) -> None:
    state = _make_state(tmp_path)
    src = _write_mq5(tmp_path / "Tmp.mq5")
    out = register_ea(
        RegisterEAInput(source_path=src),
        state=state,
        managed_root=tmp_path / "managed",
    )
    target_dir = (tmp_path / "managed" / out.ea_handle)
    assert target_dir.exists()

    res = remove_ea(
        out.ea_handle,
        state=state,
        managed_root=tmp_path / "managed",
        also_delete_workspace=True,
    )
    assert res.removed is True
    assert state.get_ea(out.ea_handle) is None
    assert not target_dir.exists()


def test_remove_ea_unknown_raises(tmp_path: Path) -> None:
    state = _make_state(tmp_path)
    with pytest.raises(WorkspaceError) as info:
        remove_ea("ghost", state=state, managed_root=tmp_path / "managed")
    assert info.value.code is ErrorCode.EA_NOT_FOUND


def test_remove_ea_blocked_by_active_run(tmp_path: Path) -> None:
    state = _make_state(tmp_path)
    src = _write_mq5(tmp_path / "Active.mq5")
    register_ea(
        RegisterEAInput(source_path=src),
        state=state,
        managed_root=tmp_path / "managed",
    )
    state.create_run(run_id="20990101t000000z-aaaaaa", ea_id="active", kind="backtest")
    state.update_run("20990101t000000z-aaaaaa", status="running")

    with pytest.raises(WorkspaceError) as info:
        remove_ea("active", state=state, managed_root=tmp_path / "managed")
    assert info.value.code is ErrorCode.EA_ALREADY_EXISTS
