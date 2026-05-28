"""MCP server bridging Claude Code to MetaTrader 5 under Wine on Linux."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__: str = version("mcp-metatrader5")
except PackageNotFoundError:  # pragma: no cover - during local dev before install
    __version__ = "0.0.0+local"

__all__ = ["__version__"]
