"""Phase 11 of ``sdd/sleep-learning-loop`` — Prometheus metrics.

Pinned behavioural contract:

* :data:`q_table_promotions_total` increments by exactly 1 per call to
  :func:`increment_q_table_promotion`, partitioned by
  ``(project, risk_class)``.
* :data:`qtable_bytes` is set to the UTF-8 JSON encoded length of the
  table_data dict passed in.
* When the byte count exceeds ``settings.learning_qtable_warn_bytes``,
  a structured WARN log line ``aether.qtable.size_threshold_breached``
  is emitted carrying ``project_id`` + ``bytes`` + ``threshold``.
* :func:`update_episodic_rows` is rate-limited to once per
  ``EPISODIC_GAUGE_TTL_SECONDS`` per project (unless ``force=True``).

These tests poke the collectors directly — the orchestrator integration
is exercised by ``tests/sleep/test_learning_step.py`` (Phase 7).
"""

from __future__ import annotations

import logging
import uuid

import pytest


@pytest.fixture(autouse=True)
def _reset_metrics() -> None:
    """Process-global Prometheus state — reset every test."""
    from aether_api.learning.metrics import reset_for_test

    reset_for_test()
    yield
    reset_for_test()


# ---------------------------------------------------------------------------
# (a) Counter — increments and label partitioning.
# ---------------------------------------------------------------------------


def test_promotion_counter_increments() -> None:
    """Each call bumps the per-(project, risk_class) sample by 1."""
    from aether_api.learning.metrics import (
        increment_q_table_promotion,
        q_table_promotions_total,
    )

    project_id = uuid.uuid4()
    increment_q_table_promotion(project_id, "bajo")
    increment_q_table_promotion(project_id, "bajo")
    increment_q_table_promotion(project_id, "alto")

    bajo_value = q_table_promotions_total.labels(
        project=str(project_id), risk_class="bajo"
    )._value.get()  # type: ignore[attr-defined]
    alto_value = q_table_promotions_total.labels(
        project=str(project_id), risk_class="alto"
    )._value.get()  # type: ignore[attr-defined]

    assert bajo_value == 2.0
    assert alto_value == 1.0


def test_promotion_counter_normalises_invalid_risk_class() -> None:
    """A typo on the caller side collapses to ``unknown`` rather than
    exploding label cardinality on the Prometheus side."""
    from aether_api.learning.metrics import (
        increment_q_table_promotion,
        q_table_promotions_total,
    )

    project_id = uuid.uuid4()
    increment_q_table_promotion(project_id, "rojo")  # not in the enum

    value = q_table_promotions_total.labels(
        project=str(project_id), risk_class="unknown"
    )._value.get()  # type: ignore[attr-defined]
    assert value == 1.0


# ---------------------------------------------------------------------------
# (b) qtable_bytes gauge — set to JSON-encoded length.
# ---------------------------------------------------------------------------


def test_qtable_bytes_gauge_tracks_payload_size() -> None:
    from aether_api.learning.metrics import qtable_bytes, record_qtable_bytes

    project_id = uuid.uuid4()
    table = {"sk:1": {"buy": 0.1, "sell": -0.2}}
    size = record_qtable_bytes(project_id, table)

    expected = len(__import__("json").dumps(table, separators=(",", ":")).encode())
    assert size == expected

    gauge_value = qtable_bytes.labels(project=str(project_id))._value.get()  # type: ignore[attr-defined]
    assert gauge_value == float(expected)


def test_qtable_bytes_warn_fires_when_threshold_breached(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """When the payload exceeds ``settings.learning_qtable_warn_bytes`` we
    emit ``aether.qtable.size_threshold_breached`` at WARN level."""
    from aether_api.core.settings import get_settings
    from aether_api.learning.metrics import (
        QTABLE_SIZE_LOG_KEY,
        record_qtable_bytes,
    )

    # Tighten the threshold to 1 KiB so we don't have to materialise a
    # multi-MB dict in-test.
    monkeypatch.setenv("LEARNING_QTABLE_WARN_BYTES", "1024")
    get_settings.cache_clear()
    assert get_settings().learning_qtable_warn_bytes == 1024

    project_id = uuid.uuid4()
    # 200 state keys × ~30 bytes each ≈ 6 KiB encoded.
    table = {f"sk:{i:04d}": {"buy": float(i), "sell": -float(i)} for i in range(200)}

    caplog.set_level(logging.WARNING, logger="aether_api.learning.metrics")
    size = record_qtable_bytes(project_id, table)

    assert size > 1024
    # Find our structured warn.
    matches = [r for r in caplog.records if r.message == QTABLE_SIZE_LOG_KEY]
    assert matches, f"expected {QTABLE_SIZE_LOG_KEY!r} in log records"
    record = matches[-1]
    assert record.levelno == logging.WARNING
    assert record.project_id == str(project_id)
    assert record.bytes == size
    assert record.threshold == 1024


def test_qtable_bytes_no_warn_below_threshold(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Under the threshold ⇒ NO log line. Pins the "default is silent"
    behaviour so a quiet system doesn't spam warns."""
    from aether_api.core.settings import get_settings
    from aether_api.learning.metrics import (
        QTABLE_SIZE_LOG_KEY,
        record_qtable_bytes,
    )

    monkeypatch.setenv("LEARNING_QTABLE_WARN_BYTES", str(1024 * 1024))  # 1 MiB
    get_settings.cache_clear()

    caplog.set_level(logging.WARNING, logger="aether_api.learning.metrics")
    record_qtable_bytes(uuid.uuid4(), {"sk:tiny": {"buy": 0.1}})

    assert not [r for r in caplog.records if r.message == QTABLE_SIZE_LOG_KEY]


# ---------------------------------------------------------------------------
# (c) episodic_rows gauge — TTL-throttled updates.
# ---------------------------------------------------------------------------


def test_episodic_rows_gauge_updates_within_force_flag() -> None:
    """``force=True`` always writes regardless of TTL — used by warm
    paths and by the lifespan loader."""
    from aether_api.learning.metrics import episodic_rows, update_episodic_rows

    project_id = uuid.uuid4()
    update_episodic_rows(project_id, 17, force=True)
    gauge_value = episodic_rows.labels(project=str(project_id))._value.get()  # type: ignore[attr-defined]
    assert gauge_value == 17.0

    # Second write also lands (force=True bypasses the TTL).
    update_episodic_rows(project_id, 42, force=True)
    gauge_value = episodic_rows.labels(project=str(project_id))._value.get()  # type: ignore[attr-defined]
    assert gauge_value == 42.0


def test_episodic_rows_gauge_throttled_without_force() -> None:
    """Without ``force=True`` a second update inside the TTL is a no-op."""
    from aether_api.learning.metrics import episodic_rows, update_episodic_rows

    project_id = uuid.uuid4()
    update_episodic_rows(project_id, 5)
    update_episodic_rows(project_id, 999)  # would be dropped by the TTL.

    gauge_value = episodic_rows.labels(project=str(project_id))._value.get()  # type: ignore[attr-defined]
    assert gauge_value == 5.0
