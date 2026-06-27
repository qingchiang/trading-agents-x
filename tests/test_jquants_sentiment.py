"""J-Quants investor-type flows (the Japanese sentiment proxy). Network mocked."""
import unittest
from unittest import mock

import pytest

from tradingagents.dataflows import jquants_sentiment as js
from tradingagents.dataflows.jquants_sentiment import get_investor_flows


def _week(pub, st, en, *, frgn="1000", ind="-500", invtr="200",
          trstbnk="300", busco="50"):
    return {
        "PubDate": pub, "StDate": st, "EnDate": en, "Section": "TSEPrime",
        "FrgnBal": frgn, "IndBal": ind, "InvTrBal": invtr,
        "TrstBnkBal": trstbnk, "BusCoBal": busco,
    }


def _patch(records=None, side_effect=None):
    return mock.patch.object(
        js, "fetch_records", return_value=records, side_effect=side_effect
    )


@pytest.mark.unit
class InvestorFlowTests(unittest.TestCase):
    def test_non_tokyo_ticker_returns_empty(self):
        # No network call for a market this signal doesn't cover.
        with mock.patch.object(js, "fetch_records") as fr:
            self.assertEqual(get_investor_flows("AAPL", "2026-06-25"), "")
            self.assertEqual(get_investor_flows("0700.HK", "2026-06-25"), "")
        fr.assert_not_called()

    def test_renders_recent_weeks_with_signed_net_flows(self):
        weeks = [
            _week("2026-06-12", "2026-06-01", "2026-06-05", frgn="2000", ind="-800"),
            _week("2026-06-19", "2026-06-08", "2026-06-12", frgn="1500", ind="-300"),
        ]
        with _patch(weeks):
            out = get_investor_flows("9984.T", "2026-06-25")
        self.assertIn("TSEPrime", out)
        self.assertIn("投資部門別売買状況", out)
        self.assertIn("Foreigners +1,500", out)   # newest week first
        self.assertIn("Individuals -300", out)
        self.assertLess(out.index("+1,500"), out.index("+2,000"))  # newest on top

    def test_lookahead_excludes_unpublished_weeks(self):
        weeks = [
            _week("2026-06-19", "2026-06-08", "2026-06-12", frgn="111"),
            _week("2026-06-26", "2026-06-15", "2026-06-19", frgn="999"),  # pub after curr
        ]
        with _patch(weeks):
            out = get_investor_flows("9984.T", "2026-06-25")
        self.assertIn("+111", out)
        self.assertNotIn("+999", out)

    def test_no_published_data_returns_placeholder(self):
        with _patch([_week("2026-06-26", "2026-06-15", "2026-06-19")]):
            out = get_investor_flows("9984.T", "2026-06-25")
        self.assertIn("<no investor-flow data published", out)

    def test_caps_to_look_back_weeks(self):
        weeks = [
            _week(f"2026-0{m}-10", f"2026-0{m}-01", f"2026-0{m}-05")
            for m in range(1, 7)  # 6 weekly rows, all published before curr_date
        ]
        with _patch(weeks):
            out = get_investor_flows("9984.T", "2026-06-25", look_back_weeks=4)
        self.assertEqual(out.count("- Week "), 4)

    def test_fetch_error_degrades_without_raising(self):
        with _patch(side_effect=RuntimeError("boom")):
            out = get_investor_flows("9984.T", "2026-06-25")
        self.assertIn("<investor flows unavailable: RuntimeError>", out)

    def test_malformed_curr_date_degrades_without_raising(self):
        # The never-raise prefetch contract covers a bad curr_date, not just a
        # fetch error: the strptime must degrade, not escape the node.
        with _patch([_week("2026-06-19", "2026-06-08", "2026-06-12")]):
            out = get_investor_flows("9984.T", "not-a-date")
        self.assertIn("<investor flows unavailable: ValueError>", out)

    def test_missing_field_renders_na(self):
        with _patch([_week("2026-06-19", "2026-06-08", "2026-06-12", frgn=None)]):
            out = get_investor_flows("9984.T", "2026-06-25")
        self.assertIn("Foreigners N/A", out)


if __name__ == "__main__":
    unittest.main()
