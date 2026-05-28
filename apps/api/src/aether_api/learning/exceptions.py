"""Typed exceptions for the learning substrate.

Every public function in :mod:`aether_api.learning` raises one of these
on invalid input. The sleep-phase orchestrator (the only consumer that
runs them in a hot path) catches :class:`LearningError` to escalate to
the human operator, so callers MUST NOT swallow these silently — they
indicate a contract violation by upstream code, not a transient failure.

The hierarchy mirrors the spec ``specs/sleep-learning`` (engram #2069):

* :class:`QUpdateError`  → :func:`aether_api.learning.q_update`
* :class:`StateKeyError` → :func:`aether_api.learning.state_key`
* :class:`RecoveryError` → :class:`aether_api.learning.RecoveryLoader`
  (lifespan hook; wired in a later phase).

All three inherit from :class:`LearningError` so callers can catch the
domain umbrella when a finer-grained split adds no value.
"""

from __future__ import annotations


class LearningError(Exception):
    """Base class for every learning-module exception.

    The sleep orchestrator catches this umbrella to surface a single
    structured failure to the user; subclasses exist so unit tests and
    log lines can distinguish the failure mode.
    """


class QUpdateError(LearningError):
    """:func:`q_update` rejected an input.

    Raised when:

    * ``alpha`` is outside ``[0.0, 1.0]`` (inclusive bounds).
    * ``gamma`` is outside ``[0.0, 1.0]`` (inclusive bounds).
    * Any numeric argument is ``NaN`` or ``±Inf``.

    The message MUST name the offending argument so the orchestrator
    can attribute the failure in its log line; carrying the value is
    optional but recommended.
    """


class StateKeyError(LearningError):
    """:func:`state_key` rejected an input.

    Raised when the input mapping contains:

    * A non-string key (the canonical JSON form requires string keys).
    * An unsupported value type (``set``, ``tuple``, custom objects…)
      — JSON cannot represent it without lossy coercion, and lossy
      coercion would break determinism.
    * A non-finite float (``NaN`` / ``±Inf``) anywhere in the tree.
    """


class RecoveryError(LearningError):
    """The container-boot recovery loader failed to rebuild project state.

    Raised by :class:`RecoveryLoader` (wired in a later phase) when a
    project's persisted Q-Table / episodic / semantic rows cannot be
    materialised back into the in-process cache. Surfacing the error
    halts auto-wake for that project — never silently start trading
    with stale or partial state.
    """
