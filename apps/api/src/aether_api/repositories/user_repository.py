"""User-table data access.

``users`` is *not* a tenant-scoped table (it's the tenant root itself),
so it does NOT use :meth:`BaseRepository._for_user`. Authorization is
handled at the route level: only the authenticated user themselves and
admins may touch a user row.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from aether_api.models.user import User


class EmailAlreadyTakenError(Exception):
    """Raised when an email change collides with the ``users.email`` unique index.

    Wraps the underlying :class:`sqlalchemy.exc.IntegrityError` so route
    handlers can map to HTTP 409 without leaking ORM internals.
    """


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        return await self.session.get(User, user_id)

    async def get_by_email(self, email: str) -> User | None:
        # ``email`` is stored lower-case; callers MUST also lower-case
        # before passing in. We do not normalise here so the caller can
        # decide whether mismatched casing is "not found" or an error.
        stmt = select(User).where(User.email == email)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------
    async def create(
        self,
        *,
        email: str,
        password_hash: str | None,
        display_name: str | None = None,
        is_admin: bool = False,
    ) -> User:
        user = User(
            email=email.lower(),
            password_hash=password_hash,
            display_name=display_name,
            is_admin=is_admin,
        )
        self.session.add(user)
        await self.session.flush()  # populate user.id / created_at without committing
        return user

    async def update_last_login(self, user_id: uuid.UUID, *, now: datetime | None = None) -> None:
        when = now or datetime.utcnow()
        user = await self.session.get(User, user_id)
        if user is None:
            return
        user.last_login_at = when

    async def increment_failed_login(self, user_id: uuid.UUID) -> int:
        """Bump the failed counter; return the new value."""
        user = await self.session.get(User, user_id)
        if user is None:
            return 0
        user.failed_login_count = (user.failed_login_count or 0) + 1
        return user.failed_login_count

    async def reset_failed_login(self, user_id: uuid.UUID) -> None:
        user = await self.session.get(User, user_id)
        if user is None:
            return
        user.failed_login_count = 0
        user.locked_until = None

    async def lock_until(
        self, user_id: uuid.UUID, *, minutes: int, now: datetime | None = None
    ) -> None:
        """Lock the account until ``now + minutes`` and zero the counter."""
        when = (now or datetime.utcnow()) + timedelta(minutes=minutes)
        user = await self.session.get(User, user_id)
        if user is None:
            return
        user.locked_until = when
        user.failed_login_count = 0

    # ------------------------------------------------------------------
    # Self-service profile / credentials writes
    # (used by /api/me — see routers/me.py)
    # ------------------------------------------------------------------
    async def update_password_hash(
        self, user_id: uuid.UUID, new_hash: str, *, now: datetime | None = None
    ) -> None:
        """Replace the user's argon2id hash.

        Bumps ``updated_at`` so audit consumers can see the rotation.
        The caller owns the transaction — no commit happens here.
        """
        when = now or datetime.utcnow()
        user = await self.session.get(User, user_id)
        if user is None:
            return
        user.password_hash = new_hash
        user.updated_at = when

    async def update_email(
        self,
        user_id: uuid.UUID,
        new_email_lowercased: str,
        *,
        now: datetime | None = None,
    ) -> None:
        """Set ``users.email`` to a new (lowercased) value.

        Also clears ``email_verified_at`` (the new address is unverified
        by definition) and bumps ``updated_at``. Translates a unique-index
        :class:`IntegrityError` into :class:`EmailAlreadyTakenError` so the
        route handler can map cleanly to HTTP 409.
        """
        when = now or datetime.utcnow()
        user = await self.session.get(User, user_id)
        if user is None:
            return
        user.email = new_email_lowercased
        user.email_verified_at = None
        user.updated_at = when
        try:
            await self.session.flush()
        except IntegrityError as exc:
            raise EmailAlreadyTakenError(new_email_lowercased) from exc

    # ------------------------------------------------------------------
    # MFA (TOTP) — pre-wired columns activated by the mfa-totp change.
    # The plaintext secret never reaches the DB: the caller passes the
    # encrypted-by-SecretBox reference, and the disable path nulls it.
    # ------------------------------------------------------------------
    async def set_mfa_secret_ref(
        self,
        user_id: uuid.UUID,
        encrypted_ref: str,
        *,
        now: datetime | None = None,
    ) -> None:
        """Persist the encrypted TOTP secret reference on the user row.

        ``mfa_enabled`` is NOT touched here — verification flips that
        bit separately so a half-completed enrolment cannot accidentally
        enable MFA.
        """
        when = now or datetime.utcnow()
        user = await self.session.get(User, user_id)
        if user is None:
            return
        user.mfa_secret_ref = encrypted_ref
        user.updated_at = when

    async def enable_mfa(
        self, user_id: uuid.UUID, *, now: datetime | None = None
    ) -> None:
        """Flip ``mfa_enabled = TRUE``. Idempotent.

        The caller MUST have already (1) stored the encrypted secret via
        :meth:`set_mfa_secret_ref` and (2) verified a fresh TOTP code
        against that secret. We do not re-verify here — that's the
        responsibility of the route layer.
        """
        when = now or datetime.utcnow()
        user = await self.session.get(User, user_id)
        if user is None:
            return
        user.mfa_enabled = True
        user.updated_at = when

    async def disable_mfa(
        self, user_id: uuid.UUID, *, now: datetime | None = None
    ) -> None:
        """Clear ``mfa_enabled`` AND ``mfa_secret_ref`` in a single update.

        Recovery-code rows live in a separate table and are torn down by
        the route handler (so the audit trail can mention the row count).
        """
        when = now or datetime.utcnow()
        user = await self.session.get(User, user_id)
        if user is None:
            return
        user.mfa_enabled = False
        user.mfa_secret_ref = None
        user.updated_at = when

    async def update_profile(
        self,
        user_id: uuid.UUID,
        *,
        display_name: str | None = None,
        avatar_url: str | None = None,
        update_display_name: bool = False,
        update_avatar_url: bool = False,
        now: datetime | None = None,
    ) -> User | None:
        """Partial update of ``display_name`` / ``avatar_url``.

        Only fields flagged via ``update_display_name`` / ``update_avatar_url``
        are written — that's how the caller distinguishes "set to NULL"
        from "leave alone" without inventing a sentinel value. ``updated_at``
        is bumped whenever at least one field is written. Returns the
        refreshed user row, or ``None`` if no such user.
        """
        user = await self.session.get(User, user_id)
        if user is None:
            return None
        touched = False
        if update_display_name:
            user.display_name = display_name
            touched = True
        if update_avatar_url:
            user.avatar_url = avatar_url
            touched = True
        if touched:
            user.updated_at = now or datetime.utcnow()
            await self.session.flush()
        return user
