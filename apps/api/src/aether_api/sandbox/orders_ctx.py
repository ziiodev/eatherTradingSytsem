"""Sandbox-side ``OrdersProxy`` — the agent's ONLY write path into ``orders``.

Mirrors the :class:`aether_api.sandbox.learning_ctx.EpisodicProxy` pattern
verbatim: a frozen, pickle-safe dataclass that routes every call back to
the parent process through the existing duplex
``multiprocessing.Connection`` set up by
:class:`aether_api.sandbox.engine.Engine`.

The proxy is **write-only**. Three methods:

* :meth:`OrdersProxy.record_open` — UPSERT-by-ticket the row for a freshly
  opened position. Status defaults to ``filled`` on the parent side.
* :meth:`OrdersProxy.record_modify` — UPDATE ``sl`` / ``tp`` / ``comment``
  on an existing row keyed by ticket. The Worker uses this for trailing
  stops, partial close adjustments, etc.
* :meth:`OrdersProxy.record_close` — UPDATE ``status='closed'`` plus the
  close-side fields (``close_time`` / ``close_price`` / ``commission`` /
  ``swap`` / ``profit_gross`` / ``profit_net``) when a position closes.

Hard constraints — enforced here and in the dispatcher (defence in depth):

1. **Frozen identity.** ``user_id`` / ``project_id`` / ``agent_id`` are
   bound at construction (frozen dataclass). The agent CANNOT mutate
   them; an attempt raises ``dataclasses.FrozenInstanceError``.

2. **Parent strips child-supplied tenancy.** Even if a future bug let the
   child re-bind its proxy fields, the parent-side dispatcher
   (:class:`aether_api.sandbox.rpc.RpcHandlers`) IGNORES any ``user_id``
   / ``project_id`` / ``agent_id`` keys in the child's payload and uses
   the tuple it itself recorded at spawn time. The handlers never trust
   the child for tenancy.

3. **Pickle-safe.** The proxy holds only an :class:`RpcClient`
   (Connection-backed) and three string UUIDs. No SQLAlchemy objects, no
   sessions, no open sockets.

4. **No reads.** Historical lookups happen parent-side via dedicated
   surfaces (REST/WS); the Worker has no read methods on this proxy.

The NO-OP variant :class:`NoopOrders` is wired in when
``AETHER_OPERATIVA_PROXY_ENABLED=false``. Any method raises
``RuntimeError("operativa proxy disabled")`` so a Worker that depends on
the proxy fails loudly rather than silently dropping writes.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from aether_api.sandbox.learning_ctx import RpcClientProtocol

__all__ = [
    "NoopOrders",
    "OrderRef",
    "OrdersProxy",
]


# ---------------------------------------------------------------------------
# Wire-friendly value types — frozen dataclasses, pickle-safe.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OrderRef:
    """Receipt returned by every :class:`OrdersProxy` write method.

    ``id`` is the ``orders.id`` UUID minted (or located) parent-side.
    ``ticket`` echoes the ``mt5_ticket`` the Worker passed in (string at
    the API boundary — MT5 deal ids can exceed 32-bit ranges on some
    brokers). ``status`` is the post-write ``orders.status`` value so
    the Worker can confirm the parent saw the transition it expected.
    """

    id: uuid.UUID
    ticket: str
    status: str


# ---------------------------------------------------------------------------
# Helpers — coerce Decimal-friendly inputs to str on the wire.
# ---------------------------------------------------------------------------


def _coerce_optional_number(value: Any) -> str | None:
    """Coerce a number-ish input to a ``str`` for safe pickle transit.

    ``Decimal`` round-trips through pickle but the parent-side handler
    needs to feed it into a ``Numeric`` column anyway. Strings are the
    least surprising wire format (no float-rounding drift) and the
    repository ``upsert_by_ticket`` already calls into SQLAlchemy which
    casts string → Numeric idiomatically.

    ``None`` passes through — every numeric column the proxy touches is
    NULLABLE (per migration ``0013_operativa_orders_extend``).
    """
    if value is None:
        return None
    if isinstance(value, str):
        # Validate it parses as a Decimal so we surface bad input here
        # rather than on the parent side. We discard the result; the
        # string is what we ship.
        Decimal(value)
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, int | float):
        return str(value)
    raise TypeError(f"numeric arg must be number/Decimal/str/None, got {type(value).__name__}")


def _coerce_required_number(value: Any, name: str) -> str:
    """Same as :func:`_coerce_optional_number` but rejects ``None``.

    The Worker MUST supply ``volume`` / ``open_price`` / ``sl`` on
    ``record_open`` — those columns are NOT NULL in the schema (``sl``
    in particular is a CHARTER invariant: every order carries a Stop-
    Loss).
    """
    coerced = _coerce_optional_number(value)
    if coerced is None:
        raise TypeError(f"{name} is required (got None)")
    return coerced


# ---------------------------------------------------------------------------
# Orders proxy — WRITE-ONLY (three record_* methods). No read, no delete.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OrdersProxy:
    """Write-only sink for the Operativa lifecycle (open / modify / close).

    Every method returns a frozen :class:`OrderRef` so the Worker can
    confirm the row id and status post-write. The bound
    ``user_id`` / ``project_id`` / ``agent_id`` are NOT included in the
    payload — the parent-side handler uses its own bound copy so a
    tampered child cannot point a write at a foreign project.
    """

    _rpc: RpcClientProtocol
    user_id: str
    project_id: str
    agent_id: str | None

    def record_open(
        self,
        *,
        ticket: str,
        symbol: str,
        side: str,
        volume: Any,
        open_price: Any,
        sl: Any,
        tp: Any = None,
        magic: int | None = None,
        comment: str | None = None,
    ) -> OrderRef:
        """UPSERT a row for a freshly-opened position.

        ``ticket`` is the broker-side identifier (string at the API
        boundary). ``volume`` / ``open_price`` / ``sl`` MUST be supplied
        (NOT NULL on the schema; ``sl`` is the CHARTER invariant). The
        rest are NULLABLE.
        """
        if not isinstance(ticket, str) or not ticket:
            raise TypeError(f"ticket must be a non-empty str, got {ticket!r}")
        if not isinstance(symbol, str) or not symbol:
            raise TypeError(f"symbol must be a non-empty str, got {symbol!r}")
        if side not in ("buy", "sell"):
            raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")
        raw: dict[str, Any] = self._rpc.call(
            "orders.record_open",
            ticket=ticket,
            symbol=symbol,
            side=side,
            volume=_coerce_required_number(volume, "volume"),
            open_price=_coerce_required_number(open_price, "open_price"),
            sl=_coerce_required_number(sl, "sl"),
            tp=_coerce_optional_number(tp),
            magic=None if magic is None else int(magic),
            comment=None if comment is None else str(comment),
        )
        return OrderRef(
            id=uuid.UUID(str(raw["id"])),
            ticket=str(raw["ticket"]),
            status=str(raw["status"]),
        )

    def record_modify(
        self,
        *,
        ticket: str,
        sl: Any = None,
        tp: Any = None,
        comment: str | None = None,
    ) -> OrderRef:
        """UPDATE ``sl`` / ``tp`` / ``comment`` on an existing row by ticket.

        At least one of ``sl`` / ``tp`` / ``comment`` SHOULD be supplied;
        the parent-side handler is tolerant of a no-op modify so the
        Worker can ping a row safely.
        """
        if not isinstance(ticket, str) or not ticket:
            raise TypeError(f"ticket must be a non-empty str, got {ticket!r}")
        raw: dict[str, Any] = self._rpc.call(
            "orders.record_modify",
            ticket=ticket,
            sl=_coerce_optional_number(sl),
            tp=_coerce_optional_number(tp),
            comment=None if comment is None else str(comment),
        )
        return OrderRef(
            id=uuid.UUID(str(raw["id"])),
            ticket=str(raw["ticket"]),
            status=str(raw["status"]),
        )

    def record_close(
        self,
        *,
        ticket: str,
        close_price: Any,
        close_time: Any,
        commission: Any = None,
        swap: Any = None,
        profit_gross: Any = None,
        profit_net: Any = None,
        comment: str | None = None,
    ) -> OrderRef:
        """UPDATE ``status='closed'`` plus the close-side fields by ticket.

        ``close_time`` may be a ``datetime`` (preferred), an ISO-8601
        string, or ``None`` (the parent uses ``NOW()`` as fallback in
        the handler). Numeric inputs are coerced to ``str`` on the wire.
        """
        if not isinstance(ticket, str) or not ticket:
            raise TypeError(f"ticket must be a non-empty str, got {ticket!r}")
        raw: dict[str, Any] = self._rpc.call(
            "orders.record_close",
            ticket=ticket,
            close_price=_coerce_required_number(close_price, "close_price"),
            close_time=_serialize_close_time(close_time),
            commission=_coerce_optional_number(commission),
            swap=_coerce_optional_number(swap),
            profit_gross=_coerce_optional_number(profit_gross),
            profit_net=_coerce_optional_number(profit_net),
            comment=None if comment is None else str(comment),
        )
        return OrderRef(
            id=uuid.UUID(str(raw["id"])),
            ticket=str(raw["ticket"]),
            status=str(raw["status"]),
        )


def _serialize_close_time(value: Any) -> str | None:
    """Coerce ``close_time`` for the wire.

    ``None`` passes through (parent uses NOW()). ``datetime`` is rendered
    via :meth:`datetime.isoformat`. Strings pass through verbatim — the
    parent validates ISO-8601 there. Any other type raises locally so
    the Worker gets a clear error on the child side.
    """
    from datetime import datetime

    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        return value
    raise TypeError(f"close_time must be datetime / str / None, got {type(value).__name__}")


# ---------------------------------------------------------------------------
# NO-OP variant — wired in when AETHER_OPERATIVA_PROXY_ENABLED=false.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NoopOrders:
    """Drop-in replacement when the operativa proxy is disabled.

    Mirrors :class:`aether_api.sandbox.learning_ctx.NoopEpisodic`: any
    method raises ``RuntimeError("operativa proxy disabled")`` so a
    Worker that depends on the proxy fails loudly rather than silently
    dropping writes. Frozen so agent code cannot rebind
    ``user_id`` / ``project_id`` / ``agent_id``.
    """

    user_id: str = ""
    project_id: str = ""
    agent_id: str | None = None

    def record_open(
        self,
        *,
        ticket: str,  # noqa: ARG002
        symbol: str,  # noqa: ARG002
        side: str,  # noqa: ARG002
        volume: Any,  # noqa: ARG002
        open_price: Any,  # noqa: ARG002
        sl: Any,  # noqa: ARG002
        tp: Any = None,  # noqa: ARG002
        magic: int | None = None,  # noqa: ARG002
        comment: str | None = None,  # noqa: ARG002
    ) -> OrderRef:
        raise RuntimeError("operativa proxy disabled")

    def record_modify(
        self,
        *,
        ticket: str,  # noqa: ARG002
        sl: Any = None,  # noqa: ARG002
        tp: Any = None,  # noqa: ARG002
        comment: str | None = None,  # noqa: ARG002
    ) -> OrderRef:
        raise RuntimeError("operativa proxy disabled")

    def record_close(
        self,
        *,
        ticket: str,  # noqa: ARG002
        close_price: Any,  # noqa: ARG002
        close_time: Any,  # noqa: ARG002
        commission: Any = None,  # noqa: ARG002
        swap: Any = None,  # noqa: ARG002
        profit_gross: Any = None,  # noqa: ARG002
        profit_net: Any = None,  # noqa: ARG002
        comment: str | None = None,  # noqa: ARG002
    ) -> OrderRef:
        raise RuntimeError("operativa proxy disabled")
