"""WebSocket router for the Operativa realtime surface.

Phase 4 of the ``project-operativa`` change. Mounts a single WS
endpoint at ``/api/pairs/{pair_id}/operativa/ws`` that pushes
account / position / mcp_status / reconciler events to the operator
dashboard.

Hard rules enforced HERE (per
``sdd/project-operativa/spec/multi-tenancy-delta`` #2122):

* Auth via the ``aether_access`` httpOnly JWT cookie ONLY. No tokens
  via query string, ``Sec-WebSocket-Protocol``, or message-level
  payload. A missing/invalid cookie → close with code 1008 BEFORE any
  ``accept()`` frame is sent.
* Origin header MUST match ``settings.cors_allowed_origins``. A
  mismatch → close with 1008 BEFORE accept.
* Cross-tenant access (cookie user does NOT own ``pair_id``) →
  close with 1008 AND emit a structured WARN via
  ``log_cross_tenant_attempt``. No existence leak — the close looks
  identical to an unauthenticated request.
* The endpoint never leaks the project id existence on a failed gate.

The router is mounted from :mod:`aether_api.main` only when
``settings.operativa_ws_enabled`` is True. When False, the route does
not exist at all (HTTP 404 on upgrade attempts).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from aether_api.auth.cookies import ACCESS_COOKIE
from aether_api.auth.tokens import verify_access_token
from aether_api.core.settings import get_settings
from aether_api.db.session import get_session_maker
from aether_api.learning.audit import log_cross_tenant_attempt
from aether_api.repositories.pair_repository import PairRepository
from aether_api.repositories.user_repository import UserRepository
from aether_api.services.live_bus import LiveBus, Subscriber

__all__ = ["router"]

logger = logging.getLogger(__name__)

#: WebSocket policy-violation close code per RFC 6455 §7.4.1.
WS_CLOSE_POLICY_VIOLATION: int = 1008

router = APIRouter(prefix="/api/pairs", tags=["operativa-ws"])


@router.websocket("/{pair_id}/operativa/ws")
async def operativa_ws(
    websocket: WebSocket,
    pair_id: uuid.UUID,
) -> None:
    """Operativa WS endpoint.

    The handshake sequence is strict:

    1. Verify the ``Origin`` header against the allowed list. Mismatch
       → close 1008, no accept frame.
    2. Read the access JWT from the cookie. Missing/invalid → close
       1008.
    3. Load the authenticated user — disabled accounts are treated
       like an invalid cookie.
    4. Verify pair ownership. Cross-tenant → close 1008 + audit
       log.
    5. ``websocket.accept()`` — only now is a connection established.
    6. Subscribe to the :class:`LiveBus` and forward events to the
       client. The first event a client sees on a healthy connection
       is the bus heartbeat ping (sent at most ``heartbeat`` seconds
       after subscription).

    The function never returns a value; it runs until the client
    disconnects or the bus task ends.
    """
    settings = get_settings()

    # ----- (1) Origin allowlist check ----------------------------------
    origin = websocket.headers.get("origin")
    if origin is not None and origin not in settings.cors_allowed_origins:
        await _close_policy(
            websocket,
            reason="origin_not_allowed",
        )
        return

    # ----- (2) Access token in httpOnly cookie -------------------------
    access_token = websocket.cookies.get(ACCESS_COOKIE)
    if not access_token:
        await _close_policy(websocket, reason="no_access_cookie")
        return

    user_id = verify_access_token(access_token)
    if user_id is None:
        await _close_policy(websocket, reason="invalid_access_token")
        return

    # ----- (3) + (4) DB-side checks (user is active, owns pair) ----
    session_maker = get_session_maker()
    async with session_maker() as session:
        user = await UserRepository(session).get_by_id(user_id)
        if user is None or not user.is_active:
            await _close_policy(websocket, reason="user_disabled_or_missing")
            return

        pair_row = await PairRepository(session).get_for_user(user_id, pair_id)

    if pair_row is None:
        # Either the pair does not exist OR the caller is not the
        # owner. Spec REQUIRES we treat the cross-tenant case as an
        # audit event. We can't distinguish from a single SELECT, but
        # the spec only requires the audit when the pair actually
        # exists for ANOTHER user — emit the warn here regardless
        # (rate-limiting prevents abuse), keep the close shape stable.
        await log_cross_tenant_attempt(
            actor_user_id=user_id,
            target_project_id=pair_id,
            table_name="pairs",
            operation="operativa_ws_subscribe",
        )
        await _close_policy(websocket, reason="pair_not_owned_or_missing")
        return

    # ----- (5) Accept + (6) subscribe to LiveBus ----------------------
    live_bus: LiveBus | None = _extract_bus(websocket)
    if live_bus is None:
        # Bus not wired (lifespan misconfiguration / feature flag race).
        # Refuse the upgrade rather than accept a doomed connection.
        await _close_policy(websocket, reason="live_bus_unavailable")
        return

    await websocket.accept()
    conn_handle = object()
    subscriber = await live_bus.subscribe(
        pair_id=pair_id,
        user_id=user_id,
        conn_handle=conn_handle,
    )
    try:
        await _pump_events(websocket, subscriber)
    finally:
        await live_bus.unsubscribe(subscriber)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


async def _pump_events(websocket: WebSocket, subscriber: Subscriber) -> None:
    """Forward bus events to the WebSocket until the client disconnects.

    We also drain incoming frames in the background — the spec doesn't
    require a client-to-server protocol, but the WS layer needs the
    receive side serviced or the ASGI server can stall.
    """
    queue = subscriber.queue

    async def _drain_inbound() -> None:
        """Consume + discard any inbound frames so the ASGI socket stays alive."""
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            return
        except Exception:  # noqa: BLE001 — defensive teardown
            return

    drain_task = asyncio.create_task(_drain_inbound(), name="operativa-ws-drain")
    try:
        while True:
            try:
                event = await queue.get()
            except asyncio.CancelledError:
                raise
            try:
                await websocket.send_json(event.to_dict())
            except WebSocketDisconnect:
                return
            except RuntimeError:
                # Starlette raises RuntimeError on a half-closed socket.
                return
            if drain_task.done():
                # Client hung up — exit the pump.
                return
    finally:
        if not drain_task.done():
            drain_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await drain_task


async def _close_policy(websocket: WebSocket, *, reason: str) -> None:
    """Close the WS with code 1008 BEFORE any accept frame is sent.

    Starlette's :class:`WebSocket` API does NOT require an
    ``accept()`` before ``close()`` — the close happens at the ASGI
    layer with a single ``websocket.close`` message. The browser
    sees the upgrade fail with the 1008 code.

    We also log a structured WARN so a sudden burst of failed
    upgrades is visible to the operator. The ``reason`` is NEVER sent
    to the client — it stays in our logs.
    """
    logger.warning(
        "aether.operativa_ws.close",
        extra={
            "reason": reason,
            "code": WS_CLOSE_POLICY_VIOLATION,
        },
    )
    with contextlib.suppress(RuntimeError):
        # RuntimeError: socket already closed by the peer.
        await websocket.close(code=WS_CLOSE_POLICY_VIOLATION)


def _extract_bus(websocket: WebSocket) -> LiveBus | None:
    """Return the :class:`LiveBus` attached to ``app.state`` (or None).

    Stored via the FastAPI lifespan in :mod:`aether_api.main`. We
    look it up via the ASGI app reference rather than a module-level
    binding so the bus is per-process and survives test recreation.
    """
    scope_app = websocket.scope.get("app")
    if scope_app is None:
        return None
    state = getattr(scope_app, "state", None)
    if state is None:
        return None
    return getattr(state, "live_bus", None)


