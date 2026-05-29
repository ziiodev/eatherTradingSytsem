"""Pickle-safe context object shipped from parent to child.

The parent NEVER ships DB handles, secrets, or open FDs into the child;
the only payload the child receives is an :class:`AgentContext` dataclass
serialised by pickle and read off stdin. Every field is a plain type
(str / int / dict / list of primitives) so pickle can round-trip it
without dragging in user-defined classes.

The child receives ``ctx`` as the single positional argument to the
agent entrypoint::

    def on_tick(ctx: AgentContext) -> dict:
        bars = ctx.mcp.bars(ctx.symbol, ctx.timeframe, n=100)
        return {"signal": "buy"}

``ctx.mcp`` is a tiny proxy (see :mod:`aether_api.sandbox.mcp_proxy`)
whose ``connect`` is the only network exit the child has. Everything
else on the dataclass is plain data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class McpEndpoint:
    """Where the child is allowed to talk to the project's MCP server.

    The socket guard in :mod:`aether_api.sandbox.child` whitelists EXACTLY
    this ``host``:``port`` pair; any other ``socket.connect`` call raises
    :class:`aether_api.sandbox.errors.NetworkDenied`.
    """

    url: str
    host: str
    port: int


@dataclass
class AgentContext:
    """The single payload the child receives.

    Plain dataclass on purpose — pickle can serialise it without
    ``__reduce__`` shenanigans, and the field set is small enough to eye
    in a code review. Anything that smells like a live resource (DB
    session, opened file, cryptographic key) MUST be derived inside the
    child rather than passed in.
    """

    # --- Identity / tenancy
    user_id: str
    project_id: str
    agent_id: str

    # --- Project shape (project_lifecycle / charter fields the user code
    # is allowed to read).
    symbol: str
    timeframe: str

    # --- MCP endpoint (the ONLY allowed network egress).
    mcp: McpEndpoint

    # --- Caller-supplied inputs for this run. Free-form JSON-ish dict —
    # the router validated it was JSON-serialisable before forwarding.
    inputs: dict[str, Any] = field(default_factory=dict)

    # --- Mode hint so the same entrypoint can short-circuit replay /
    # dry-run paths. Closed set: "live" | "manual" | "backtest" | "dry_run".
    mode: str = "manual"

    # --- Dry-run flag — when True, the child is expected to compute but
    # NOT call any side-effecting MCP method (``place_order`` etc.). The
    # mcp_proxy short-circuits write methods when this is set.
    dry_run: bool = False

    # --- Learning enabled flag. When False, the child binds the NO-OP
    # learning proxies (reads return None/[]; ``ctx.episodic.record``
    # raises ``RuntimeError("learning disabled")``). When True, the child
    # constructs an :class:`aether_api.sandbox.rpc.RpcClient` from
    # :attr:`rpc_conn` and wraps it in the three real proxies before
    # handing control to the user entrypoint.
    #
    # The proxies themselves are NOT shipped on this dataclass — they
    # depend on a live :class:`multiprocessing.connection.Connection`
    # which the child constructs locally. See
    # :func:`aether_api.sandbox.child.child_main` for the assembly site.
    learning_enabled: bool = False

    # --- Operativa-proxy enabled flag. When False, the child binds the
    # NO-OP :class:`aether_api.sandbox.orders_ctx.NoopOrders` variant —
    # any ``ctx.orders.record_*`` call raises
    # ``RuntimeError("operativa proxy disabled")``. When True, the child
    # constructs (or reuses) an :class:`aether_api.sandbox.rpc.RpcClient`
    # and wraps it in :class:`aether_api.sandbox.orders_ctx.OrdersProxy`.
    # See ``sdd/project-operativa/spec/agent-sandbox-delta`` (#2119).
    operativa_enabled: bool = False
