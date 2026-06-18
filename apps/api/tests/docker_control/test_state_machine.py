"""Docker-event → canonical-status state-machine table tests.

The docker_control change extends the project lifecycle state machine
through :mod:`docker_lifecycle_transitions` — without touching the
canonical ``VALID_TRANSITIONS`` dict in ``projects-crud``. This test
matrix pins every supported event → target status pair AND every
illegal edge (when an event resolves to a status that the canonical
matrix denies).
"""

from __future__ import annotations

import pytest
from aether_api.docker_control.docker_lifecycle_transitions import (
    DOCKER_EVENT_TRANSITIONS,
    UnknownDockerEvent,
    assert_event,
    can_apply_event,
    resolve_event,
)
from aether_api.services.pair_lifecycle import (
    VALID_TRANSITIONS,
    InvalidTransition,
)


# ---------------------------------------------------------------------------
# Resolve: every documented event maps to a known canonical status.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("event", "target"),
    [
        ("build_failed", "error"),
        ("daemon_reports_stopped", "stopped"),
        ("drift_detected", "error"),
    ],
)
def test_resolve_event(event: str, target: str) -> None:
    assert resolve_event(event) == target
    assert DOCKER_EVENT_TRANSITIONS[event] == target


def test_resolve_event_unknown_raises() -> None:
    with pytest.raises(UnknownDockerEvent):
        resolve_event("not_a_real_event")


# ---------------------------------------------------------------------------
# Composition with the canonical matrix: every (from, event) pair the
# docker_control module emits must compose to an edge allowed by
# VALID_TRANSITIONS in projects-crud's source of truth.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("from_status", "event", "expected"),
    [
        # build_failed -> error. Canonical: only active -> error is legal.
        ("active", "build_failed", True),
        ("inactive", "build_failed", False),
        ("paused", "build_failed", False),
        ("stopped", "build_failed", False),
        ("error", "build_failed", False),
        ("maintenance", "build_failed", False),
        # daemon_reports_stopped -> stopped. Canonical: active, paused,
        # error all permit -> stopped.
        ("active", "daemon_reports_stopped", True),
        ("paused", "daemon_reports_stopped", True),
        ("error", "daemon_reports_stopped", True),
        ("inactive", "daemon_reports_stopped", False),
        ("maintenance", "daemon_reports_stopped", False),
        # drift_detected -> error: same canonical allowlist as build_failed.
        ("active", "drift_detected", True),
        ("inactive", "drift_detected", False),
        ("maintenance", "drift_detected", False),
    ],
)
def test_can_apply_event_matrix(
    from_status: str, event: str, expected: bool
) -> None:
    assert can_apply_event(from_status, event) is expected


def test_can_apply_event_returns_false_on_unknown_event() -> None:
    assert can_apply_event("active", "fictional_event") is False


def test_assert_event_returns_target_status() -> None:
    assert assert_event("active", "build_failed") == "error"


def test_assert_event_raises_invalid_transition_on_illegal_edge() -> None:
    with pytest.raises(InvalidTransition):
        assert_event("inactive", "build_failed")


def test_assert_event_raises_unknown_event() -> None:
    with pytest.raises(UnknownDockerEvent):
        assert_event("active", "no_such_event")


# ---------------------------------------------------------------------------
# Invariant: this module only EXTENDS — every target status is a member
# of the canonical PROJECT_STATUSES set (no synthetic statuses leak).
# ---------------------------------------------------------------------------
def test_every_event_target_is_a_canonical_status() -> None:
    canonical = set(VALID_TRANSITIONS.keys())
    for event, target in DOCKER_EVENT_TRANSITIONS.items():
        assert target in canonical, (
            f"event {event!r} targets {target!r} which is not a canonical status; "
            "docker_control may not synthesise new statuses — see CHARTER."
        )
