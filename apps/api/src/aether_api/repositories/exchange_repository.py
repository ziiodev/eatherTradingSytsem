"""``exchanges`` data access — tenant-scoped.

The top of the accounts-pairs hierarchy ``Exchange → Account → Pair →
Agents``. This module is the only place that issues SQL against the
``exchanges`` table; every read / write goes through the ``user_id``
tenant predicate via :meth:`BaseRepository._for_user`.

``code`` is unique per tenant (``uq_exchanges_user_code``). The repo
exposes a :meth:`code_taken_for_user` probe so the router can return a
clean 409 instead of surfacing the raw ``IntegrityError``.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select

from aether_api.models.exchange import Exchange
from aether_api.repositories.base import BaseRepository


class ExchangeRepository(BaseRepository):
    model = Exchange

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    async def list_for_user(
        self,
        user_id: uuid.UUID,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Exchange]:
        """Return up to ``limit`` exchanges owned by ``user_id``, newest first."""
        stmt = (
            self._for_user(select(Exchange), user_id)
            .order_by(Exchange.created_at.desc(), Exchange.id.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_for_user(self, user_id: uuid.UUID) -> int:
        """Total exchange count for the tenant."""
        stmt = self._for_user(select(func.count(Exchange.id)), user_id)
        result = await self.session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def get_for_user(
        self, user_id: uuid.UUID, exchange_id: uuid.UUID
    ) -> Exchange | None:
        """Return the exchange IFF owned by ``user_id``, else ``None`` (404)."""
        stmt = self._for_user(
            select(Exchange).where(Exchange.id == exchange_id), user_id
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def code_taken_for_user(
        self,
        user_id: uuid.UUID,
        code: str,
        *,
        exclude_id: uuid.UUID | None = None,
    ) -> bool:
        """Return True iff the tenant already owns an exchange with this code.

        Backs the ``uq_exchanges_user_code`` constraint with a friendly
        pre-check. Pass ``exclude_id`` on PATCH so re-saving the same
        code on the same row isn't flagged.
        """
        stmt = self._for_user(
            select(Exchange.id).where(Exchange.code == code), user_id
        )
        if exclude_id is not None:
            stmt = stmt.where(Exchange.id != exclude_id)
        result = await self.session.execute(stmt.limit(1))
        return result.first() is not None

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------
    async def create(self, user_id: uuid.UUID, **fields: Any) -> Exchange:
        """Insert a new exchange for ``user_id``.

        Caller-supplied ``user_id`` in ``fields`` is rejected — the
        tenant is taken from the explicit argument, never the kwargs.
        """
        if "user_id" in fields:
            raise ValueError(
                "user_id must not be passed in fields; use the explicit argument"
            )
        exchange = Exchange(user_id=user_id, **fields)
        self.session.add(exchange)
        await self.session.flush()
        await self.session.refresh(exchange)
        return exchange

    async def update_fields(
        self,
        user_id: uuid.UUID,
        exchange_id: uuid.UUID,
        fields: dict[str, Any],
    ) -> Exchange | None:
        """Partial update of an owned exchange. Returns ``None`` if not owned."""
        if "user_id" in fields:
            raise ValueError("update_fields must not touch user_id")
        if not fields:
            return await self.get_for_user(user_id, exchange_id)

        existing = await self.get_for_user(user_id, exchange_id)
        if existing is None:
            return None
        for key, value in fields.items():
            setattr(existing, key, value)
        await self.session.flush()
        await self.session.refresh(existing)
        return existing

    async def delete(self, user_id: uuid.UUID, exchange_id: uuid.UUID) -> bool:
        """Hard-delete the exchange. Returns True iff a row was removed.

        The DB ``ON DELETE RESTRICT`` from ``accounts`` blocks deletion
        while any account still references it; the router maps the
        resulting ``IntegrityError`` to a 409.
        """
        stmt = (
            sql_delete(Exchange)
            .where(Exchange.id == exchange_id)
            .where(Exchange.user_id == user_id)
            .returning(Exchange.id)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None
