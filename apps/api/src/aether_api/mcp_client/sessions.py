"""Session clock for the charter's 5 named trading windows.

The charter mandates ``trading_sessions`` (sydney/shanghai/tokyo/europe/
new_york) on every project. Live orders MUST only flow during the union
of those windows. This module is a **pure** function — no IO, no clock
mock, ``now_utc`` is always passed in.

DST rules captured here:

* ``new_york`` / ``europe`` follow Northern-hemisphere DST and shift the
  window earlier in UTC during summer.
* ``sydney`` follows Southern-hemisphere DST (inverted).
* ``shanghai`` and ``tokyo`` do not observe DST.

The windows are conservative (broker-observed): we intentionally err
on the smaller side because surfacing "session closed" when in fact
liquidity is fine is a benign UX issue, while the opposite is a real
risk event.
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Literal

#: The 5 named sessions from the charter (and the DB CHECK constraint
#: on :data:`aether_api.models.project.TRADING_SESSIONS`).
SessionName = Literal["sydney", "shanghai", "tokyo", "europe", "new_york"]
SESSION_NAMES: tuple[SessionName, ...] = (
    "sydney",
    "shanghai",
    "tokyo",
    "europe",
    "new_york",
)


def _is_us_dst(d: date) -> bool:
    """Northern-hemisphere DST window used by NY: 2nd Sunday Mar → 1st Sunday Nov."""
    if d.month < 3 or d.month > 11:
        return False
    if d.month > 3 and d.month < 11:
        return True
    # March: DST starts on the 2nd Sunday.
    if d.month == 3:
        # First day of week 1 (Monday) ranges 1..7; 2nd Sunday = day 8..14 falling
        # on the first Sunday + 7.
        first_sunday = 1 + ((6 - date(d.year, 3, 1).weekday()) % 7)
        second_sunday = first_sunday + 7
        return d.day >= second_sunday
    # November: DST ends on the 1st Sunday.
    first_sunday = 1 + ((6 - date(d.year, 11, 1).weekday()) % 7)
    return d.day < first_sunday


def _is_eu_dst(d: date) -> bool:
    """Last Sunday in March → last Sunday in October."""
    if d.month < 3 or d.month > 10:
        return False
    if d.month > 3 and d.month < 10:
        return True
    if d.month == 3:
        # Last Sunday of March.
        for day in range(31, 24, -1):
            if date(d.year, 3, day).weekday() == 6:
                return d.day >= day
        return False
    # October — last Sunday.
    for day in range(31, 24, -1):
        if date(d.year, 10, day).weekday() == 6:
            return d.day < day
    return False


def _is_aus_dst(d: date) -> bool:
    """Sydney DST: 1st Sunday Oct → 1st Sunday Apr (inverted hemisphere)."""
    if d.month >= 10 or d.month <= 3:
        # In the "summer" half — definitely on DST except the very edges.
        if d.month == 10:
            first_sunday = 1 + ((6 - date(d.year, 10, 1).weekday()) % 7)
            return d.day >= first_sunday
        return True
    if d.month == 4:
        first_sunday = 1 + ((6 - date(d.year, 4, 1).weekday()) % 7)
        return d.day < first_sunday
    return False


def _window_for(session: SessionName, d: date) -> tuple[time, time]:
    """Return the UTC ``(open, close)`` window for ``session`` on ``d``.

    Times are inclusive on the open, exclusive on the close. Windows
    that cross midnight (Sydney summer) are normalised by returning
    open > close — :func:`is_session_open` handles wrap-around.
    """
    if session == "new_york":
        # 13:00–22:00 UTC standard; 12:00–21:00 UTC during US DST.
        return (time(12, 0), time(21, 0)) if _is_us_dst(d) else (time(13, 0), time(22, 0))
    if session == "europe":
        # London 08:00–17:00 local → 08:00–17:00 UTC standard; 07:00–16:00 during DST.
        return (time(7, 0), time(16, 0)) if _is_eu_dst(d) else (time(8, 0), time(17, 0))
    if session == "tokyo":
        # JST is UTC+9 year-round. 09:00–18:00 JST → 00:00–09:00 UTC.
        return (time(0, 0), time(9, 0))
    if session == "shanghai":
        # CST is UTC+8 year-round. 09:30–15:00 local → 01:30–07:00 UTC.
        return (time(1, 30), time(7, 0))
    # sydney
    # AEST = UTC+10, AEDT = UTC+11.
    # 10:00–17:00 local → 23:00–06:00 UTC (DST) or 00:00–07:00 UTC (standard).
    if _is_aus_dst(d):
        return (time(23, 0), time(6, 0))
    return (time(0, 0), time(7, 0))


def is_session_open(sessions: list[str], now_utc: datetime) -> bool:
    """Return True iff ``now_utc`` falls inside the union of ``sessions``.

    Parameters
    ----------
    sessions:
        Names from :data:`SESSION_NAMES`. Unknown names are silently
        ignored — projects with empty ``trading_sessions`` get ``False``.
    now_utc:
        Aware UTC ``datetime`` (the caller MUST normalise; callers in
        this codebase always do, via :func:`datetime.now(tz=UTC)`).
    """
    if not sessions:
        return False
    t = now_utc.time().replace(second=0, microsecond=0)
    d = now_utc.date()
    for s in sessions:
        if s not in SESSION_NAMES:
            continue
        open_, close = _window_for(s, d)
        if open_ <= close:
            if open_ <= t < close:
                return True
        else:
            # Wraps midnight — open today OR before close today.
            if t >= open_ or t < close:
                return True
    return False


__all__ = ["SESSION_NAMES", "SessionName", "is_session_open"]
