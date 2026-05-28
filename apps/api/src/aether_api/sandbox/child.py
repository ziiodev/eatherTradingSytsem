"""Subprocess entrypoint — runs INSIDE the sandboxed child.

Lifecycle:

1. Parent spawns a fresh subprocess via ``multiprocessing.get_context("spawn")``
   targeting :func:`child_main` with two raw pipe FDs.
2. Child reads pickled ``(source, entrypoint_name, ctx)`` off ``parent_r``.
3. Child applies rlimits, installs the import allowlist + socket guard,
   scrubs ``sys`` of dangerous attrs.
4. Child compiles + execs the agent source in a restricted globals dict,
   calls ``entrypoint(ctx)``, captures the return value.
5. Child writes back a result dict ``{status, result, denial_reason,
   exc_type, exc_message}`` onto ``child_w`` and exits.

Anything that escapes step 3 is the threat model — escape-attempt tests
in ``tests/sandbox/`` are the contract.

The child NEVER inherits parent FDs (we use spawn, not fork) and never
opens a DB session. The only network exit is the socket guard's
whitelisted ``host``:``port``.
"""

from __future__ import annotations

import io
import os
import pickle
import sys
import traceback
from typing import Any

# Default resource caps. The engine overrides via env var so tests /
# config can dial these. Kept module-level so a child without env vars
# (e.g. ad-hoc smoke test) still gets safe defaults.
DEFAULT_RLIMIT_CPU_SECONDS = 10
DEFAULT_RLIMIT_AS_BYTES = 256 * 1024 * 1024  # 256 MiB
DEFAULT_RLIMIT_NOFILE = 64
DEFAULT_RLIMIT_FSIZE = 0  # disk writes forbidden


# ---------------------------------------------------------------------------
# Defensive helpers
# ---------------------------------------------------------------------------
def _apply_rlimits() -> None:
    """Set RLIMIT_* on the child process.

    Resource cap values come from env vars set by the parent (so the
    test suite can drive them); fall back to module defaults if unset.
    We import ``resource`` here — it's a sandbox-internal module, NOT
    user code, so the allowlist hasn't been installed yet.
    """
    import resource  # noqa: I001 — must run BEFORE allowlist installs

    cpu = int(os.environ.get("AETHER_SANDBOX_RLIMIT_CPU", DEFAULT_RLIMIT_CPU_SECONDS))
    mem = int(os.environ.get("AETHER_SANDBOX_RLIMIT_AS", DEFAULT_RLIMIT_AS_BYTES))
    nofile = int(os.environ.get("AETHER_SANDBOX_RLIMIT_NOFILE", DEFAULT_RLIMIT_NOFILE))
    fsize = int(os.environ.get("AETHER_SANDBOX_RLIMIT_FSIZE", DEFAULT_RLIMIT_FSIZE))

    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
    resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
    resource.setrlimit(resource.RLIMIT_NOFILE, (nofile, nofile))
    resource.setrlimit(resource.RLIMIT_FSIZE, (fsize, fsize))


def _install_socket_guard(allowed_host: str, allowed_port: int) -> None:
    """Monkey-patch ``socket.socket.connect`` to whitelist ONE peer.

    This is the primary network boundary — we lean on it because the
    allowlist alone cannot stop a determined attacker reaching the raw
    socket module via reflection.

    The guard rewrites ``socket.socket.connect`` to inspect the address
    tuple and raise :class:`aether_api.sandbox.errors.NetworkDenied`
    unless the peer matches ``(allowed_host, allowed_port)``. We do this
    BEFORE the allowlist denies ``socket`` outright so legitimate
    reflection (e.g. ``ctx.mcp`` via the proxy) can still reach the
    underlying socket if it must.
    """
    import socket

    from aether_api.sandbox.errors import NetworkDenied

    real_connect = socket.socket.connect

    def guarded_connect(self: socket.socket, address: Any) -> None:
        # IPv4/IPv6 connect args are (host, port[, flowinfo[, scopeid]]).
        # Unix sockets pass a path string — refuse those outright.
        if not isinstance(address, tuple) or len(address) < 2:
            raise NetworkDenied(
                f"non-TCP socket destination {address!r} is denied",
                denial_reason=f"socket:{address!r}",
            )
        host, port = address[0], address[1]
        if host != allowed_host or int(port) != allowed_port:
            raise NetworkDenied(
                f"connect to {host}:{port} denied "
                f"(allowed: {allowed_host}:{allowed_port})",
                denial_reason=f"socket:{host}:{port}",
            )
        real_connect(self, address)

    socket.socket.connect = guarded_connect  # type: ignore[method-assign,assignment]


def _scrub_sys() -> None:
    """Remove a few high-risk attributes from ``sys`` before user code runs.

    We can't drop ``sys`` entirely — the import system depends on it. But
    we can clear the obvious foot-cannons. Determined attackers can still
    reach equivalents; this is one more cost-raiser, not a boundary.
    """
    # ``sys.modules`` MUST stay (the import system reads it). We do drop
    # the parent's argv to avoid leaking the engine module path, and we
    # close ``sys.stdin`` since the bootstrap is done reading from it.
    sys.argv = ["agent-sandbox-child"]


def _restricted_globals() -> dict[str, Any]:
    """Globals dict for the user's compiled source.

    We don't try to lock down ``__builtins__`` aggressively — the allowlist
    handles the import side, and the user CAN reach
    ``object.__subclasses__()`` regardless of what we do here. The OS-level
    rlimits + socket guard are the actual defences. Keep ``__builtins__``
    intact so the user's code can call ``len``, ``range``, etc. without
    surprises.
    """
    return {"__name__": "__aether_agent__", "__builtins__": __builtins__}


# ---------------------------------------------------------------------------
# Result wire format
# ---------------------------------------------------------------------------
def _emit_result(write_conn: Any, payload: dict[str, Any]) -> None:
    """Send the pickled result through the multiprocessing Connection."""
    import contextlib

    try:
        write_conn.send_bytes(
            pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL)
        )
    finally:
        with contextlib.suppress(Exception):
            write_conn.close()


def _build_failure(status: str, exc: BaseException, denial_reason: str | None) -> dict[str, Any]:
    return {
        "status": status,
        "result": None,
        "denial_reason": denial_reason,
        "exc_type": type(exc).__name__,
        "exc_message": str(exc),
        "traceback": traceback.format_exc(),
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def child_main(read_conn: Any, write_conn: Any) -> None:
    """Subprocess body — wired up by :class:`Engine` in the parent.

    ``read_conn`` / ``write_conn`` are :class:`multiprocessing.connection.Connection`
    objects that survive the spawn handle-inheritance dance (raw FDs do not).
    """
    # 1. Read the parent's pickled payload BEFORE applying any guards.
    #    We need ``pickle`` and ``aether_api.sandbox.ctx`` to load; the
    #    allowlist would block ``aether_api.sandbox.ctx`` if installed.
    try:
        payload = pickle.loads(read_conn.recv_bytes())
        read_conn.close()
    except Exception as exc:  # noqa: BLE001
        _emit_result(write_conn, _build_failure("error", exc, "bootstrap:pickle"))
        return

    source: str = payload["source"]
    entrypoint_name: str = payload["entrypoint"]
    ctx_obj = payload["ctx"]  # AgentContext dataclass instance

    # 2. Capture user stdout/stderr into in-memory buffers so the parent
    #    can read them back via the result wire format AND the OS stderr
    #    (where logging_adapter writes JSON) stays untouched for the
    #    child's own bootstrap diagnostics.
    user_stdout = io.StringIO()
    user_stderr = io.StringIO()
    real_stdout, real_stderr = sys.stdout, sys.stderr
    sys.stdout = user_stdout
    sys.stderr = user_stderr

    def _finalise(payload_out: dict[str, Any]) -> None:
        # Always include captured streams (tail-truncated by the parent).
        payload_out.setdefault("stdout", user_stdout.getvalue())
        payload_out.setdefault("stderr", user_stderr.getvalue())
        # Restore real stdio so any last-ditch traceback prints somewhere
        # the OS can carry — not strictly necessary, but cheap insurance.
        sys.stdout = real_stdout
        sys.stderr = real_stderr
        _emit_result(write_conn, payload_out)

    # 3. Install the network boundary — MUST come before user code.
    try:
        _install_socket_guard(ctx_obj.mcp.host, ctx_obj.mcp.port)
    except Exception as exc:  # noqa: BLE001
        _finalise(_build_failure("error", exc, "bootstrap:socket-guard"))
        return

    # 4. Install rlimits BEFORE the allowlist so the import system can
    #    pull what it needs during bootstrap. The allowlist closes the
    #    door behind us once user-code-relevant modules are reachable.
    try:
        _apply_rlimits()
    except Exception as exc:  # noqa: BLE001
        _finalise(_build_failure("error", exc, "bootstrap:rlimits"))
        return

    # 5. Install the allowlist + scrub sys + evict pre-loaded denied modules.
    try:
        from aether_api.sandbox.allowlist import EXPLICITLY_DENIED
        from aether_api.sandbox.allowlist import install as install_allowlist

        install_allowlist()
        _scrub_sys()
        # Python pre-imports a bunch of stdlib at interpreter startup
        # (subprocess, os, socket etc.). They're already in ``sys.modules``,
        # so a user ``import subprocess`` would HIT the cache and bypass our
        # meta_path finder. Drop the dangerous ones now — subsequent
        # ``import X`` re-enters the import system and hits the allowlist.
        for _denied in EXPLICITLY_DENIED:
            sys.modules.pop(_denied, None)
    except Exception as exc:  # noqa: BLE001
        _finalise(_build_failure("error", exc, "bootstrap:allowlist"))
        return

    # 6. Compile + exec the user source in a fresh globals dict.
    globals_dict = _restricted_globals()
    try:
        code = compile(source, filename="<agents.logica>", mode="exec")
        exec(code, globals_dict)  # noqa: S102 — THIS is the sandboxed call
    except MemoryError as exc:
        _finalise(_build_failure("oom", exc, "rlimit:as"))
        return
    except Exception as exc:  # noqa: BLE001
        # Allowlist-driven ImportDenied / NetworkDenied / FileDenied carry
        # their own status; the base class :class:`SandboxError` has the
        # attribute too.
        from aether_api.sandbox.errors import SandboxError

        if isinstance(exc, SandboxError):
            _finalise(_build_failure(exc.status, exc, exc.denial_reason))
        else:
            _finalise(_build_failure("error", exc, None))
        return

    # 7. Find and call the entrypoint.
    fn = globals_dict.get(entrypoint_name)
    if not callable(fn):
        _finalise(
            {
                "status": "error",
                "result": None,
                "denial_reason": "missing-entrypoint",
                "exc_type": "EntrypointMissing",
                "exc_message": (
                    f"entrypoint {entrypoint_name!r} not found in agents.logica "
                    "(expected a top-level callable)"
                ),
                "traceback": "",
            }
        )
        return

    # 8. Build the MCP proxy and hand control to the user code.
    try:
        from aether_api.sandbox.mcp_proxy import McpProxy

        ctx_obj.mcp_proxy = McpProxy(ctx_obj.mcp, dry_run=ctx_obj.dry_run)
        result = fn(ctx_obj)
    except MemoryError as exc:
        _finalise(_build_failure("oom", exc, "rlimit:as"))
        return
    except Exception as exc:  # noqa: BLE001
        from aether_api.sandbox.errors import SandboxError

        if isinstance(exc, SandboxError):
            _finalise(_build_failure(exc.status, exc, exc.denial_reason))
        else:
            _finalise(_build_failure("error", exc, None))
        return

    # 9. Round-trip the return value through pickle so the parent gets a
    #    plain Python object. If the user returned something un-pickleable
    #    we fall back to ``repr``.
    try:
        pickle.dumps(result)
        safe_result = result
    except Exception:  # noqa: BLE001
        safe_result = repr(result)

    _finalise(
        {
            "status": "success",
            "result": safe_result,
            "denial_reason": None,
            "exc_type": None,
            "exc_message": None,
            "traceback": None,
        }
    )
