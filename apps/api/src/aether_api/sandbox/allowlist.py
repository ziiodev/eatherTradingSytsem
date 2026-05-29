"""Module allowlist installed on the child's ``sys.meta_path``.

**Defence-in-depth, NOT the primary boundary.** A determined attacker
can reach blocked modules via CPython internals (``object.__subclasses__()``
walking is the textbook escape — see ``design.md``). The OS-level rlimits
and the socket guard are the two boundaries we actually trust; this layer
exists to raise the cost of trivial escapes and to give the operator a
clearer audit signal ("import denied: ctypes") than a deep stack trace.

v1 allowlist deliberately keeps to numerical / data-analysis libs the
charter mentions:

    math, datetime, json, decimal, statistics, collections, dataclasses,
    typing, enum, itertools, functools, re, numpy, pandas, pandas_ta

Anything else raises :class:`aether_api.sandbox.errors.ImportDenied`
during ``find_module`` — including ``os``, ``sys``, ``subprocess``,
``ctypes``, ``importlib``, and raw ``socket`` (the child reaches the
project MCP via :mod:`aether_api.sandbox.mcp_proxy` instead).
"""

from __future__ import annotations

from collections.abc import Sequence
from importlib.abc import MetaPathFinder
from importlib.machinery import ModuleSpec
from typing import Any, Final

#: User-facing allowlist. Submodules of these (e.g. ``numpy.linalg``,
#: ``pandas.api.types``) are allowed automatically — the finder does a
#: prefix match, not exact equality.
ALLOWED_TOP_LEVEL: Final[frozenset[str]] = frozenset(
    {
        # Stdlib safe subset
        "math",
        "datetime",
        "json",
        "decimal",
        "statistics",
        "collections",
        "dataclasses",
        "typing",
        "enum",
        "itertools",
        "functools",
        "re",
        # Numerical / data
        "numpy",
        "pandas",
        "pandas_ta",
        # Internal bridge — the child needs to reach back into the proxy
        # shim and the ctx serializer. These are exposed READ-ONLY; the
        # ``mcp_proxy`` module itself does the egress filtering.
        "aether_api.sandbox.ctx",
        "aether_api.sandbox.mcp_proxy",
        "aether_api.sandbox.errors",
        # Learning ctx — frozen dataclasses + read/write proxies. The
        # proxies themselves enforce tenancy; the child code can import
        # the module to look up type symbols if it wants.
        "aether_api.sandbox.learning_ctx",
        "aether_api.sandbox.rpc",
        # Operativa ctx — frozen ``OrdersProxy`` + ``NoopOrders``. Same
        # rationale: the child bootstrap imports it to assemble
        # ``ctx.orders``; user agent code can also reach the type
        # symbols for isinstance checks.
        "aether_api.sandbox.orders_ctx",
    }
)

#: Hard-deny set. These never pass the finder even if a future allowlist
#: edit slips them in by mistake — defence-in-depth's own defence.
EXPLICITLY_DENIED: Final[frozenset[str]] = frozenset(
    {
        "os",
        "subprocess",
        "ctypes",
        "socket",
        "ssl",
        "importlib",
        "pickle",
        "marshal",
        "shutil",
        "pathlib",
        "tempfile",
        "fcntl",
        "resource",
        # ``sys`` is intentionally excluded from EXPLICITLY_DENIED — the
        # child needs sys.modules / sys.exc_info during normal operation,
        # and our import finder installs onto sys.meta_path. We rely on
        # the child bootstrap to scrub the dangerous attributes BEFORE
        # handing control to the user entrypoint (see child.py).
    }
)


def _is_allowed(fullname: str) -> bool:
    """Return True iff ``fullname`` (e.g. ``"numpy.linalg"``) is allowed.

    Submodule resolution: any module whose top-level package is in
    :data:`ALLOWED_TOP_LEVEL` is permitted, except those that explicitly
    appear in :data:`EXPLICITLY_DENIED` (which takes priority).
    """
    top = fullname.split(".", 1)[0]
    if top in EXPLICITLY_DENIED or fullname in EXPLICITLY_DENIED:
        return False
    if fullname in ALLOWED_TOP_LEVEL:
        return True
    # ``aether_api.sandbox.*`` namespace + numpy/pandas submodules fall
    # through here — any module whose top-level package is allowlisted.
    return top in ALLOWED_TOP_LEVEL


class AllowlistFinder(MetaPathFinder):
    """``sys.meta_path`` finder that rejects non-allowlisted imports.

    Installed by :mod:`aether_api.sandbox.child` BEFORE the user
    entrypoint is loaded. Raising :class:`ImportError` (subclass of it,
    really — :class:`aether_api.sandbox.errors.ImportDenied`) is what the
    Python import system expects; the user sees a normal ImportError
    traceback and the child bootstrap converts the marker into the
    ``denied_import`` agent_run status.
    """

    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None = None,  # noqa: ARG002 — required by ABC
        target: Any = None,  # noqa: ARG002 — required by ABC
    ) -> ModuleSpec | None:
        # Defer to the rest of ``sys.meta_path`` when allowed. Returning
        # ``None`` here means "I have no opinion; ask the next finder."
        if _is_allowed(fullname):
            return None
        # Lazy import to avoid pulling errors.py into the parent at
        # allowlist-import time.
        from aether_api.sandbox.errors import ImportDenied

        raise ImportDenied(
            f"import of '{fullname}' is denied by sandbox allowlist",
            denial_reason=f"import:{fullname}",
        )


def install() -> AllowlistFinder:
    """Push :class:`AllowlistFinder` onto the FRONT of ``sys.meta_path``.

    Idempotent — duplicate installs are a no-op so a child that
    accidentally re-runs the bootstrap doesn't end up with two finders.
    Returns the installed finder (mostly for the test suite).
    """
    import sys

    for existing in sys.meta_path:
        if isinstance(existing, AllowlistFinder):
            return existing
    finder = AllowlistFinder()
    sys.meta_path.insert(0, finder)
    return finder
