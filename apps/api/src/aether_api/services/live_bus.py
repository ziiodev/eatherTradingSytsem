"""``LiveBus`` — in-process pub/sub for the Operativa realtime surface.

Phase 4 of the ``project-operativa`` change. The bus owns one
``asyncio.Task`` per ACTIVE pair subscription set; each task polls
the per-pair MCP endpoint on two cadences (5s for account +
positions, 30s for closed-trade reconciliation) and broadcasts diff
events to every WebSocket subscriber attached to that pair.

Topology decisions (locked in ``sdd/project-operativa/design`` #2125):

* **Per-pair ref-counted task**: the polling task is started on the
  first subscriber for a pair and cancelled when the LAST subscriber
  disconnects. No idle CPU is burned for pairs with zero active
  operator WS connections.
* **Bounded per-subscriber queue** (``maxsize=100``, drop-oldest on
  overflow): a slow consumer cannot back up the producer task or
  consume unbounded memory. The contract is "best-effort latest" —
  losing an old snapshot to free room for a newer one is correct.
* **In-process only**: no Redis / no broker. A second backend process
  would have its own bus and would poll independently — both correct,
  just not shared. Spec ADR-001 declares this acceptable for v1.
* **Tenancy / cross-tenant isolation**: each subscriber carries the
  ``user_id`` of the cookie that authenticated the WS upgrade. The
  router is responsible for refusing cross-tenant pair access
  BEFORE calling :meth:`LiveBus.subscribe`; the bus assumes the
  caller has already gated and only routes by ``pair_id``.

Public surface (the rest is internal):

* :class:`LiveEvent`     — wire-shape envelope.
* :class:`Subscriber`    — opaque handle returned by :meth:`subscribe`.
* :class:`LiveBus`       — singleton attached to ``app.state.live_bus``.

The bus never touches the DB itself except through the reconciler
helper, which opens a fresh session via the injected ``session_factory``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aether_api.mcp_client.client import MCPClient, get_mcp_client
from aether_api.mcp_client.errors import MCPClientError, MCPUnreachable
from aether_api.models.order import Order
from aether_api.models.pair import Pair
from aether_api.repositories.order_repository import OrderRepository
from aether_api.services.operativa_metrics import set_mcp_status, set_ws_subscribers

__all__ = [
    "DEFAULT_HEARTBEAT_INTERVAL_SECONDS",
    "DEFAULT_POSITIONS_POLL_SECONDS",
    "DEFAULT_RECONCILE_POLL_SECONDS",
    "WORKER_AUTHORED_FIELDS",
    "LiveBus",
    "LiveEvent",
    "MCPClientFactory",
    "SessionFactory",
    "Subscriber",
    "reconcile_history",
]

logger = logging.getLogger(__name__)

#: Wall-clock cadence for the account + positions poll (charter window:
#: 5s). Configurable per-bus only for tests; production callers MUST
#: leave the default.
DEFAULT_POSITIONS_POLL_SECONDS: float = 5.0

#: Wall-clock cadence for the closed-trade reconciler poll (30 s).
DEFAULT_RECONCILE_POLL_SECONDS: float = 30.0

#: Heartbeat ping cadence. Sent on every tick so a TCP-dead WS shows up
#: in the parent task and the subscriber can be reaped.
DEFAULT_HEARTBEAT_INTERVAL_SECONDS: float = 30.0

#: Per-subscriber queue depth. Drop-OLDEST on overflow.
SUBSCRIBER_QUEUE_MAXSIZE: int = 100


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


SessionFactory = async_sessionmaker[AsyncSession]
MCPClientFactory = Callable[[Pair], MCPClient]


@dataclass(slots=True, frozen=True)
class LiveEvent:
    """One bus event sent to subscribers.

    The wire shape (after :meth:`to_dict`) follows the spec event
    protocol — one ``type`` discriminator plus a ``payload`` dict that
    carries snapshot data. The ``ts`` field is the server-side
    timestamp at emission (ISO-8601, always UTC).
    """

    type: str
    payload: dict[str, Any]
    ts: datetime = field(default_factory=lambda: datetime.now(tz=UTC))

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "payload": self.payload,
            "ts": self.ts.isoformat(),
        }


class Subscriber:
    """Handle returned by :meth:`LiveBus.subscribe`.

    Carries the per-subscriber bounded queue, the tenant id (audit
    only — the router gates BEFORE calling subscribe), and an opaque
    connection token used by :meth:`LiveBus.unsubscribe` to identify
    the right row in the subscriber set.

    Hashing is by identity so subscribers can live in a ``set`` even
    though their ``queue`` attribute is itself unhashable. Two
    subscribers are equal iff they are the same object — there's no
    "merge same-conn" semantic.
    """

    __slots__ = ("pair_id", "user_id", "conn_handle", "queue")

    def __init__(
        self,
        *,
        pair_id: uuid.UUID,
        user_id: uuid.UUID,
        conn_handle: object,
        queue: asyncio.Queue[LiveEvent],
    ) -> None:
        self.pair_id = pair_id
        self.user_id = user_id
        self.conn_handle = conn_handle
        self.queue = queue

    def __hash__(self) -> int:
        return id(self)

    def __eq__(self, other: object) -> bool:
        return self is other


# ---------------------------------------------------------------------------
# Internal session state
# ---------------------------------------------------------------------------


@dataclass
class _PairSession:
    """Bus state for one ``pair_id``.

    * ``task`` — the background asyncio.Task running
      :meth:`LiveBus._pair_loop` for this pair.
    * ``subscribers`` — ref-count set; the task is cancelled in
      :meth:`LiveBus.unsubscribe` when the last subscriber leaves.
    * ``last_account_snapshot`` / ``last_positions_snapshot`` — the
      most recently broadcast payloads, used to suppress no-op events
      on the 5 s tick.
    * ``mcp_available`` — last broadcast availability state, so
      transitions (DOWN → UP, UP → DOWN) emit exactly one event.
    """

    pair_id: uuid.UUID
    task: asyncio.Task[None] | None = None
    subscribers: set[Subscriber] = field(default_factory=set)
    last_account_snapshot: dict[str, Any] | None = None
    last_positions_snapshot: dict[str, Any] | None = None
    mcp_available: bool = True


# ---------------------------------------------------------------------------
# LiveBus
# ---------------------------------------------------------------------------


class LiveBus:
    """Per-process realtime fanout for the Operativa surface.

    Construct ONCE at startup and attach to ``app.state.live_bus``.
    The constructor is cheap (no I/O, no task creation) — the first
    background task only starts when :meth:`subscribe` is called.

    Parameters
    ----------
    session_factory
        Returns a fresh :class:`AsyncSession` per call. The bus uses
        short-lived sessions inside the polling loop — never holds a
        session across awaits beyond one DB round-trip.
    mcp_client_factory
        Callable that returns an :class:`MCPClient` for a pair. The
        default is :func:`aether_api.mcp_client.client.get_mcp_client`;
        tests inject a fake to assert wire behaviour without TCP.
    positions_poll_seconds, reconcile_poll_seconds, heartbeat_seconds
        Cadence knobs — defaults match the spec. Tests pass shorter
        intervals so the lifecycle assertions don't time out.
    """

    def __init__(
        self,
        session_factory: SessionFactory,
        mcp_client_factory: MCPClientFactory | None = None,
        *,
        positions_poll_seconds: float = DEFAULT_POSITIONS_POLL_SECONDS,
        reconcile_poll_seconds: float = DEFAULT_RECONCILE_POLL_SECONDS,
        heartbeat_seconds: float = DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
    ) -> None:
        self._session_factory = session_factory
        self._mcp_client_factory: MCPClientFactory = (
            mcp_client_factory if mcp_client_factory is not None else get_mcp_client
        )
        self._positions_poll = positions_poll_seconds
        self._reconcile_poll = reconcile_poll_seconds
        self._heartbeat = heartbeat_seconds

        self._sessions: dict[uuid.UUID, _PairSession] = {}
        # One lock guards the subscriber dictionary mutation surface.
        # Polling work itself runs without holding this lock.
        self._mutex: asyncio.Lock = asyncio.Lock()
        self._shutdown_called: bool = False

    # ------------------------------------------------------------------
    # Subscriber management
    # ------------------------------------------------------------------

    async def subscribe(
        self,
        *,
        pair_id: uuid.UUID,
        user_id: uuid.UUID,
        conn_handle: object,
    ) -> Subscriber:
        """Register a new subscriber for ``pair_id``.

        Returns the :class:`Subscriber` handle. If this is the FIRST
        subscriber for the pair, the background polling task is
        started here. The caller is responsible for tenant gating
        BEFORE calling this method — the bus does not re-verify
        ownership.
        """
        queue: asyncio.Queue[LiveEvent] = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_MAXSIZE)
        subscriber = Subscriber(
            pair_id=pair_id,
            user_id=user_id,
            conn_handle=conn_handle,
            queue=queue,
        )
        async with self._mutex:
            session = self._sessions.get(pair_id)
            if session is None:
                session = _PairSession(pair_id=pair_id)
                self._sessions[pair_id] = session
            session.subscribers.add(subscriber)
            if session.task is None or session.task.done():
                # First subscriber for this pair → start the loop.
                session.task = asyncio.create_task(
                    self._pair_loop(pair_id),
                    name=f"live-bus-pair-{pair_id}",
                )
            subscriber_count = len(session.subscribers)
        # Update the Prometheus gauge OUTSIDE the asyncio lock — the
        # collector is process-global and synchronous; holding the
        # mutex over it serialises subscribes pointlessly.
        set_ws_subscribers(pair_id, subscriber_count)
        return subscriber

    async def unsubscribe(self, subscriber: Subscriber) -> None:
        """Remove ``subscriber`` from the bus.

        Cancels the per-pair polling task when the last subscriber
        for that pair disconnects. The cancellation is awaited
        with :func:`contextlib.suppress` so a clean teardown never
        leaks ``CancelledError`` into the caller.
        """
        task_to_cancel: asyncio.Task[None] | None = None
        remaining: int = 0
        async with self._mutex:
            session = self._sessions.get(subscriber.pair_id)
            if session is None:
                return
            session.subscribers.discard(subscriber)
            remaining = len(session.subscribers)
            if not session.subscribers:
                task_to_cancel = session.task
                session.task = None
                # Drop the session record so a fresh subscriber
                # starts a fresh session with empty snapshots.
                self._sessions.pop(subscriber.pair_id, None)

        # Reflect the new subscriber count (possibly 0) into the gauge.
        set_ws_subscribers(subscriber.pair_id, remaining)

        if task_to_cancel is not None and not task_to_cancel.done():
            task_to_cancel.cancel()
            try:
                await task_to_cancel
            except asyncio.CancelledError:
                # Expected — we just cancelled it.
                pass
            except Exception:  # noqa: BLE001 — teardown is best-effort.
                logger.exception(
                    "aether.live_bus.task_teardown_raised",
                    extra={"pair_id": str(subscriber.pair_id)},
                )

    def subscriber_count(self, pair_id: uuid.UUID) -> int:
        """Return the live subscriber count for ``pair_id`` (snapshot)."""
        session = self._sessions.get(pair_id)
        return 0 if session is None else len(session.subscribers)

    def has_task(self, pair_id: uuid.UUID) -> bool:
        """Return True iff a polling task is currently registered.

        Useful in tests that assert lifecycle ("started on first
        subscribe / cancelled on last unsubscribe").
        """
        session = self._sessions.get(pair_id)
        return session is not None and session.task is not None and not session.task.done()

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    async def shutdown(self) -> None:
        """Cancel every running task. Called from the FastAPI lifespan."""
        self._shutdown_called = True
        tasks: list[asyncio.Task[None]] = []
        async with self._mutex:
            for session in self._sessions.values():
                if session.task is not None and not session.task.done():
                    session.task.cancel()
                    tasks.append(session.task)
            self._sessions.clear()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001 — teardown is best-effort.
                logger.exception("aether.live_bus.shutdown_task_raised")

    # ------------------------------------------------------------------
    # Broadcast helper
    # ------------------------------------------------------------------

    def _broadcast(self, pair_id: uuid.UUID, event: LiveEvent) -> None:
        """Push ``event`` to every subscriber's queue. Drop-oldest on full."""
        session = self._sessions.get(pair_id)
        if session is None:
            return
        for sub in tuple(session.subscribers):
            # Drop-OLDEST policy: if the queue is full, remove the
            # head (the stalest event) and push the new one. A slow
            # consumer therefore always sees the LATEST snapshots
            # rather than a historical backlog.
            if sub.queue.full():
                with contextlib.suppress(asyncio.QueueEmpty):  # pragma: no cover — race
                    sub.queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):  # pragma: no cover — race
                sub.queue.put_nowait(event)

    # ------------------------------------------------------------------
    # Per-project polling loop
    # ------------------------------------------------------------------

    async def _pair_loop(self, pair_id: uuid.UUID) -> None:
        """Background task body — runs while subscribers exist for ``pair_id``.

        Cadences (in one cooperative loop):

        * Every ``positions_poll`` seconds: ``get_account`` +
          ``get_positions`` → diff vs last broadcast → emit on change.
        * Every ``reconcile_poll`` seconds: ``get_history`` for the
          last 5 minutes → reconcile rows.
        * Every ``heartbeat`` seconds: send a ``ping`` event so a
          dead-TCP subscriber can be cleaned up by the WS handler
          (which expects to see traffic).
        """
        pair = await self._load_pair_or_none(pair_id)
        if pair is None:
            # The pair was deleted between subscribe + loop start.
            # Drop the session quietly.
            logger.info(
                "aether.live_bus.pair_missing",
                extra={"pair_id": str(pair_id)},
            )
            return

        client = self._mcp_client_factory(pair)
        last_reconcile = datetime.now(tz=UTC) - timedelta(seconds=self._reconcile_poll)
        last_heartbeat = datetime.now(tz=UTC) - timedelta(seconds=self._heartbeat)

        try:
            while True:
                now = datetime.now(tz=UTC)
                # --- 5s positions/account tick -----------------------
                await self._tick_account_and_positions(pair_id, client)

                # --- 30s reconcile tick ------------------------------
                if (now - last_reconcile).total_seconds() >= self._reconcile_poll:
                    await self._tick_reconcile(pair_id, pair, client)
                    last_reconcile = datetime.now(tz=UTC)

                # --- heartbeat ---------------------------------------
                if (now - last_heartbeat).total_seconds() >= self._heartbeat:
                    self._broadcast(pair_id, LiveEvent(type="ping", payload={}))
                    last_heartbeat = datetime.now(tz=UTC)

                await asyncio.sleep(self._positions_poll)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — defence in depth
            logger.exception(
                "aether.live_bus.pair_loop_unhandled",
                extra={"pair_id": str(pair_id)},
            )

    async def _load_pair_or_none(self, pair_id: uuid.UUID) -> Pair | None:
        """Fetch the :class:`Pair` row by id, no tenant filter.

        The bus has already been authorised by the WS router; the
        polling task itself needs the row only to construct the
        :class:`MCPClient` endpoint.
        """
        async with self._session_factory() as session:
            pair = await session.get(Pair, pair_id)
            return pair

    # ------------------------------------------------------------------
    # Tick: account + positions
    # ------------------------------------------------------------------

    async def _tick_account_and_positions(
        self, pair_id: uuid.UUID, client: MCPClient
    ) -> None:
        """One 5s tick — pull account + positions; diff & broadcast."""
        session = self._sessions.get(pair_id)
        if session is None:
            return

        try:
            account = await client.get_account()
            positions = await client.get_positions()
        except MCPUnreachable as exc:
            # Spec mapping: MCPUnreachable → mcp_status=false with
            # error_code in {TIMEOUT, UNREACHABLE, UNAUTHENTICATED}.
            # The client today raises this for connect/read errors,
            # non-2xx, malformed body — we widen-or-narrow into the
            # spec's stable shape.
            await self._handle_mcp_failure(
                pair_id, error_code=_classify_mcp_error(exc), exc=exc
            )
            return
        except MCPClientError as exc:
            await self._handle_mcp_failure(
                pair_id, error_code=_classify_mcp_error(exc), exc=exc
            )
            return

        # Recovery edge — if last tick was DOWN and this one succeeded,
        # emit an "available=true" status event.
        if not session.mcp_available:
            session.mcp_available = True
            set_mcp_status(pair_id, available=True)
            self._broadcast(
                pair_id,
                LiveEvent(
                    type="mcp_status",
                    payload={"available": True},
                ),
            )

        if account != session.last_account_snapshot:
            session.last_account_snapshot = account
            self._broadcast(
                pair_id,
                LiveEvent(type="account_snapshot", payload=dict(account)),
            )
        if positions != session.last_positions_snapshot:
            session.last_positions_snapshot = positions
            self._broadcast(
                pair_id,
                LiveEvent(type="position_snapshot", payload=dict(positions)),
            )

    async def _handle_mcp_failure(
        self,
        pair_id: uuid.UUID,
        *,
        error_code: str,
        exc: MCPClientError,
    ) -> None:
        """Emit one mcp_status=false event on transition to DOWN."""
        session = self._sessions.get(pair_id)
        if session is None:
            return
        if session.mcp_available:
            session.mcp_available = False
            set_mcp_status(pair_id, available=False)
            self._broadcast(
                pair_id,
                LiveEvent(
                    type="mcp_status",
                    payload={
                        "available": False,
                        "error_code": error_code,
                        "retry_in": int(self._reconcile_poll),
                    },
                ),
            )
        logger.warning(
            "aether.live_bus.mcp_failure",
            extra={
                "pair_id": str(pair_id),
                "error_code": error_code,
                "message": exc.message,
            },
        )

    # ------------------------------------------------------------------
    # Tick: reconcile history
    # ------------------------------------------------------------------

    async def _tick_reconcile(
        self,
        pair_id: uuid.UUID,
        pair: Pair,
        client: MCPClient,
    ) -> None:
        """Pull MCP history (last 5 min) and reconcile into ``orders``."""
        now = datetime.now(tz=UTC)
        date_from = now - timedelta(minutes=5)
        try:
            history = await client.get_history(date_from=date_from, date_to=now)
        except MCPClientError:
            # The 5s tick already handles mcp_status events; here we
            # silently skip — next reconcile_poll will try again.
            return

        deals = _extract_deals(history)
        if not deals:
            return

        async with self._session_factory() as session:
            await reconcile_history(
                session=session,
                user_id=pair.user_id,
                pair_id=pair_id,
                deals=deals,
            )
            await session.commit()


# ---------------------------------------------------------------------------
# Reconciler (module-level — testable without spinning the bus)
# ---------------------------------------------------------------------------


def _extract_deals(history: dict[str, Any]) -> list[dict[str, Any]]:
    """Pull the deals/list out of an MCP ``get_history`` response.

    The MCP server may shape the response as ``{"deals": [...]}``,
    ``{"history": [...]}``, or simply ``{"orders": [...]}``. We accept
    any of those keys and return the raw list of dicts. Non-list /
    missing → empty.
    """
    for key in ("deals", "history", "orders", "trades"):
        value = history.get(key)
        if isinstance(value, list):
            return [d for d in value if isinstance(d, dict)]
    return []


def _coerce_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (ValueError, ArithmeticError, TypeError):
        return None


def _coerce_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


#: Fields on :class:`Order` that the reconciler must NEVER overwrite
#: when the row already exists. These are Worker-authored P&L /
#: pricing fields; the reconciler instead writes its broker view
#: into ``meta_data.broker_*`` for the operator to compare. Exported
#: as a constant so tests can assert on the exact set (the
#: no-overwrite invariant is critical for the project-operativa change).
WORKER_AUTHORED_FIELDS: frozenset[str] = frozenset(
    {
        "profit_gross",
        "profit_net",
        "commission",
        "swap",
        "open_price",
        "close_price",
    }
)


async def reconcile_history(
    *,
    session: AsyncSession,
    user_id: uuid.UUID,
    pair_id: uuid.UUID,
    deals: list[dict[str, Any]],
) -> int:
    """Reconcile broker-reported deals against the local ``orders`` table.

    For each deal:

    * If a row with the matching ``mt5_ticket`` already exists, write
      the broker's authoritative numbers into
      ``meta_data.broker_*`` and a derived ``divergence_pct`` vs the
      Worker's ``profit_net`` — but NEVER overwrite the Worker-authored
      columns (``profit_*``, ``commission``, ``swap``, ``open_price``,
      ``close_price``).
    * If no row exists, create one with ``meta_data.reconciler_authored
      = true`` so the operator surface can flag it as
      reconciler-originated (rather than from a live Worker write).

    Returns the count of rows touched. The caller is responsible for
    ``session.commit()``.
    """
    repo = OrderRepository(session)
    touched = 0

    for deal in deals:
        ticket = deal.get("ticket") or deal.get("deal_id") or deal.get("id")
        if ticket is None:
            continue
        try:
            ticket_int = int(ticket)
        except (TypeError, ValueError):
            continue
        ticket_str = str(ticket_int)

        broker_profit_gross = _coerce_decimal(
            deal.get("profit_gross", deal.get("profit"))
        )
        broker_commission = _coerce_decimal(deal.get("commission"))
        broker_swap = _coerce_decimal(deal.get("swap"))
        broker_profit_net: Decimal | None = None
        if broker_profit_gross is not None:
            broker_profit_net = broker_profit_gross
            if broker_commission is not None:
                broker_profit_net += broker_commission
            if broker_swap is not None:
                broker_profit_net += broker_swap

        # Look up an existing row by ticket BEFORE we call
        # ``upsert_by_ticket`` so we can detect "already-Worker-written"
        # rows and route the broker view into ``meta_data`` exclusively.
        stmt = select(Order).where(
            Order.pair_id == pair_id,
            Order.mt5_ticket == ticket_int,
        )
        existing = (await session.execute(stmt)).scalar_one_or_none()

        broker_meta: dict[str, Any] = {
            "broker_profit_gross": _decimal_to_str(broker_profit_gross),
            "broker_profit_net": _decimal_to_str(broker_profit_net),
            "broker_commission": _decimal_to_str(broker_commission),
            "broker_swap": _decimal_to_str(broker_swap),
            "reconciled_at": datetime.now(tz=UTC).isoformat(),
        }

        if existing is not None:
            # NEVER overwrite Worker-authored fields. Merge broker
            # view into meta_data only.
            divergence_pct: float | None = None
            if (
                broker_profit_net is not None
                and existing.profit_net is not None
                and existing.profit_net != 0
            ):
                try:
                    divergence_pct = float(
                        (broker_profit_net - existing.profit_net)
                        / existing.profit_net
                        * Decimal("100")
                    )
                except (ArithmeticError, ValueError):
                    divergence_pct = None
            broker_meta["divergence_pct"] = divergence_pct

            current_meta = dict(existing.meta_data or {})
            current_meta.update(broker_meta)
            await repo.upsert_by_ticket(
                user_id=user_id,
                project_id=pair_id,
                ticket=ticket_str,
                fields={"meta_data": current_meta},
            )
            touched += 1
        else:
            # Reconciler-discovered row. Fields here include the
            # broker's pricing/timing so the operator gets SOMETHING
            # visible immediately, but Worker-authored columns stay
            # NULL — the next Worker write will fill them, and that
            # write must not be clobbered by the next reconcile.
            broker_meta["reconciler_authored"] = True

            volume = _coerce_decimal(deal.get("volume", "0")) or Decimal("0")
            sl_dec = _coerce_decimal(deal.get("sl")) or Decimal("0")
            # SL is NOT NULL at the DB layer — reconciler-discovered
            # rows that lack one cannot be persisted. Skip rather than
            # raise; the next reconcile pass will retry.
            if sl_dec <= 0:
                continue

            open_time = _coerce_datetime(deal.get("open_time", deal.get("time")))
            close_time = _coerce_datetime(deal.get("close_time"))
            open_price = _coerce_decimal(deal.get("open_price"))
            close_price = _coerce_decimal(deal.get("close_price"))

            await repo.upsert_by_ticket(
                user_id=user_id,
                project_id=pair_id,
                ticket=ticket_str,
                fields={
                    "symbol": str(deal.get("symbol") or "UNKNOWN").upper(),
                    "side": str(deal.get("side") or "buy"),
                    "volume": volume,
                    "sl": sl_dec,
                    "tp": _coerce_decimal(deal.get("tp")),
                    "status": str(deal.get("status") or "closed"),
                    "open_time": open_time,
                    "close_time": close_time,
                    "open_price": open_price,
                    "close_price": close_price,
                    "meta_data": broker_meta,
                },
            )
            touched += 1

    return touched


def _decimal_to_str(value: Decimal | None) -> str | None:
    """Decimal → string for JSONB storage (preserves precision)."""
    return None if value is None else str(value)


def _classify_mcp_error(exc: MCPClientError) -> str:
    """Map an MCP exception to the spec's stable error-code token.

    Per the operativa-live spec the WS event carries one of
    ``TIMEOUT``, ``UNREACHABLE``, or ``UNAUTHENTICATED``. The client
    today encodes most failures as :class:`MCPUnreachable` with a
    descriptive ``message``; we keep the classification simple:

    * ``MCPUnreachable`` + message contains "timeout" → ``TIMEOUT``.
    * Any other :class:`MCPUnreachable` → ``UNREACHABLE``.
    * Other :class:`MCPClientError` codes carrying an auth hint →
      ``UNAUTHENTICATED``.
    """
    message = (exc.message or "").lower()
    if isinstance(exc, MCPUnreachable):
        if "timeout" in message:
            return "TIMEOUT"
        return "UNREACHABLE"
    if "auth" in message or "unauthorized" in message or "unauthenticated" in message:
        return "UNAUTHENTICATED"
    return "UNREACHABLE"


