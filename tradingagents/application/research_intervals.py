"""Canonical inclusive-calendar interval operations for research coverage."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Protocol


class DateIntervalLike(Protocol):
    start: date
    end: date


@dataclass(frozen=True, order=True)
class DateInterval:
    start: date
    end: date

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise ValueError("interval start must not be after end")


@dataclass(frozen=True)
class DateIntervalSet:
    """A normalized union of inclusive calendar-date intervals."""

    intervals: tuple[DateInterval, ...] = ()

    def __init__(self, intervals: tuple[DateIntervalLike, ...] = ()) -> None:
        ordered = sorted(DateInterval(item.start, item.end) for item in intervals)
        normalized: list[DateInterval] = []
        for current in ordered:
            if not normalized or current.start > normalized[-1].end + timedelta(days=1):
                normalized.append(current)
                continue
            previous = normalized[-1]
            normalized[-1] = DateInterval(previous.start, max(previous.end, current.end))
        object.__setattr__(self, "intervals", tuple(normalized))

    def covers(self, value: date) -> bool:
        return any(item.start <= value <= item.end for item in self.intervals)

    def gaps(self, start: date, end: date) -> tuple[DateInterval, ...]:
        requested = DateInterval(start, end)
        gaps: list[DateInterval] = []
        cursor = requested.start
        for item in self.intervals:
            if item.end < requested.start:
                continue
            if item.start > requested.end:
                break
            interval_start = max(requested.start, item.start)
            interval_end = min(requested.end, item.end)
            if interval_start > cursor:
                gaps.append(DateInterval(cursor, interval_start - timedelta(days=1)))
            cursor = max(cursor, interval_end + timedelta(days=1))
        if cursor <= requested.end:
            gaps.append(DateInterval(cursor, requested.end))
        return tuple(gaps)
