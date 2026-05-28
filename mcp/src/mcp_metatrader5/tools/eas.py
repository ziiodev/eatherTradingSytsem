"""EA management tools: register / list / get / remove.

These functions are pure with respect to Wine (no subprocess, no MT5). They
read/write the SQLite state store and the on-disk managed workspace.

Workspace layout
----------------
Each EA lives under::

    <wineprefix>/drive_c/Program Files/MetaTrader 5/MQL5/Experts/managed/<ea_handle>/<ea_handle>.mq5

When the server runs without a Wine prefix (``MT5_TERMINAL_PATH`` unset), we
still maintain a managed tree under ``<workspace_dir>/managed/<ea_handle>/`` so
register/list/get/remove work for tooling and tests. The actual location is
resolved by the caller (server bootstrap) which passes a ``managed_root`` path.

Security notes (from the spec)
------------------------------
- ``source_path`` must be readable by the server process — we surface an
  ``EA_SOURCE_INVALID`` error otherwise rather than leaking POSIX errno text.
- We refuse to follow symlinks whose final target lives outside the original
  parent. This is defense-in-depth: a registered file is copied (not linked),
  but a maliciously crafted source could still trick the copy step into
  reading from a sensitive location.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

from ..errors import ErrorCode, WorkspaceError
from ..logging import get_logger
from ..state import StateStore
from ..workspace import slugify
from .schemas import (
    EaDetail,
    EaSummary,
    GetEAOutput,
    ListEAsOutput,
    RegisterEAInput,
    RegisterEAOutput,
    RemoveEAOutput,
)

_log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fp:
        for chunk in iter(lambda: fp.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _resolve_source(source_path: Path) -> Path:
    """Resolve a registration source path to a single ``.mq5`` file.

    - If the path is a file: must end with ``.mq5``.
    - If the path is a directory: must contain exactly one top-level ``.mq5``
      whose stem matches the directory name (or be the only ``.mq5``); we use
      that as the entry source.
    """

    expanded = source_path.expanduser()
    if not expanded.exists():
        raise WorkspaceError(
            ErrorCode.EA_SOURCE_INVALID,
            f"source_path does not exist: {expanded}",
        )

    real = expanded.resolve(strict=True)

    if real.is_file():
        if real.suffix.lower() != ".mq5":
            raise WorkspaceError(
                ErrorCode.EA_SOURCE_INVALID,
                f"source_path must point to a .mq5 file (got {real.name!r})",
            )
        if not os.access(real, os.R_OK):
            raise WorkspaceError(
                ErrorCode.EA_SOURCE_INVALID,
                f"source_path is not readable: {real}",
            )
        return real

    if real.is_dir():
        candidates = sorted(p for p in real.iterdir() if p.is_file() and p.suffix.lower() == ".mq5")
        if not candidates:
            raise WorkspaceError(
                ErrorCode.EA_SOURCE_INVALID,
                f"directory contains no .mq5 file: {real}",
            )
        if len(candidates) > 1:
            same_stem = [p for p in candidates if p.stem == real.name]
            if len(same_stem) == 1:
                return same_stem[0]
            raise WorkspaceError(
                ErrorCode.EA_SOURCE_INVALID,
                "directory contains multiple .mq5 files; provide the file path "
                f"explicitly. Found: {[p.name for p in candidates]}",
            )
        return candidates[0]

    raise WorkspaceError(
        ErrorCode.EA_SOURCE_INVALID,
        f"source_path is neither a file nor a directory: {real}",
    )


def _derive_handle(*, ea_name: str | None, source: Path) -> str:
    base = ea_name if ea_name else source.stem
    return slugify(base)


def _ea_dir(managed_root: Path, ea_handle: str) -> Path:
    return managed_root / ea_handle


def _ea_file(managed_root: Path, ea_handle: str) -> Path:
    return _ea_dir(managed_root, ea_handle) / f"{ea_handle}.mq5"


# ---------------------------------------------------------------------------
# register_ea
# ---------------------------------------------------------------------------


def register_ea(
    payload: RegisterEAInput,
    *,
    state: StateStore,
    managed_root: Path,
) -> RegisterEAOutput:
    """Copy a ``.mq5`` source into the managed workspace and persist a row.

    Parameters
    ----------
    payload:
        Validated input from MCP.
    state:
        SQLite state store.
    managed_root:
        Absolute path to the directory under which one subdir per EA lives.
        Server bootstrap passes ``MQL5/Experts/managed/`` (under Wine prefix
        when present) or a fallback under the workspace root.
    """

    src = _resolve_source(payload.source_path)
    handle = _derive_handle(ea_name=payload.ea_name, source=src)

    existing = state.get_ea(handle)
    if existing is not None and not payload.overwrite:
        raise WorkspaceError(
            ErrorCode.EA_ALREADY_EXISTS,
            f"ea_handle {handle!r} already registered; pass overwrite=true to replace",
            details={"ea_handle": handle},
        )

    # Refuse if any active runs (queued/running) reference this handle when
    # overwriting — replacing source under an in-flight job is a footgun.
    if existing is not None and payload.overwrite:
        active = state.list_runs(ea_id=handle, status="queued") + state.list_runs(
            ea_id=handle, status="running"
        )
        if active:
            raise WorkspaceError(
                ErrorCode.EA_ALREADY_EXISTS,
                f"ea_handle {handle!r} has {len(active)} active run(s); cannot overwrite",
                details={"ea_handle": handle, "active_runs": [r.run_id for r in active]},
            )

    target_dir = _ea_dir(managed_root, handle)
    target_file = _ea_file(managed_root, handle)
    target_dir.mkdir(parents=True, exist_ok=True)

    # Copy source as bytes so we don't rely on text-mode encoding guessing.
    shutil.copyfile(src, target_file)
    sha = _sha256_file(target_file)

    canonical_name = payload.ea_name if payload.ea_name else src.stem
    rec = state.upsert_ea(
        ea_id=handle,
        ea_name=canonical_name,
        source_path=str(target_file),
        sha256=sha,
    )
    _log.info(
        "ea_registered",
        ea_handle=handle,
        ea_name=canonical_name,
        sha256=sha,
        path=str(target_file),
    )

    return RegisterEAOutput(
        ea_handle=handle,
        workspace_path=str(target_file),
        registered_at=rec.updated_at,
    )


# ---------------------------------------------------------------------------
# list / get / remove
# ---------------------------------------------------------------------------


def list_eas(*, state: StateStore) -> ListEAsOutput:
    items = [
        EaSummary(
            ea_handle=r.ea_id,
            ea_name=r.ea_name,
            created_at=r.created_at,
            updated_at=r.updated_at,
            sha256=r.sha256,
        )
        for r in state.list_eas()
    ]
    return ListEAsOutput(eas=items)


def get_ea(ea_handle: str, *, state: StateStore) -> GetEAOutput:
    rec = state.get_ea(ea_handle)
    if rec is None:
        raise WorkspaceError(
            ErrorCode.EA_NOT_FOUND,
            f"ea_handle {ea_handle!r} not found",
            details={"ea_handle": ea_handle},
        )
    return GetEAOutput(
        ea=EaDetail(
            ea_handle=rec.ea_id,
            ea_name=rec.ea_name,
            created_at=rec.created_at,
            updated_at=rec.updated_at,
            sha256=rec.sha256,
            workspace_path=rec.source_path,
        )
    )


def remove_ea(
    ea_handle: str,
    *,
    state: StateStore,
    managed_root: Path,
    also_delete_workspace: bool = False,
) -> RemoveEAOutput:
    rec = state.get_ea(ea_handle)
    if rec is None:
        raise WorkspaceError(
            ErrorCode.EA_NOT_FOUND,
            f"ea_handle {ea_handle!r} not found",
            details={"ea_handle": ea_handle},
        )

    active = state.list_runs(ea_id=ea_handle, status="queued") + state.list_runs(
        ea_id=ea_handle, status="running"
    )
    if active:
        raise WorkspaceError(
            ErrorCode.EA_ALREADY_EXISTS,
            f"ea_handle {ea_handle!r} has {len(active)} active run(s); cannot remove",
            details={"ea_handle": ea_handle, "active_runs": [r.run_id for r in active]},
        )

    removed = state.delete_ea(ea_handle)
    if also_delete_workspace:
        ea_dir = _ea_dir(managed_root, ea_handle)
        if ea_dir.is_dir():
            # Defense in depth: ensure ea_dir is inside managed_root.
            try:
                ea_dir.resolve(strict=True).relative_to(managed_root.resolve(strict=False))
            except ValueError as exc:
                raise WorkspaceError(
                    ErrorCode.WORKSPACE_INVALID,
                    f"refusing to delete path outside managed_root: {ea_dir}",
                ) from exc
            shutil.rmtree(ea_dir)

    _log.info("ea_removed", ea_handle=ea_handle, deleted_workspace=also_delete_workspace)
    return RemoveEAOutput(removed=removed)


__all__ = [
    "get_ea",
    "list_eas",
    "register_ea",
    "remove_ea",
]
