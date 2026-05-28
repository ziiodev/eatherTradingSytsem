"""Structured log forwarder used inside the child process.

We do not give the child a structlog binding (that pulls a lot of
modules through the import allowlist for no real gain). Instead, the
child writes JSON lines to stderr; the parent captures stderr verbatim
and tail-truncates it into ``agent_runs.stderr``.
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any


def emit(level: str, message: str, **fields: Any) -> None:
    """Write a single JSON line to stderr from inside the child.

    ``fields`` are merged onto the record. We avoid any non-stdlib
    dependency here so the allowlist doesn't have to bend.
    """
    record: dict[str, Any] = {
        "ts": time.time(),
        "level": level,
        "message": message,
    }
    record.update(fields)
    try:
        sys.stderr.write(json.dumps(record, default=str) + "\n")
        sys.stderr.flush()
    except Exception:  # noqa: BLE001 — last-ditch logger, can't itself raise
        # Stderr may be closed or detached; swallow so we don't crash
        # the entire child mid-cleanup.
        pass
