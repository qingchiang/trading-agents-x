"""Shared primitives for the macro vendors (fred / estat / boj).

These vendors deliberately share a return shape and rendering, so the cross-region
panel and the get_macro_indicators microscope tool treat every source uniformly.
This module holds the pieces that would otherwise be copy-pasted across them. It
imports no vendor (leaf module), so the vendors can import from it freely.
"""
from __future__ import annotations

from collections import OrderedDict


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
