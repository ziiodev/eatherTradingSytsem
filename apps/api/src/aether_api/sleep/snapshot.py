"""Project-snapshot helpers — serialise and apply the mutable surface.

The ``config_versions.snapshot`` column stores the SAME set of fields
the Sleep Phase classifier inspects: risk caps, trading sessions, the
three per-agent ``*_params`` JSONBs, and any meta-only fields that
operators may tune (notes / tags / strategy descriptions).

Why a single helper instead of inlining in the orchestrator: the
applier (POST /api/config-versions/{id}/approve) and the revert path
share the same projection. A divergence between "what we store" and
"what we apply" is a class of bug we'd rather rule out by construction.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Final

from aether_api.models.pair import Pair

#: Fields the Sleep Phase considers part of a snapshot. Order
#: irrelevant — comparison is by dict equality, not list equality.
SNAPSHOT_FIELDS: Final[tuple[str, ...]] = (
    # Risk caps
    "risk_per_trade",
    "max_daily_dd",
    "max_total_dd",
    "max_exposure",
    # Schedule / ventanas operativas
    "trading_sessions",
    # Per-agent params buckets
    "worker_params",
    "investigator_params",
    "auditor_params",
    # Free metadata
    "notes",
    "tags",
    "strategy_description",
    "base_logic",
)


def _coerce(value: Any) -> Any:
    """Make the value JSON-safe (Decimal → str, set → list)."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_coerce(item) for item in value]
    if isinstance(value, dict):
        return {key: _coerce(val) for key, val in value.items()}
    return value


def take_snapshot(pair: Pair) -> dict[str, Any]:
    """Project the current row into a JSON-safe snapshot dict.

    Decimal columns are emitted as strings so the JSONB round-trip
    doesn't quietly lose precision (PostgreSQL JSONB stores numbers as
    arbitrary-precision but Python ``float`` cannot).
    """
    return {field: _coerce(getattr(pair, field, None)) for field in SNAPSHOT_FIELDS}


def diff_keys(
    *, current: dict[str, Any], proposed: dict[str, Any]
) -> list[str]:
    """Return the set of keys that differ between two snapshots."""
    keys = set(current.keys()) | set(proposed.keys())
    return sorted(k for k in keys if current.get(k) != proposed.get(k))


def _decode(field: str, value: Any) -> Any:
    """Decode a snapshot value back to the type Pair expects.

    Only Decimal columns need the round-trip — everything else is plain
    JSON.
    """
    if value is None:
        return None
    if field in {
        "risk_per_trade",
        "max_daily_dd",
        "max_total_dd",
        "max_exposure",
    }:
        if isinstance(value, Decimal):
            return value
        try:
            return Decimal(str(value))
        except Exception:  # noqa: BLE001
            return None
    return value


def apply_snapshot_to_project(pair: Pair, snapshot: dict[str, Any]) -> None:
    """Mutate ``pair`` in-place so its mutable surface matches ``snapshot``.

    Caller is responsible for flushing the session. We deliberately do
    NOT touch any field that isn't in :data:`SNAPSHOT_FIELDS` — status,
    container_id, account_*, etc. are out of scope for the Sleep Phase.
    """
    for field in SNAPSHOT_FIELDS:
        if field not in snapshot:
            # Snapshot from an older schema — leave the column untouched.
            continue
        setattr(pair, field, _decode(field, snapshot[field]))


__all__ = [
    "SNAPSHOT_FIELDS",
    "apply_snapshot_to_project",
    "diff_keys",
    "take_snapshot",
]
