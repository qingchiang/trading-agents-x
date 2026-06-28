"""Shared macro primitives: the bounded SeriesCache used by fred/estat/boj."""
import unittest

import pytest

from tradingagents.dataflows.macro_common import SeriesCache


@pytest.mark.unit
class SeriesCacheTests(unittest.TestCase):
    def test_get_returns_none_when_absent(self):
        self.assertIsNone(SeriesCache().get(("x", "2026-01-01", 365)))

    def test_put_then_get_round_trips(self):
        c = SeriesCache()
        c.put(("cpi", "2026-01-01", 365), {"points": [("d", "1")]})
        self.assertEqual(c.get(("cpi", "2026-01-01", 365)), {"points": [("d", "1")]})

    def test_clear_empties_the_cache(self):
        c = SeriesCache()
        c.put(("k",), "v")
        c.clear()
        self.assertIsNone(c.get(("k",)))

    def test_evicts_least_recently_used_past_bound(self):
        c = SeriesCache(max_entries=2)
        c.put("a", 1)
        c.put("b", 2)
        c.get("a")          # touch "a" so "b" is now the LRU
        c.put("c", 3)       # exceeds bound -> evicts "b"
        self.assertEqual(c.get("a"), 1)
        self.assertIsNone(c.get("b"))
        self.assertEqual(c.get("c"), 3)

    def test_re_put_existing_key_updates_without_growing(self):
        c = SeriesCache(max_entries=2)
        c.put("a", 1)
        c.put("b", 2)
        c.put("a", 99)      # update, not a third entry
        self.assertEqual(c.get("a"), 99)
        self.assertEqual(c.get("b"), 2)  # "b" not evicted


if __name__ == "__main__":
    unittest.main()
