"""Macro microscope dispatch: get_macro_indicators routes an indicator to the
vendor that owns it via the router's NoMarketDataError fall-through chain.

The default chain is "estat,boj,fred": estat/boj raise NoMarketDataError for an
indicator they don't serve, so the chain falls through to fred (the catch-all).
Each vendor's fetch_series is mocked, so these run without network or keys.
"""
import copy
import unittest
from unittest import mock

import pytest

import tradingagents.dataflows.config as config_module
import tradingagents.default_config as default_config
from tradingagents.dataflows import boj, estat, fred
from tradingagents.dataflows.interface import route_to_vendor


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

    def test_default_chain_is_estat_boj_fred(self):
        self.assertEqual(
            default_config.DEFAULT_CONFIG["data_vendors"]["macro_data"], "estat,boj,fred"
        )

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

    def test_us_indicator_falls_through_to_fred(self):
        # Neither estat nor boj owns "cpi"; both raise NoMarketDataError -> fred.
        with mock.patch.object(fred, "fetch_series",
                               return_value=_data("CPIAUCSL", "US CPI", [("2026-05-01", "333.9")])):
            out = route_to_vendor("get_macro_indicators", "cpi", "2026-06-20")
        self.assertIn("## FRED: US CPI", out)


if __name__ == "__main__":
    unittest.main()
