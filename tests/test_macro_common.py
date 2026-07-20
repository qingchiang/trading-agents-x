"""Shared macro primitives: the bounded SeriesCache used by fred/estat/boj."""

import datetime
import os
import tempfile
import time
import unittest
from unittest import mock

import pytest

from tradingagents.dataflows import macro_common
from tradingagents.dataflows.config import set_config
from tradingagents.dataflows.macro_common import SeriesCache, exact_year_over_year


@pytest.mark.unit
def test_exact_year_over_year_requires_matching_calendar_point():
    result = exact_year_over_year(
        [("2025-06-01", "100"), ("2026-05-01", "500"), ("2026-06-01", "103")]
    )
    assert result is not None
    assert result.prior_date == "2025-06-01"
    assert result.pct == pytest.approx(3.0)
    assert exact_year_over_year([("2025-05-01", "100"), ("2026-06-01", "103")]) is None


@pytest.mark.unit
def test_exact_year_over_year_handles_leap_day_without_interpolation():
    result = exact_year_over_year([("2023-02-28", "100"), ("2024-02-29", "110")])
    assert result is not None
    assert result.prior_date == "2023-02-28"
    assert result.pct == pytest.approx(10.0)


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
        c.get("a")  # touch "a" so "b" is now the LRU
        c.put("c", 3)  # exceeds bound -> evicts "b"
        self.assertEqual(c.get("a"), 1)
        self.assertIsNone(c.get("b"))
        self.assertEqual(c.get("c"), 3)

    def test_re_put_existing_key_updates_without_growing(self):
        c = SeriesCache(max_entries=2)
        c.put("a", 1)
        c.put("b", 2)
        c.put("a", 99)  # update, not a third entry
        self.assertEqual(c.get("a"), 99)
        self.assertEqual(c.get("b"), 2)  # "b" not evicted

    def test_positional_arg_is_max_entries_not_namespace(self):
        # namespace is keyword-only, so a positional int stays the bound (its
        # historical contract) and is never misread as a namespace.
        c = SeriesCache(1)
        c.put("a", 1)
        c.put("b", 2)  # exceeds bound 1 -> "a" evicted
        self.assertIsNone(c.get("a"))
        self.assertEqual(c.get("b"), 2)

    def test_namespaceless_cache_never_touches_disk(self):
        # The default (no namespace) is memory-only, so it must not create files
        # even for a settled key.
        with tempfile.TemporaryDirectory() as tmp:
            set_config({"data_cache_dir": tmp})
            SeriesCache().put(("cpi", "2020-01-01", 365), {"points": []})
            self.assertEqual(os.listdir(tmp), [])


@pytest.mark.unit
class SeriesCacheDiskTests(unittest.TestCase):
    """The opt-in cross-run disk layer (namespaced caches only)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        set_config({"data_cache_dir": self._tmp.name})

    def tearDown(self):
        self._tmp.cleanup()

    @staticmethod
    def _past_key():
        return ("cpi", "2020-01-01", 365)  # strictly before today -> settled

    def test_settled_entry_persists_across_instances(self):
        # Vendors build points as tuples; a disk round-trip must preserve that shape.
        value = {"points": [("2020-01-01", "1.0")]}
        SeriesCache(namespace="fred").put(self._past_key(), value)
        # A fresh instance (new process, same namespace) reads it back from disk.
        self.assertEqual(SeriesCache(namespace="fred").get(self._past_key()), value)

    def test_today_is_persisted_in_short_lived_cache(self):
        today = datetime.date.today().isoformat()
        SeriesCache(namespace="fred").put(("cpi", today, 365), {"points": []})
        self.assertEqual(
            SeriesCache(namespace="fred").get(("cpi", today, 365)),
            {"points": []},
        )

    def test_recent_entry_expires_after_one_hour(self):
        today = datetime.date.today().isoformat()
        key = ("cpi", today, 365)
        c = SeriesCache(namespace="fred")
        c.put(key, {"points": [(today, "1")]})
        path = c._recent_disk_file(key)
        old = time.time() - macro_common._RECENT_DISK_TTL_SECONDS - 1
        os.utime(path, (old, old))
        self.assertIsNone(SeriesCache(namespace="fred").get(key))
        self.assertFalse(os.path.exists(path))

    def test_recent_memory_entry_expires_after_one_hour(self):
        today = datetime.date.today().isoformat()
        key = ("cpi", today, 365)
        c = SeriesCache(namespace="fred")
        now = time.time()
        with mock.patch.object(macro_common.time, "time", return_value=now):
            c.put(key, {"points": [(today, "1")]})
        path = c._recent_disk_file(key)
        os.utime(path, (now, now))
        with mock.patch.object(
            macro_common.time,
            "time",
            return_value=now + macro_common._RECENT_DISK_TTL_SECONDS + 1,
        ):
            self.assertIsNone(c.get(key))

    def test_recent_memory_entry_is_invalidated_when_key_becomes_settled(self):
        key = ("cpi", "2026-01-01", 365)
        c = SeriesCache(namespace="fred")
        with mock.patch.object(c, "_is_settled", return_value=False):
            c.put(key, {"points": [("2026-01-01", "recent")]})
        with mock.patch.object(c, "_is_settled", return_value=True):
            self.assertIsNone(c.get(key))

    def test_recent_file_is_not_read_after_key_becomes_settled(self):
        key = ("cpi", "2026-01-01", 365)
        c = SeriesCache(namespace="fred")
        recent_path = c._recent_disk_file(key)
        os.makedirs(os.path.dirname(recent_path), exist_ok=True)
        with open(recent_path, "w", encoding="utf-8") as fh:
            fh.write('{"points": [["2026-01-01", "stale"]]}')
        with mock.patch.object(c, "_is_settled", return_value=True):
            self.assertIsNone(c.get(key))

    def test_distinct_keys_that_sanitize_alike_do_not_collide(self):
        # "a/b" and "a?b" both sanitize to the prefix "a_b"; the key hash must keep
        # their files apart so one series never serves another's data (FRED accepts
        # arbitrary raw series ids, so this is reachable).
        k1 = ("a/b", "2020-01-01", 365)
        k2 = ("a?b", "2020-01-01", 365)
        c = SeriesCache(namespace="fred")
        self.assertNotEqual(c._disk_file(k1), c._disk_file(k2))
        c.put(k1, {"id": "slash"})
        c.put(k2, {"id": "question"})
        self.assertEqual(SeriesCache(namespace="fred").get(k1), {"id": "slash"})
        self.assertEqual(SeriesCache(namespace="fred").get(k2), {"id": "question"})

    def test_namespaces_do_not_collide_on_disk(self):
        SeriesCache(namespace="fred").put(self._past_key(), {"src": "fred"})
        SeriesCache(namespace="estat").put(self._past_key(), {"src": "estat"})
        self.assertEqual(SeriesCache(namespace="fred").get(self._past_key()), {"src": "fred"})
        self.assertEqual(SeriesCache(namespace="estat").get(self._past_key()), {"src": "estat"})

    def test_clear_is_memory_only_and_keeps_disk(self):
        # clear() must NOT delete the persisted layer: several vendor tests call it
        # without redirecting data_cache_dir, and wiping disk would destroy the
        # user's real macro cache. A fresh instance still reads the entry from disk.
        c = SeriesCache(namespace="fred")
        c.put(self._past_key(), {"points": []})
        c.clear()
        self.assertEqual(SeriesCache(namespace="fred").get(self._past_key()), {"points": []})

    def test_corrupt_disk_file_degrades_to_miss(self):
        c = SeriesCache(namespace="fred")
        path = c._disk_file(self._past_key())
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.assertIsNone(c.get(self._past_key()))

    def test_disk_hit_restores_point_tuples(self):
        # A live fetch returns points as tuples; a disk hit must too (JSON would
        # otherwise hand back lists and make the return shape cache-dependent).
        SeriesCache(namespace="fred").put(self._past_key(), {"points": [("2020-01-01", "1.0")]})
        got = SeriesCache(namespace="fred").get(self._past_key())
        self.assertEqual(got["points"], [("2020-01-01", "1.0")])
        self.assertIsInstance(got["points"][0], tuple)

    def test_stale_disk_entry_is_ignored_and_deleted(self):
        # Past-date values can be revised upstream, so an aged file is re-fetched
        # rather than trusted forever — and removed, not left to accumulate.
        c = SeriesCache(namespace="fred")
        c.put(self._past_key(), {"points": []})
        path = c._disk_file(self._past_key())
        old = time.time() - (macro_common._DISK_TTL_SECONDS + 10)
        os.utime(path, (old, old))
        c._data.clear()  # force the disk path
        self.assertIsNone(c.get(self._past_key()))
        self.assertFalse(os.path.exists(path))  # deleted, not merely ignored

    def test_prune_caps_file_count_evicting_oldest(self):
        # The disk layer is count-bounded like the in-memory LRU: a prune keeps the
        # newest _DISK_MAX_FILES by mtime so a long backtest can't grow it forever.
        c = SeriesCache(namespace="fred")
        keys = [("cpi", f"2020-02-{i + 1:02d}", 365) for i in range(5)]
        for age, key in enumerate(keys):
            c.put(key, {"points": []})
            os.utime(c._disk_file(key), (1000 + age, 1000 + age))  # deterministic mtime
        disk_dir = c._disk_dir()
        with mock.patch.object(macro_common, "_DISK_MAX_FILES", 2):
            c._prune(disk_dir)
        survivors = {f for f in os.listdir(disk_dir) if f.endswith(".json")}
        self.assertEqual(len(survivors), 2)
        # The two newest keys (last written) survive; the three oldest are evicted.
        self.assertEqual(survivors, {os.path.basename(c._disk_file(k)) for k in keys[-2:]})

    def test_new_process_prunes_on_first_write(self):
        # Prior runs leave the dir over cap; a brand-new instance (new process, with
        # a fresh write counter that would never hit the throttle) must still prune
        # on its first settled write — the cross-run bound.
        seed = SeriesCache(namespace="fred")
        for i in range(5):
            key = ("cpi", f"2020-06-{i + 1:02d}", 365)
            seed.put(key, {"points": []})
            os.utime(seed._disk_file(key), (1000 + i, 1000 + i))
        with mock.patch.object(macro_common, "_DISK_MAX_FILES", 3):
            fresh = SeriesCache(namespace="fred")  # simulates a new process
            fresh.put(("cpi", "2020-06-06", 365), {"points": []})  # first write -> prunes
        survivors = [f for f in os.listdir(fresh._disk_dir()) if f.endswith(".json")]
        self.assertEqual(len(survivors), 3)  # capped despite the fresh write counter

    def test_recent_dates_within_grace_are_not_persisted(self):
        # A source can lag the host clock, so a very recent date may still be
        # publishing: it stays memory-only until it clears the grace window.
        c = SeriesCache(namespace="fred")
        today = datetime.date.today()
        recent = (today - datetime.timedelta(days=1)).isoformat()
        cleared = (today - datetime.timedelta(days=macro_common._SETTLE_GRACE_DAYS + 1)).isoformat()
        self.assertFalse(c._is_settled(("cpi", recent, 365)))  # too recent -> not persisted
        self.assertTrue(c._is_settled(("cpi", cleared, 365)))  # past the grace -> persisted

    def test_is_settled_tolerates_unpadded_dates(self):
        # A non-zero-padded curr_date must still be classified by calendar date, not
        # a string compare that mis-orders it (which would silently skip caching).
        c = SeriesCache(namespace="fred")
        self.assertTrue(c._is_settled(("cpi", "2020-1-5", 365)))  # past, non-padded
        today = datetime.date.today()
        unpadded_today = f"{today.year}-{today.month}-{today.day}"
        self.assertFalse(c._is_settled(("cpi", unpadded_today, 365)))  # today, not settled

    def test_prune_reclaims_orphaned_temp_files(self):
        # A crash between mkstemp and rename leaves a *.tmp; prune reclaims aged ones
        # but leaves a fresh (possibly in-flight) one alone.
        c = SeriesCache(namespace="fred")
        disk_dir = c._disk_dir()
        os.makedirs(disk_dir, exist_ok=True)
        old_tmp = os.path.join(disk_dir, "orphan.tmp")
        fresh_tmp = os.path.join(disk_dir, "inflight.tmp")
        for p in (old_tmp, fresh_tmp):
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("partial")
        os.utime(old_tmp, (time.time() - macro_common._TMP_ORPHAN_SECONDS - 10,) * 2)
        c._prune(disk_dir)
        self.assertFalse(os.path.exists(old_tmp))  # aged orphan reclaimed
        self.assertTrue(os.path.exists(fresh_tmp))  # recent tmp left untouched

    def test_long_process_still_prunes_every_interval(self):
        # Once pruned for the process, the write counter continues to bound a long run.
        c = SeriesCache(namespace="fred")
        c._pruned = True  # simulate the first-write prune already having happened
        with (
            mock.patch.object(macro_common, "_PRUNE_EVERY", 2),
            mock.patch.object(c, "_prune") as prune,
        ):
            c.put(("cpi", "2020-07-01", 365), {"points": []})  # write 1: below threshold
            prune.assert_not_called()
            c.put(("cpi", "2020-07-02", 365), {"points": []})  # write 2: threshold hit
        prune.assert_called_once()


if __name__ == "__main__":
    unittest.main()
