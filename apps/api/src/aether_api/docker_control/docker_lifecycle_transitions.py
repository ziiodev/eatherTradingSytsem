"""Docker-control-internal extensions to the project lifecycle state machine.

The canonical state machine in
:mod:`aether_api.services.pair_lifecycle` is OWNED by the
``projects-crud`` change. This module is the only place the
``project-docker-orchestration`` change extends it — additively, in
process, and without ever editing the canonical dict.

Why a sidecar module instead of patching ``VALID_TRANSITIONS`` in
place?

* The canonical dict is :class:`Final[dict[Status, frozenset[Status]]]`
  with a closed :class:`Literal` for ``Status``. Adding new pseudo-
  statuses to that union would force a breaking change on every reader
  (routers, repositories, tests) and propagate beyond the docker-
  control surface.
* The docker-internal transitions encode **events** (a build failed, a
  reconciler observed daemon-stopped, a drift was detected) rather than
  new persistent statuses. We resolve them to existing canonical
  statuses at the call site.

Public surface:

* :data:`DOCKER_EVENT_TRANSITIONS` — mapping ``event_name → target
  canonical status``. Read by the lifecycle / reconcile modules.
* :func:`resolve_event` — return the canonical target status for a
  docker event; raise if the event is unknown.
* :func:`assert_event` — assert that ``(current_status, event)`` is a
  legal edge once expanded through :data:`VALID_TRANSITIONS`.

This module IS allowed to import from
``aether_api.services.pair_lifecycle``. The canonical module MUST
NOT import from here — that would create a cycle and entangle the
sandbox/docker layers with projects-crud's wave 1 surface.
"""

from __future__ import annotations

from typing import Final

from aether_api.services.pair_lifecycle import (
    InvalidTransition,
    Status,
    assert_transition,
    can_transition,
)

#: Docker-internal *event* → canonical target *status*.
#:
#: Each entry encodes an observable signal that the docker_control
#: module produces, paired with the canonical status the project should
#: end up in. The lifecycle / reconcile callers expand the event to its
#: target status and then go through
#: :func:`aether_api.services.pair_lifecycle.assert_transition` for
#: the actual edge check — so the docker-internal layer never bypasses
#: the canonical state machine.
DOCKER_EVENT_TRANSITIONS: Final[dict[str, Status]] = {
    # The image build failed (``aiodocker.images.build(...)`` raised).
    # Project moves to ``error`` so the operator can investigate via the
    # infraestructura tab. Allowed from ``active`` and ``maintenance``
    # only — that's the projects-crud canonical matrix.
    "build_failed": "error",
    # The reconciler queried ``/containers/{id}/json`` and the daemon
    # reports the container is not running (state.Status in
    # {"exited","dead"}). Project moves to ``stopped``. Allowed from
    # ``active`` and ``paused``.
    "daemon_reports_stopped": "stopped",
    # The reconciler queried ``/containers/{id}/json`` and got 404 — the
    # container we believed to own no longer exists. Project moves to
    # ``error`` and ``container_id`` is cleared. Allowed from
    # ``active`` and ``maintenance``.
    "drift_detected": "error",
}


class UnknownDockerEvent(ValueError):
    """Raised when callers reference a docker event we don't know about."""

    def __init__(self, event: str) -> None:
        self.event = event
        super().__init__(f"unknown docker lifecycle event: {event!r}")


def resolve_event(event: str) -> Status:
    """Return the canonical target status for a docker event.

    Raises :class:`UnknownDockerEvent` if the event is not in
    :data:`DOCKER_EVENT_TRANSITIONS`.
    """
    if event not in DOCKER_EVENT_TRANSITIONS:
        raise UnknownDockerEvent(event)
    return DOCKER_EVENT_TRANSITIONS[event]


def can_apply_event(from_status: str, event: str) -> bool:
    """Return True iff ``(from_status, event)`` resolves to an allowed edge.

    Composition of :func:`resolve_event` + canonical
    :func:`aether_api.services.pair_lifecycle.can_transition`. Returns
    False for unknown events instead of raising — callers that want a
    hard fail use :func:`assert_event`.
    """
    if event not in DOCKER_EVENT_TRANSITIONS:
        return False
    return can_transition(from_status, DOCKER_EVENT_TRANSITIONS[event])


def assert_event(from_status: str, event: str) -> Status:
    """Resolve ``event`` and assert the canonical transition is legal.

    Raises :class:`UnknownDockerEvent` for unknown events,
    :class:`aether_api.services.pair_lifecycle.InvalidTransition` for
    a known event that maps to an illegal edge in the canonical matrix.

    Returns the target status so callers can pass it to
    :meth:`PairRepository.update_status_if` without a second lookup.
    """
    target = resolve_event(event)
    assert_transition(from_status, target)
    return target


__all__ = [
    "DOCKER_EVENT_TRANSITIONS",
    "InvalidTransition",
    "UnknownDockerEvent",
    "assert_event",
    "can_apply_event",
    "resolve_event",
]
