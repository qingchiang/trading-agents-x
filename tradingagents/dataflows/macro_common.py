"""Shared primitives for the macro vendors (fred / estat / boj).

These vendors deliberately share a return shape and rendering, so the cross-region
panel and the get_macro_indicators microscope tool treat every source uniformly.
This module holds the pieces that would otherwise be copy-pasted across them. It
imports no vendor (leaf module), so the vendors can import from it freely.
"""
from __future__ import annotations

from collections import OrderedDict
from typing import NamedTuple

# Rows cap for a rendered observation table: recent values matter most for a
# decision, and daily series (yields, VIX) over a long window would flood context.
MAX_ROWS = 40


class SeriesCache:
    """A bounded process-level cache for fetched macro series (LRU eviction).

    Key is ``(alias, curr_date, look_back_days)``; value is the fetched series
    dict. Only *successful* fetches are stored — callers skip :meth:`put` on a
    miss — so :meth:`get` returning ``None`` means "not cached", never "cached
    empty" (a transient outage must be retryable). Bounded so a long-running
    process (e.g. a multi-date backtest, which creates a distinct key per date)
    cannot grow it without limit.
    """

    def __init__(self, max_entries: int = 512):
        self._data: OrderedDict = OrderedDict()
        self._max = max_entries

    def get(self, key):
        if key not in self._data:
            return None
        self._data.move_to_end(key)  # mark most-recently-used
        return self._data[key]

    def put(self, key, value) -> None:
        self._data[key] = value
        self._data.move_to_end(key)
        while len(self._data) > self._max:
            self._data.popitem(last=False)  # evict least-recently-used

    def clear(self) -> None:
        self._data.clear()


class PointsSummary(NamedTuple):
    """Latest reading and window change for a series' observation list.

    ``delta``/``pct`` are ``None`` when the window has fewer than two points (so a
    single point is not rendered as a fabricated "+0.00" change) or a value isn't
    numeric; ``pct`` is also ``None`` when the base is zero. ``pct`` is the
    meaningful figure for index series (CPI), where an absolute delta is opaque.
    """
    last_val: str
    last_date: str
    first_val: str
    first_date: str
    delta: float | None
    pct: float | None


def summarize_points(points: list) -> PointsSummary | None:
    """Summarize an ascending ``[(date, value), ...]`` list, or None if empty.

    Shared by the macro vendors' report rendering and the cross-region panel so
    the latest/change computation stays in one place.
    """
    if not points:
        return None
    first_date, first_val = points[0]
    last_date, last_val = points[-1]
    delta = pct = None
    if len(points) >= 2:
        try:
            delta = float(last_val) - float(first_val)
            base = float(first_val)
            pct = (delta / base * 100) if base != 0 else None
        except (TypeError, ValueError):
            delta = pct = None
    return PointsSummary(last_val, last_date, first_val, first_date, delta, pct)


def render_macro_report(source_label: str, data: dict, curr_date: str) -> str:
    """Render a fetched macro series (the :func:`fetch_series` dict) as markdown.

    ``source_label`` heads the report (e.g. "FRED", "e-Stat", "BOJ"); every macro
    vendor renders through this so the microscope tool's output is uniform across
    sources. Shows title/units/frequency, the latest value, the change over the
    window, and a recent observation table (capped at ``MAX_ROWS``).
    """
    series_id = data["series_id"]
    seasonal = data["seasonal"]
    points = data["points"]
    header = (
        f"## {source_label}: {data['title']} ({series_id})\n"
        f"- Units: {data['units']}\n"
        f"- Frequency: {data['frequency']}"
        f"{f' ({seasonal})' if seasonal else ''}\n"
        f"- Window: {data['start_date']} to {curr_date}\n"
    )

    if not points:
        return header + (
            f"\nNo observations for {series_id} in this window. The series may "
            f"report less frequently than the window length; widen look_back_days."
        )

    s = summarize_points(points)
    if s.delta is None:
        summary = f"\n**Latest:** {s.last_val} ({s.last_date})\n"
    else:
        pct = f" ({s.pct:+.2f}%)" if s.pct is not None else ""
        summary = (
            f"\n**Latest:** {s.last_val} ({s.last_date}) | "
            f"**Change over window:** {s.delta:+.2f}{pct} "
            f"from {s.first_val} ({s.first_date})\n"
        )

    shown = points
    note = ""
    if len(points) > MAX_ROWS:
        shown = points[-MAX_ROWS:]
        note = f"\n_(showing the most recent {MAX_ROWS} of {len(points)} observations)_\n"

    table = (
        "\n| Date | Value |\n| --- | --- |\n"
        + "\n".join(f"| {d} | {v} |" for d, v in shown)
        + "\n"
    )
    return header + summary + note + table
