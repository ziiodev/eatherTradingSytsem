"""Typed exceptions for the sandbox engine.

Each maps 1:1 to one of the ``agent_runs.status`` enum values:

    SandboxTimeout    → "timeout"
    SandboxOOM        → "oom"
    ImportDenied      → "denied_import"
    NetworkDenied     → "denied_network"
    FileDenied        → "denied_file"
    SandboxError      → "error"   (uncaught — parent-side fallback)

The child process does NOT raise these as exceptions across the pipe; it
serialises a structured ``Result`` dict whose ``status`` key the parent
inspects. These types exist for the parent-side public surface (the
router catches them) and for the child-side raise sites the test suite
verifies.
"""

from __future__ import annotations


class SandboxError(Exception):
    """Base class — every sandbox-originated failure inherits from this.

    Carries an optional ``denial_reason`` short marker (e.g.
    ``"import:ctypes"``, ``"socket:8.8.8.8:53"``) the engine forwards
    verbatim to the ``agent_runs.denial_reason`` column.
    """

    #: Maps onto the ``agent_runs.status`` enum. Subclasses override.
    status: str = "error"

    def __init__(self, message: str = "", denial_reason: str | None = None) -> None:
        super().__init__(message)
        self.denial_reason = denial_reason


class SandboxTimeout(SandboxError):
    """CPU or wall-clock deadline exceeded.

    Raised by the parent when the wall-clock guard fires (15 s default),
    AND surfaced from the child if its CPU rlimit trips before the parent
    notices. The two cases are not distinguished — both are "the child
    ran too long".
    """

    status = "timeout"


class SandboxOOM(SandboxError):
    """``RLIMIT_AS`` tripped — child allocated past its memory cap."""

    status = "oom"


class ImportDenied(SandboxError, ImportError):
    """Module allowlist tripped.

    Documented as defence-in-depth, NOT the primary boundary — see
    ``design.md`` "Defence layers". The OS-level rlimit + socket guard
    must hold even if this layer is bypassed (e.g. via
    ``object.__subclasses__()`` walking).

    We multi-inherit from :class:`ImportError` so the CPython import
    machinery treats this as a normal import failure and does NOT wrap
    it (which would break the child's ``isinstance(exc, SandboxError)``
    classification).
    """

    status = "denied_import"


class NetworkDenied(SandboxError):
    """Socket guard tripped — child attempted to connect outside the
    project's ``mcp_url``:``mcp_port`` whitelist.

    This IS a primary boundary; we lean on it heavily because the
    allowlist alone cannot stop a determined attacker from reaching
    ``socket.socket`` via reflection.
    """

    status = "denied_network"


class FileDenied(SandboxError):
    """RLIMIT_FSIZE tripped or a file open denied by the guard."""

    status = "denied_file"
