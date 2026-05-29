"""Prometheus metrics for the Operativa realtime surface.

Phase 8 of ``sdd/project-operativa``. Exposes two labelled Gauges that
mirror the ``learning/metrics.py`` pattern from ``sleep-learning-loop``
Phase 11 and land on the same process-global Prometheus registry that
``aether_api.core.observability`` already mounts at ``/metrics``:

* :data:`operativa_ws_subscribers` (Gauge) — number of live WebSocket
  subscribers per project. Updated by
  :func:`set_ws_subscribers` from
  :class:`aether_api.services.live_bus.LiveBus` on every subscribe /
  unsubscribe. Labels: ``project``.
* :data:`operativa_mcp_status` (Gauge) — last-known MCP availability
  per project: ``1.0`` = available, ``0.0`` = unavailable. Updated by
  :func:`set_mcp_status` from the LiveBus polling loop on every
  ``mcp_status`` transition. Labels: ``project``.

Tests SHOULD call :func:`reset_for_test` between cases — the Gauge
state is process-global.
"""

from __future__ import annotations

import uuid

from prometheus_client import Gauge

__all__ = [
    "operativa_mcp_status",
    "operativa_ws_subscribers",
    "reset_for_test",
    "set_mcp_status",
    "set_ws_subscribers",
]


# ---------------------------------------------------------------------------
# Collectors
# ---------------------------------------------------------------------------


operativa_ws_subscribers: Gauge = Gauge(
    "aether_operativa_ws_subscribers",
    "Number of live WebSocket subscribers attached to the Operativa "
    "LiveBus for a given project_id. Goes to 0 when the last "
    "subscriber disconnects (and the per-project polling task is "
    "cancelled).",
    labelnames=("project",),
)


operativa_mcp_status: Gauge = Gauge(
    "aether_operativa_mcp_status",
    "Last-known MCP availability for a given project_id. 1=available, "
    "0=unavailable. Updated by the LiveBus polling task on every "
    "MCP status transition emitted to subscribers.",
    labelnames=("project",),
)


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def _stringify_project(project_id: uuid.UUID | str) -> str:
    """Cast ``project_id`` to ``str`` — Prometheus labels are strings."""
    return str(project_id)


def set_ws_subscribers(project_id: uuid.UUID | str, count: int) -> None:
    """Set the live-subscriber Gauge for ``project_id`` to ``count``.

    Called from :class:`LiveBus.subscribe` / :class:`LiveBus.unsubscribe`
    so the gauge always reflects the size of the subscriber set.
    """
    operativa_ws_subscribers.labels(project=_stringify_project(project_id)).set(
        float(count)
    )


def set_mcp_status(project_id: uuid.UUID | str, *, available: bool) -> None:
    """Set the MCP availability Gauge for ``project_id``.

    ``available=True`` → 1, ``available=False`` → 0. Called from the
    LiveBus polling task on the recovery edge (DOWN → UP) and on the
    failure edge (UP → DOWN), mirroring the ``mcp_status`` WS events.
    """
    operativa_mcp_status.labels(project=_stringify_project(project_id)).set(
        1.0 if available else 0.0
    )


# ---------------------------------------------------------------------------
# Test helper — Prometheus state is process-global.
# ---------------------------------------------------------------------------


def reset_for_test() -> None:
    """Drop all label samples. Tests only.

    Production callers MUST NOT call this — it would wipe live
    instrumentation state during normal operation.
    """
    operativa_ws_subscribers.clear()
    operativa_mcp_status.clear()
