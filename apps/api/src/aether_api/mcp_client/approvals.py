"""ApprovalGate — poll-based large-order approval workflow.

The gate is poll-based on purpose: LISTEN/NOTIFY support is a future
deliverable (deferred per design doc) so the v1 surface is dirt-simple
and dependency-free. Every 2 seconds the gate re-queries the row's
``status`` until it transitions out of ``pending`` or the row's
``expires_at`` passes.

The expiry is enforced TWICE:

* In the poll loop — :class:`ApprovalTimeout` raised once ``now >
  expires_at``.
* By a background sweep (run by the API process every poll-tick) that
  flips dangling ``pending`` rows to ``expired``. This means a crashed
  poller cannot leave the row dangling.

This module is pure-Python + ``AsyncSession`` — no Postgres-specific
features.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aether_api.models.order import OrderApproval

from .errors import ApprovalRejected, ApprovalTimeout

#: How often the gate polls Postgres for the approval row's status. 2s
#: balances UI responsiveness against query load — the operator pane
#: also polls at 2s so a status change shows up at the same cadence.
POLL_INTERVAL_SECONDS: float = 2.0


class ApprovalGate:
    """Poll-based approval workflow over the ``order_approvals`` table."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        poll_interval_seconds: float = POLL_INTERVAL_SECONDS,
    ) -> None:
        self.session = session
        self.poll_interval = poll_interval_seconds

    async def request(
        self,
        *,
        project_id: uuid.UUID,
        user_id: uuid.UUID,
        payload: dict[str, Any],
        ttl_seconds: int = 300,
        agent_id: uuid.UUID | None = None,
    ) -> OrderApproval:
        """Insert a new pending approval row and return it."""
        now = datetime.now(tz=UTC).replace(tzinfo=None)
        row = OrderApproval(
            pair_id=project_id,
            user_id=user_id,
            agent_id=agent_id,
            payload=payload,
            status="pending",
            requested_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return row

    async def wait(self, approval_id: uuid.UUID) -> OrderApproval:
        """Poll until decided or expired. Raise on rejection / timeout.

        Returns the refreshed ``approved`` row on success.
        """
        while True:
            row = await self._get(approval_id)
            if row is None:
                # Defensive — should never happen because we just inserted it.
                raise ApprovalTimeout(
                    "approval row vanished",
                    details={"approval_id": str(approval_id)},
                )
            if row.status == "approved":
                return row
            if row.status == "rejected":
                raise ApprovalRejected(
                    "approval rejected by operator",
                    details={"approval_id": str(approval_id)},
                )
            if row.status == "expired" or self._is_expired(row):
                # Best-effort flip to ``expired`` in case the sweep
                # hasn't run yet. The state-machine guarantee is that
                # only ``pending`` ever transitions away.
                if row.status == "pending":
                    row.status = "expired"
                    await self.session.flush()
                raise ApprovalTimeout(
                    "approval expired",
                    details={"approval_id": str(approval_id)},
                )
            await asyncio.sleep(self.poll_interval)

    async def _get(self, approval_id: uuid.UUID) -> OrderApproval | None:
        # Force a fresh read so the poller observes external updates
        # (the API process commits the approval inside another request).
        await self.session.commit()
        stmt = select(OrderApproval).where(OrderApproval.id == approval_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    def _is_expired(self, row: OrderApproval) -> bool:
        now = datetime.now(tz=UTC).replace(tzinfo=None)
        return row.expires_at <= now


async def list_pending_approvals(
    session: AsyncSession, *, project_id: uuid.UUID
) -> list[OrderApproval]:
    """Return all ``pending`` rows for the project (newest first)."""
    stmt = (
        select(OrderApproval)
        .where(OrderApproval.pair_id == project_id)
        .where(OrderApproval.status == "pending")
        .order_by(OrderApproval.requested_at.desc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def decide_approval(
    session: AsyncSession,
    *,
    approval_id: uuid.UUID,
    project_id: uuid.UUID,
    decided_by: uuid.UUID,
    approve: bool,
) -> OrderApproval | None:
    """Move ``approval_id`` to ``approved`` / ``rejected``.

    Returns the updated row, or ``None`` if not found / not for this
    project / not in ``pending`` (tenant-scoped — cross-tenant returns
    None which the router maps to 404).
    """
    stmt = (
        select(OrderApproval)
        .where(OrderApproval.id == approval_id)
        .where(OrderApproval.pair_id == project_id)
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None or row.status != "pending":
        return None

    row.status = "approved" if approve else "rejected"
    row.decided_at = datetime.now(tz=UTC).replace(tzinfo=None)
    row.decided_by = decided_by
    await session.flush()
    await session.refresh(row)
    return row


__all__ = [
    "POLL_INTERVAL_SECONDS",
    "ApprovalGate",
    "decide_approval",
    "list_pending_approvals",
]
