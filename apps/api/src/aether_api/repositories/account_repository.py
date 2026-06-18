"""``accounts`` data access — tenant-scoped.

The grouping layer of the accounts-pairs hierarchy ``Exchange → Account
→ Pair → Agents``. Owns the broker-credential block lifted off the old
``projects`` table. This module is the only place that issues SQL against
the ``accounts`` table; every read / write goes through the ``user_id``
tenant predicate via :meth:`BaseRepository._for_user`.

Cross-exchange ownership is enforced application-side: :meth:`create`
verifies the target ``exchange_id`` belongs to the same tenant before
inserting, so an account can never dangle off another tenant's exchange.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select

from aether_api.models.account import Account
from aether_api.models.exchange import Exchange
from aether_api.repositories.base import BaseRepository


class AccountRepository(BaseRepository):
    model = Account

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    async def _user_owns_exchange(
        self, user_id: uuid.UUID, exchange_id: uuid.UUID
    ) -> bool:
        stmt = select(Exchange.id).where(
            Exchange.id == exchange_id, Exchange.user_id == user_id
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    async def list_for_user(
        self,
        user_id: uuid.UUID,
        *,
        exchange_id: uuid.UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Account]:
        """Return up to ``limit`` accounts owned by ``user_id``, newest first.

        Pass ``exchange_id`` to narrow to a single exchange's accounts.
        """
        stmt = self._for_user(select(Account), user_id)
        if exchange_id is not None:
            stmt = stmt.where(Account.exchange_id == exchange_id)
        stmt = (
            stmt.order_by(Account.created_at.desc(), Account.id.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_for_user(
        self, user_id: uuid.UUID, *, exchange_id: uuid.UUID | None = None
    ) -> int:
        """Total account count for the tenant (optionally per exchange)."""
        stmt = self._for_user(select(func.count(Account.id)), user_id)
        if exchange_id is not None:
            stmt = stmt.where(Account.exchange_id == exchange_id)
        result = await self.session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def get_for_user(
        self, user_id: uuid.UUID, account_id: uuid.UUID
    ) -> Account | None:
        """Return the account IFF owned by ``user_id``, else ``None`` (404)."""
        stmt = self._for_user(
            select(Account).where(Account.id == account_id), user_id
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------
    async def create(
        self, user_id: uuid.UUID, *, exchange_id: uuid.UUID, **fields: Any
    ) -> Account:
        """Insert a new account for ``user_id`` under ``exchange_id``.

        Refuses cross-tenant parenting: if ``exchange_id`` is not owned by
        ``user_id`` we raise ``PermissionError`` rather than persisting an
        account that dangles off a foreign exchange.
        """
        if "user_id" in fields:
            raise ValueError(
                "user_id must not be passed in fields; use the explicit argument"
            )
        if not await self._user_owns_exchange(user_id, exchange_id):
            raise PermissionError(
                f"user {user_id} does not own exchange {exchange_id}"
            )
        account = Account(user_id=user_id, exchange_id=exchange_id, **fields)
        self.session.add(account)
        await self.session.flush()
        await self.session.refresh(account)
        return account

    async def update_fields(
        self,
        user_id: uuid.UUID,
        account_id: uuid.UUID,
        fields: dict[str, Any],
    ) -> Account | None:
        """Partial update of an owned account. Returns ``None`` if not owned.

        Reparenting (changing ``exchange_id``) verifies the new exchange
        is owned by the same tenant.
        """
        if "user_id" in fields:
            raise ValueError("update_fields must not touch user_id")
        if not fields:
            return await self.get_for_user(user_id, account_id)

        if "exchange_id" in fields and not await self._user_owns_exchange(
            user_id, fields["exchange_id"]
        ):
            raise PermissionError(
                f"user {user_id} does not own exchange {fields['exchange_id']}"
            )

        existing = await self.get_for_user(user_id, account_id)
        if existing is None:
            return None
        for key, value in fields.items():
            setattr(existing, key, value)
        await self.session.flush()
        await self.session.refresh(existing)
        return existing

    async def delete(self, user_id: uuid.UUID, account_id: uuid.UUID) -> bool:
        """Hard-delete the account. Returns True iff a row was removed.

        The DB ``ON DELETE RESTRICT`` from ``pairs`` blocks deletion while
        any pair still references it; the router maps the resulting
        ``IntegrityError`` to a 409.
        """
        stmt = (
            sql_delete(Account)
            .where(Account.id == account_id)
            .where(Account.user_id == user_id)
            .returning(Account.id)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None
