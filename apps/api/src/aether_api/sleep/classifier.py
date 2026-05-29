"""Pure-Python risk classifier for proposed config deltas.

Design constraint: zero IO, zero DB, zero settings imports. Callable
from any layer; testable as a unit without fixtures.

Rules (per :file:`sdd/sleep-phase/design`):

1. Touching ANY risk cap (``risk_per_trade``, ``max_daily_dd``,
   ``max_total_dd``, ``max_exposure``) → ``alto``.
2. Touching ``trading_sessions`` or ``agents.logica`` (passed in as the
   field ``logica`` on any *_params bucket) → ``alto``.
3. Touching an *unknown* top-level field (i.e. not in the known sets
   below) → ``alto`` (conservative default — operators see + approve
   before novel deltas land).
4. Numeric delta of ≤ ±10 % on a known parameter → ``bajo``.
5. Numeric delta of ≤ ±30 % on a known parameter → ``medio``.
6. Anything else (numeric delta > ±30 %, structural non-numeric change,
   etc.) → ``alto``.

The classifier returns the worst class across all changes — Sleep Phase
v1 packages every delta into a single ConfigVersion row, and human
approval is per-row.

**Q-Table extension** (``sleep-learning-loop``, design #2070): when the
caller passes ``qtable_delta`` (the ``{"old": ..., "new": ...}`` pair
for a candidate Q-Table promotion), the classifier delegates to
:func:`aether_api.learning.classify_qtable_delta` and folds its risk
class into the result via the same ``alto > medio > bajo`` max-severity
rule. Callers that don't propose a Q-Table change leave ``qtable_delta``
as ``None`` — the existing behaviour is unchanged.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Final

from aether_api.learning.qtable_versioning import (
    classify_qtable_delta as _classify_qtable_delta,
)

#: Risk caps — any touch goes straight to ``alto`` per CHARTER.
_RISK_CAP_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "risk_per_trade",
        "max_daily_dd",
        "max_total_dd",
        "max_exposure",
    }
)

#: Top-level fields the classifier knows about. Anything outside this
#: set is treated as unknown and classified ``alto`` so an op gets a
#: human gate.
_KNOWN_TOP_LEVEL_FIELDS: Final[frozenset[str]] = frozenset(
    {
        # Risk caps
        "risk_per_trade",
        "max_daily_dd",
        "max_total_dd",
        "max_exposure",
        # Schedule / ventanas operativas
        "trading_sessions",
        # Per-agent params buckets (free JSONB). The keys inside are
        # numeric or string; the same numeric-bracket rules apply
        # element-wise (handled in :func:`_classify_value`).
        "worker_params",
        "investigator_params",
        "auditor_params",
        # Free text — only relevant as metadata, not policy.
        "notes",
        "tags",
        "strategy_description",
        "base_logic",
        # The agent ``logica`` field is the hard "alto" trigger; surfaced
        # by the orchestrator as a synthetic top-level key.
        "logica",
    }
)

#: Risk ordering for "worst across deltas".
_RISK_ORDER: Final[tuple[str, ...]] = ("bajo", "medio", "alto")


def _worst(a: str, b: str) -> str:
    """Return the higher of two risk classes."""
    return a if _RISK_ORDER.index(a) >= _RISK_ORDER.index(b) else b


def _as_number(value: Any) -> Decimal | None:
    """Return ``value`` as a Decimal if numeric, else None."""
    if isinstance(value, bool):
        # bool is an int subclass — guard so True/False don't sneak in.
        return None
    if isinstance(value, (int, float, Decimal)):
        try:
            return Decimal(str(value))
        except Exception:  # noqa: BLE001
            return None
    return None


def _numeric_pct_class(*, current: Decimal, proposed: Decimal) -> str:
    """Classify a numeric delta by absolute percentage change.

    Returns ``"bajo"`` for ≤10 %, ``"medio"`` for ≤30 %, ``"alto"`` otherwise.
    Anchors on ``current``; a change against a zero anchor with non-zero
    proposed is treated as ``alto`` (infinite percentage).
    """
    if current == 0:
        # 0 → 0 is no change; 0 → anything is "alto" (∞%).
        return "bajo" if proposed == 0 else "alto"
    delta_pct = (abs(proposed - current) / abs(current)) * Decimal(100)
    if delta_pct <= Decimal(10):
        return "bajo"
    if delta_pct <= Decimal(30):
        return "medio"
    return "alto"


def _classify_value(field: str, current: Any, proposed: Any) -> str:
    """Classify a single field change.

    Rules:

    * Risk cap field → ``alto`` regardless of magnitude.
    * Same shape, both numeric → percentage bracket.
    * Anything non-numeric / shape change → ``alto`` (structural).
    """
    if field in _RISK_CAP_FIELDS:
        return "alto"

    current_num = _as_number(current)
    proposed_num = _as_number(proposed)
    if current_num is not None and proposed_num is not None:
        return _numeric_pct_class(current=current_num, proposed=proposed_num)

    # Both lists (e.g. trading_sessions) — any reordering / add / remove
    # touches the policy surface; pass through to the caller's rule
    # 2 path. Same for dicts (per-agent params).
    return "alto"


def classify_changes(
    *,
    current: dict[str, Any],
    proposed: dict[str, Any],
    qtable_delta: dict[str, Any] | None = None,
    project: Any | None = None,
    top_k_states: list[tuple[str, int]] | None = None,
) -> str:
    """Classify a proposed snapshot against the current snapshot.

    Returns the worst risk class across every changed key. Touches the
    Decimal path for numeric brackets so SQLAlchemy ``Numeric`` columns
    don't lose precision.

    Both ``current`` and ``proposed`` are JSON-like dicts (the same
    shape as the row stored in ``config_versions.snapshot``).

    Q-Table extension
    -----------------

    ``qtable_delta`` is an optional mapping ``{"old": dict, "new": dict}``
    describing a candidate Q-Table promotion. When supplied, the
    classifier also runs
    :func:`aether_api.learning.classify_qtable_delta` over the pair and
    folds its result into the final risk class via the same max-severity
    rule (``alto > medio > bajo``). The Q-Table branch requires both
    ``project`` (so the risk caps can be read) and ``top_k_states`` (the
    classifier's TOP-K walk input); if either is missing the Q-Table
    delta is **ignored** — never silently substitute a default that might
    paper over a real escalation. Existing callers that don't propose a
    Q-Table change leave the three new arguments as ``None`` and observe
    no behavioural change.
    """
    worst = "bajo"

    # Union of keys — any add / remove counts as a change.
    keys = set(current.keys()) | set(proposed.keys())

    for key in keys:
        was = current.get(key)
        now = proposed.get(key)

        if was == now:
            continue

        # Unknown top-level field → alto. This is rule 3: we want a human
        # gate the first time a new field shows up in a snapshot.
        if key not in _KNOWN_TOP_LEVEL_FIELDS:
            return "alto"

        # Trading sessions touch → alto (rule 2).
        if key == "trading_sessions":
            return "alto"

        # Per-agent ``logica`` change (orchestrator synthesises this
        # key when an agent reflection proposes a code-level edit).
        if key == "logica":
            return "alto"

        # JSONB params buckets — recurse element-wise. Risk-cap-shaped
        # keys carried inside a params bucket retain their bracket; the
        # bucket itself is just a passthrough.
        if key in {"worker_params", "investigator_params", "auditor_params"}:
            was_dict = was if isinstance(was, dict) else {}
            now_dict = now if isinstance(now, dict) else {}
            inner_keys = set(was_dict.keys()) | set(now_dict.keys())
            for inner in inner_keys:
                inner_was = was_dict.get(inner)
                inner_now = now_dict.get(inner)
                if inner_was == inner_now:
                    continue
                # Heuristic: if the inner key name matches a risk-cap name,
                # treat it as alto regardless.
                if inner in _RISK_CAP_FIELDS:
                    worst = _worst(worst, "alto")
                    continue
                worst = _worst(worst, _classify_value(inner, inner_was, inner_now))
                if worst == "alto":
                    return worst
            continue

        # Risk-cap or other known scalar.
        worst = _worst(worst, _classify_value(key, was, now))
        if worst == "alto":
            return worst

    # Q-Table delta — only consulted when the caller supplied both the
    # delta and the supporting inputs (project + top_k_states). We do
    # NOT fabricate defaults here: a missing project would make a "size
    # > max_exposure" check meaningless, and an absent top-k list is the
    # cold-start signal the learning classifier already knows how to
    # interpret (empty list → magnitude-only path). Existing callers
    # that don't promote a Q-Table simply pass ``qtable_delta=None``.
    if qtable_delta is not None and project is not None:
        old_table = qtable_delta.get("old") or {}
        new_table = qtable_delta.get("new") or {}
        qt_class = _classify_qtable_delta(
            old_table=old_table,
            new_table=new_table,
            project=project,
            top_k_states=top_k_states or [],
        )
        worst = _worst(worst, qt_class)

    return worst


__all__ = ["classify_changes"]
