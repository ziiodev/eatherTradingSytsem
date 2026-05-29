"""Q-Table delta risk classifier.

This module owns the **policy-implication risk class** for a proposed
Q-Table promotion. It complements :func:`aether_api.sleep.classifier.classify_changes`
(which classifies *config* deltas like risk caps and per-agent params)
by analysing what the new Q-values would imply if the Worker followed
them as a greedy policy.

Algorithm (per design #2070, classifier section — TOP-K walk +
magnitude fallback):

1. **TOP-K walk** — for each ``(state_key, freq)`` in ``top_k_states``
   (descending frequency, supplied by
   :meth:`EpisodicMemoryRepository.top_k_states`):

   a. Take the argmax action of the new Q-Table at that state — that is
      the action the Worker would pick under a greedy policy.
   b. Map the action to an implied **size** (lots) and **risk per trade**
      (% equity) via the heuristic in :func:`_action_implications`.
   c. If the implied size exceeds ``project.max_exposure`` OR the implied
      ``risk_per_trade`` exceeds ``2 × project.risk_per_trade`` → return
      ``"alto"`` immediately (short-circuit).

2. **Magnitude fallback** — if the TOP-K walk found no violation (or
   ``top_k_states`` is empty / cold-start), measure the average
   normalised absolute delta over **overlapping (state, action) cells**:

       avg = mean( |q_new − q_old| / max(|q_old|, 1e-6) )

   Thresholds:

   * ``avg < 0.10`` → ``"bajo"``
   * ``avg < 0.30`` → ``"medio"``
   * else            → ``"alto"``

   If ``new_table`` is empty (no cells at all) the function returns
   ``"bajo"`` — there is no policy implication to escalate.

3. The result is merged with the rest of the sleep-phase classifier
   output via a max-severity rule (``alto > medio > bajo``) — that
   composition happens in :func:`aether_api.sleep.classifier.classify_changes`,
   not here.

Action → size mapping
---------------------

The Worker's action vocabulary is not yet pinned by a typed contract
(it lands in :class:`EpisodicMemory.action` as a free-form ``VARCHAR(60)``
string). For v1 we use a **conservative keyword heuristic** that errs on
the side of caution:

* ``"close"`` / ``"flat"`` / ``"hold"`` — implied size = 0 lots, risk = 0.
  Safe — closing or holding a position never breaches an exposure cap.
* ``"open_long"`` / ``"open_short"`` / ``"buy"`` / ``"sell"`` — implied
  size = ``project.lot_size_default`` if present, else 1.0 lot; implied
  ``risk_per_trade`` = the project's configured ``risk_per_trade`` (the
  current floor; an action that the Worker frames as a normal entry is
  assumed to be sized at the project's nominal risk budget).
* Anything else — falls into the default ``open_*``-shaped bucket so an
  unknown action is treated as a normal entry, NOT auto-escalated. A
  future change SHOULD widen the parse once the action vocabulary is
  pinned down by a contract.

**Flagged as heuristic v1** — refine after live data accumulates. The
moment the Worker spec pins action names down to a typed enum, replace
this parser with a lookup table and remove the keyword guesswork. See
the canonical sleep-learning spec (engram #2069) for the broader
contract.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

#: Risk class labels — same vocabulary as :mod:`aether_api.sleep.classifier`.
#: Kept as a ``Literal`` (not an enum) so JSON round-trips and existing
#: ``str`` consumers keep working without conversion. Defined via the
#: ``type`` statement (PEP 695, Python 3.12) so it is a proper lazy
#: alias rather than an explicit ``TypeAlias`` annotation.
type RiskClass = Literal["bajo", "medio", "alto"]

#: Default lot size used when the project row does not pin one. The
#: project model (``models/project.py``) does NOT currently carry a
#: ``lot_size_default`` column — the lookup in :func:`_action_implications`
#: falls back to this constant. Update both sites if the column is added.
_DEFAULT_LOT_SIZE: float = 1.0

#: Floor used in the magnitude denominator to avoid division by zero on
#: ``old_q == 0`` cells. Matches the design note ("max(|old|, 1e-6)").
_MAGNITUDE_FLOOR: float = 1e-6

#: Risk bracket thresholds for the magnitude fallback. Mirror the design.
_MAGNITUDE_BAJO_THRESHOLD: float = 0.10
_MAGNITUDE_MEDIO_THRESHOLD: float = 0.30

#: Risk ordering for max-severity composition (re-exported so callers in
#: the sleep classifier can compose without re-deriving).
_RISK_ORDER: tuple[RiskClass, ...] = ("bajo", "medio", "alto")


__all__ = ["RiskClass", "classify_qtable_delta", "worst_risk"]


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------
def worst_risk(a: RiskClass, b: RiskClass) -> RiskClass:
    """Return the higher of two :data:`RiskClass` labels (max-severity).

    Helper exposed so :mod:`aether_api.sleep.classifier` (and any other
    composition site) can merge results without re-implementing the
    ordering table.
    """
    return a if _RISK_ORDER.index(a) >= _RISK_ORDER.index(b) else b


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _as_float(value: Any) -> float | None:
    """Best-effort coercion of a Q-Table cell value to ``float``.

    Returns ``None`` for non-numeric input (the cell is then skipped by
    the magnitude walk). ``bool`` is rejected because it would otherwise
    silently classify as 0/1 (Python ``bool`` is an int subclass).
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, Decimal):
        try:
            return float(value)
        except (ValueError, ArithmeticError):
            return None
    return None


def _project_attr(project: Any, name: str, default: float) -> float:
    """Read ``project.{name}`` and coerce to ``float``.

    Tolerant of:

    * Missing attribute → ``default``.
    * ``None`` value → ``default`` (the project model leaves several
      risk fields nullable; the classifier MUST NOT crash on a freshly
      created project with no overrides).
    * ``Decimal`` value → ``float`` (risk caps are stored as Numeric).
    """
    value = getattr(project, name, None)
    if value is None:
        return default
    if isinstance(value, Decimal):
        try:
            return float(value)
        except (ValueError, ArithmeticError):
            return default
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return default


def _action_implications(action: str, project: Any) -> tuple[float, float]:
    """Map an action string to ``(implied_size, implied_risk_per_trade)``.

    Heuristic v1 — see module docstring. Returns floats so the caller
    can compare against ``project.max_exposure`` / ``project.risk_per_trade``
    without re-coercing.

    * Closing / flattening / holding → ``(0.0, 0.0)``. Safe.
    * Opening (long / short / buy / sell / unknown) → ``(lot_size, risk_per_trade)``
      where ``lot_size`` defaults to :data:`_DEFAULT_LOT_SIZE` if the
      project does not pin one, and ``risk_per_trade`` is the project's
      configured per-trade risk (a normal entry is sized at the nominal
      budget).
    """
    lowered = action.lower()
    if any(keyword in lowered for keyword in ("close", "flat", "hold")):
        return 0.0, 0.0

    lot_size = _project_attr(project, "lot_size_default", _DEFAULT_LOT_SIZE)
    # ``risk_per_trade`` is the project's per-trade-risk floor (% equity).
    # Default 1.0 % per CHARTER.
    risk_per_trade = _project_attr(project, "risk_per_trade", 1.0)
    return lot_size, risk_per_trade


def _argmax_action(cell: dict[str, Any]) -> str | None:
    """Return the action with the highest Q-value in ``cell``.

    Ties break alphabetically (deterministic — matches the
    ``top_k_states`` repository's tie-break rule on state_key). Returns
    ``None`` if the cell has no numeric entries.
    """
    best: tuple[float, str] | None = None
    for action, q in cell.items():
        q_float = _as_float(q)
        if q_float is None:
            continue
        # Tie-break: lexicographically smaller action wins, so a
        # higher tuple key beats it on q (max ranks by q then by name).
        # We negate the alphabetical ordering by tracking a key that
        # prefers larger q and, on tie, smaller action string.
        if best is None:
            best = (q_float, action)
            continue
        if q_float > best[0] or (q_float == best[0] and action < best[1]):
            best = (q_float, action)
    return best[1] if best else None


def _magnitude_class(
    old_table: dict[str, dict[str, Any]],
    new_table: dict[str, dict[str, Any]],
) -> RiskClass:
    """Average normalised absolute delta over overlapping (state, action) cells.

    Returns ``"bajo"`` for ``avg < 0.10``, ``"medio"`` for ``avg < 0.30``,
    else ``"alto"``. If no overlapping cells exist (e.g. a fresh table),
    returns ``"bajo"`` — there is nothing to compare, so no policy
    implication to escalate.
    """
    total = 0.0
    count = 0
    for state_key, new_cell in new_table.items():
        if not isinstance(new_cell, dict):
            continue
        old_cell = old_table.get(state_key)
        if not isinstance(old_cell, dict):
            continue
        for action, new_q in new_cell.items():
            if action not in old_cell:
                continue
            old_q_f = _as_float(old_cell[action])
            new_q_f = _as_float(new_q)
            if old_q_f is None or new_q_f is None:
                continue
            denom = max(abs(old_q_f), _MAGNITUDE_FLOOR)
            total += abs(new_q_f - old_q_f) / denom
            count += 1

    if count == 0:
        return "bajo"

    avg = total / count
    if avg < _MAGNITUDE_BAJO_THRESHOLD:
        return "bajo"
    if avg < _MAGNITUDE_MEDIO_THRESHOLD:
        return "medio"
    return "alto"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def classify_qtable_delta(
    *,
    old_table: dict[str, Any],
    new_table: dict[str, Any],
    project: Any,
    top_k_states: list[tuple[str, int]],
) -> RiskClass:
    """Classify a proposed Q-Table promotion as ``bajo`` / ``medio`` / ``alto``.

    Parameters
    ----------
    old_table
        The currently-promoted Q-Table ``{state_key: {action: q_value}}``.
        May be empty (cold start — no prior policy to compare against).
    new_table
        The candidate Q-Table to promote. Same shape as ``old_table``. If
        empty the function short-circuits to ``"bajo"`` (no change to
        apply).
    project
        The :class:`Project` row (or any object exposing ``max_exposure``,
        ``risk_per_trade`` and optionally ``lot_size_default``). Read-only;
        the classifier never mutates the project.
    top_k_states
        ``[(state_key, freq), ...]`` ordered by frequency descending,
        sourced from :meth:`EpisodicMemoryRepository.top_k_states`. Empty
        list means cold start (no episodic history) — the classifier
        skips the TOP-K walk and goes straight to the magnitude
        fallback.

    Returns
    -------
    :data:`RiskClass`
        ``"bajo"`` / ``"medio"`` / ``"alto"`` — composable via
        :func:`worst_risk` with the rest of the sleep-phase classifier.

    Notes
    -----
    * The classifier is pure (no I/O); it does not touch the database
      and never mutates its arguments.
    * Heuristic ``_action_implications`` is v1 — see the module
      docstring. Refine the mapping once the Worker's action vocabulary
      is pinned by a typed contract.
    """
    # Early-out: empty proposal = no change.
    if not isinstance(new_table, dict) or not new_table:
        return "bajo"

    max_exposure = _project_attr(project, "max_exposure", 10.0)
    project_risk_per_trade = _project_attr(project, "risk_per_trade", 1.0)
    risk_budget_alto_threshold = 2.0 * project_risk_per_trade

    # ---- Step 1: TOP-K walk (short-circuits on the first breach). ----
    for state_key, _freq in top_k_states:
        cell = new_table.get(state_key)
        if not isinstance(cell, dict):
            continue
        best_action = _argmax_action(cell)
        if best_action is None:
            continue
        implied_size, implied_risk = _action_implications(best_action, project)
        if implied_size > max_exposure:
            return "alto"
        if implied_risk > risk_budget_alto_threshold:
            return "alto"

    # ---- Step 2: magnitude fallback. ----
    old_normalised: dict[str, dict[str, Any]] = old_table if isinstance(old_table, dict) else {}
    return _magnitude_class(old_normalised, new_table)
