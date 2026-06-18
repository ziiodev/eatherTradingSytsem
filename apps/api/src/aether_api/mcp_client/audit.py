"""``OrderAuditor`` — two-phase ``order_log`` writes.

Phase 1 (BEFORE the MCP call): insert a row with ``status='pending'``
and the inputs (payload_in, risk_check). Critically the INSERT is
committed BEFORE we leave for the broker; a process crash between phase
1 and phase 2 leaves a clean forensic record.

Phase 2 (AFTER the MCP call): UPDATE the same row to ``status='filled'``
+ ``payload_out`` + the broker ticket, OR to ``status='failed'`` +
``error`` + ``payload_out`` (which may carry the typed MT5 error code).

Approval / risk-blocked paths short-circuit at phase 1 by writing
``status='blocked'`` and never reaching the MCP call.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from aether_api.models.order import OrderLog


class OrderAuditor:
    """Wrapper around the ``order_log`` table for two-phase writes."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def write_pending(
        self,
        *,
        project_id: uuid.UUID,
        user_id: uuid.UUID,
        action: str,
        payload_in: dict[str, Any],
        risk_check: dict[str, Any] | None = None,
        order_id: uuid.UUID | None = None,
    ) -> OrderLog:
        """Insert a phase-1 row and FLUSH (callers MUST commit).

        Returns the row so the caller can pass its ``id`` into the
        phase-2 update without re-selecting it.
        """
        row = OrderLog(
            order_id=order_id,
            pair_id=project_id,
            user_id=user_id,
            action=action,
            payload_in=payload_in,
            risk_check=risk_check,
            status="pending",
        )
        self.session.add(row)
        # Flush so the row receives its DB-side defaults (id, created_at)
        # and any constraint failure surfaces NOW, not in phase 2.
        await self.session.flush()
        await self.session.refresh(row)
        return row

    async def write_blocked(
        self,
        *,
        project_id: uuid.UUID,
        user_id: uuid.UUID,
        action: str,
        payload_in: dict[str, Any],
        risk_check: dict[str, Any] | None,
        error: str,
    ) -> OrderLog:
        """Insert a single phase-1 row with ``status='blocked'`` (no phase 2)."""
        row = OrderLog(
            pair_id=project_id,
            user_id=user_id,
            action=action,
            payload_in=payload_in,
            risk_check=risk_check,
            status="blocked",
            error=error,
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return row

    async def finalise(
        self,
        log_row: OrderLog,
        *,
        status: str,
        payload_out: dict[str, Any] | None = None,
        error: str | None = None,
        order_id: uuid.UUID | None = None,
    ) -> OrderLog:
        """Phase-2 update of an existing ``order_log`` row.

        ``order_id`` may be passed to link the log to the freshly
        inserted ``orders`` row (the link is otherwise NULL when phase 1
        ran before the order row was created — the approval path).
        """
        log_row.status = status
        log_row.payload_out = payload_out
        if error is not None:
            log_row.error = error
        if order_id is not None:
            log_row.order_id = order_id
        await self.session.flush()
        await self.session.refresh(log_row)
        return log_row


__all__ = ["OrderAuditor"]
