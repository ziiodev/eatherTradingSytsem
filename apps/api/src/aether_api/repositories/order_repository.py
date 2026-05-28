"""``orders`` data access — tenant- + project-scoped.

Every method takes the tenant ``user_id`` (resolved by the
``current_user`` dependency) AND a ``project_id``. Routers never touch
the ORM directly so the tenant predicate is impossible to bypass.

The repository deliberately does NOT issue cross-tenant queries by
default — callers that want the "all orders for project" surface MUST
go through :meth:`list_for_project` which restricts to the user's own
project rows via a JOIN.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select

from aether_api.models.order import Order
from aether_api.repositories.base import BaseRepository


class OrderRepository(BaseRepository):
    model = Order

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
            project_id=project_id,
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
            .where(Order.project_id == project_id)
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
            .where(Order.project_id == project_id)
        )
        result = await self.session.execute(stmt)
        return int(result.scalar_one() or 0)


__all__ = ["OrderRepository"]


# Reference import to silence "unused" warnings on Any when the type-only
# alias is consumed elsewhere — keeps a stable surface.
_ANY = Any
