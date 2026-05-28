"""Agent execution sandbox — the ONLY code path that executes ``agents.logica``.

See ``sdd/agent-execution-sandbox/{proposal,spec,design}`` for the full
threat model. This package never imports user code into the parent
process; everything funnels through :class:`aether_api.sandbox.engine.Engine`,
which spawns a fresh ``multiprocessing`` "spawn" subprocess per run.

Public surface (intentionally tiny):

* :func:`aether_api.sandbox.engine.Engine.run_agent` — the only callable
  that ever runs user-supplied ``agents.logica`` on this host.
* :class:`aether_api.sandbox.ctx.AgentContext` — the pickle-safe payload
  the parent ships to the child.
* :mod:`aether_api.sandbox.errors` — typed exceptions the engine raises.

Everything else (``child``, ``allowlist``, ``mcp_proxy``,
``logging_adapter``) is private to this package; importing them from
outside ``sandbox/`` is a layering violation.
"""

from aether_api.sandbox.engine import Engine, EngineResult
from aether_api.sandbox.errors import (
    FileDenied,
    ImportDenied,
    NetworkDenied,
    SandboxError,
    SandboxOOM,
    SandboxTimeout,
)

__all__ = [
    "Engine",
    "EngineResult",
    "FileDenied",
    "ImportDenied",
    "NetworkDenied",
    "SandboxError",
    "SandboxOOM",
    "SandboxTimeout",
]
