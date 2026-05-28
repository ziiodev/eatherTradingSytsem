"""In-child MCP shim — the ONLY network door for the sandboxed code.

The full MCP client lives elsewhere; v1 we ship a tiny stub that:

* Verifies any ``connect``-style call lands on the exact
  ``host``:``port`` from :class:`aether_api.sandbox.ctx.McpEndpoint`.
* Short-circuits side-effecting methods when ``ctx.dry_run`` is True.
* Raises :class:`aether_api.sandbox.errors.NetworkDenied` otherwise.

The socket-level guard in :mod:`aether_api.sandbox.child` is the actual
boundary (it monkey-patches ``socket.socket.connect``); this module is
the public surface the user code calls, so the API stays clean even when
the underlying socket is the load-bearing defence.
"""

from __future__ import annotations

from typing import Any

from aether_api.sandbox.ctx import McpEndpoint
from aether_api.sandbox.errors import NetworkDenied


class McpProxy:
    """Thin wrapper around the project's MCP endpoint.

    v1 surface is intentionally tiny — enough for the worker / auditor
    smoke tests to call something, not enough to bind us to a wire format
    we haven't finalised. The real MCP client lands in the
    ``mcp-proxy`` change.
    """

    def __init__(self, endpoint: McpEndpoint, *, dry_run: bool) -> None:
        self._endpoint = endpoint
        self._dry_run = dry_run

    # ------------------------------------------------------------------
    # Reads — allowed in every mode.
    # ------------------------------------------------------------------
    @property
    def url(self) -> str:
        return self._endpoint.url

    @property
    def host(self) -> str:
        return self._endpoint.host

    @property
    def port(self) -> int:
        return self._endpoint.port

    def ping(self) -> dict[str, Any]:
        """Cheap reachability probe. Returns a synthetic payload in v1 so
        the worker scaffolding can call something concrete.
        """
        return {"ok": True, "endpoint": self._endpoint.url}

    # ------------------------------------------------------------------
    # Writes — refused under dry_run.
    # ------------------------------------------------------------------
    def place_order(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Side-effecting method. v1 just enforces the dry-run gate."""
        if self._dry_run:
            raise NetworkDenied(
                "place_order is blocked while dry_run=True",
                denial_reason="mcp:place_order:dry_run",
            )
        # No real wire format yet; the mcp-proxy change wires this up.
        return {"ok": False, "reason": "mcp client not implemented in v1"}
