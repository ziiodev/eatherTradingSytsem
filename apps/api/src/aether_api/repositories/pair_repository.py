"""``pairs`` data access — tenant-scoped (renamed from ``ProjectRepository``).

This module is the *only* place that issues SQL against the ``pairs``
table. The router calls these methods and never touches the ORM directly,
which keeps the tenant predicate (`user_id = current_user.id`) impossible
to bypass by accident.

Method parameters use the ``pair_id`` name. (Some sibling repositories
still expose ``project_id=`` keyword parameters internally — those are
the historical contract and translate to the ``pair_id`` column.)

Pagination strategy:

* Offset-based (``limit``/``offset``) is the v1 contract — simple for the
  client, correct in the presence of inserts because we always order by
  ``created_at DESC, id DESC`` (id breaks ties).
* When the operator surface grows past a few hundred pairs per tenant
  we will migrate to a cursor (created_at, id) without changing the
  outward shape.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select, update
from sqlalchemy.orm import selectinload

from aether_api.models.pair import Pair
from aether_api.repositories.base import BaseRepository


class PairRepository(BaseRepository):
    model = Pair

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    async def list_for_user(
        self,
        user_id: uuid.UUID,
        *,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Pair]:
        """Return up to ``limit`` pairs owned by ``user_id``.

        Ordering: ``created_at DESC, id DESC``. The id tiebreak makes the
        pagination deterministic even if two rows share the same
        ``created_at`` (e.g. seed scripts).
        """
        stmt = self._for_user(select(Pair), user_id)
        if status is not None:
            stmt = stmt.where(Pair.status == status)
        stmt = (
            stmt.order_by(Pair.created_at.desc(), Pair.id.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_for_user(
        self, user_id: uuid.UUID, *, status: str | None = None
    ) -> int:
        """Total pair count for the tenant — drives the X-Total-Count header."""
        stmt = self._for_user(select(func.count(Pair.id)), user_id)
        if status is not None:
            stmt = stmt.where(Pair.status == status)
        result = await self.session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def get_for_user(
        self, user_id: uuid.UUID, pair_id: uuid.UUID
    ) -> Pair | None:
        """Return the pair IFF it belongs to ``user_id``, else ``None``.

        Callers MUST map ``None`` to HTTP 404 (NOT 403). 403 would
        confirm the pair exists; we never confirm existence to a
        non-owner.
        """
        stmt = self._for_user(select(Pair).where(Pair.id == pair_id), user_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_account(
        self,
        user_id: uuid.UUID,
        account_id: uuid.UUID,
        *,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Pair]:
        """Return the tenant's pairs under ``account_id`` (newest first)."""
        stmt = self._for_user(
            select(Pair).where(Pair.account_id == account_id), user_id
        )
        if status is not None:
            stmt = stmt.where(Pair.status == status)
        stmt = (
            stmt.order_by(Pair.created_at.desc(), Pair.id.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_for_account(
        self,
        user_id: uuid.UUID,
        account_id: uuid.UUID,
        *,
        status: str | None = None,
    ) -> int:
        """Total pair count for the tenant under ``account_id``."""
        stmt = self._for_user(
            select(func.count(Pair.id)).where(Pair.account_id == account_id),
            user_id,
        )
        if status is not None:
            stmt = stmt.where(Pair.status == status)
        result = await self.session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def get_for_user_with_account(
        self, user_id: uuid.UUID, pair_id: uuid.UUID
    ) -> Pair | None:
        """Return the pair (with its ``account`` eager-loaded) IFF owned.

        ``Pair.account`` is ``lazy="raise"`` so callers that need the
        broker-credential block (Dockerfile render / build) MUST go
        through this method — a plain :meth:`get_for_user` would raise on
        the first ``pair.account`` access.
        """
        stmt = self._for_user(
            select(Pair)
            .where(Pair.id == pair_id)
            .options(selectinload(Pair.account)),
            user_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def name_taken_for_user(
        self,
        user_id: uuid.UUID,
        name: str,
        *,
        exclude_id: uuid.UUID | None = None,
    ) -> bool:
        """Return True iff the tenant already owns a pair with this name.

        Names are not DB-uniqued (CHARTER leaves them free-form) but the
        operator surface enforces per-tenant uniqueness for sanity. Pass
        ``exclude_id`` on PATCH so editing a pair keeping the same
        name is not flagged as a duplicate.
        """
        stmt = self._for_user(
            select(Pair.id).where(Pair.name == name),
            user_id,
        )
        if exclude_id is not None:
            stmt = stmt.where(Pair.id != exclude_id)
        result = await self.session.execute(stmt.limit(1))
        return result.first() is not None

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------
    async def create(self, user_id: uuid.UUID, **fields: Any) -> Pair:
        """Insert a new pair for ``user_id``.

        Caller-supplied ``user_id`` in ``fields`` is rejected — the
        tenant is taken from the explicit argument, never the kwargs.
        """
        if "user_id" in fields:
            raise ValueError(
                "user_id must not be passed in fields; use the explicit argument"
            )
        pair = Pair(user_id=user_id, **fields)
        self.session.add(pair)
        await self.session.flush()
        await self.session.refresh(pair)
        return pair

    async def update_fields(
        self,
        user_id: uuid.UUID,
        pair_id: uuid.UUID,
        fields: dict[str, Any],
    ) -> Pair | None:
        """Partial update. Caller MUST have already filtered fields by allowlist.

        Returns the updated pair, or ``None`` if not found / not owned
        (router maps to 404). Refuses to touch the ``status`` column on
        purpose — status changes go through :meth:`update_status_if`.
        """
        if not fields:
            return await self.get_for_user(user_id, pair_id)

        if "status" in fields:
            raise ValueError("update_fields must not touch status — use update_status_if")
        if "user_id" in fields:
            raise ValueError("update_fields must not touch user_id")

        existing = await self.get_for_user(user_id, pair_id)
        if existing is None:
            return None

        for key, value in fields.items():
            setattr(existing, key, value)
        await self.session.flush()
        await self.session.refresh(existing)
        return existing

    async def update_status_if(
        self,
        user_id: uuid.UUID,
        pair_id: uuid.UUID,
        *,
        from_status: str,
        to_status: str,
    ) -> Pair | None:
        """Move ``pair_id`` to ``to_status`` only if it is currently in ``from_status``.

        Returns the refreshed pair on success, ``None`` on either
        "row missing / not owned" or "wrong from_status" (i.e. someone
        else moved it in between). Callers distinguish those two by
        re-fetching the row.

        Implemented as a single ``UPDATE ... WHERE id=? AND user_id=?
        AND status=?`` so concurrent transitions can't race the read.
        """
        stmt = (
            update(Pair)
            .where(Pair.id == pair_id)
            .where(Pair.user_id == user_id)
            .where(Pair.status == from_status)
            .values(status=to_status)
            .returning(Pair.id)
        )
        result = await self.session.execute(stmt)
        if result.scalar_one_or_none() is None:
            return None
        await self.session.flush()
        # Re-fetch so callers get a clean ORM instance with all columns loaded.
        return await self.get_for_user(user_id, pair_id)

    async def delete(self, user_id: uuid.UUID, pair_id: uuid.UUID) -> bool:
        """Hard-delete the pair. Returns True iff a row was removed.

        Caller is responsible for state-machine guard (``is_deletable``);
        this just enforces the tenant predicate.
        """
        stmt = (
            sql_delete(Pair)
            .where(Pair.id == pair_id)
            .where(Pair.user_id == user_id)
            .returning(Pair.id)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None
