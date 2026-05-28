"""Workspace layout, slug helpers, and an advisory file lock.

This module is **pure** — it does not invoke Wine, MetaEditor, or MT5. It only
resolves paths, generates stable slugs, and provides a cross-process advisory
lock via :mod:`fcntl`.

Workspace layout (under ``settings.workspace_dir``)::

    workspace/
      state.sqlite                       # state store
      mcp.lock                           # advisory flock target
      runs/<run_id>/                     # per-run artifacts
        run.ini
        terminal.log
        report.html
        report.xml                       # optimization cache
        compile.log
"""

from __future__ import annotations

import contextlib
import errno
import fcntl
import os
import re
import secrets
import time
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .errors import ErrorCode, LockError, WorkspaceError

# A slug is lowercase ASCII alphanumerics plus single hyphens, 1-64 chars.
_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_SLUG_INVALID_CHARS_RE = re.compile(r"[^a-z0-9]+")


def slugify(value: str, *, max_len: int = 64) -> str:
    """Return a filesystem-safe slug derived from *value*.

    - Unicode normalised to NFKD; non-ASCII stripped.
    - Lowercased.
    - Non-alphanumeric runs collapsed to ``-``.
    - Trimmed of leading/trailing hyphens.
    - Truncated to ``max_len``.

    Raises :class:`WorkspaceError` if the result would be empty.
    """

    if not isinstance(value, str):  # pragma: no cover - defensive
        raise WorkspaceError(
            ErrorCode.WORKSPACE_INVALID,
            f"slugify expects str, got {type(value).__name__}",
        )

    # Strip accents, then keep ASCII letters/digits.
    normalised = unicodedata.normalize("NFKD", value)
    ascii_only = normalised.encode("ascii", "ignore").decode("ascii")
    lowered = ascii_only.lower()
    collapsed = _SLUG_INVALID_CHARS_RE.sub("-", lowered).strip("-")
    truncated = collapsed[:max_len].strip("-")

    if not truncated:
        raise WorkspaceError(
            ErrorCode.WORKSPACE_INVALID,
            f"value {value!r} does not produce a valid slug",
        )
    return truncated


def is_valid_slug(value: str) -> bool:
    """Return True iff *value* matches the canonical slug grammar."""
    return bool(_SLUG_RE.match(value))


def new_run_id(*, now: datetime | None = None) -> str:
    """Return a chronologically-sortable run identifier.

    Format: ``YYYYMMDDTHHMMSSZ-<6 hex>`` — sorts lexically by start time.
    """

    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    timestamp = moment.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    suffix = secrets.token_hex(3)
    return f"{timestamp}-{suffix}"


class WorkspacePaths:
    """Path resolver for the on-disk workspace.

    Construct once per server lifetime; methods are cheap.
    """

    def __init__(self, root: Path) -> None:
        self.root = root

    # -------- top-level paths

    @property
    def state_db(self) -> Path:
        return self.root / "state.sqlite"

    @property
    def lock_file(self) -> Path:
        return self.root / "mcp.lock"

    @property
    def runs_dir(self) -> Path:
        return self.root / "runs"

    # -------- per-run paths

    def run_dir(self, run_id: str) -> Path:
        if not is_valid_slug_or_runid(run_id):
            raise WorkspaceError(
                ErrorCode.WORKSPACE_INVALID,
                f"invalid run_id {run_id!r}",
            )
        return self.runs_dir / run_id

    def run_ini(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "run.ini"

    def run_terminal_log(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "terminal.log"

    def run_report_html(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "report.html"

    def run_report_xml(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "report.xml"

    def run_compile_log(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "compile.log"

    # -------- bootstrap

    def ensure(self) -> None:
        """Create workspace directory structure if missing."""
        self.root.mkdir(parents=True, exist_ok=True)
        self.runs_dir.mkdir(parents=True, exist_ok=True)


_RUN_ID_RE = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{6}$")


def is_valid_slug_or_runid(value: str) -> bool:
    """Accept either a regular slug or a generated run id."""
    return bool(_SLUG_RE.match(value) or _RUN_ID_RE.match(value))


@contextmanager
def workspace_lock(
    lock_path: Path,
    *,
    timeout: float = 0.0,
    poll_interval: float = 0.1,
) -> Iterator[None]:
    """Acquire an exclusive advisory lock on *lock_path* via :func:`fcntl.flock`.

    Parameters
    ----------
    lock_path:
        File whose flock is taken. Created (touched) if missing.
    timeout:
        Seconds to wait for the lock. ``0`` means non-blocking — raise
        :class:`LockError` immediately if held.
    poll_interval:
        How long to sleep between retries when ``timeout > 0``.

    Raises
    ------
    LockError
        If the lock cannot be acquired within *timeout* seconds.
    """

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in (errno.EWOULDBLOCK, errno.EAGAIN):
                    raise
                if time.monotonic() >= deadline:
                    raise LockError(
                        ErrorCode.LOCK_HELD,
                        f"workspace lock {lock_path} is held by another process",
                    ) from exc
                time.sleep(poll_interval)
        try:
            yield
        finally:
            with contextlib.suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


__all__ = [
    "WorkspacePaths",
    "is_valid_slug",
    "is_valid_slug_or_runid",
    "new_run_id",
    "slugify",
    "workspace_lock",
]
