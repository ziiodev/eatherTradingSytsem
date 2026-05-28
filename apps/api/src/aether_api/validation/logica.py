"""``logica`` shape validation — parses Python source WITHOUT executing it.

The agents domain stores Python source in ``agents.logica``. Before we
persist it we want a cheap "is this even parseable Python?" gate, so the
operator gets a syntax error inline on save instead of discovering it
only when the sandbox tries to import the module days later.

Two hard rules:

1. We do NOT execute the source. ``ast.parse`` builds a syntax tree —
   it does not import, exec, or eval. Anything heavier lives in the
   ``agent-execution-sandbox`` change.
2. We do NOT block the event loop. ``ast.parse`` is CPU work; for a
   pathological 256 KiB input it could take a long time, so we run it
   inside ``asyncio.to_thread`` and ``asyncio.wait_for`` with a hard
   timeout. Slow input gets rejected, not stalled.
"""

from __future__ import annotations

import ast
import asyncio
from typing import Final

#: Hard cap on ``logica`` UTF-8 byte length. Picked to be generous for any
#: realistic Python agent (most fit in <10 KiB) while still small enough
#: that a single PATCH cannot exhaust DB / memory budgets. The HTTP body
#: size guard in ``main.py`` is a coarser, request-level cap that fires
#: before the body is even read — this one enforces the per-field budget.
MAX_LOGICA_BYTES: Final[int] = 256 * 1024

#: Maximum wall-clock seconds we allow ``ast.parse`` to run. Real Python
#: source parses in <50 ms even for very large files; 1.5 s is comfortably
#: above that ceiling while still bounding worst-case latency.
_PARSE_TIMEOUT_SECONDS: Final[float] = 1.5


class LogicaTooLargeError(ValueError):
    """The ``logica`` payload exceeds :data:`MAX_LOGICA_BYTES`."""

    def __init__(self, size_bytes: int) -> None:
        super().__init__(
            f"logica too large: {size_bytes} bytes (max {MAX_LOGICA_BYTES})"
        )
        self.size_bytes = size_bytes


class LogicaSyntaxError(ValueError):
    """``logica`` source did not parse as valid Python."""

    def __init__(self, line: int | None, col: int | None, message: str) -> None:
        # Normalise None → 0 in the human-readable message so the API
        # response is always well-formed. The structured fields keep
        # ``None`` so callers can render conditionally.
        super().__init__(f"syntax error at line {line or 0}, col {col or 0}: {message}")
        self.line = line
        self.col = col
        self.message = message


class LogicaParseTimeoutError(ValueError):
    """``ast.parse`` exceeded the validation timeout."""

    def __init__(self) -> None:
        super().__init__("logica validation timed out")


async def validate_logica_shape(source: str) -> None:
    """Validate the *shape* of ``source`` as Python — does NOT execute it.

    Raises one of:

    * :class:`LogicaTooLargeError` — UTF-8 byte length exceeds the cap.
    * :class:`LogicaSyntaxError` — ``ast.parse`` raised ``SyntaxError``.
      The line/col/msg are surfaced verbatim so the editor can highlight.
    * :class:`LogicaParseTimeoutError` — parsing did not finish within
      :data:`_PARSE_TIMEOUT_SECONDS`. Treated as a 422 by the router.

    Returns ``None`` on success — the caller persists the original
    source byte-for-byte (no normalisation here, on purpose).
    """
    # Size gate FIRST — cheap and avoids feeding a multi-MB blob to the
    # parser thread only to reject it after the fact.
    size_bytes = len(source.encode("utf-8"))
    if size_bytes > MAX_LOGICA_BYTES:
        raise LogicaTooLargeError(size_bytes)

    try:
        await asyncio.wait_for(
            asyncio.to_thread(ast.parse, source),
            timeout=_PARSE_TIMEOUT_SECONDS,
        )
    except TimeoutError as exc:  # asyncio.TimeoutError is TimeoutError on 3.11+
        raise LogicaParseTimeoutError() from exc
    except SyntaxError as exc:
        # ``offset`` is 1-based and may be None for some edge cases.
        raise LogicaSyntaxError(
            line=exc.lineno,
            col=exc.offset,
            message=exc.msg or "invalid syntax",
        ) from exc


#: Entrypoint name convention. Surfaced as a non-blocking warning when
#: the user-declared entrypoint is not present as a top-level ``def`` in
#: the parsed source. Conventions per agent type live in the router.
ENTRYPOINT_REGEX: Final[str] = r"^[A-Za-z_][A-Za-z0-9_]{0,119}$"
