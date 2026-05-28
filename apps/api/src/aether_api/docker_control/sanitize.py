"""Strict allowlist for any value that touches a Dockerfile, container
name, label, or environment variable.

The regex ``^[A-Za-z0-9_\\-.@:/]+$`` is intentionally narrow:

* Letters / digits cover identifiers (broker names, symbols, accounts).
* ``_ - .`` cover normal punctuation in identifiers.
* ``@ : /`` cover image refs (e.g. ``registry/repo:tag@sha256:...``).

What this allowlist deliberately excludes:

* SPACE and TAB     → would let an attacker append a second token to a
                      Dockerfile directive (e.g. ``FROM x RUN ...``).
* Backticks / ``$`` → would allow shell metacharacters once a value
                      lands inside a ``RUN`` directive.
* Quotes / brackets → break out of label values.
* Newlines / CR     → would inject new directives.

Pairing: Jinja2 ``autoescape=True`` is a second line of defence, never
the first. This sanitizer runs BEFORE the value reaches Jinja, and on
EVERY value that the renderer might interpolate.
"""

from __future__ import annotations

import re

# Anchored, monotonically increasing whitelist. Used by both the
# Dockerfile renderer (every interpolated value) and the lifecycle
# helpers (every container_name + label value).
_ALLOWED_RE = re.compile(r"^[A-Za-z0-9_\-.@:/]+$")


class UnsafeValueError(ValueError):
    """Raised when an interpolated value contains a forbidden character.

    Carries both the offending field name and the value (truncated) on
    the instance so callers can surface ``{"field": "broker_name",
    "value": "IC; rm -rf"}`` payloads in a 422.
    """

    def __init__(self, field: str, value: str) -> None:
        self.field = field
        # Truncate aggressively so error messages stay short and we don't
        # echo a giant attacker-controlled blob back unbounded.
        self.value = value[:80]
        super().__init__(
            f"unsafe value for {field!r}: {self.value!r} "
            f"(allowed: ^[A-Za-z0-9_\\-.@:/]+$)"
        )


def is_safe(value: str) -> bool:
    """Return True iff ``value`` matches the strict whitelist.

    Empty strings are NOT safe — the ``+`` quantifier requires at least
    one character. Callers that allow optional fields should check
    ``value is None`` before this.
    """
    if not isinstance(value, str):
        return False
    if not value:
        return False
    return bool(_ALLOWED_RE.match(value))


def sanitize_env_value(value: str, *, field: str = "value") -> str:
    """Return ``value`` unchanged if it matches the whitelist, else raise.

    The function is intentionally NOT lossy — it does not strip, escape,
    or rewrite the input. A value either passes the allowlist or it
    raises :class:`UnsafeValueError`. This keeps the contract
    deterministic and auditable: a router that catches the exception can
    map it to a 422 with field-level error without second-guessing what
    the renderer would emit.
    """
    if not is_safe(value):
        raise UnsafeValueError(field, value or "")
    return value


def sanitize_optional(value: str | None, *, field: str = "value") -> str | None:
    """Mirror of :func:`sanitize_env_value` for nullable columns."""
    if value is None:
        return None
    return sanitize_env_value(value, field=field)


__all__ = [
    "UnsafeValueError",
    "is_safe",
    "sanitize_env_value",
    "sanitize_optional",
]
