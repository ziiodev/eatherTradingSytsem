"""Null Wine runner — used when the server is configured without MT5 paths.

Implements :class:`mcp_metatrader5.manager.WineRunnerProtocol` but every
method raises :class:`MT5MCPError` with code
:attr:`ErrorCode.WINE_RUNNER_NOT_IMPLEMENTED`. This lets tools that don't
need a runner (register/list/get/remove EAs, list/get runs, read artifacts)
work end-to-end against a server bootstrapped without Wine, while making
compile/backtest/optimize fail loudly and predictably.

The error path goes through the manager's worker, so the resulting run row
has ``status=failed`` and ``error_code=wine_runner_not_implemented``.
"""

from __future__ import annotations

from pathlib import Path

from ..errors import ErrorCode, MT5MCPError
from ..manager import BacktestOutcome, CompileOutcome, OptimizeOutcome


class NullWineRunner:
    """A :class:`WineRunnerProtocol` that always raises."""

    _msg = (
        "Wine runner not configured. Set MT5_WINEPREFIX, MT5_TERMINAL_PATH, "
        "and MT5_METAEDITOR_PATH (and ensure Wine is installed) to enable "
        "compile/backtest/optimize tools."
    )

    async def compile(
        self,
        *,
        ea_handle: str,
        ea_workspace_path: Path,
        run_dir: Path,
    ) -> CompileOutcome:
        raise MT5MCPError(ErrorCode.WINE_RUNNER_NOT_IMPLEMENTED, self._msg)

    async def backtest(
        self,
        *,
        ea_handle: str,
        run_dir: Path,
        ini_text: str,
    ) -> BacktestOutcome:
        raise MT5MCPError(ErrorCode.WINE_RUNNER_NOT_IMPLEMENTED, self._msg)

    async def optimize(
        self,
        *,
        ea_handle: str,
        run_dir: Path,
        ini_text: str,
    ) -> OptimizeOutcome:
        raise MT5MCPError(ErrorCode.WINE_RUNNER_NOT_IMPLEMENTED, self._msg)


__all__ = ["NullWineRunner"]
