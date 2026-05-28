"""``sessions`` table data access.

Session rows are partially tenant-scoped: they belong to a user but
are normally addressed via the (hashed) refresh token rather than via
``user_id``. The lookup methods filter by ``revoked_at IS NULL`` so the
caller cannot accidentally resurrect a revoked session.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, cast

from sqlalchemy import CursorResult, and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from aether_api.models.session import UserSession


class SessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    async def get_active_by_token_hash(self, token_hash: str) -> UserSession | None:
        """Return the live session whose hash matches, else ``None``.

        "Live" = not revoked AND not expired. Expiry is checked in
        application code (not in the query) so the caller can produce a
        distinct "session expired" log line.
        """
        stmt = (
            select(UserSession)
            .where(UserSession.refresh_token_hash == token_hash)
            .where(UserSession.revoked_at.is_(None))
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------
    async def create(
        self,
        *,
        user_id: uuid.UUID,
        refresh_token_hash: str,
        expires_at: datetime,
        ip_address: str | None,
        user_agent: str | None,
    ) -> UserSession:
        row = UserSession(
            user_id=user_id,
            refresh_token_hash=refresh_token_hash,
            expires_at=expires_at,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def revoke(self, session_id: uuid.UUID, *, now: datetime | None = None) -> None:
        when = now or datetime.utcnow()
        row = await self.session.get(UserSession, session_id)
        if row is None or row.revoked_at is not None:
            return
        row.revoked_at = when

    async def revoke_all_for_user(
        self, user_id: uuid.UUID, *, now: datetime | None = None
    ) -> int:
        """Mark every active session for the user as revoked. Returns row count."""
        when = now or datetime.utcnow()
        stmt = (
            update(UserSession)
            .where(UserSession.user_id == user_id)
            .where(UserSession.revoked_at.is_(None))
            .values(revoked_at=when)
        )
        # `session.execute()` on a bulk UPDATE returns a `CursorResult` at
        # runtime, but its declared return type is the broader `Result[Any]`
        # which doesn't expose `.rowcount`. Narrow with `cast()` so mypy is
        # happy without changing behaviour.
        result = cast(CursorResult[Any], await self.session.execute(stmt))
        # `result.rowcount` is the count of affected rows in PG.
        return int(result.rowcount or 0)

    async def touch_last_used(
        self, session_id: uuid.UUID, *, now: datetime | None = None
    ) -> None:
        when = now or datetime.utcnow()
        row = await self.session.get(UserSession, session_id)
        if row is None:
            return
        row.last_used_at = when

    # ------------------------------------------------------------------
    # Self-service session listing / revocation
    # (used by /api/me/sessions — see routers/me.py)
    # ------------------------------------------------------------------
    async def get_for_user_active(self, session_id: uuid.UUID) -> UserSession | None:
        """Return a session row by id IF it is not revoked, else ``None``.

        Does NOT scope by user — the caller is expected to use this only
        after authenticating via the access JWT, which already binds the
        session id to a user. Used by ``/api/me`` helpers that need to
        identify the caller's current session from the JWT ``sid`` claim
        (the refresh cookie's path scope prevents using its hash here).
        """
        stmt = (
            select(UserSession)
            .where(UserSession.id == session_id)
            .where(UserSession.revoked_at.is_(None))
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_for_user(
        self, user_id: uuid.UUID, session_id: uuid.UUID
    ) -> UserSession | None:
        """Return a session row only if it belongs to ``user_id``.

        Cross-tenant accesses return ``None`` so the caller can map to a
        plain 404 (no leak of "exists but belongs to another user").
        """
        stmt = (
            select(UserSession)
            .where(UserSession.id == session_id)
            .where(UserSession.user_id == user_id)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_user(
        self,
        user_id: uuid.UUID,
        *,
        limit: int,
        cursor: tuple[datetime, uuid.UUID] | None = None,
    ) -> tuple[list[UserSession], tuple[datetime, uuid.UUID] | None]:
        """Paginated session list for the caller, ordered ``issued_at DESC, id DESC``.

        Returns ``(rows, next_cursor)``. ``next_cursor`` is ``None`` when
        the page is the last one. Uses a keyset (seek) cursor on the
        compound ``(issued_at, id)`` key — strictly stable under inserts.
        Revoked rows ARE included (the UI shows them faded with their
        ``revoked_at`` timestamp).
        """
        stmt = select(UserSession).where(UserSession.user_id == user_id)
        if cursor is not None:
            cursor_issued_at, cursor_id = cursor
            stmt = stmt.where(
                or_(
                    UserSession.issued_at < cursor_issued_at,
                    and_(
                        UserSession.issued_at == cursor_issued_at,
                        UserSession.id < cursor_id,
                    ),
                )
            )
        stmt = stmt.order_by(
            UserSession.issued_at.desc(), UserSession.id.desc()
        ).limit(limit + 1)

        result = await self.session.execute(stmt)
        rows = list(result.scalars().all())

        next_cursor: tuple[datetime, uuid.UUID] | None = None
        if len(rows) > limit:
            # Drop the lookahead row and emit its predecessor as the
            # next cursor — the client replays it verbatim.
            last_returned = rows[limit - 1]
            next_cursor = (last_returned.issued_at, last_returned.id)
            rows = rows[:limit]
        return rows, next_cursor

    async def revoke_others_for_user(
        self,
        user_id: uuid.UUID,
        *,
        except_session_id: uuid.UUID,
        now: datetime | None = None,
    ) -> int:
        """Mark every active session for ``user_id`` revoked EXCEPT the given one.

        Used by "sign out other devices" (password change + sign_out_others,
        explicit /sessions/revoke-others endpoint). Returns the count of
        affected rows. Idempotent: re-running it returns 0.
        """
        when = now or datetime.utcnow()
        stmt = (
            update(UserSession)
            .where(UserSession.user_id == user_id)
            .where(UserSession.id != except_session_id)
            .where(UserSession.revoked_at.is_(None))
            .values(revoked_at=when)
        )
        result = cast(CursorResult[Any], await self.session.execute(stmt))
        return int(result.rowcount or 0)
