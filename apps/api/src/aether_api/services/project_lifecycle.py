"""Project lifecycle state machine — canonical source of truth.

Co-owned with the future ``project-docker-orchestration`` change, but THIS
change (`projects-crud`) is the canonical owner of:

* the status set (``PROJECT_STATUSES``)
* the allowed transitions matrix (``VALID_TRANSITIONS``)
* the predicate / assertion API (``can_transition`` / ``assert_transition``)

Downstream consumers (the Docker orchestrator, the agent scheduler, the
sleep-phase reconciler) MAY read this module to react to status changes;
they MUST NOT redefine the state set or transitions without a SDD delta
against ``sdd/projects-crud/spec``.

Design properties (intentional):

* Pure-Python — no DB imports, no SQLAlchemy import. Importable from any
  layer (router, repository, worker, test).
* Transitions are an explicit ``dict[Status, frozenset[Status]]`` — easy to
  audit by reading the matrix below.
* All errors raise :class:`InvalidTransition` with both endpoints in the
  message so log entries pinpoint the exact bad edge.

Transition matrix (rows = FROM, cols = TO; ``X`` = allowed):

::

                 inactive  active  paused  stopped  error  maintenance
    inactive       -        X       -       -        -      X
    active         -        -       X       X        X      X
    paused         -        X       -       X        -      -
    stopped        X        X       -       -        -      -
    error          -        -       -       X        -      X
    maintenance    X        X       -       -        -      -

Notes:
- ``inactive`` is the DDL default. New rows land here; only ``active`` or
  ``maintenance`` are reachable directly.
- ``stopped`` is the "deletable" sink (with ``inactive``); the router's
  DELETE handler enforces that.
- ``error`` cannot self-recover to ``active`` directly — operators must
  drive it through ``stopped`` or ``maintenance``.
"""

from __future__ import annotations

from typing import Final, Literal

Status = Literal["inactive", "active", "paused", "stopped", "error", "maintenance"]

#: Canonical, ordered tuple of every allowed project status.
PROJECT_STATUSES: Final[tuple[Status, ...]] = (
    "inactive",
    "active",
    "paused",
    "stopped",
    "error",
    "maintenance",
)

#: Statuses from which a project may be hard-deleted.
DELETABLE_STATUSES: Final[frozenset[Status]] = frozenset({"inactive", "stopped"})

#: FROM → set of allowed TO statuses. Mirrors the table in the module docstring.
VALID_TRANSITIONS: Final[dict[Status, frozenset[Status]]] = {
    "inactive": frozenset({"active", "maintenance"}),
    "active": frozenset({"paused", "stopped", "error", "maintenance"}),
    "paused": frozenset({"active", "stopped"}),
    "stopped": frozenset({"inactive", "active"}),
    "error": frozenset({"stopped", "maintenance"}),
    "maintenance": frozenset({"inactive", "active"}),
}


class InvalidTransition(ValueError):
    """Raised when a transition is not in :data:`VALID_TRANSITIONS`.

    Carries both endpoints on the instance so callers can surface them in
    HTTP error payloads without re-parsing the message.
    """

    def __init__(self, from_status: str, to_status: str) -> None:
        self.from_status = from_status
        self.to_status = to_status
        super().__init__(
            f"invalid project status transition: {from_status!r} -> {to_status!r}"
        )


def _coerce(status: str) -> Status:
    """Validate that ``status`` is a known status string. Raises ValueError otherwise."""
    if status not in PROJECT_STATUSES:
        raise ValueError(f"unknown project status: {status!r}")
    # Membership in PROJECT_STATUSES (a tuple of literals) narrows ``status``
    # to the ``Status`` literal union — no cast needed.
    return status


def can_transition(from_status: str, to_status: str) -> bool:
    """Return True iff ``from_status -> to_status`` is in :data:`VALID_TRANSITIONS`.

    Unknown statuses (typos, future statuses not yet declared) return
    ``False`` — this keeps the predicate total over ``str`` so router code
    can short-circuit without try/except.
    """
    if from_status not in PROJECT_STATUSES or to_status not in PROJECT_STATUSES:
        return False
    return to_status in VALID_TRANSITIONS[_coerce(from_status)]


def assert_transition(from_status: str, to_status: str) -> None:
    """Raise :class:`InvalidTransition` if the edge is not allowed.

    Use this in router / service code where the caller wants the failure
    to be loud (e.g. to map to HTTP 409).
    """
    if not can_transition(from_status, to_status):
        raise InvalidTransition(from_status, to_status)


def is_deletable(status: str) -> bool:
    """Return True iff a project in ``status`` may be hard-deleted."""
    return status in DELETABLE_STATUSES


__all__ = [
    "DELETABLE_STATUSES",
    "InvalidTransition",
    "PROJECT_STATUSES",
    "Status",
    "VALID_TRANSITIONS",
    "assert_transition",
    "can_transition",
    "is_deletable",
]
