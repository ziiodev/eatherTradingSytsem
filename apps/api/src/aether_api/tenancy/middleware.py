"""FastAPI dependencies for auth + CSRF + admin.

Why these are *dependencies* and not middleware proper:

* Per-handler granularity — login MUST be exempt from the CSRF check
  (that's how the cookie is bootstrapped); admin endpoints want
  :func:`admin_required` layered ON TOP of :func:`current_user`.
* Middleware would run before routing, which is too early to discover
  which endpoints are exempt.
"""

from __future__ import annotations

import hmac
import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from aether_api.auth.cookies import (
    ACCESS_COOKIE,
    read_csrf_from_request,
    read_csrf_header,
)
from aether_api.auth.tokens import verify_access_token
from aether_api.db.session import get_session
from aether_api.models.user import User
from aether_api.repositories.user_repository import UserRepository


# -----------------------------------------------------------------------------
# current_user
# -----------------------------------------------------------------------------
async def current_user(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    """Resolve the user from the ``aether_access`` cookie. 401 on any failure.

    Side effects:

    * Attaches the loaded :class:`User` to ``request.state.user`` so
      downstream code (or future audit logs) can read it without
      re-querying.
    """
    token = request.cookies.get(ACCESS_COOKIE)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")

    user_id = verify_access_token(token)
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid session")

    user = await UserRepository(session).get_by_id(user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid session")

    request.state.user = user
    return user


# -----------------------------------------------------------------------------
# csrf_dependency
# -----------------------------------------------------------------------------
async def csrf_dependency(request: Request) -> None:
    """Verify double-submit CSRF: cookie value == ``X-CSRF-Token`` header.

    Returns nothing on success; raises HTTP 403 on any mismatch.

    Login is NOT decorated with this dependency — login is where the
    cookie gets minted, so a client can't have it yet on its first
    request. Refresh / logout / signup / state-changing endpoints MUST
    declare ``Depends(csrf_dependency)`` explicitly.
    """
    cookie_value = read_csrf_from_request(request)
    header_value = read_csrf_header(request)

    if not cookie_value or not header_value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="csrf required")

    # Constant-time compare — defends against the (very narrow) timing
    # oracle if an attacker could probe header values.
    if not hmac.compare_digest(cookie_value, header_value):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="csrf mismatch")


# Convenience: import path for routers that just want the dependency object.
def csrf_protected() -> object:
    """Return the dependency callable, sugar for ``Depends(csrf_dependency)``."""
    return Depends(csrf_dependency)


# -----------------------------------------------------------------------------
# admin_required
# -----------------------------------------------------------------------------
async def admin_required(
    user: Annotated[User, Depends(current_user)],
) -> User:
    """Layer on top of :func:`current_user`. 403 for non-admins."""
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin required")
    return user


# Re-exported so callers can write ``from aether_api.tenancy.middleware import _``
# instead of digging into ``secrets`` for misc. helpers.
random_token = secrets.token_urlsafe
