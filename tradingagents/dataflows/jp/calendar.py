"""Tokyo Stock Exchange trading-day calendar for look-ahead-safe publication lags.

Some J-Quants datasets (e.g. weekly margin balances, ``/markets/margin-interest``)
carry the record date but no publication date. TSE releases those a fixed number
of *business days* after the record, so to know when a record became public we
must count trading days, not calendar days — a fixed calendar-day lag leaks around
the year-end and Golden Week closures, where T+2 business days can span 10+
calendar days (e.g. a Friday-2024-12-27 record is only public on 2025-01-06, ten
days later, not the seven a flat lag would assume).

TSE is closed on weekends, Japanese national holidays (via :mod:`jpholiday`), and
the exchange's own year-end/new-year break (Dec 31 and Jan 2–3; Jan 1 is already a
national holiday). This is NOT a full corporate-actions calendar — just enough to
place a publication date safely for the look-ahead guards.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import jpholiday

_TOKYO = ZoneInfo("Asia/Tokyo")


def tokyo_now(now: datetime | None = None) -> datetime:
    """Return an aware Tokyo datetime, independent of the host timezone."""
    current = now or datetime.now(_TOKYO)
    if current.tzinfo is None:
        current = current.replace(tzinfo=_TOKYO)
    else:
        current = current.astimezone(_TOKYO)
    return current


def tokyo_today(now: datetime | None = None) -> date:
    """Return the current Tokyo calendar date, independent of host timezone."""
    return tokyo_now(now).date()


def is_tse_open(d: date) -> bool:
    """True when the Tokyo Stock Exchange trades on ``d`` (weekday, not a holiday)."""
    if d.weekday() >= 5:  # Saturday / Sunday
        return False
    if d.month == 12 and d.day == 31:  # 大晦日 — exchange year-end close
        return False
    if d.month == 1 and d.day in (2, 3):  # 年始休業 (Jan 1 is a national holiday)
        return False
    return not jpholiday.is_holiday(d)


def is_government_business_day(d: date) -> bool:
    """Return whether Japanese national-government offices are open on ``d``.

    The MOF publication calendar differs slightly from JPX around year-end:
    national-government offices close from December 29 through January 3,
    while the exchange can still trade on December 29 or 30.  JP10Y visibility
    therefore uses this calendar rather than :func:`is_tse_open`.
    """
    if d.weekday() >= 5:
        return False
    if d.month == 12 and d.day >= 29:
        return False
    if d.month == 1 and d.day <= 3:
        return False
    return not jpholiday.is_holiday(d)


def add_government_business_days(d: date, n: int) -> date:
    """Return the date ``n`` Japanese government business days after ``d``."""
    if n < 0:
        raise ValueError("n must be non-negative")
    result = d
    while n > 0:
        result += timedelta(days=1)
        if is_government_business_day(result):
            n -= 1
    return result


def add_business_days(d: date, n: int) -> date:
    """Return the date ``n`` TSE trading days after ``d`` (``n`` >= 0)."""
    result = d
    while n > 0:
        result += timedelta(days=1)
        if is_tse_open(result):
            n -= 1
    return result


def completed_market_date(d: date, now: datetime | None = None) -> date:
    """Return the latest completed TSE date, using 17:00 Tokyo as daily cutoff."""
    current = tokyo_now(now)
    result = d
    if result == current.date() and current.time() < time(17):
        result -= timedelta(days=1)
    while not is_tse_open(result):
        result -= timedelta(days=1)
    return result
