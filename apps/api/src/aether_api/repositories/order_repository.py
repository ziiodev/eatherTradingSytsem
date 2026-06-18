"""``orders`` data access — tenant- + project-scoped.

Every method takes the tenant ``user_id`` (resolved by the
``current_user`` dependency) AND a ``project_id``. Routers never touch
the ORM directly so the tenant predicate is impossible to bypass.

The repository deliberately does NOT issue cross-tenant queries by
default — callers that want the "all orders for project" surface MUST
go through :meth:`list_for_project` (or the richer
:meth:`list_filtered`) which restricts to the user's own project rows
via a JOIN through ``projects.user_id``.

Operativa extension (Phase 1 of the ``project-operativa`` change):

* :class:`AccountSummary` — DB-side aggregate that feeds the Operativa
  account-summary panel. Realtime MCP fields (equity / balance / free
  margin) are layered in by the WS / REST router in Phase 4; the DB
  side of v1 just sums ``profit_net`` of closed trades.
* :class:`MetricsResult` — paired with the new
  :func:`aether_api.services.orders_metrics` primitives. JSON-safe
  shape so the router can return it verbatim.
* :meth:`list_filtered` — driver of the Operativa filtered-list view.
  Every filter is optional and ``user_id``-scoped via a JOIN through
  ``projects``.
* :meth:`aggregate_metrics` — same JOIN-scope, calling the pure
  metric primitives.
* :meth:`upsert_by_ticket` — write path the Worker / reconciler use
  to land broker-reported fills. The tenant gate is enforced via
  ``projects.user_id`` BEFORE any ``INSERT`` / ``UPDATE`` runs.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select

from aether_api.models.order import Order
from aether_api.models.pair import Pair
from aether_api.repositories.base import BaseRepository
from aether_api.services import orders_metrics


# ---------------------------------------------------------------------------
# Result DTOs (plain dataclasses — JSON-friendly, no ORM coupling).
# ---------------------------------------------------------------------------
@dataclass(slots=True, frozen=True)
class AccountSummary:
    """DB-side aggregate of the Operativa account-summary surface.

    Only the realised-P&L slice lives on the DB. Live equity / balance /
    free margin come from MCP via :mod:`aether_api.realtime.live_bus`
    (Phase 4 of ``project-operativa``); for now those fields are ``None``
    so the wire shape is stable from v1.
    """

    project_id: uuid.UUID
    closed_pnl: Decimal
    open_positions: int
    realtime_equity: Decimal | None = None
    realtime_balance: Decimal | None = None
    realtime_margin: Decimal | None = None


@dataclass(slots=True, frozen=True)
class MetricsResult:
    """Aggregated metrics over a tenant-scoped slice of ``orders``.

    ``profit_factor`` is ``float | str`` — the string ``"Infinity"``
    leaks through when there are wins but zero losses (the wire
    contract is a stable JSON-safe token, not ``NaN`` / ``null``).
    ``avg_rr`` is ``float | None`` — ``None`` means no trade in the
    slice had a valid R denominator.
    """

    trades_total: int
    win_rate: float
    profit_factor: float | str
    avg_rr: float | None
    total_pnl: Decimal


class OrderRepository(BaseRepository):
    model = Order

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    async def _assert_user_owns_project(self, user_id: uuid.UUID, project_id: uuid.UUID) -> bool:
        """Return ``True`` iff ``project_id`` is owned by ``user_id``.

        The repo deliberately returns a bool rather than raising — the
        Operativa read paths quietly downgrade cross-tenant access to
        empty results / ``None``, which the routers translate to 404
        without leaking the existence of the resource.
        """
        stmt = select(Pair.id).where(Pair.id == project_id, Pair.user_id == user_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------
    async def create(
        self,
        *,
        project_id: uuid.UUID,
        user_id: uuid.UUID,
        agent_id: uuid.UUID | None,
        symbol: str,
        side: str,
        volume: Decimal,
        sl: Decimal,
        tp: Decimal | None,
        status: str,
        comment: str | None = None,
        magic: int | None = None,
    ) -> Order:
        """Insert a new order row. Caller MUST commit."""
        order = Order(
            pair_id=project_id,
            user_id=user_id,
            agent_id=agent_id,
            symbol=symbol,
            side=side,
            volume=volume,
            sl=sl,
            tp=tp,
            status=status,
            comment=comment,
            magic=magic,
        )
        self.session.add(order)
        await self.session.flush()
        await self.session.refresh(order)
        return order

    async def mark_filled(
        self,
        order_id: uuid.UUID,
        *,
        mt5_ticket: int,
        filled_at: datetime | None = None,
    ) -> Order | None:
        """Phase-2 update: mark the order as filled with the broker ticket."""
        order = await self.session.get(Order, order_id)
        if order is None:
            return None
        order.status = "filled"
        order.mt5_ticket = mt5_ticket
        order.filled_at = filled_at or datetime.now(tz=UTC).replace(tzinfo=None)
        await self.session.flush()
        await self.session.refresh(order)
        return order

    async def mark_failed(self, order_id: uuid.UUID) -> Order | None:
        order = await self.session.get(Order, order_id)
        if order is None:
            return None
        order.status = "failed"
        await self.session.flush()
        await self.session.refresh(order)
        return order

    async def upsert_by_ticket(
        self,
        *,
        user_id: uuid.UUID,
        project_id: uuid.UUID,
        ticket: str,
        fields: dict[str, Any],
    ) -> Order | None:
        """INSERT-or-UPDATE the order row keyed by ``mt5_ticket``.

        The Worker / reconciler use this to land broker-reported fills
        where the broker is authoritative for the ticket. ``user_id``
        is asserted against ``projects.user_id`` BEFORE any write —
        cross-tenant attempts return ``None`` rather than raising, so
        the caller can translate to 404 without leaking existence.

        ``ticket`` is a string at the API boundary (MT5 deal ids can
        exceed 32-bit ranges on some brokers) and is cast to int for
        the ORM column. ``fields`` may carry any column on :class:`Order`
        except ``id`` / ``project_id`` / ``user_id`` / ``mt5_ticket``
        (those are derived from the explicit args).
        """
        if not await self._assert_user_owns_project(user_id, project_id):
            return None

        try:
            ticket_int = int(ticket)
        except (TypeError, ValueError):
            return None

        stmt = select(Order).where(Order.pair_id == project_id, Order.mt5_ticket == ticket_int)
        result = await self.session.execute(stmt)
        order = result.scalar_one_or_none()

        # Filter out fields that the caller MUST NOT set via upsert.
        # Tenant + identity columns are owned by the upsert call args.
        protected = {"id", "pair_id", "project_id", "user_id", "mt5_ticket"}
        clean_fields = {k: v for k, v in fields.items() if k not in protected}

        if order is None:
            # New row. Apply args + provided fields. Defaults match the
            # Worker reconciler's contract: status='filled' unless the
            # caller overrides; sl is required at the DB layer.
            defaults: dict[str, Any] = {
                "pair_id": project_id,
                "user_id": user_id,
                "mt5_ticket": ticket_int,
                "status": clean_fields.pop("status", "filled"),
            }
            defaults.update(clean_fields)
            order = Order(**defaults)
            self.session.add(order)
        else:
            for key, value in clean_fields.items():
                setattr(order, key, value)

        await self.session.flush()
        await self.session.refresh(order)
        return order

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    async def list_for_project(
        self,
        *,
        user_id: uuid.UUID,
        project_id: uuid.UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Order]:
        stmt = (
            select(Order)
            .where(Order.user_id == user_id)
            .where(Order.pair_id == project_id)
            .order_by(Order.created_at.desc(), Order.id.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_for_project(
        self,
        *,
        user_id: uuid.UUID,
        project_id: uuid.UUID,
    ) -> int:
        stmt = (
            select(func.count(Order.id))
            .where(Order.user_id == user_id)
            .where(Order.pair_id == project_id)
        )
        result = await self.session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def list_filtered(
        self,
        *,
        user_id: uuid.UUID,
        project_id: uuid.UUID,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        symbol: str | None = None,
        side: str | None = None,
        result: str | None = None,
        magic: int | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Order], int]:
        """Tenant-scoped filtered list of orders for ``project_id``.

        Filters are AND-composed; ``None`` means "do not narrow on
        this dimension". Tenancy is enforced by a JOIN to
        ``projects.user_id`` — cross-tenant attempts return
        ``([], 0)`` without leaking which IDs exist.

        ``result`` is one of ``win`` / ``loss`` / ``flat`` and narrows
        on ``profit_net`` sign. ``from_date`` / ``to_date`` filter on
        ``open_time`` (the canonical "when did this trade happen"
        axis); rows with ``open_time IS NULL`` are excluded when
        either bound is provided.

        Ordering: ``open_time DESC`` first (canonical history view),
        ``created_at DESC`` as the tie-breaker, ``id DESC`` as the
        final stable tie-breaker.
        """
        # Tenant gate: empty result if the user doesn't own the project.
        if not await self._assert_user_owns_project(user_id, project_id):
            return ([], 0)

        # Build the WHERE conjunction once, then re-use for both the
        # paged scalar select and the total-count select.
        conditions = [
            Order.pair_id == project_id,
            Order.user_id == user_id,
        ]
        if symbol is not None:
            conditions.append(Order.symbol == symbol)
        if side is not None:
            conditions.append(Order.side == side)
        if status is not None:
            conditions.append(Order.status == status)
        if magic is not None:
            conditions.append(Order.magic == magic)
        if from_date is not None:
            conditions.append(Order.open_time >= from_date)
        if to_date is not None:
            conditions.append(Order.open_time <= to_date)
        if result == "win":
            conditions.append(Order.profit_net > 0)
        elif result == "loss":
            conditions.append(Order.profit_net < 0)
        elif result == "flat":
            conditions.append(Order.profit_net == 0)

        stmt = (
            select(Order)
            .where(*conditions)
            .order_by(
                Order.open_time.desc().nulls_last(),
                Order.created_at.desc(),
                Order.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
        rows_result = await self.session.execute(stmt)
        rows = list(rows_result.scalars().all())

        count_stmt = select(func.count(Order.id)).where(*conditions)
        count_result = await self.session.execute(count_stmt)
        total = int(count_result.scalar_one() or 0)

        return (rows, total)

    async def aggregate_metrics(
        self,
        *,
        user_id: uuid.UUID,
        project_id: uuid.UUID,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
    ) -> MetricsResult:
        """Aggregate Operativa metrics over the tenant-scoped slice.

        Cross-tenant attempts return a zeroed :class:`MetricsResult`
        (trades_total = 0, etc.) so the router can return ``404`` or
        an empty card without leaking existence. The metric primitives
        live in :mod:`aether_api.services.orders_metrics` — this method
        is just the SQL + glue.
        """
        if not await self._assert_user_owns_project(user_id, project_id):
            return MetricsResult(
                trades_total=0,
                win_rate=0.0,
                profit_factor=0.0,
                avg_rr=None,
                total_pnl=Decimal("0"),
            )

        # We only aggregate over CLOSED trades — by definition they're
        # the only ones with a final ``profit_net``. The pure metric
        # primitives already skip ``profit_net is None`` defensively
        # but narrowing here makes the SQL cheaper.
        conditions = [
            Order.pair_id == project_id,
            Order.user_id == user_id,
            Order.status == "closed",
        ]
        if from_date is not None:
            conditions.append(Order.open_time >= from_date)
        if to_date is not None:
            conditions.append(Order.open_time <= to_date)

        stmt = select(Order).where(*conditions)
        rows = list((await self.session.execute(stmt)).scalars().all())

        return MetricsResult(
            trades_total=sum(1 for r in rows if r.profit_net is not None),
            win_rate=orders_metrics.win_rate(rows),
            profit_factor=orders_metrics.profit_factor(rows),
            avg_rr=orders_metrics.avg_rr(rows),
            total_pnl=orders_metrics.total_pnl(rows),
        )

    async def account_summary_for_project(
        self,
        *,
        user_id: uuid.UUID,
        project_id: uuid.UUID,
    ) -> AccountSummary | None:
        """Tenant-scoped DB-side aggregate for the Operativa account panel.

        Returns ``None`` when the user does not own the project (the
        router translates to 404). Returns an :class:`AccountSummary`
        with the realtime fields set to ``None`` — the LiveBus
        decorates them with broker-reported equity / balance / margin
        in Phase 4 of ``project-operativa``.
        """
        if not await self._assert_user_owns_project(user_id, project_id):
            return None

        # Closed-trade realised P&L.
        closed_stmt = select(func.coalesce(func.sum(Order.profit_net), 0)).where(
            Order.pair_id == project_id,
            Order.user_id == user_id,
            Order.status == "closed",
        )
        closed_pnl = Decimal(str((await self.session.execute(closed_stmt)).scalar_one() or 0))

        # "Open" = a row whose lifecycle is filled but not yet closed.
        # ``filled`` is the canonical open state; ``approved_pending_send``
        # is not on the broker yet (no exposure) so it doesn't count.
        open_stmt = select(func.count(Order.id)).where(
            Order.pair_id == project_id,
            Order.user_id == user_id,
            Order.status == "filled",
        )
        open_positions = int((await self.session.execute(open_stmt)).scalar_one() or 0)

        return AccountSummary(
            project_id=project_id,
            closed_pnl=closed_pnl,
            open_positions=open_positions,
        )


__all__ = [
    "AccountSummary",
    "MetricsResult",
    "OrderRepository",
]
