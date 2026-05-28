"""Error types and codes for the MCP MetaTrader 5 server.

Every failure raised by the server inherits from :class:`MT5MCPError` and
carries a stable :class:`ErrorCode`. The MCP tool layer maps these to JSON-RPC
errors with the code name surfaced in `error.data.code` so agents can branch
on programmatic codes rather than message strings.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    """Stable, programmatic error codes.

    The string value is what is exposed over MCP. Add new codes; never repurpose
    existing ones.
    """

    # --- configuration ---
    CONFIG_MISSING = "config_missing"
    CONFIG_INVALID = "config_invalid"

    # --- workspace / EA registration ---
    WORKSPACE_INVALID = "workspace_invalid"
    EA_NOT_FOUND = "ea_not_found"
    EA_ALREADY_EXISTS = "ea_already_exists"
    EA_SOURCE_INVALID = "ea_source_invalid"

    # --- compile ---
    COMPILE_FAILED = "compile_failed"
    COMPILE_TIMEOUT = "compile_timeout"
    COMPILE_LOG_UNREADABLE = "compile_log_unreadable"

    # --- backtest / optimization ---
    BACKTEST_FAILED = "backtest_failed"
    BACKTEST_TIMEOUT = "backtest_timeout"
    OPTIMIZATION_FAILED = "optimization_failed"
    OPTIMIZATION_TIMEOUT = "optimization_timeout"
    REPORT_PARSE_FAILED = "report_parse_failed"

    # --- runs / persistence ---
    RUN_NOT_FOUND = "run_not_found"
    RUN_INTERRUPTED = "run_interrupted"
    STATE_CORRUPTED = "state_corrupted"

    # --- concurrency / locking ---
    LOCK_HELD = "lock_held"

    # --- runner availability ---
    WINE_RUNNER_NOT_IMPLEMENTED = "wine_runner_not_implemented"

    # --- live trading (mt5-integration change, Phase A) ---
    # The seven live tools added in tools/live/ raise MT5Error with one of
    # the following codes. Code names are stable — agents branch on them.
    MT5_NOT_INITIALIZED = "mt5_not_initialized"
    MT5_CONNECT_FAILED = "mt5_connect_failed"
    MT5_AUTH_FAILED = "mt5_auth_failed"
    SYMBOL_NOT_FOUND = "symbol_not_found"
    ORDER_REJECTED = "order_rejected"
    INVALID_VOLUME = "invalid_volume"
    INVALID_STOPS = "invalid_stops"
    TICKET_NOT_FOUND = "ticket_not_found"
    LIVE_DISABLED = "live_disabled"
    #: Charter-mandated guard: every order MUST carry a non-null SL. This
    #: code fires at the WRAPPER boundary (before the broker round-trip),
    #: AND on attempts to clear an existing SL via mt5_modify_order.
    CHARTER_VIOLATION_MISSING_SL = "charter_violation_missing_sl"

    # --- generic / input ---
    INVALID_INPUT = "invalid_input"
    INTERNAL = "internal"


class MT5MCPError(Exception):
    """Base exception for all server-side errors.

    Attributes
    ----------
    code:
        A stable :class:`ErrorCode` for programmatic dispatch.
    message:
        Human-readable error message (already redacted of credentials).
    details:
        Optional structured payload included verbatim in MCP error data.
    """

    code: ErrorCode

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        """Serialise for MCP `error.data` payloads."""
        return {
            "code": self.code.value,
            "message": self.message,
            "details": self.details,
        }

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"{type(self).__name__}(code={self.code.value!r}, message={self.message!r})"


class ConfigError(MT5MCPError):
    """Configuration / environment error."""


class WorkspaceError(MT5MCPError):
    """Workspace or EA-registration error."""


class CompileError(MT5MCPError):
    """MetaEditor compile failure or log parsing error."""


class BacktestError(MT5MCPError):
    """Strategy-tester backtest failure."""


class OptimizationError(MT5MCPError):
    """Strategy-tester optimization failure."""


class StateError(MT5MCPError):
    """SQLite state-store corruption or constraint violation."""


class LockError(MT5MCPError):
    """Concurrency / advisory-lock error."""


class MT5Error(MT5MCPError):
    """Live-trading error raised by the ``tools/live/`` subpackage.

    Carries an optional ``mt5_retcode`` (the raw integer returned by the
    ``MetaTrader5`` Python binding via ``mt5.last_error()`` or
    ``order_send`` results) so callers can correlate against MT5's own
    documentation without losing programmatic dispatch via
    :class:`ErrorCode`.
    """

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        mt5_retcode: int | None = None,
    ) -> None:
        super().__init__(code, message, details=details)
        self.mt5_retcode = mt5_retcode

    def to_dict(self) -> dict[str, Any]:
        base = super().to_dict()
        if self.mt5_retcode is not None:
            base["mt5_retcode"] = self.mt5_retcode
        return base


__all__ = [
    "BacktestError",
    "CompileError",
    "ConfigError",
    "ErrorCode",
    "LockError",
    "MT5Error",
    "MT5MCPError",
    "OptimizationError",
    "StateError",
    "WorkspaceError",
]
