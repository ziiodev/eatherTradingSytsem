"""Structured logging for the MCP MetaTrader 5 server.

Uses :mod:`structlog` with a JSON renderer by default (toggleable via
``MT5_LOG_JSON``). Includes a **credential redactor** processor that masks any
field whose key looks like a secret (``password``, ``token``, ``api_key`` …)
or whose value looks like an MT5 login string before emission.

The redactor is conservative: it never tries to redact arbitrary substrings
inside `event` strings — callers are responsible for not interpolating secrets
into the human-readable message. It does redact ``str``/``bytes`` values bound
to suspicious keys and recursively descends into ``dict``/``list``/``tuple``.
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Mapping, MutableMapping
from typing import Any

import structlog
from structlog.types import EventDict, Processor, WrappedLogger

# Keys whose value should be masked entirely.
_SECRET_KEY_PATTERN = re.compile(
    r"(?i)(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|"
    r"private[_-]?key|credential|auth|session[_-]?id|mt5[_-]?login|account[_-]?password)"
)

# Heuristic: MT5 server passwords frequently look like long alphanumerics; we
# don't try to detect those — the key-name check is the contract.

_REDACTED = "***REDACTED***"


def _redact_value(value: Any) -> Any:
    """Recursively redact suspicious keys inside containers."""

    if isinstance(value, Mapping):
        out: dict[Any, Any] = {}
        for k, v in value.items():
            if isinstance(k, str) and _SECRET_KEY_PATTERN.search(k):
                out[k] = _REDACTED
            else:
                out[k] = _redact_value(v)
        return out
    if isinstance(value, list):
        return [_redact_value(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(v) for v in value)
    return value


def credential_redactor(
    _logger: WrappedLogger,
    _method_name: str,
    event_dict: EventDict,
) -> EventDict:
    """structlog processor that masks credential-like fields in-place."""

    redacted: MutableMapping[str, Any] = {}
    for key, value in event_dict.items():
        if isinstance(key, str) and _SECRET_KEY_PATTERN.search(key):
            redacted[key] = _REDACTED
        else:
            redacted[key] = _redact_value(value)
    return dict(redacted)


def configure_logging(*, level: str = "INFO", json: bool = True) -> None:
    """Configure structlog + stdlib logging for the server.

    Idempotent: safe to call multiple times (e.g. once per test module).
    """

    log_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        format="%(message)s",
        level=log_level,
        stream=sys.stderr,
        force=True,
    )

    processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        credential_redactor,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if json:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer(colors=False))

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger.

    Parameters
    ----------
    name:
        Optional logical name; bound as ``logger`` in the event dict.
    """

    log = structlog.get_logger()
    if name is not None:
        log = log.bind(logger=name)
    return log  # type: ignore[no-any-return]


__all__ = [
    "configure_logging",
    "credential_redactor",
    "get_logger",
]
