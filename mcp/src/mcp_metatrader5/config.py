"""Configuration loader for the MCP MetaTrader 5 server.

Loads settings from `MT5_*` environment variables. The server is purely driven
by env vars — no YAML/TOML config file at runtime. This module is **pure**: it
does not touch Wine, MT5, or the filesystem beyond resolving paths.

All `MT5_*` variables are documented inline. Any unrecognised `MT5_*` variable
raises a `ConfigError` so typos do not silently fall back to defaults.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .errors import ConfigError, ErrorCode

# Recognised env var names. Any other `MT5_*` env var triggers ConfigError.
_KNOWN_VARS: frozenset[str] = frozenset(
    {
        "MT5_WINEPREFIX",
        "MT5_TERMINAL_PATH",
        "MT5_METAEDITOR_PATH",
        "MT5_WORKSPACE_DIR",
        "MT5_LOG_LEVEL",
        "MT5_LOG_JSON",
        "MT5_RUN_TIMEOUT_SECONDS",
        "MT5_COMPILE_TIMEOUT_SECONDS",
        "MT5_XVFB",
        # --- mt5-integration (Phase A): live-trading + TCP transport ---
        "MT5_MCP_TRANSPORT",
        "MT5_MCP_HOST",
        "MT5_MCP_PORT",
        "MT5_LIVE_ENABLED",
        "MT5_LOGIN",
        "MT5_PASSWORD",
        "MT5_SERVER",
    }
)


@dataclass(frozen=True, slots=True)
class Settings:
    """Resolved server settings.

    Path attributes are absolute :class:`pathlib.Path` instances when set.
    Wine-related paths are optional: when ``terminal_path`` is unset the
    server runs in "no-Wine" mode (registered EAs and run history are still
    accessible; compile/backtest/optimize tools queue jobs that fail with
    ``WINE_RUNNER_NOT_IMPLEMENTED`` until the runner is configured).
    """

    workspace_dir: Path
    wineprefix: Path | None = None
    terminal_path: Path | None = None
    metaeditor_path: Path | None = None
    log_level: str = "INFO"
    log_json: bool = True
    run_timeout_seconds: int = 3600
    compile_timeout_seconds: int = 120
    use_xvfb: bool = True
    # --- mt5-integration Phase A additions -------------------------------
    #: When False, the live tools registered in ``tools/live/`` refuse to
    #: contact MT5 and raise :class:`ErrorCode.LIVE_DISABLED`. Backtest
    #: tools are unaffected.
    live_enabled: bool = False
    #: ``stdio`` keeps the historical Claude-Desktop launch path. ``tcp``
    #: listens on ``tcp_host:tcp_port`` so ``apps/api`` can dial the
    #: per-project container's MCP endpoint (charter mandate:
    #: ``projects.mcp_url:projects.mcp_port``).
    transport: Literal["stdio", "tcp"] = "stdio"
    tcp_host: str = "0.0.0.0"
    tcp_port: int = 8765
    #: MT5 broker credentials. When unset, the ``_mt5`` wrapper assumes
    #: the user pre-configured the terminal interactively and skips
    #: ``mt5.login()``. The password is held as a plain ``str`` here for
    #: the dataclass shape; structlog redaction handles it at logging
    #: time (see ``logging.py``).
    mt5_login: int | None = None
    mt5_password: str | None = None
    mt5_server: str | None = None
    extra_env: dict[str, str] = field(default_factory=dict)

    @property
    def wine_available(self) -> bool:
        """True iff every Wine-related path is configured."""
        return (
            self.wineprefix is not None
            and self.terminal_path is not None
            and self.metaeditor_path is not None
        )


def _coerce_bool(value: str, var_name: str) -> bool:
    lowered = value.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(
        ErrorCode.CONFIG_INVALID,
        f"{var_name} must be a boolean (got {value!r})",
    )


def _coerce_int(value: str, var_name: str, *, minimum: int = 1) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ConfigError(
            ErrorCode.CONFIG_INVALID,
            f"{var_name} must be an integer (got {value!r})",
        ) from exc
    if parsed < minimum:
        raise ConfigError(
            ErrorCode.CONFIG_INVALID,
            f"{var_name} must be >= {minimum} (got {parsed})",
        )
    return parsed


def load_settings(env: dict[str, str] | None = None) -> Settings:
    """Build a :class:`Settings` from environment variables.

    Parameters
    ----------
    env:
        Environment mapping; defaults to ``os.environ``. Pass an explicit dict
        in tests to avoid leaking process state.
    """

    source: dict[str, str] = dict(os.environ if env is None else env)

    # Detect typos: any MT5_-prefixed var not in the known set is rejected.
    unknown = sorted(k for k in source if k.startswith("MT5_") and k not in _KNOWN_VARS)
    if unknown:
        raise ConfigError(
            ErrorCode.CONFIG_INVALID,
            f"Unknown MT5_* environment variables: {', '.join(unknown)}",
        )

    # Wine paths are now optional. If ANY of them is set, ALL must be set —
    # this guards against half-configured environments that would crash later.
    wine_vars = ["MT5_WINEPREFIX", "MT5_TERMINAL_PATH", "MT5_METAEDITOR_PATH"]
    wine_present = [name for name in wine_vars if source.get(name)]
    wine_missing = [name for name in wine_vars if not source.get(name)]
    if wine_present and wine_missing:
        raise ConfigError(
            ErrorCode.CONFIG_MISSING,
            f"Partial Wine configuration: set all of {wine_vars} or none "
            f"(missing: {', '.join(wine_missing)})",
        )

    if wine_present:
        wineprefix: Path | None = Path(source["MT5_WINEPREFIX"]).expanduser().resolve()
        terminal_path: Path | None = Path(source["MT5_TERMINAL_PATH"]).expanduser()
        metaeditor_path: Path | None = Path(source["MT5_METAEDITOR_PATH"]).expanduser()
    else:
        wineprefix = None
        terminal_path = None
        metaeditor_path = None

    # Workspace defaults to ./mcp/workspace next to the package's project root.
    workspace_raw = source.get("MT5_WORKSPACE_DIR")
    if workspace_raw:
        workspace_dir = Path(workspace_raw).expanduser().resolve()
    else:
        workspace_dir = (Path.cwd() / "workspace").resolve()

    log_level = source.get("MT5_LOG_LEVEL", "INFO").upper()
    if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ConfigError(
            ErrorCode.CONFIG_INVALID,
            f"MT5_LOG_LEVEL must be one of DEBUG/INFO/WARNING/ERROR/CRITICAL (got {log_level!r})",
        )

    log_json = _coerce_bool(source.get("MT5_LOG_JSON", "true"), "MT5_LOG_JSON")
    run_timeout = _coerce_int(
        source.get("MT5_RUN_TIMEOUT_SECONDS", "3600"),
        "MT5_RUN_TIMEOUT_SECONDS",
    )
    compile_timeout = _coerce_int(
        source.get("MT5_COMPILE_TIMEOUT_SECONDS", "120"),
        "MT5_COMPILE_TIMEOUT_SECONDS",
    )
    use_xvfb = _coerce_bool(source.get("MT5_XVFB", "true"), "MT5_XVFB")

    # --- mt5-integration Phase A: live + transport -----------------------
    live_enabled = _coerce_bool(
        source.get("MT5_LIVE_ENABLED", "false"), "MT5_LIVE_ENABLED"
    )
    transport_raw = source.get("MT5_MCP_TRANSPORT", "stdio").lower()
    if transport_raw not in {"stdio", "tcp"}:
        raise ConfigError(
            ErrorCode.CONFIG_INVALID,
            f"MT5_MCP_TRANSPORT must be 'stdio' or 'tcp' (got {transport_raw!r})",
        )
    transport: Literal["stdio", "tcp"] = "tcp" if transport_raw == "tcp" else "stdio"
    tcp_host = source.get("MT5_MCP_HOST", "0.0.0.0")
    tcp_port_raw = source.get("MT5_MCP_PORT", "8765")
    tcp_port = _coerce_int(tcp_port_raw, "MT5_MCP_PORT", minimum=1)
    if tcp_port > 65535:
        raise ConfigError(
            ErrorCode.CONFIG_INVALID,
            f"MT5_MCP_PORT must be in 1..65535 (got {tcp_port})",
        )

    mt5_login: int | None = None
    if source.get("MT5_LOGIN"):
        mt5_login = _coerce_int(source["MT5_LOGIN"], "MT5_LOGIN", minimum=1)
    mt5_password: str | None = source.get("MT5_PASSWORD") or None
    mt5_server: str | None = source.get("MT5_SERVER") or None

    return Settings(
        wineprefix=wineprefix,
        terminal_path=terminal_path,
        metaeditor_path=metaeditor_path,
        workspace_dir=workspace_dir,
        log_level=log_level,
        log_json=log_json,
        run_timeout_seconds=run_timeout,
        compile_timeout_seconds=compile_timeout,
        use_xvfb=use_xvfb,
        live_enabled=live_enabled,
        transport=transport,
        tcp_host=tcp_host,
        tcp_port=tcp_port,
        mt5_login=mt5_login,
        mt5_password=mt5_password,
        mt5_server=mt5_server,
    )


__all__ = ["Settings", "load_settings"]
