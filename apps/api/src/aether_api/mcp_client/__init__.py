"""``aether_api.mcp_client`` — typed MT5 MCP client + risk + approval gates.

This package is the *bridge* between ``apps/api`` (FastAPI) and the
per-project MCP server (`./mcp`). It is the **last defence layer** before
an order is sent to a real broker — every charter invariant (mandatory
SL, risk caps, session windows, large-order approval, audit) is enforced
HERE, never on the broker side and never inside ``agents.logica``.

Public surface (re-exported):

* :class:`MCPClient`                  — per-project connection abstraction.
* :class:`RiskEnforcer`               — pure-Python risk gate.
* :class:`ApprovalGate`               — poll-based approval workflow.
* :func:`is_session_open`             — UTC session clock with DST flags.
* :class:`OrderAuditor`               — two-phase ``order_log`` writes.
* Error classes (``MCPUnreachable``, ``RiskViolationError``,
  ``ApprovalRequired``, ``ApprovalRejected``, ``ApprovalTimeout``,
  ``CharterViolation``).

Per the design document, every entry into this package is async and
session-bound — repositories receive the ``AsyncSession`` of the request
so 2-phase writes share the transaction with the order row.
"""

from __future__ import annotations

from .approvals import ApprovalGate, decide_approval, list_pending_approvals
from .audit import OrderAuditor
from .client import MCPClient, get_mcp_client
from .errors import (
    ApprovalRejected,
    ApprovalRequired,
    ApprovalTimeout,
    CharterViolation,
    MCPClientError,
    MCPUnreachable,
    RiskViolationError,
)
from .risk import RiskCheckResult, RiskEnforcer
from .sessions import SESSION_NAMES, is_session_open

__all__ = [
    "SESSION_NAMES",
    "ApprovalGate",
    "ApprovalRejected",
    "ApprovalRequired",
    "ApprovalTimeout",
    "CharterViolation",
    "MCPClient",
    "MCPClientError",
    "MCPUnreachable",
    "OrderAuditor",
    "RiskCheckResult",
    "RiskEnforcer",
    "RiskViolationError",
    "decide_approval",
    "get_mcp_client",
    "is_session_open",
    "list_pending_approvals",
]
