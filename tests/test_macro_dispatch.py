"""Macro microscope dispatch: get_macro_indicators routes an indicator to the
vendor that owns it.

The default macro_data vendor is "macro" (macro.py), which dispatches by indicator
to a single owning source (e-Stat for Japan CPI, BOJ for Japan policy rate /
Tankan, fred otherwise). Each vendor's fetch_series is mocked, so these run
without network or keys.
"""
import copy
import unittest
from unittest import mock

import pytest

import tradingagents.dataflows.config as config_module
import tradingagents.default_config as default_config
from tradingagents.dataflows import boj, estat, fred, macro_panel
from tradingagents.dataflows.interface import route_to_vendor
from tradingagents.provenance import extract_provenance


def _data(series_id, title, points):
    return {
        "series_id": series_id, "title": title, "units": "u", "frequency": "Monthly",
        "seasonal": "", "start_date": "2025-06-01", "points": points,
    }


@pytest.mark.unit
class MacroDispatchTests(unittest.TestCase):
    def setUp(self):
        config_module._config = copy.deepcopy(default_config.DEFAULT_CONFIG)
        for vendor in (fred, estat, boj):
            vendor._series_cache.clear()

    def tearDown(self):
        config_module._config = copy.deepcopy(default_config.DEFAULT_CONFIG)

    def test_default_macro_vendor_is_the_dispatcher(self):
        self.assertEqual(
            default_config.DEFAULT_CONFIG["data_vendors"]["macro_data"], "macro"
        )

    def test_owned_but_empty_japan_series_does_not_fall_through_to_fred(self):
        # An owned alias with an empty window returns e-Stat's "no data" note; the
        # dispatcher must NOT fall through to fred (fred can't serve jp_cpi).
        with mock.patch.object(estat, "fetch_series", return_value=None), \
                mock.patch.object(fred, "fetch_series", side_effect=AssertionError("fred called")):
            out = route_to_vendor("get_macro_indicators", "jp_cpi", "2026-06-20")
        self.assertIn("e-Stat: no data", out)
        record = extract_provenance(out)[0]
        self.assertEqual(record.effective, "—")
        self.assertEqual(
            record.timing,
            "available; no observations in requested window",
        )

    def test_invalid_fred_indicator_is_not_marked_as_observation_data(self):
        with mock.patch.object(
            fred,
            "fetch_series",
            side_effect=ValueError("not a known macro alias"),
        ):
            out = route_to_vendor(
                "get_macro_indicators",
                "not a valid indicator phrase",
                "2026-06-20",
            )
        record = extract_provenance(out)[0]
        self.assertEqual(record.effective, "—")
        self.assertEqual(record.timing, "invalid indicator or vendor request")

    def test_japan_cpi_routes_to_estat(self):
        with mock.patch.object(estat, "fetch_series",
                               return_value=_data("0003427113/0001", "Japan CPI", [("2026-05-01", "113.5")])), \
                mock.patch.object(boj, "fetch_series", side_effect=AssertionError("boj called")), \
                mock.patch.object(fred, "fetch_series", side_effect=AssertionError("fred called")):
            out = route_to_vendor("get_macro_indicators", "jp_cpi", "2026-06-20")
        self.assertIn("## e-Stat: Japan CPI", out)

    def test_japan_policy_rate_falls_through_estat_to_boj(self):
        # estat doesn't own jp_policy_rate -> NoMarketDataError -> boj serves it.
        with mock.patch.object(boj, "fetch_series",
                               return_value=_data("STRDCLUCON", "Japan policy rate", [("2026-06-19", "0.977")])), \
                mock.patch.object(fred, "fetch_series", side_effect=AssertionError("fred called")):
            out = route_to_vendor("get_macro_indicators", "jp_policy_rate", "2026-06-20")
        self.assertIn("## BOJ: Japan policy rate", out)

    def test_us_indicator_routes_to_fred(self):
        with mock.patch.object(fred, "fetch_series",
                               return_value=_data("CPIAUCSL", "US CPI", [("2026-05-01", "333.9")])):
            out = route_to_vendor("get_macro_indicators", "cpi", "2026-06-20")
        self.assertIn("## FRED: US CPI", out)

    def test_missing_fred_key_degrades_with_the_fred_reason_not_a_jp_one(self):
        # Regression guard: a US indicator with no FRED key must degrade naming the
        # real cause (FRED unavailable), NOT a misleading "not a BOJ series" no-data
        # verdict (the old fall-through-chain leaked the last vendor's rejection).
        with mock.patch.object(fred, "fetch_series",
                               side_effect=fred.FredNotConfiguredError("FRED_API_KEY not set")):
            out = route_to_vendor("get_macro_indicators", "cpi", "2026-06-20")
        self.assertIn("DATA_UNAVAILABLE", out)
        self.assertIn("FRED_API_KEY", out)
        self.assertNotIn("not a BOJ series", out)
        self.assertNotIn("NO_DATA_AVAILABLE", out)


@pytest.mark.unit
class PanelDispatchConsistencyTests(unittest.TestCase):
    """The panel's per-cell source must agree with where the dispatcher would
    route that indicator, so panel and microscope never diverge on a source.

    Ownership lives in two encodings — the dispatcher's ESTAT_SERIES/BOJ_SERIES
    membership and the panel's hardcoded (source, indicator) tuples — so this
    guards against silent drift (a rename/move would otherwise make a panel cell
    render "n/a" while the microscope still works). FRED cells use raw series IDs
    that are intentionally in no SERIES dict, so they must resolve to fred."""

    def _panel_specs(self):
        specs = []
        for _dim, section in macro_panel._REGIONAL_SECTIONS:
            for _label, by_region in section:
                specs.extend(spec for spec in by_region.values() if spec)
        specs.extend(spec for _label, spec in macro_panel._GLOBAL_RISK)
        return specs

    def test_every_panel_cell_source_matches_dispatch_ownership(self):
        for source, indicator in self._panel_specs():
            key = indicator.strip().lower()
            in_estat, in_boj = key in estat.ESTAT_SERIES, key in boj.BOJ_SERIES
            if source == "estat":
                self.assertTrue(in_estat, f"{indicator!r} not owned by e-Stat")
                self.assertFalse(in_boj)
            elif source == "boj":
                self.assertTrue(in_boj, f"{indicator!r} not owned by BOJ")
                self.assertFalse(in_estat)
            else:  # fred: a raw FRED id/alias, must be in no JP SERIES dict
                self.assertEqual(source, "fred")
                self.assertFalse(in_estat or in_boj, f"{indicator!r} is JP-owned, not fred")


if __name__ == "__main__":
    unittest.main()
