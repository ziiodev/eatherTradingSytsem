"""Pure Q-learning primitives.

Two functions live here:

* :func:`q_update` — the canonical Bellman update.
* :func:`state_key` — the canonical state-hashing contract used both as
  the JSONB key in :class:`aether_api.models.q_table.QTable` and as the
  ``state_key``/``next_state_key`` column on ``episodic_memory``.

Both are **pure**: no I/O, no globals, no clock, no random. They are the
only mathematical contract the sleep-learning loop relies on, so the
spec (engram #2069) pins their behaviour down to the byte. Changing
either of them silently breaks every persisted Q-Table — never widen
the surface or change a tie-breaking rule without bumping the spec.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from aether_api.learning.exceptions import QUpdateError, StateKeyError

__all__ = ["q_update", "state_key"]


# ---------------------------------------------------------------------------
# Q-update — canonical Bellman step.
# ---------------------------------------------------------------------------
def q_update(
    q_value: float,
    reward: float,
    max_next_q: float,
    alpha: float,
    gamma: float = 0.92,
) -> float:
    """Apply one Q-learning update and return ``Q(s, a)`` after the step.

    The formula is::

        Q' = Q + α · (r + γ · max_a' Q(s', a') − Q)

    Arguments
    ---------
    q_value
        ``Q(s, a)`` before the update. Must be a finite float.
    reward
        Reward received for taking action ``a`` in state ``s``. In this
        system it is the percentage of equity gained or lost on the
        trade, *net of costs* (see the canonical spec). Must be finite.
    max_next_q
        ``max_a' Q(s', a')``. For terminal transitions (trade fully
        closed and the agent will not act again from ``s'``), pass
        ``0.0``. Must be finite.
    alpha
        Learning rate ∈ ``[0.0, 1.0]``. The canonical band is
        ``[0.15, 0.35]`` (``alpha_normal`` vs ``alpha_special`` on the
        ``q_tables`` row) — values outside that band are legal but
        will trigger the risk classifier later in the pipeline.
    gamma
        Discount factor ∈ ``[0.0, 1.0]``. Defaults to the canonical
        ``0.92``; the sleep orchestrator overrides only on the basis of
        a tuned ``q_tables.gamma`` column.

    Raises
    ------
    QUpdateError
        If any numeric argument is non-finite (``NaN`` / ``±Inf``), or
        if ``alpha`` / ``gamma`` is outside ``[0.0, 1.0]``.

    Returns
    -------
    float
        The updated ``Q(s, a)``. May exceed the ``[0, 1]`` band — Q
        values are unbounded; the caller (or the classifier) is
        responsible for clipping/escalating policy implications.
    """
    # Validate finiteness first — bounds checks on NaN are nonsensical.
    if not math.isfinite(q_value):
        raise QUpdateError(f"q_value must be finite, got {q_value!r}")
    if not math.isfinite(reward):
        raise QUpdateError(f"reward must be finite, got {reward!r}")
    if not math.isfinite(max_next_q):
        raise QUpdateError(f"max_next_q must be finite, got {max_next_q!r}")
    if not math.isfinite(alpha):
        raise QUpdateError(f"alpha must be finite, got {alpha!r}")
    if not math.isfinite(gamma):
        raise QUpdateError(f"gamma must be finite, got {gamma!r}")

    # Bounds — inclusive at both ends so α=0 ("don't learn") and α=1
    # ("full replacement") remain legal, matching the textbook formula.
    if not (0.0 <= alpha <= 1.0):
        raise QUpdateError(f"alpha must be in [0.0, 1.0], got {alpha!r}")
    if not (0.0 <= gamma <= 1.0):
        raise QUpdateError(f"gamma must be in [0.0, 1.0], got {gamma!r}")

    target = reward + gamma * max_next_q
    return q_value + alpha * (target - q_value)


# ---------------------------------------------------------------------------
# State-key canonicalisation.
# ---------------------------------------------------------------------------
# The allowed leaf set is intentionally narrow — every type accepted by
# :func:`_validate_value` has a one-to-one JSON representation, so the
# resulting hash is stable across Python versions:
#
#   str | bool | int | float (finite) | None | list | dict
#
# Anything else (set, tuple, bytes, custom objects, NaN, Inf) is
# rejected before serialisation so the canonical form can never drift.


def _validate_value(value: Any) -> None:
    """Recursively ensure *value* is JSON-canonicalisable and finite.

    The traversal mirrors how :func:`json.dumps` would walk the object,
    but raises :class:`StateKeyError` before any serialisation runs so
    the error message can name the offending type/value.
    """
    # bool is a subclass of int in Python — check it before int branches.
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise StateKeyError(f"float values must be finite, got {value!r}")
        return
    if isinstance(value, str):
        return
    if isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str):
                raise StateKeyError(
                    f"dict keys must be str for canonical JSON, got {type(k).__name__}: {k!r}"
                )
            _validate_value(v)
        return
    if isinstance(value, list):
        for item in value:
            _validate_value(item)
        return
    # Catch-all — set, tuple, frozenset, bytes, custom objects, etc.
    raise StateKeyError(
        f"unsupported value type for canonical state: {type(value).__name__} ({value!r})"
    )


def state_key(state: dict[str, Any]) -> str:
    """Return the canonical SHA-256 hex digest of *state*.

    The contract is:

    1. *state* MUST be a ``dict`` whose keys are strings and whose
       values are recursively composed of strings, bools, ints, finite
       floats, ``None``, lists, and nested dicts. Anything else (set,
       tuple, bytes, custom objects, NaN, Inf) raises
       :class:`StateKeyError`.
    2. The mapping is serialised with ``json.dumps(..., sort_keys=True,
       separators=(",", ":"))`` so ``{"a": 1, "b": 2}`` and
       ``{"b": 2, "a": 1}`` hash identically. Whitespace is suppressed
       so the canonical form is byte-stable across Python builds.
    3. The hex digest of SHA-256 over the UTF-8 encoded canonical JSON
       is returned. The digest is 64 hex chars; the caller stores it
       verbatim in the ``state_key`` column (VARCHAR(120)).

    Pure: no I/O, no globals, deterministic on identical input.
    """
    if not isinstance(state, dict):
        raise StateKeyError(f"state must be a dict, got {type(state).__name__}: {state!r}")

    _validate_value(state)

    # ``allow_nan=False`` is defence-in-depth: we already validated the
    # tree above, so this path is only ever reached on valid input.
    canonical = json.dumps(state, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
