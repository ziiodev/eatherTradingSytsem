"""ASGI middleware: per-request correlation ID + structlog context binding.

Mounted FIRST in the ASGI stack so every other layer (CORS, body-size
guards, routers, exception handlers) emits log records that already carry
the ``request_id``. The middleware also binds ``user_id`` / ``project_id``
LATER — those are set by ``current_user`` and the project-scoping
dependency once the request is authenticated, NOT here.

Design choices:

* Raw ASGI (not Starlette's :class:`BaseHTTPMiddleware`) — BaseHTTPMiddleware
  spins up an async task per request which breaks ``contextvars``
  propagation in a few edge cases. The flat ASGI form is fewer moving
  parts AND has no task hop.
* The incoming ``X-Request-ID`` header is honoured as-is when it is a
  syntactically valid UUID (case-insensitive); otherwise we generate a
  fresh UUID4. That keeps trust narrow: a proxy upstream can correlate
  but cannot inject arbitrary log keys.
* The middleware echoes the chosen request id back in the response
  ``X-Request-ID`` header so clients (browser devtools, curl, k6) can
  paste it straight into a log search.
"""

from __future__ import annotations

import uuid
from typing import Final

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

#: Header name used both for the incoming hint and the echoed-back response
#: id. Case-insensitive per HTTP spec; we lowercase before comparing to
#: ASGI's lowercase header tuples.
_REQUEST_ID_HEADER: Final[bytes] = b"x-request-id"

#: Reasonable upper bound on the size of an incoming request-id hint. ASGI
#: headers are arbitrary bytes; we don't want to bind 4 KiB of attacker-
#: controlled junk into ``contextvars``.
_REQUEST_ID_MAX_LEN: Final[int] = 64


def _extract_incoming_request_id(scope: Scope) -> str | None:
    """Return a trusted request id from the request headers, or None.

    Headers in ASGI scope are a list of ``(name_bytes, value_bytes)`` with
    name pre-lowercased. We accept the value only if it parses as a UUID —
    that bounds what an upstream proxy can inject while still letting
    standard correlation tools (kong, traefik, cloud LBs) flow through.
    """
    for name, value in scope.get("headers", []):
        if name != _REQUEST_ID_HEADER:
            continue
        if len(value) > _REQUEST_ID_MAX_LEN:
            return None
        try:
            text = value.decode("ascii")
        except UnicodeDecodeError:
            return None
        try:
            return str(uuid.UUID(text))
        except ValueError:
            return None
    return None


class RequestIDMiddleware:
    """Bind ``request_id`` into structlog contextvars for the request lifetime.

    Also echoes the chosen id in the ``X-Request-ID`` response header. The
    binding is cleared on the way out so concurrent requests on the same
    event loop don't see each other's id.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Only HTTP scope binds; WebSocket / lifespan pass through untouched.
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _extract_incoming_request_id(scope) or str(uuid.uuid4())

        # Clear any prior bindings before binding so a previous request on
        # the same event-loop task can't leak its keys into this one.
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        # Stash the id in scope.state so non-logging code (e.g. error
        # responses) can also expose it.
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_with_header(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                # Replace any upstream value rather than appending — keep
                # the echo deterministic.
                headers = [
                    (n, v) for n, v in headers if n.lower() != _REQUEST_ID_HEADER
                ]
                headers.append((_REQUEST_ID_HEADER, request_id.encode("ascii")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_header)
        finally:
            structlog.contextvars.clear_contextvars()
