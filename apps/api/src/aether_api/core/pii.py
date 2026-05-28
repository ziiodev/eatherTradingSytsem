"""PII / secret scrubbing for log records and error reports.

Used by two consumers:

* The structlog processor chain — see :func:`scrub_pii_processor`.
* The Sentry SDK ``before_send`` / ``before_breadcrumb`` hooks — see
  :func:`scrub_event`.

The same forbidden-keys list lives in the frontend Sentry config
(``apps/web/src/lib/sentry-scrub.ts``) — keep them in sync. Drift in one
direction (backend masks but frontend doesn't, or vice versa) leaks
secrets through the slow path.

The contract:

* If a mapping key is in :data:`FORBIDDEN_KEYS` (case-insensitive), its
  value is replaced with :data:`MASK`. The key itself is kept so log
  consumers can still see "yes, password was present".
* String values that look like a JWT (regex :data:`_JWT_PATTERN`) are
  masked too — guards against an accidental ``logger.info(f"got {token}")``.
* Scrubbing recurses into nested mappings + sequences but bottoms out
  at primitives. Cycles are not expected (log events are constructed
  fresh per call); we do NOT detect them.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, MutableMapping, Sequence
from typing import Any, Final

#: Replacement string used wherever a forbidden value is removed. Loud and
#: searchable so post-mortem greps surface "did we leak X" quickly.
MASK: Final[str] = "***REDACTED***"

#: Keys whose values MUST never appear in logs or error reports. Match is
#: case-insensitive and substring-free — only exact matches trigger the
#: mask so legitimate keys like ``mfa_enabled`` are not over-scrubbed.
FORBIDDEN_KEYS: Final[frozenset[str]] = frozenset(
    {
        "password",
        "password_hash",
        "current_password",
        "new_password",
        "csrf_token",
        "x_csrf_token",
        "aether_access",
        "aether_refresh",
        "refresh_token",
        "refresh_token_hash",
        "access_token",
        "mfa_secret",
        "mfa_secret_ref",
        "jwt_secret",
        "authorization",
        "cookie",
        "set_cookie",
        "api_key",
        "mt5_password",
    }
)

#: JWT-shaped strings — three base64url segments separated by dots, the
#: first two starting with ``eyJ`` (i.e. the base64 of a ``{`` opener).
#: We mask the whole thing rather than try to keep the header — once a
#: JWT is in a log line, the entire token is the secret.
_JWT_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"
)


def _scrub_string(value: str) -> str:
    """Mask any JWT-shaped substring inside ``value``."""
    return _JWT_PATTERN.sub(MASK, value)


def _scrub_value(key: str | None, value: Any) -> Any:
    """Recursive worker. ``key`` is the parent key (for the forbidden check)."""
    if key is not None and key.lower() in FORBIDDEN_KEYS:
        return MASK

    if isinstance(value, str):
        return _scrub_string(value)

    if isinstance(value, Mapping):
        return {k: _scrub_value(str(k), v) for k, v in value.items()}

    # Bytes / bytearray are passed through unchanged — Sentry / structlog
    # don't typically render them; if they do, they'll be base64'd anyway.
    if isinstance(value, (bytes, bytearray)):
        return value

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_scrub_value(None, item) for item in value]

    return value


def scrub_mapping(data: Mapping[str, Any]) -> dict[str, Any]:
    """Return a scrubbed copy of an arbitrary mapping.

    Use this from Sentry's ``before_send`` callback (the event payload
    Sentry passes is a plain ``dict`` of nested mappings/sequences).
    """
    return {k: _scrub_value(str(k), v) for k, v in data.items()}


def scrub_pii_processor(
    _logger: Any, _method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Structlog processor — masks forbidden keys + JWT-shaped strings.

    The processor signature is fixed by structlog: ``(logger, method_name,
    event_dict)``. We mutate ``event_dict`` in place AND return it (structlog
    accepts either; returning is the documented contract).
    """
    for k in list(event_dict.keys()):
        event_dict[k] = _scrub_value(str(k), event_dict[k])
    return event_dict


def scrub_event(event: dict[str, Any], _hint: dict[str, Any] | None = None) -> dict[str, Any]:
    """Sentry ``before_send`` hook — returns a scrubbed copy of the event.

    Returning ``None`` would drop the event entirely. We always return the
    scrubbed payload so error visibility is preserved, just sanitised.
    """
    return scrub_mapping(event)
