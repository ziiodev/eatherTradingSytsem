"""Cookie writers / readers.

Cookie name choices MUST match the frontend (apps/web). The relevant
constants are mirrored in :file:`apps/web/middleware.ts` and
:file:`apps/web/src/lib/auth.ts`. Renaming one without the other is a
silent logout-loop bug.

Attribute matrix:

================  ============  =========  =================================
Cookie            httpOnly      Secure     Path
================  ============  =========  =================================
aether_access     yes           prod-only  /
aether_refresh    yes           prod-only  /api/auth/refresh
csrf_token        NO (JS reads) prod-only  /
================  ============  =========  =================================

``SameSite=Lax`` on all three — strong default against CSRF while still
allowing top-level navigation from external links.
"""

from __future__ import annotations

import secrets
from typing import Final

from fastapi import Request, Response

from aether_api.core.settings import get_settings

# Cookie names — keep these in lockstep with apps/web.
ACCESS_COOKIE: Final[str] = "aether_access"
REFRESH_COOKIE: Final[str] = "aether_refresh"
CSRF_COOKIE: Final[str] = "csrf_token"

# Header the JS client mirrors the csrf cookie into (double-submit).
CSRF_HEADER: Final[str] = "X-CSRF-Token"

# Path-scoping for the refresh cookie. The browser will only attach it
# to requests targeting this path, which keeps the long-lived secret
# out of every other API call.
REFRESH_COOKIE_PATH: Final[str] = "/api/auth/refresh"


def _common_kwargs() -> dict[str, object]:
    s = get_settings()
    kwargs: dict[str, object] = {
        "secure": s.cookie_secure,
        "samesite": "lax",
    }
    if s.cookie_domain:
        kwargs["domain"] = s.cookie_domain
    return kwargs


def set_auth_cookies(response: Response, *, access_token: str, refresh_token: str) -> None:
    """Set the access + refresh cookies. Does NOT touch the CSRF cookie."""
    s = get_settings()
    common = _common_kwargs()

    response.set_cookie(
        key=ACCESS_COOKIE,
        value=access_token,
        httponly=True,
        max_age=s.access_token_ttl_minutes * 60,
        path="/",
        **common,  # type: ignore[arg-type]
    )
    response.set_cookie(
        key=REFRESH_COOKIE,
        value=refresh_token,
        httponly=True,
        max_age=s.refresh_token_ttl_days * 24 * 60 * 60,
        path=REFRESH_COOKIE_PATH,
        **common,  # type: ignore[arg-type]
    )


def set_csrf_cookie(response: Response) -> str:
    """Mint a fresh CSRF token, set the non-httpOnly cookie, return the value.

    The token is returned so the caller can also include it in the
    response body if useful (it's not required — the JS client reads it
    from ``document.cookie``).
    """
    s = get_settings()
    common = _common_kwargs()
    token = secrets.token_urlsafe(32)
    response.set_cookie(
        key=CSRF_COOKIE,
        value=token,
        httponly=False,  # JS MUST read this — that's the whole double-submit point
        max_age=s.access_token_ttl_minutes * 60,
        path="/",
        **common,  # type: ignore[arg-type]
    )
    return token


def clear_auth_cookies(response: Response) -> None:
    """Delete access + refresh + csrf cookies (sets empty value + max-age=0).

    Path attributes MUST match the original set; otherwise the browser
    treats them as different cookies and the originals stay.
    """
    common = _common_kwargs()
    response.delete_cookie(
        ACCESS_COOKIE, path="/", **common  # type: ignore[arg-type]
    )
    response.delete_cookie(
        REFRESH_COOKIE, path=REFRESH_COOKIE_PATH, **common  # type: ignore[arg-type]
    )
    response.delete_cookie(
        CSRF_COOKIE, path="/", **common  # type: ignore[arg-type]
    )


def read_csrf_from_request(request: Request) -> str | None:
    """Return the value of the csrf cookie, or None."""
    return request.cookies.get(CSRF_COOKIE)


def read_csrf_header(request: Request) -> str | None:
    """Return the X-CSRF-Token header value, or None."""
    return request.headers.get(CSRF_HEADER)
