"""Typed errors for the MCP client + risk + approval pipeline.

Every error here carries a stable ``code`` attribute the router layer
translates into HTTP statuses + a structured ``detail`` body. Agents
branch on the ``code``, not the message string.
"""

from __future__ import annotations

from typing import Any


class MCPClientError(Exception):
    """Base for every error raised by :mod:`aether_api.mcp_client`."""

    code: str = "mcp_client_error"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


class MCPUnreachable(MCPClientError):
    """The per-project MCP endpoint did not answer in time.

    The endpoint handler maps this to HTTP 502 and (separately) flips the
    project's status to ``error`` via ``ProjectRepository.update_status_if``
    AND writes one ``order_log`` row with ``status='failed'``.
    """

    code = "mcp_unreachable"


class CharterViolation(MCPClientError):
    """The order broke a charter invariant before any external call.

    Today: missing / zero stop-loss. The wrapper boundary on
    ``/mcp/`` re-checks the same thing so both layers refuse.
    """

    code = "charter_violation"


class RiskViolationError(MCPClientError):
    """RiskEnforcer rejected the order.

    ``details`` carries the structured :class:`RiskCheckResult` so the
    UI can render *which* rule fired (sl/risk/exposure/dd/session) and
    the offending numbers.
    """

    code = "risk_violation"


class ApprovalRequired(MCPClientError):
    """RiskEnforcer flagged the order as "needs human approval".

    Routers translate this to HTTP 202 + the approval row id so the UI
    can poll. Not actually an error in the traditional sense — re-using
    the exception channel so the order flow short-circuits cleanly.
    """

    code = "approval_required"


class ApprovalRejected(MCPClientError):
    """A human operator rejected the approval request."""

    code = "approval_rejected"


class ApprovalTimeout(MCPClientError):
    """Approval was not granted before ``expires_at``.

    The poll loop in :class:`ApprovalGate` raises this when its deadline
    elapses; a background sweep moves the row to ``status='expired'``
    independently so a crashed poller cannot leave the row dangling.
    """

    code = "approval_timeout"


__all__ = [
    "ApprovalRejected",
    "ApprovalRequired",
    "ApprovalTimeout",
    "CharterViolation",
    "MCPClientError",
    "MCPUnreachable",
    "RiskViolationError",
]
