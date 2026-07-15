"""EDINET ticker→code resolution: seed load, self-healing cache, persistence.

The seed snapshot is real (shipped in-package); the learned cache is redirected
to a temp dir so tests never touch the user's real cache."""
import json
import os
import tempfile
import unittest
from unittest import mock

import pytest

import tradingagents.dataflows.config as config_module
import tradingagents.default_config as default_config
from tradingagents.dataflows.config import set_config
from tradingagents.dataflows.jp import edinet_code_map as cm


@pytest.mark.unit
class CodeMapTests(unittest.TestCase):
    def setUp(self):
        # Redirect the learned cache to a throwaway dir and reset in-memory maps.
        self._tmp = tempfile.TemporaryDirectory()
        set_config({"data_cache_dir": self._tmp.name})
        cm._reset_for_tests()

    def tearDown(self):
        cm._reset_for_tests()
        config_module._config = __import__("copy").deepcopy(default_config.DEFAULT_CONFIG)
        self._tmp.cleanup()

    def _cache_file(self):
        return os.path.join(self._tmp.name, cm._CACHE_FILENAME)

    def test_seed_resolves_known_tickers(self):
        # From the committed snapshot: SoftBank Group and Toyota.
        self.assertEqual(cm.resolve_edinet_code("9984.T"), "E02778")
        self.assertEqual(cm.resolve_edinet_code("7203.T"), "E02144")

    def test_unknown_ticker_returns_none(self):
        self.assertIsNone(cm.resolve_edinet_code("0000.T"))

    def test_learn_adds_unknown_issuer_and_persists(self):
        self.assertIsNone(cm.resolve_edinet_code("0000.T"))
        cm.learn("00000", "E99999")  # 5-digit secCode → 4-digit base "0000"
        self.assertEqual(cm.resolve_edinet_code("0000.T"), "E99999")
        # Persisted to the learned cache file (not the seed).
        with open(self._cache_file(), encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"0000": "E99999"})

    def test_learned_cache_survives_reload(self):
        cm.learn("00000", "E99999")
        cm._reset_for_tests()  # forget in-memory; force reload from disk
        self.assertEqual(cm.resolve_edinet_code("0000.T"), "E99999")

    def test_learn_skips_known_pairs_without_writing(self):
        # An issuer already in the seed should not create a cache file.
        cm.learn("99840", "E02778")  # SoftBank, already seeded
        self.assertFalse(os.path.exists(self._cache_file()))

    def test_learn_never_overrides_seed(self):
        # The curated seed is authoritative: a (mis-)learned pair for a seeded base
        # must not shadow it, and must not be persisted.
        cm.learn("99840", "E_WRONG")  # SoftBank base 9984 is in the seed
        self.assertEqual(cm.resolve_edinet_code("9984.T"), "E02778")
        self.assertFalse(os.path.exists(self._cache_file()))
        cm._reset_for_tests()  # and it never leaked to disk to win on reload
        self.assertEqual(cm.resolve_edinet_code("9984.T"), "E02778")

    def test_learn_many_batches_into_one_write(self):
        with mock.patch.object(cm, "_persist", wraps=cm._persist) as persist:
            cm.learn_many([
                ("00000", "E90000"),   # new base 0000
                ("00010", "E90001"),   # new base 0001
                ("99840", "E_WRONG"),  # seeded → ignored
                ("00000", "E90000"),   # duplicate of the first → no extra change
            ])
        persist.assert_called_once()  # one cache write for the whole batch
        self.assertEqual(cm.resolve_edinet_code("0000.T"), "E90000")
        self.assertEqual(cm.resolve_edinet_code("0001.T"), "E90001")

    def test_learn_many_no_changes_skips_write(self):
        with mock.patch.object(cm, "_persist") as persist:
            cm.learn_many([("99840", "E02778"), ("", "E1"), ("123", "E2")])
        persist.assert_not_called()

    def test_learn_ignores_incomplete_pairs(self):
        cm.learn("", "E1")
        cm.learn("00000", "")
        cm.learn(None, None)
        self.assertFalse(os.path.exists(self._cache_file()))

    def test_learn_ignores_non_listed_codes(self):
        # A filer secCode that is not a 5-digit listed base (e.g. empty/foreign)
        # must not pollute the map.
        cm.learn("123", "E55555")  # too short to be a listing base
        self.assertFalse(os.path.exists(self._cache_file()))

    def test_poisoned_cache_is_ignored(self):
        with open(self._cache_file(), "w", encoding="utf-8") as f:
            f.write("{not valid json")
        cm._reset_for_tests()
        # Seed still resolves; the bad cache is tolerated, not fatal.
        self.assertEqual(cm.resolve_edinet_code("9984.T"), "E02778")


if __name__ == "__main__":
    unittest.main()
