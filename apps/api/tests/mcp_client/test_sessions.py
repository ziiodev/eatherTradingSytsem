"""Session clock — UTC ranges + DST flags.

Pure-function tests; no DB. Each scenario picks a date where the DST
window is unambiguous so the test doesn't flake at boundary years.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from aether_api.mcp_client.sessions import is_session_open


# --- new_york (US DST) ------------------------------------------------------
@pytest.mark.parametrize(
    ("now", "expected"),
    [
        # 2025-07-15 (DST): NY is 12:00-21:00 UTC.
        (datetime(2025, 7, 15, 12, 30, tzinfo=UTC), True),
        (datetime(2025, 7, 15, 21, 0, tzinfo=UTC), False),  # exclusive close
        # 2025-01-15 (no DST): NY is 13:00-22:00 UTC.
        (datetime(2025, 1, 15, 13, 30, tzinfo=UTC), True),
        (datetime(2025, 1, 15, 11, 0, tzinfo=UTC), False),
    ],
)
def test_new_york_session(now: datetime, expected: bool) -> None:
    assert is_session_open(["new_york"], now) is expected


# --- europe (EU DST) --------------------------------------------------------
@pytest.mark.parametrize(
    ("now", "expected"),
    [
        # 2025-07-15 (DST): EU is 07:00-16:00 UTC.
        (datetime(2025, 7, 15, 8, 0, tzinfo=UTC), True),
        (datetime(2025, 7, 15, 16, 0, tzinfo=UTC), False),
        # 2025-01-15 (no DST): EU is 08:00-17:00 UTC.
        (datetime(2025, 1, 15, 9, 0, tzinfo=UTC), True),
        (datetime(2025, 1, 15, 7, 30, tzinfo=UTC), False),
    ],
)
def test_europe_session(now: datetime, expected: bool) -> None:
    assert is_session_open(["europe"], now) is expected


# --- tokyo / shanghai (no DST) ---------------------------------------------
def test_tokyo_no_dst() -> None:
    assert is_session_open(["tokyo"], datetime(2025, 6, 15, 3, 0, tzinfo=UTC)) is True
    assert is_session_open(["tokyo"], datetime(2025, 6, 15, 9, 0, tzinfo=UTC)) is False


def test_shanghai_no_dst() -> None:
    assert (
        is_session_open(["shanghai"], datetime(2025, 6, 15, 2, 0, tzinfo=UTC)) is True
    )
    assert (
        is_session_open(["shanghai"], datetime(2025, 6, 15, 7, 0, tzinfo=UTC)) is False
    )


# --- sydney (inverted DST) --------------------------------------------------
def test_sydney_dst_window_wraps_midnight() -> None:
    # 2025-01-15 — Sydney IS on AEDT (summer); window is 23:00-06:00 UTC.
    assert is_session_open(["sydney"], datetime(2025, 1, 15, 23, 30, tzinfo=UTC)) is True
    assert is_session_open(["sydney"], datetime(2025, 1, 15, 5, 0, tzinfo=UTC)) is True
    assert is_session_open(["sydney"], datetime(2025, 1, 15, 10, 0, tzinfo=UTC)) is False


def test_sydney_no_dst() -> None:
    # 2025-06-15 — Sydney is on AEST (winter); window is 00:00-07:00 UTC.
    assert is_session_open(["sydney"], datetime(2025, 6, 15, 1, 0, tzinfo=UTC)) is True
    assert is_session_open(["sydney"], datetime(2025, 6, 15, 7, 0, tzinfo=UTC)) is False


# --- union of sessions ------------------------------------------------------
def test_union_of_sessions() -> None:
    # 2025-07-15 14:00 UTC: NY (DST) is open, EU (DST) closed.
    now = datetime(2025, 7, 15, 14, 0, tzinfo=UTC)
    assert is_session_open(["europe", "new_york"], now) is True


def test_empty_sessions_closed() -> None:
    assert is_session_open([], datetime(2025, 7, 15, 14, 0, tzinfo=UTC)) is False


def test_unknown_sessions_ignored() -> None:
    # Should not crash, should treat unknown name as no session.
    assert is_session_open(["mars"], datetime(2025, 7, 15, 14, 0, tzinfo=UTC)) is False
