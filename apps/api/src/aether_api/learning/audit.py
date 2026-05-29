"""Cross-tenant write audit log + token-bucket rate limit.

Phase 11 of ``sdd/sleep-learning-loop`` (multi-tenancy-delta #2068).

The four learning-table repositories raise ``PermissionError`` when a
caller tries to write a row whose ``project_id`` belongs to another
user (see :mod:`aether_api.repositories.q_table_repository`,
:mod:`aether_api.repositories.episodic_memory_repository`,
:mod:`aether_api.repositories.semantic_memory_repository`,
:mod:`aether_api.repositories.sleep_report_repository`). The exception
is the **boundary**; this module adds the **audit trail** required by
the multi-tenancy delta spec: every refused write emits a structured
WARN log line so an operator can spot a sustained probe.

Hostile callers could spam refused writes to weaponise log volume, so
the audit log is rate-limited per ``actor_user_id`` via a token bucket:
``capacity`` warns per refill window, refilled continuously at
``capacity / window`` tokens/second. Over-limit attempts SILENTLY drop
their log line — the ``PermissionError`` itself still raises so the
write is always rejected.

Both ``capacity`` and ``window_seconds`` come from settings
(``learning_audit_rate_capacity`` and
``learning_audit_rate_window_seconds``) so deployments / tests can
tune them without code edits.

Threading
---------

The token-bucket state is a process-global dict guarded by an
``asyncio.Lock`` — the four repositories are async and so are their
callers. The lock is fine-grained (one acquire per audit call) and
held only long enough to mutate the per-actor bucket; the underlying
log emission is synchronous (stdlib ``logging``) and happens OUTSIDE
the lock.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass

from aether_api.core.settings import get_settings

__all__ = [
    "AUDIT_LOG_KEY",
    "TokenBucket",
    "log_cross_tenant_attempt",
    "reset_for_test",
]


#: Structured WARN log key emitted on every (non-rate-limited)
#: cross-tenant write attempt. Pinned so the alerting layer has a
#: constant to match on.
AUDIT_LOG_KEY = "aether.learning.cross_tenant_write_denied"


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Token bucket
# ---------------------------------------------------------------------------


@dataclass
class TokenBucket:
    """Continuous-refill token bucket.

    Starts full (``tokens == capacity``) so the first attempt always
    logs. Refills at ``capacity / window_seconds`` tokens/second up to
    a maximum of ``capacity``.

    Not thread-safe on its own — callers MUST hold the module-level
    ``asyncio.Lock`` while mutating.
    """

    capacity: int
    window_seconds: float
    tokens: float
    last_refill_monotonic: float

    @classmethod
    def fresh(cls, capacity: int, window_seconds: float) -> TokenBucket:
        """Build a brand-new bucket — starts at full capacity."""
        return cls(
            capacity=capacity,
            window_seconds=window_seconds,
            tokens=float(capacity),
            last_refill_monotonic=time.monotonic(),
        )

    def take(self) -> bool:
        """Try to consume one token. Returns True if successful.

        Side effects: refills the bucket based on elapsed wall time
        BEFORE checking, so a bucket emptied just before the refill
        window expired CAN issue another token immediately afterwards.
        """
        now = time.monotonic()
        elapsed = max(0.0, now - self.last_refill_monotonic)
        if self.window_seconds > 0:
            refill = (elapsed / self.window_seconds) * self.capacity
            self.tokens = min(float(self.capacity), self.tokens + refill)
        self.last_refill_monotonic = now

        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False


# ---------------------------------------------------------------------------
# Module-level state.
# ---------------------------------------------------------------------------


#: ``{actor_user_id_str: TokenBucket}``. Process-global because the
#: audit boundary is per-process. Reset via :func:`reset_for_test` in
#: the test suite.
_BUCKETS: dict[str, TokenBucket] = {}

#: Async lock guarding ``_BUCKETS`` mutation. We use an asyncio lock
#: rather than a thread lock because every caller of
#: :func:`log_cross_tenant_attempt` is an ``async def`` repository
#: method.
_LOCK: asyncio.Lock = asyncio.Lock()


def _bucket_for(actor_user_id: uuid.UUID | str) -> TokenBucket:
    """Lazy-create / return the bucket for ``actor_user_id``.

    Settings are read at bucket-creation time so tests that
    monkeypatch ``LEARNING_AUDIT_RATE_*`` and call
    ``get_settings.cache_clear()`` see the new values immediately
    (after :func:`reset_for_test`).
    """
    key = str(actor_user_id)
    bucket = _BUCKETS.get(key)
    if bucket is None:
        settings = get_settings()
        bucket = TokenBucket.fresh(
            capacity=settings.learning_audit_rate_capacity,
            window_seconds=settings.learning_audit_rate_window_seconds,
        )
        _BUCKETS[key] = bucket
    return bucket


# ---------------------------------------------------------------------------
# Public API.
# ---------------------------------------------------------------------------


async def log_cross_tenant_attempt(
    *,
    actor_user_id: uuid.UUID | str,
    target_project_id: uuid.UUID | str,
    table_name: str,
    operation: str,
) -> bool:
    """Record one denied cross-tenant write attempt.

    Returns True iff the bucket had a token to spend (i.e. the WARN
    line was actually emitted). Callers MAY use the return value for
    tests / instrumentation; production callsites generally ignore it
    because the ``PermissionError`` they're about to raise carries the
    "operation refused" signal regardless.

    Parameters
    ----------
    actor_user_id
        The user_id of the caller who *attempted* the write — NOT the
        legitimate project owner. Used as the rate-bucket key.
    target_project_id
        The resource the caller was trying to write to. For
        ``sleep_reports`` the caller passes the ``sleep_run_id`` (the
        natural foreign key for that table); both shapes are UUIDs so
        downstream consumers don't need to special-case.
    table_name
        Which of the four learning tables ``q_tables`` /
        ``episodic_memory`` / ``semantic_memory`` / ``sleep_reports``.
    operation
        The repository method that refused the write
        (``insert_version`` / ``insert`` / ``mark_special`` / ...).
        Helps a future alerting rule distinguish a probe targeting
        ``q_tables`` versions vs one probing ``episodic_memory``.
    """
    async with _LOCK:
        bucket = _bucket_for(actor_user_id)
        allowed = bucket.take()

    if not allowed:
        return False

    # Structured fields via stdlib logging's ``extra=`` so the structlog
    # JSON renderer at the top of the stack preserves them as named
    # keys. NO payload, NO Q-Table data — only identifiers.
    logger.warning(
        AUDIT_LOG_KEY,
        extra={
            "actor_user_id": str(actor_user_id),
            "target_project_id": str(target_project_id),
            "table_name": table_name,
            "operation": operation,
        },
    )
    return True


def reset_for_test() -> None:
    """Drop all buckets — process-global state must be flushed between
    tests so a 10-attempt bucket from one test doesn't taint the next.

    Production callers MUST NOT call this — it nukes the rate limiter
    state and lets a hostile caller burst again immediately.
    """
    _BUCKETS.clear()
