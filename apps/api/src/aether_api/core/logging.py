"""Structured logging via structlog with a stdlib bridge.

Single source of truth for log output across the FastAPI app, uvicorn,
and SQLAlchemy. The chain is:

* ``structlog.contextvars.merge_contextvars`` — pulls per-request bindings
  (``request_id``, ``user_id``, ``project_id``) attached by the Request-ID
  middleware (see :mod:`aether_api.core.middleware`).
* ``add_log_level`` — promotes the method name (info/warn/...) into a
  ``level`` field.
* ``TimeStamper(fmt="iso", utc=True)`` — ISO-8601 UTC ``timestamp``.
* ``format_exc_info`` — turns an ``exc_info`` kwarg into a stringified
  traceback at the ``exception`` key (so JSON consumers can render it).
* :func:`aether_api.core.pii.scrub_pii_processor` — masks forbidden keys
  + JWT-shaped strings.
* ``JSONRenderer`` — terminal renderer, one JSON object per line.

Stdlib loggers (uvicorn.access, uvicorn.error, sqlalchemy.engine, ...) are
funnelled through the same chain via ``structlog.stdlib.ProcessorFormatter``
so the operator sees ONE stream of JSON, not two.

Setup is idempotent: tests that re-import / re-create the app do not
accumulate handlers or processor chains.
"""

from __future__ import annotations

import logging
import sys
from typing import Final

import structlog

from aether_api.core.pii import scrub_pii_processor

#: Loggers uvicorn configures by default. We strip their handlers and let
#: them propagate to root, where the single JSON handler is installed.
_UVICORN_LOGGERS: Final[tuple[str, ...]] = (
    "uvicorn",
    "uvicorn.error",
    "uvicorn.access",
)

#: SQLAlchemy logger — also bridged so query timing / errors join the JSON
#: stream. Default level INFO; the engine is configured with ``echo=False``
#: so this is quiet in practice.
_SQLALCHEMY_LOGGER: Final[str] = "sqlalchemy.engine"

#: Marker attribute on the root logger — set on first install so a second
#: call is a no-op for processors but still re-applies levels (cheap).
_INSTALLED_MARKER: Final[str] = "_aether_structlog_installed"


# ---------------------------------------------------------------------------
# Processor chain factory — shared between structlog and the stdlib bridge.
# ---------------------------------------------------------------------------
def _shared_processors() -> list[structlog.types.Processor]:
    """The processors every log record (structlog or stdlib) flows through.

    The stdlib bridge invokes these as ``foreign_pre_chain`` so a logger
    emitted via :mod:`logging` ends up with the same shape as a logger
    emitted via :mod:`structlog`.
    """
    return [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True, key="timestamp"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        scrub_pii_processor,
    ]


def setup_logging(level: str = "INFO") -> None:
    """Configure structlog + the stdlib bridge to emit JSON lines on stdout.

    Idempotent: re-invoking drops any prior handlers and re-applies the
    chain. This matters for the test suite, which re-creates the FastAPI
    app per session.
    """
    numeric_level = logging.getLevelName(level.upper())
    if not isinstance(numeric_level, int):
        numeric_level = logging.INFO

    shared = _shared_processors()

    # -----------------------------------------------------------------------
    # structlog configuration — native callers (``structlog.get_logger()``).
    # -----------------------------------------------------------------------
    structlog.configure(
        processors=[
            *shared,
            # The ProcessorFormatter receives a ready event dict; the final
            # renderer is attached on the formatter side (see below). For
            # native structlog calls we still need a renderer here.
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # -----------------------------------------------------------------------
    # Stdlib bridge — uvicorn / sqlalchemy / anything using logging.getLogger.
    # -----------------------------------------------------------------------
    formatter = structlog.stdlib.ProcessorFormatter(
        # ``foreign_pre_chain`` runs on records emitted by stdlib loggers,
        # bringing them up to the same shape as native structlog events.
        foreign_pre_chain=shared,
        # ``processors`` runs once the event is in dict form (regardless of
        # source). The terminal JSONRenderer turns the dict into a line.
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(formatter)
    handler.setLevel(numeric_level)

    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(handler)
    root.setLevel(numeric_level)

    # Force uvicorn's loggers to drop their default text handler and bubble
    # up to root. ``propagate = True`` is the post-strip default but we
    # set it explicitly so re-init behavior is obvious.
    for name in (*_UVICORN_LOGGERS, _SQLALCHEMY_LOGGER):
        ul = logging.getLogger(name)
        for h in list(ul.handlers):
            ul.removeHandler(h)
        ul.setLevel(numeric_level)
        ul.propagate = True

    # Mark root so a future call from the same process knows we've already
    # wired things up. We still re-run the install to handle level changes.
    setattr(root, _INSTALLED_MARKER, True)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Convenience wrapper so callers don't import structlog directly.

    ``name`` is forwarded to :func:`structlog.get_logger` which uses it as
    the ``logger_name`` field via :func:`add_logger_name`.
    """
    logger = structlog.get_logger(name) if name else structlog.get_logger()
    return logger  # type: ignore[no-any-return]  # structlog's stub returns Any
