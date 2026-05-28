"""Run-history tools: list, get, and read run artifacts.

These functions are pure with respect to Wine — they only read state and the
on-disk per-run directory. Artifact paths are constrained to the configured
run directory so a malicious caller cannot read arbitrary files.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Literal

from ..errors import ErrorCode, MT5MCPError, StateError, WorkspaceError
from ..state import StateStore
from ..workspace import WorkspacePaths
from .schemas import (
    ArtifactKind,
    GetRunArtifactInput,
    GetRunArtifactOutput,
    GetRunInput,
    GetRunOutput,
    ListRunsInput,
    ListRunsOutput,
    RunDetail,
    RunKind,
    RunStatus,
    RunSummary,
)

# ---------------------------------------------------------------------------
# list_runs / get_run
# ---------------------------------------------------------------------------


def list_runs(payload: ListRunsInput, *, state: StateStore) -> ListRunsOutput:
    """Return a page of recent runs, newest first.

    The state store's underlying SQL paginates with ``LIMIT`` only; for the
    MCP surface we slice in Python to honour ``offset`` without breaking the
    existing query shape.
    """

    raw = state.list_runs(
        ea_id=payload.ea_handle,
        status=payload.status.value if payload.status is not None else None,
        limit=payload.limit + payload.offset,
    )
    sliced = raw[payload.offset : payload.offset + payload.limit]
    items = [
        RunSummary(
            run_id=r.run_id,
            ea_handle=r.ea_id,
            kind=RunKind(r.kind),
            status=RunStatus(r.status),
            created_at=r.created_at,
            started_at=r.started_at,
            finished_at=r.finished_at,
        )
        for r in sliced
    ]
    return ListRunsOutput(runs=items)


def get_run(payload: GetRunInput, *, state: StateStore) -> GetRunOutput:
    rec = state.get_run(payload.run_id)
    if rec is None:
        raise StateError(
            ErrorCode.RUN_NOT_FOUND,
            f"run {payload.run_id!r} not found",
            details={"run_id": payload.run_id},
        )

    artifacts = rec.artifacts or {}
    detail = RunDetail(
        run_id=rec.run_id,
        ea_handle=rec.ea_id,
        kind=RunKind(rec.kind),
        status=RunStatus(rec.status),
        created_at=rec.created_at,
        started_at=rec.started_at,
        finished_at=rec.finished_at,
        summary=rec.summary or {},
        artifacts=artifacts,
        report_path=artifacts.get("report_path"),
        log_path=artifacts.get("log_path"),
        error_kind=rec.error_code,
        error_message=rec.error_msg,
    )
    return GetRunOutput(run=detail)


# ---------------------------------------------------------------------------
# get_run_artifact
# ---------------------------------------------------------------------------


_ArtifactSpec = tuple[Path, str, Literal["utf-8", "utf-16-le", "base64"]]


def _resolve_artifact(
    paths: WorkspacePaths, run_id: str, artifact: ArtifactKind
) -> _ArtifactSpec:
    """Return ``(path, mime_type, encoding)`` for a known artifact kind.

    Encoding is the *expected* on-disk encoding; we re-detect for compile logs
    written as UTF-16 by MetaEditor.
    """

    run_dir = paths.run_dir(run_id)
    if artifact == "report":
        # Backtest report (HTML) lives at report.html. For optimization runs
        # the SpreadsheetML XML lives at report.xml; pick whichever exists.
        html = run_dir / "report.html"
        xml = run_dir / "report.xml"
        if html.exists():
            return (html, "text/html", "utf-8")
        if xml.exists():
            return (xml, "application/xml", "utf-8")
        return (html, "text/html", "utf-8")  # let caller see the not-found error
    if artifact == "log":
        # Two possibilities: tester (terminal.log) and MetaEditor compile log.
        terminal = run_dir / "terminal.log"
        compile_log = run_dir / "compile.log"
        if terminal.exists():
            return (terminal, "text/plain", "utf-8")
        if compile_log.exists():
            return (compile_log, "text/plain", "utf-16-le")
        return (terminal, "text/plain", "utf-8")
    if artifact == "results":
        return (run_dir / "report.xml", "application/xml", "utf-8")

    raise MT5MCPError(  # pragma: no cover — pydantic validates the literal
        ErrorCode.INTERNAL,
        f"unhandled artifact kind: {artifact!r}",
    )


def get_run_artifact(
    payload: GetRunArtifactInput,
    *,
    state: StateStore,
    paths: WorkspacePaths,
) -> GetRunArtifactOutput:
    rec = state.get_run(payload.run_id)
    if rec is None:
        raise StateError(
            ErrorCode.RUN_NOT_FOUND,
            f"run {payload.run_id!r} not found",
            details={"run_id": payload.run_id},
        )

    target, mime, encoding = _resolve_artifact(paths, payload.run_id, payload.artifact)

    # Defense in depth: ensure target is inside the run directory.
    run_dir = paths.run_dir(payload.run_id).resolve(strict=False)
    try:
        target.resolve(strict=False).relative_to(run_dir)
    except ValueError as exc:
        raise WorkspaceError(
            ErrorCode.WORKSPACE_INVALID,
            f"artifact path escapes run directory: {target}",
        ) from exc

    if not target.exists():
        raise WorkspaceError(
            ErrorCode.RUN_NOT_FOUND,
            f"artifact {payload.artifact!r} for run {payload.run_id!r} not found at {target}",
            details={"run_id": payload.run_id, "artifact": payload.artifact},
        )

    raw = target.read_bytes()
    try:
        if encoding == "utf-16-le":
            text = raw.decode("utf-16-le", errors="replace").lstrip("﻿")
            out_encoding: Literal["utf-8", "utf-16-le", "base64"] = "utf-16-le"
        else:
            text = raw.decode("utf-8")
            out_encoding = "utf-8"
    except UnicodeDecodeError:
        text = base64.b64encode(raw).decode("ascii")
        out_encoding = "base64"

    return GetRunArtifactOutput(content=text, encoding=out_encoding, mime_type=mime)


__all__ = ["get_run", "get_run_artifact", "list_runs"]
