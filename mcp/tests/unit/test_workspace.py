"""Unit tests for workspace path/slug/lock helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from mcp_metatrader5.errors import LockError, WorkspaceError
from mcp_metatrader5.workspace import (
    WorkspacePaths,
    is_valid_slug,
    is_valid_slug_or_runid,
    new_run_id,
    slugify,
    workspace_lock,
)


def test_slugify_basic() -> None:
    assert slugify("My EA v1") == "my-ea-v1"


def test_slugify_strips_unicode() -> None:
    assert slugify("Año Niño") == "ano-nino"


def test_slugify_truncates() -> None:
    out = slugify("a" * 200, max_len=10)
    assert out == "a" * 10


def test_slugify_rejects_empty() -> None:
    with pytest.raises(WorkspaceError):
        slugify("!!!")


def test_is_valid_slug() -> None:
    assert is_valid_slug("my-ea")
    assert is_valid_slug("ea1")
    assert not is_valid_slug("My-EA")  # uppercase
    assert not is_valid_slug("-ea")
    assert not is_valid_slug("ea-")
    assert not is_valid_slug("")


def test_new_run_id_format() -> None:
    moment = datetime(2026, 5, 10, 12, 34, 56, tzinfo=UTC)
    rid = new_run_id(now=moment)
    assert rid.startswith("20260510T123456Z-")
    assert is_valid_slug_or_runid(rid)


def test_workspace_paths_layout(tmp_path: Path) -> None:
    wp = WorkspacePaths(tmp_path)
    wp.ensure()
    assert wp.state_db == tmp_path / "state.sqlite"
    assert wp.lock_file == tmp_path / "mcp.lock"
    assert wp.runs_dir == tmp_path / "runs"
    assert (tmp_path / "runs").is_dir()


def test_workspace_paths_run_paths(tmp_path: Path) -> None:
    wp = WorkspacePaths(tmp_path)
    rid = "20260101T000000Z-abcdef"
    assert wp.run_dir(rid) == tmp_path / "runs" / rid
    assert wp.run_ini(rid).name == "run.ini"
    assert wp.run_terminal_log(rid).name == "terminal.log"
    assert wp.run_report_html(rid).name == "report.html"
    assert wp.run_report_xml(rid).name == "report.xml"
    assert wp.run_compile_log(rid).name == "compile.log"


def test_workspace_paths_rejects_bad_run_id(tmp_path: Path) -> None:
    wp = WorkspacePaths(tmp_path)
    with pytest.raises(WorkspaceError):
        wp.run_dir("../../etc/passwd")


def test_workspace_lock_acquires_and_releases(tmp_path: Path) -> None:
    lock_path = tmp_path / "mcp.lock"
    with workspace_lock(lock_path):
        assert lock_path.exists()
    # second acquisition after release should succeed
    with workspace_lock(lock_path):
        pass


def test_workspace_lock_contention(tmp_path: Path) -> None:
    lock_path = tmp_path / "mcp.lock"
    with (
        workspace_lock(lock_path),
        pytest.raises(LockError),
        workspace_lock(lock_path, timeout=0.0),
    ):
        pass  # pragma: no cover - never reached
