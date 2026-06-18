"""Prometheus metrics for the sleep-learning loop.

Phase 11 of ``sdd/sleep-learning-loop``. Exposes three labelled
collectors:

* :data:`q_table_promotions_total` (Counter) — incremented inside
  :func:`aether_api.sleep.learning_step.finalize_learning_step` AFTER
  the savepoint releases successfully. Labels: ``pair``,
  ``risk_class`` (``bajo`` / ``medio`` / ``alto``).
* :data:`episodic_rows` (Gauge) — set after every episodic insert via
  :func:`update_episodic_rows`. The value is the latest count cached
  for ``EPISODIC_GAUGE_TTL_SECONDS`` to keep the hot path off
  ``COUNT(*)``. Labels: ``pair``.
* :data:`qtable_bytes` (Gauge) — set after every ``q_tables`` insert
  via :func:`record_qtable_bytes`. When the value exceeds the
  ``settings.learning_qtable_warn_bytes`` threshold a structured WARN
  log line ``aether.qtable.size_threshold_breached`` is emitted with
  the pair_id + byte count payload. Labels: ``pair``.

The metrics live on the **default registry** (``prometheus_client``'s
process-global registry) so the existing ``/metrics`` endpoint mounted
by :mod:`aether_api.core.observability` exposes them automatically.
Tests SHOULD instantiate :func:`reset_for_test` between cases — the
Counter+Gauge state is process-global.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

from prometheus_client import Counter, Gauge

from aether_api.core.settings import get_settings

__all__ = [
    "QTABLE_SIZE_LOG_KEY",
    "episodic_rows",
    "increment_q_table_promotion",
    "q_table_promotions_total",
    "qtable_bytes",
    "record_qtable_bytes",
    "reset_for_test",
    "update_episodic_rows",
]

logger = logging.getLogger(__name__)

#: Structured WARN log key emitted when a Q-Table version exceeds the
#: configured byte threshold. Pinned here so the (forthcoming)
#: alerting layer has a constant to match on.
QTABLE_SIZE_LOG_KEY = "aether.qtable.size_threshold_breached"

#: TTL (seconds) of the cached episodic count. The Gauge is updated
#: only when the cache slot for ``(pair_id)`` is older than this so
#: a tight insert loop doesn't fire ``COUNT(*)`` on every write.
EPISODIC_GAUGE_TTL_SECONDS = 30.0


# ---------------------------------------------------------------------------
# Collectors
# ---------------------------------------------------------------------------


q_table_promotions_total: Counter = Counter(
    "aether_sleep_q_table_promotions_total",
    "Number of Q-Table version promotions, partitioned by pair_id "
    "and the risk class assigned by the classifier.",
    labelnames=("pair", "risk_class"),
)


episodic_rows: Gauge = Gauge(
    "aether_episodic_rows",
    "Number of rows in episodic_memory for a given pair_id. Updated "
    "after each episodic insert with a cached COUNT(*) (refreshed every "
    f"{EPISODIC_GAUGE_TTL_SECONDS:.0f} s).",
    labelnames=("pair",),
)


qtable_bytes: Gauge = Gauge(
    "aether_qtable_bytes",
    "Serialized JSON size (bytes) of the latest q_tables row for a "
    "given pair_id. Crosses settings.learning_qtable_warn_bytes "
    "triggers a structured WARN log line.",
    labelnames=("pair",),
)


# ---------------------------------------------------------------------------
# Episodic gauge — last-update cache so hot paths don't repoll.
# ---------------------------------------------------------------------------

#: ``{pair_id_str: monotonic_seconds_at_last_update}``. Process-global
#: because the Gauge itself is.
_LAST_EPISODIC_UPDATE: dict[str, float] = {}


def _stringify_pair(pair_id: uuid.UUID | str) -> str:
    """Cast ``pair_id`` to ``str`` — Prometheus labels are strings."""
    return str(pair_id)


def update_episodic_rows(
    pair_id: uuid.UUID | str,
    row_count: int,
    *,
    force: bool = False,
) -> None:
    """Set :data:`episodic_rows` for ``pair_id`` to ``row_count``.

    Throttled by ``EPISODIC_GAUGE_TTL_SECONDS`` per pair — callers
    on the episodic-insert hot path can call this on every write and
    only the first call inside the TTL window touches the Gauge.

    ``force=True`` skips the TTL check; used by tests and by the lifespan
    warm pass where a stale snapshot would mask the real row count.
    """
    label = _stringify_pair(pair_id)
    now = time.monotonic()
    if not force:
        last = _LAST_EPISODIC_UPDATE.get(label)
        if last is not None and (now - last) < EPISODIC_GAUGE_TTL_SECONDS:
            return
    _LAST_EPISODIC_UPDATE[label] = now
    episodic_rows.labels(pair=label).set(float(row_count))


# ---------------------------------------------------------------------------
# Q-Table size — gauge set + soft-warn log.
# ---------------------------------------------------------------------------


def _qtable_size_bytes(table_data: dict[str, Any]) -> int:
    """Return the size of ``table_data`` as a UTF-8-encoded JSON blob.

    The repository persists ``table_data`` as JSONB; the Postgres-side
    storage is more compact than the wire-format JSON, but the JSON
    encoding is the canonical "logical" size and is cheap to compute.
    """
    return len(json.dumps(table_data, separators=(",", ":")).encode("utf-8"))


def record_qtable_bytes(
    pair_id: uuid.UUID | str,
    table_data: dict[str, Any],
) -> int:
    """Update :data:`qtable_bytes` and warn-log on threshold breach.

    Returns the computed byte count so the caller can use it (e.g. to
    stamp the q_tables metadata payload or log the figure alongside
    other write outcomes).
    """
    label = _stringify_pair(pair_id)
    size = _qtable_size_bytes(table_data)
    qtable_bytes.labels(pair=label).set(float(size))

    threshold = get_settings().learning_qtable_warn_bytes
    if size > threshold:
        # Structured form: stdlib ``logging`` carries the structured
        # fields as ``extra``. The codebase wraps stdlib with structlog
        # at the top level, so this lands in the JSON sink with the
        # named fields preserved.
        logger.warning(
            QTABLE_SIZE_LOG_KEY,
            extra={
                "pair_id": label,
                "bytes": size,
                "threshold": threshold,
            },
        )
    return size


# ---------------------------------------------------------------------------
# Q-Table promotion counter — incremented from finalize_learning_step.
# ---------------------------------------------------------------------------


_VALID_RISK_CLASSES = frozenset({"bajo", "medio", "alto"})


def increment_q_table_promotion(
    pair_id: uuid.UUID | str,
    risk_class: str,
) -> None:
    """Bump :data:`q_table_promotions_total` for ``(pair, risk_class)``.

    Defensive on the label values — ``risk_class`` outside the closed
    enum is normalised to ``unknown`` so a typo in the caller can't
    silently explode cardinality on the Prometheus side.
    """
    label_pair = _stringify_pair(pair_id)
    if risk_class not in _VALID_RISK_CLASSES:
        risk_class = "unknown"
    q_table_promotions_total.labels(
        pair=label_pair,
        risk_class=risk_class,
    ).inc()


# ---------------------------------------------------------------------------
# Test helpers — Prometheus state is process-global, so tests need a reset.
# ---------------------------------------------------------------------------


def reset_for_test() -> None:
    """Drop all label samples + clear the per-project update cache.

    Only safe to call from the test suite — production callers should
    never reach into the collectors directly.
    """
    q_table_promotions_total.clear()
    episodic_rows.clear()
    qtable_bytes.clear()
    _LAST_EPISODIC_UPDATE.clear()
