"""``MCPClient`` — typed TCP/HTTP client to the per-project MCP server.

The client dials ``projects.mcp_url:projects.mcp_port`` per the charter
mandate. The transport flavour is JSON-RPC over HTTP (FastMCP's SSE/HTTP
mode); we use ``httpx.AsyncClient`` for the request lifecycle.

Connection pooling is a future enhancement — for v1 we open a client
per call. The cost is one TCP handshake; for the orders volume the
charter targets (operator surface, not HFT) this is invisible.

Failure mapping (centralised here so callers never re-derive):

* ConnectError / ReadTimeout / RemoteDisconnected → :class:`MCPUnreachable`.
* Non-2xx HTTP                                     → :class:`MCPUnreachable`.
* JSON-RPC ``error.data.code == 'charter_violation_missing_sl'`` →
  :class:`CharterViolation`.
* Other JSON-RPC errors                            → :class:`MCPClientError`.

The client is **stateless** — no per-project caching of MT5 state. The
RiskEnforcer reads account + positions live before each order.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

import httpx

from aether_api.models.project import Project

from .errors import CharterViolation, MCPClientError, MCPUnreachable

# Default timeout for any MCP round-trip. Lower than the broker latency
# tolerance so we never blame MT5 for an MCP-side hang.
DEFAULT_TIMEOUT_SECONDS: float = 10.0


def _resolve_endpoint(project: Project) -> str:
    """Build the JSON-RPC POST URL for ``project``.

    ``mcp_url`` is the scheme+host (e.g. ``http://proj-123.docker``) and
    ``mcp_port`` is the listener port (the value set on the MCP server
    via ``MT5_MCP_PORT``). When ``mcp_port`` is unset we fall back to
    8765 — the documented default in the MCP server's settings.
    """
    base = project.mcp_url.rstrip("/")
    port = project.mcp_port or 8765
    # If the URL already carries the port, trust the caller and just
    # tack on the JSON-RPC path. Otherwise inject the port.
    if ":" in base.split("/", 3)[-1]:
        return f"{base}/messages/"
    return f"{base}:{port}/messages/"


def _decode_decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _decode_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


class MCPClient:
    """Async JSON-RPC client to a single project's MCP endpoint.

    Constructed via :func:`get_mcp_client` rather than directly so a
    future pooled implementation can change construction without
    touching call sites.
    """

    def __init__(
        self,
        project: Project,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.project = project
        self.timeout = timeout
        self._endpoint = _resolve_endpoint(project)

    async def _rpc(self, tool: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Invoke ``tool`` via JSON-RPC and return the unwrapped result."""
        request_id = uuid.uuid4().hex
        body = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": tool, "arguments": payload},
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(self._endpoint, json=body)
        except (
            httpx.ConnectError,
            httpx.ReadError,
            httpx.ConnectTimeout,
            httpx.ReadTimeout,
        ) as exc:
            raise MCPUnreachable(
                f"MCP {tool} unreachable: {exc}",
                details={"endpoint": self._endpoint, "tool": tool},
            ) from exc
        except httpx.HTTPError as exc:
            raise MCPUnreachable(
                f"MCP {tool} transport error: {exc}",
                details={"endpoint": self._endpoint, "tool": tool},
            ) from exc

        if response.status_code >= 500:
            raise MCPUnreachable(
                f"MCP {tool} returned HTTP {response.status_code}",
                details={"endpoint": self._endpoint, "tool": tool},
            )

        try:
            envelope = response.json()
        except ValueError as exc:
            raise MCPUnreachable(
                f"MCP {tool} returned non-JSON body",
                details={"endpoint": self._endpoint, "tool": tool},
            ) from exc

        if "error" in envelope:
            err = envelope["error"] or {}
            data = err.get("data") or {}
            code = data.get("code")
            message = err.get("message") or "MCP error"
            if code == "charter_violation_missing_sl":
                raise CharterViolation(message, details=data)
            raise MCPClientError(message, details={"code": code, **data})

        result = envelope.get("result") or {}
        # FastMCP wraps tool results in ``{"content": [...]}``; flatten
        # to the inner dict when shaped that way.
        if isinstance(result, dict) and "content" in result and isinstance(result["content"], list):
            for chunk in result["content"]:
                if isinstance(chunk, dict) and chunk.get("type") == "json":
                    return dict(chunk.get("data") or {})
        return dict(result)

    # ------------------------------------------------------------------ tools
    async def get_account(self) -> dict[str, Any]:
        return await self._rpc("mt5_get_account", {})

    async def get_positions(self, *, symbol: str | None = None) -> dict[str, Any]:
        payload = {"symbol": symbol} if symbol else {}
        return await self._rpc("mt5_get_positions", payload)

    async def get_history(
        self,
        *,
        date_from: datetime,
        date_to: datetime,
        symbol: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
        }
        if symbol:
            payload["symbol"] = symbol
        return await self._rpc("mt5_get_history", payload)

    async def get_candles(self, *, symbol: str, timeframe: str, count: int) -> dict[str, Any]:
        return await self._rpc(
            "mt5_get_candles",
            {"symbol": symbol, "timeframe": timeframe, "count": count},
        )

    async def place_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._rpc("mt5_place_order", payload)

    async def modify_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._rpc("mt5_modify_order", payload)

    async def close_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._rpc("mt5_close_order", payload)


def get_mcp_client(project: Project, *, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> MCPClient:
    """Return a fresh :class:`MCPClient` for ``project``.

    Placeholder for future per-process pooling; today this is a thin
    factory. Routers should never construct :class:`MCPClient` directly.
    """
    return MCPClient(project, timeout=timeout)


__all__ = ["DEFAULT_TIMEOUT_SECONDS", "MCPClient", "get_mcp_client"]
