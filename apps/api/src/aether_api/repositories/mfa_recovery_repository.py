"""``mfa_recovery_codes`` table data access.

Single-use semantics live in :mod:`aether_api.services.mfa` (the
``UPDATE ... WHERE used_at IS NULL RETURNING id`` happens there). This
repository owns the bookkeeping — bulk delete on regenerate / disable,
counting unused rows for the UI, and the audit "last regenerated" view.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from aether_api.models.mfa_recovery_code import MfaRecoveryCode


class MfaRecoveryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    async def count_unused_for_user(self, user_id: uuid.UUID) -> int:
        """Return how many UNUSED recovery codes the user has left.

        The configuracion / seguridad UI surfaces this as a hint — "you
        have N codes left, regenerate before running out". The partial
        index on ``(user_id) WHERE used_at IS NULL`` makes this
        query cheap regardless of total row count.
        """
        stmt = (
            select(func.count())
            .select_from(MfaRecoveryCode)
            .where(
                MfaRecoveryCode.user_id == user_id,
                MfaRecoveryCode.used_at.is_(None),
            )
        )
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------
    async def delete_all_for_user(self, user_id: uuid.UUID) -> int:
        """Drop every code row for the user. Returns rows affected.

        Used by:

        * ``POST /api/me/mfa/recovery-codes/regenerate`` — the service
          layer immediately re-inserts a new batch in the same
          transaction.
        * ``POST /api/me/mfa/disable`` — leaves no codes behind once MFA
          is off.

        The caller owns the transaction (no commit here).
        """
        stmt = delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user_id)
        # ``execute`` returns ``Result`` at the type level but the runtime
        # object for a DML statement is a ``CursorResult`` exposing
        # ``rowcount``. Narrow for mypy without weakening runtime behaviour.
        result: CursorResult[Any] = await self.session.execute(stmt)  # type: ignore[assignment]
        return result.rowcount or 0
