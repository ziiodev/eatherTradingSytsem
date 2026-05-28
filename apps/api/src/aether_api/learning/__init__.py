"""Learning substrate — pure math + persistence helpers for sleep-phase RL.

This package owns the **mathematical contract** the sleep-phase
orchestrator relies on:

* :func:`q_update` — the canonical Bellman update (pure, deterministic).
* :func:`state_key` — SHA-256 over canonical JSON; the same hash is used
  both as the JSONB key in ``q_tables.table`` and as the
  ``state_key`` / ``next_state_key`` column on ``episodic_memory``.

Repositories, the in-process cache, and the boot-time
:class:`RecoveryLoader` land in later phases. Only pure primitives and
the typed exception hierarchy live in this module today.

The public surface intentionally re-exports a small, frozen set of
symbols; importing anything from a sub-module directly is allowed but
discouraged outside this package.
"""

from aether_api.learning.exceptions import (
    LearningError,
    QUpdateError,
    RecoveryError,
    StateKeyError,
)
from aether_api.learning.q_learning import q_update, state_key

__all__ = [
    "LearningError",
    "QUpdateError",
    "RecoveryError",
    "StateKeyError",
    "q_update",
    "state_key",
]
