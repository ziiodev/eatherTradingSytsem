"""Unit tests for tools/runs.py — list_runs, get_run, get_run_artifact."""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_metatrader5.errors import ErrorCode, StateError, WorkspaceError
from mcp_metatrader5.state import StateStore
from mcp_metatrader5.tools.runs import get_run, get_run_artifact, list_runs
from mcp_metatrader5.tools.schemas import (
    GetRunArtifactInput,
    GetRunInput,
    ListRunsInput,
    RunStatus,
)
from mcp_metatrader5.workspace import WorkspacePaths


def _setup(tmp_path: Path) -> tuple[StateStore, WorkspacePaths]:
    state = StateStore(tmp_path / "state.sqlite")
    state.upsert_ea(ea_id="ea1", ea_name="EA1", source_path="x.mq5", sha256="0" * 64)
    paths = WorkspacePaths(tmp_path)
    paths.ensure()
    return state, paths


def _seed_run(state: StateStore, run_id: str, *, kind: str = "compile") -> None:
    state.create_run(run_id=run_id, ea_id="ea1", kind=kind)


def test_list_runs_returns_summaries_newest_first(tmp_path: Path) -> None:
    state, _paths = _setup(tmp_path)
    _seed_run(state, "20240101t000000z-aaaaaa")
    _seed_run(state, "20240102t000000z-bbbbbb")
    out = list_runs(ListRunsInput(), state=state)
    ids = [r.run_id for r in out.runs]
    # state.list_runs ORDERs by created_at DESC, so newest is first.
    assert ids == ["20240102t000000z-bbbbbb", "20240101t000000z-aaaaaa"]


def test_list_runs_status_filter(tmp_path: Path) -> None:
    state, _paths = _setup(tmp_path)
    _seed_run(state, "20240101t000000z-aaaaaa")
    _seed_run(state, "20240102t000000z-bbbbbb")
    state.update_run("20240102t000000z-bbbbbb", status="done")

    out = list_runs(ListRunsInput(status=RunStatus.DONE), state=state)
    assert [r.run_id for r in out.runs] == ["20240102t000000z-bbbbbb"]


def test_list_runs_pagination_offset(tmp_path: Path) -> None:
    state, _paths = _setup(tmp_path)
    for i in range(5):
        _seed_run(state, f"2024010{i}t000000z-aaaaaa")
    out = list_runs(ListRunsInput(limit=2, offset=2), state=state)
    assert len(out.runs) == 2


def test_get_run_returns_detail(tmp_path: Path) -> None:
    state, _paths = _setup(tmp_path)
    _seed_run(state, "20240101t000000z-aaaaaa", kind="backtest")
    state.update_run(
        "20240101t000000z-aaaaaa",
        status="done",
        artifacts={"report_path": "/tmp/report.html", "log_path": "/tmp/x.log"},
        summary={"profit": 1.5},
    )
    out = get_run(GetRunInput(run_id="20240101t000000z-aaaaaa"), state=state)
    assert out.run.kind.value == "backtest"
    assert out.run.report_path == "/tmp/report.html"
    assert out.run.summary["profit"] == 1.5


def test_get_run_unknown_raises(tmp_path: Path) -> None:
    state, _paths = _setup(tmp_path)
    with pytest.raises(StateError) as info:
        get_run(GetRunInput(run_id="20240101t000000z-aaaaaa"), state=state)
    assert info.value.code is ErrorCode.RUN_NOT_FOUND


def test_get_run_artifact_reads_html_report(tmp_path: Path) -> None:
    state, paths = _setup(tmp_path)
    rid = "20240101t000000z-aaaaaa"
    _seed_run(state, rid, kind="backtest")
    run_dir = paths.run_dir(rid)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "report.html").write_text("<html>ok</html>", encoding="utf-8")

    out = get_run_artifact(
        GetRunArtifactInput(run_id=rid, artifact="report"),
        state=state,
        paths=paths,
    )
    assert "<html>" in out.content
    assert out.encoding == "utf-8"
    assert out.mime_type == "text/html"


def test_get_run_artifact_reads_utf16_compile_log(tmp_path: Path) -> None:
    state, paths = _setup(tmp_path)
    rid = "20240101t000000z-bbbbbb"
    _seed_run(state, rid)
    run_dir = paths.run_dir(rid)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "compile.log").write_bytes("hello compile\n".encode("utf-16-le"))

    out = get_run_artifact(
        GetRunArtifactInput(run_id=rid, artifact="log"),
        state=state,
        paths=paths,
    )
    assert "hello compile" in out.content
    assert out.encoding == "utf-16-le"


def test_get_run_artifact_missing_raises(tmp_path: Path) -> None:
    state, paths = _setup(tmp_path)
    rid = "20240101t000000z-cccccc"
    _seed_run(state, rid)
    paths.run_dir(rid).mkdir(parents=True, exist_ok=True)

    with pytest.raises(WorkspaceError) as info:
        get_run_artifact(
            GetRunArtifactInput(run_id=rid, artifact="report"),
            state=state,
            paths=paths,
        )
    assert info.value.code is ErrorCode.RUN_NOT_FOUND


def test_get_run_artifact_unknown_run_raises(tmp_path: Path) -> None:
    state, paths = _setup(tmp_path)
    with pytest.raises(StateError) as info:
        get_run_artifact(
            GetRunArtifactInput(run_id="20240101t000000z-aaaaaa", artifact="report"),
            state=state,
            paths=paths,
        )
    assert info.value.code is ErrorCode.RUN_NOT_FOUND
