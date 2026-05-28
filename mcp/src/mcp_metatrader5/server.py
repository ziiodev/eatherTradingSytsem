"""MCP server entry point — registers all 10 tools and runs over stdio.

Bootstrap order
---------------
1. Load :class:`Settings` from environment (no Wine paths required).
2. Configure structured logging.
3. Ensure the workspace tree exists.
4. Open the SQLite :class:`StateStore`.
5. Pick a :class:`WineRunnerProtocol` implementation:
   - if Wine is configured → (Phase 3) the real runner;
   - otherwise → :class:`NullWineRunner` so register/list/get/remove still work.
6. Start the :class:`RunManager`.
7. Build the :class:`FastMCP` app and register tools.
8. Acquire the workspace flock and run the server until stdin closes.
9. Drain the manager and close the state store on shutdown.

The server is launchable today even without Wine: any compile/backtest/
optimize call will queue and then fail with ``WINE_RUNNER_NOT_IMPLEMENTED``.
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from .config import Settings, load_settings
from .errors import MT5MCPError
from .logging import configure_logging, get_logger
from .manager import RunManager, WineRunnerProtocol
from .runner.null import NullWineRunner
from .state import StateStore
from .tools import backtest as t_backtest
from .tools import compile as t_compile
from .tools import eas as t_eas
from .tools import optimize as t_optimize
from .tools import runs as t_runs
from .tools.live import register_live_tools
from .tools.schemas import (
    BacktestEAInput,
    BacktestEAOutput,
    CompileEAInput,
    CompileEAOutput,
    GetEAInput,
    GetEAOutput,
    GetRunArtifactInput,
    GetRunArtifactOutput,
    GetRunInput,
    GetRunOutput,
    ListEAsInput,
    ListEAsOutput,
    ListRunsInput,
    ListRunsOutput,
    OptimizeEAInput,
    OptimizeEAOutput,
    RegisterEAInput,
    RegisterEAOutput,
    RemoveEAInput,
    RemoveEAOutput,
)
from .workspace import WorkspacePaths, workspace_lock

_log = get_logger(__name__)


def _resolve_managed_root(settings: Settings) -> Path:
    """Return the directory holding ``managed/<ea_handle>/<file>.mq5``.

    When Wine is configured we land under ``MQL5/Experts/managed/`` inside the
    Wine prefix's ``drive_c`` (so MetaEditor and terminal64 can find the file).
    Otherwise we use a fallback directory under the workspace.
    """

    if settings.wine_available and settings.wineprefix is not None:
        return (
            settings.wineprefix
            / "drive_c"
            / "Program Files"
            / "MetaTrader 5"
            / "MQL5"
            / "Experts"
            / "managed"
        )
    return settings.workspace_dir / "managed"


# ---------------------------------------------------------------------------
# App container
# ---------------------------------------------------------------------------


class MCPApp:
    """Holds the long-lived collaborators wired to FastMCP tool callables."""

    settings: Settings
    paths: WorkspacePaths
    state: StateStore
    runner: WineRunnerProtocol
    manager: RunManager
    mcp: FastMCP
    managed_root: Path

    def __init__(
        self,
        *,
        settings: Settings,
        paths: WorkspacePaths,
        state: StateStore,
        runner: WineRunnerProtocol,
        manager: RunManager,
        managed_root: Path,
    ) -> None:
        self.settings = settings
        self.paths = paths
        self.state = state
        self.runner = runner
        self.manager = manager
        self.managed_root = managed_root
        self.mcp = FastMCP(
            name="mcp-metatrader5",
            instructions=(
                "MCP server bridging Claude Code to MetaTrader 5 (compile, "
                "backtest, optimize EAs under Wine on Linux). Register a .mq5 "
                "with mt5_register_ea, then mt5_compile_ea / mt5_backtest_ea / "
                "mt5_optimize_ea. Read results via mt5_get_run / "
                "mt5_get_run_artifact."
            ),
        )
        self._register_tools()

    # ------------------------------------------------------------------ tools
    def _register_tools(self) -> None:
        mcp = self.mcp

        @mcp.tool(
            name="mt5_register_ea",
            description="Copy an EA .mq5 source into the managed workspace and return a stable ea_handle.",
        )
        def mt5_register_ea(payload: RegisterEAInput) -> RegisterEAOutput:
            return t_eas.register_ea(
                payload, state=self.state, managed_root=self.managed_root
            )

        @mcp.tool(
            name="mt5_list_eas",
            description="List every registered EA with timestamps and source SHA-256.",
        )
        def mt5_list_eas(payload: ListEAsInput) -> ListEAsOutput:
            del payload  # ListEAs has no parameters; accepted for schema parity.
            return t_eas.list_eas(state=self.state)

        @mcp.tool(
            name="mt5_get_ea",
            description="Return full EA detail (workspace path, sha256, timestamps).",
        )
        def mt5_get_ea(payload: GetEAInput) -> GetEAOutput:
            return t_eas.get_ea(payload.ea_handle, state=self.state)

        @mcp.tool(
            name="mt5_remove_ea",
            description="Remove an EA registration (and optionally its workspace files); refuses if active runs exist.",
        )
        def mt5_remove_ea(payload: RemoveEAInput) -> RemoveEAOutput:
            return t_eas.remove_ea(
                payload.ea_handle,
                state=self.state,
                managed_root=self.managed_root,
                also_delete_workspace=payload.also_delete_workspace,
            )

        @mcp.tool(
            name="mt5_compile_ea",
            description="Queue a MetaEditor compile run for ea_handle; returns run_id immediately.",
        )
        async def mt5_compile_ea(payload: CompileEAInput) -> CompileEAOutput:
            return await t_compile.compile_ea(
                payload, state=self.state, manager=self.manager
            )

        @mcp.tool(
            name="mt5_backtest_ea",
            description=(
                "Queue a Strategy Tester backtest. Returns run_id immediately; "
                "use mt5_get_run / mt5_get_run_artifact once status='done'."
            ),
        )
        async def mt5_backtest_ea(payload: BacktestEAInput) -> BacktestEAOutput:
            return await t_backtest.backtest_ea(
                payload, state=self.state, manager=self.manager
            )

        @mcp.tool(
            name="mt5_optimize_ea",
            description=(
                "Queue a Strategy Tester optimization. parameter_ranges accepts "
                "start/stop/step OR a uniformly-spaced values list."
            ),
        )
        async def mt5_optimize_ea(payload: OptimizeEAInput) -> OptimizeEAOutput:
            return await t_optimize.optimize_ea(
                payload, state=self.state, manager=self.manager
            )

        @mcp.tool(
            name="mt5_list_runs",
            description="List recent runs with optional ea_handle / status filters.",
        )
        def mt5_list_runs(payload: ListRunsInput) -> ListRunsOutput:
            return t_runs.list_runs(payload, state=self.state)

        @mcp.tool(
            name="mt5_get_run",
            description="Return full run detail: status, summary metrics, artifact paths, errors.",
        )
        def mt5_get_run(payload: GetRunInput) -> GetRunOutput:
            return t_runs.get_run(payload, state=self.state)

        @mcp.tool(
            name="mt5_get_run_artifact",
            description="Read a run artifact ('report', 'log', or 'results') as text or base64.",
        )
        def mt5_get_run_artifact(payload: GetRunArtifactInput) -> GetRunArtifactOutput:
            return t_runs.get_run_artifact(payload, state=self.state, paths=self.paths)

        # --- mt5-integration Phase A: live trading tools ----------------
        # The 7 live tools are registered alongside the backtest/EA tools.
        # The wrapper boundary in tools/live/tools.py enforces every charter
        # invariant (mandatory SL, live_enabled gate, redacted credentials).
        register_live_tools(mcp, self.settings)


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------


def build_app(
    *,
    settings: Settings | None = None,
    runner: WineRunnerProtocol | None = None,
    env: dict[str, str] | None = None,
) -> MCPApp:
    """Build (but do not start) an :class:`MCPApp`.

    Tests pass a pre-built ``runner`` (typically a fake) and bypass
    ``load_settings`` entirely by handing in a ready ``settings``.
    """

    actual_settings = settings if settings is not None else load_settings(env)

    configure_logging(level=actual_settings.log_level, json=actual_settings.log_json)

    paths = WorkspacePaths(actual_settings.workspace_dir)
    paths.ensure()
    managed_root = _resolve_managed_root(actual_settings)
    managed_root.mkdir(parents=True, exist_ok=True)

    state = StateStore(paths.state_db)
    actual_runner: WineRunnerProtocol = runner if runner is not None else NullWineRunner()

    manager = RunManager(
        state_store=state,
        workspace_paths=paths,
        wine_runner=actual_runner,
    )
    return MCPApp(
        settings=actual_settings,
        paths=paths,
        state=state,
        runner=actual_runner,
        manager=manager,
        managed_root=managed_root,
    )


async def _run_async(app: MCPApp) -> None:
    """Drive the FastMCP app over the configured transport.

    ``transport='stdio'`` keeps the historical Claude-Desktop launch path
    untouched. ``transport='tcp'`` binds an SSE-style HTTP listener (the
    transport flavour FastMCP exposes today) on ``tcp_host:tcp_port`` so
    ``apps/api`` can dial the per-project container — the charter mandates
    that every project has its own ``mcp_url:mcp_port``.
    """
    await app.manager.start()
    try:
        if app.settings.transport == "tcp":
            _log.info(
                "server_tcp_starting",
                host=app.settings.tcp_host,
                port=app.settings.tcp_port,
                live_enabled=app.settings.live_enabled,
            )
            # FastMCP's SSE/HTTP entry. Newer mcp lib versions name the
            # method ``run_sse_async``; older ones ``run_async`` over SSE.
            # We honour both; if neither exists the server reports the
            # configuration error rather than booting half-wired.
            run_async = getattr(app.mcp, "run_sse_async", None) or getattr(
                app.mcp, "run_http_async", None
            )
            if run_async is None:  # pragma: no cover — env-specific
                raise MT5MCPError(
                    code=__import__(
                        "mcp_metatrader5.errors", fromlist=["ErrorCode"]
                    ).ErrorCode.CONFIG_INVALID,
                    message=(
                        "MT5_MCP_TRANSPORT=tcp requires an MCP server build "
                        "with SSE/HTTP transport (run_sse_async)."
                    ),
                )
            # The host/port are read from FastMCP's settings via env vars
            # ``FASTMCP_HOST`` / ``FASTMCP_PORT`` in modern mcp; we mirror
            # them here so the binding wraps both styles.
            import os as _os

            _os.environ["FASTMCP_HOST"] = app.settings.tcp_host
            _os.environ["FASTMCP_PORT"] = str(app.settings.tcp_port)
            await run_async()
        else:
            await app.mcp.run_stdio_async()
    finally:
        await app.manager.stop()
        app.state.close()


def run(app: MCPApp) -> None:
    """Take the workspace flock, then run the server until stdio closes."""

    with workspace_lock(app.paths.lock_file, timeout=5.0):
        try:
            asyncio.run(_run_async(app))
        except (KeyboardInterrupt, asyncio.CancelledError):  # pragma: no cover
            _log.info("server_interrupted")


def main(argv: list[str] | None = None) -> int:
    """Console-script entry: ``mcp-metatrader5``.

    Honours ``--help`` for a quick smoke test that the package is installed
    correctly without requiring stdio to be connected.
    """

    args = list(argv) if argv is not None else None
    if args is not None and any(a in {"-h", "--help"} for a in args):
        _print_help()
        return 0
    # Use sys.argv-ish detection only when called as a script.
    import sys

    if argv is None and any(a in {"-h", "--help"} for a in sys.argv[1:]):
        _print_help()
        return 0

    try:
        app = build_app()
    except MT5MCPError as exc:
        print(f"error: {exc.message}", file=__import__("sys").stderr)
        return 2

    _log.info(
        "server_starting",
        workspace=str(app.settings.workspace_dir),
        wine_available=app.settings.wine_available,
    )
    with contextlib.suppress(KeyboardInterrupt):
        run(app)
    return 0


def _print_help() -> None:
    print(
        "mcp-metatrader5 — MCP server bridging Claude Code to MetaTrader 5\n"
        "\n"
        "Run as an MCP server over stdio. Configure via env vars:\n"
        "  MT5_WORKSPACE_DIR        (optional) path for state.sqlite + run artifacts\n"
        "  MT5_WINEPREFIX           (optional) Wine prefix containing MT5\n"
        "  MT5_TERMINAL_PATH        (optional) absolute path to terminal64.exe\n"
        "  MT5_METAEDITOR_PATH      (optional) absolute path to metaeditor64.exe\n"
        "  MT5_LOG_LEVEL            DEBUG|INFO|WARNING|ERROR|CRITICAL (default INFO)\n"
        "  MT5_LOG_JSON             true|false (default true)\n"
        "  MT5_RUN_TIMEOUT_SECONDS  per-run cap (default 3600)\n"
        "  MT5_COMPILE_TIMEOUT_SECONDS  per-compile cap (default 120)\n"
        "  MT5_XVFB                 use xvfb-run for headless tester (default true)\n"
        "\n"
        "Without Wine vars set, the server still serves register/list/get/remove\n"
        "and run-history tools; compile/backtest/optimize will fail with\n"
        "WINE_RUNNER_NOT_IMPLEMENTED until Phase 3 lands the real driver.\n"
    )


# Keep the legacy naming used by other tooling.
def _main_for_script() -> Any:  # pragma: no cover - thin wrapper
    return main()


__all__ = ["MCPApp", "build_app", "main", "run"]
