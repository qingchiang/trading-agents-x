"""J-Quants investor-type flows (the Japanese sentiment proxy). Network mocked."""
import unittest
from unittest import mock

import pytest

from tradingagents.dataflows.jp import jquants_sentiment as js
from tradingagents.dataflows.jp.jquants_sentiment import (
    get_investor_flows,
    get_margin_balance,
    get_market_investor_flows,
    get_short_positions,
)


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
    def setUp(self):
        self.market = mock.patch.object(
            js, "get_company_market_section", return_value="TSEPrime"
        )
        self.market.start()

    def tearDown(self):
        self.market.stop()

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
        self.assertIn("MARKET-LEVEL CONTEXT ONLY — NOT 9984.T ORDER FLOW", out)
        self.assertIn("no security-level attribution", out)
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

    def test_resolved_standard_section_is_used_in_request(self):
        with mock.patch.object(
            js, "get_company_market_section", return_value="TSEStandard"
        ), _patch([_week("2026-06-19", "2026-06-08", "2026-06-12")]) as fr:
            out = get_market_investor_flows("2702.T", "2026-06-25")
        self.assertIn("TSEStandard aggregate", out)
        self.assertEqual(fr.call_args.args[1]["section"], "TSEStandard")

    def test_unknown_section_does_not_fetch_or_default_to_prime(self):
        with mock.patch.object(
            js, "get_company_market_section", return_value=None
        ), mock.patch.object(js, "fetch_records") as fr:
            out = get_market_investor_flows("9999.T", "2026-06-25")
        self.assertIn("not defaulting to TSEPrime", out)
        fr.assert_not_called()


def _margin(date, *, long="22000000", short="2000000"):
    return {"Date": date, "Code": "99840", "LongVol": long, "ShrtVol": short}


@pytest.mark.unit
class MarginBalanceTests(unittest.TestCase):
    def test_non_tokyo_ticker_returns_empty(self):
        with mock.patch.object(js, "fetch_records") as fr:
            self.assertEqual(get_margin_balance("AAPL", "2026-06-25"), "")
        fr.assert_not_called()

    def test_renders_recent_weeks_newest_first_with_credit_ratio(self):
        weeks = [
            _margin("2026-05-29", long="20000000", short="2000000"),
            _margin("2026-06-05", long="22000000", short="2000000"),
        ]
        with _patch(weeks):
            out = get_margin_balance("9984.T", "2026-06-25")
        self.assertIn("信用取引", out)
        self.assertIn("Week 2026-06-05", out)
        self.assertIn("買残(long) 22,000,000", out)
        self.assertIn("credit ratio 11.00x", out)  # 22M / 2M
        self.assertLess(out.index("2026-06-05"), out.index("2026-05-29"))  # newest on top

    def test_lookahead_guard_excludes_unpublished_week(self):
        # curr_date is a Monday; last Friday's record (06-19) only publishes T+2
        # business days later (Tue 06-23), so it must be excluded, while the prior
        # week (06-12, published Tue 06-16) is visible.
        weeks = [
            _margin("2026-06-12", short="2000000"),  # published 2026-06-16 -> visible
            _margin("2026-06-19", short="9999999"),  # publishes 2026-06-23 -> excluded
        ]
        with _patch(weeks):
            out = get_margin_balance("9984.T", "2026-06-22")
        self.assertIn("2026-06-12", out)
        self.assertNotIn("2026-06-19", out)
        self.assertNotIn("9,999,999", out)

    def test_year_end_closure_does_not_leak(self):
        # Regression for the old fixed 7-day lag: a Fri 2024-12-27 record only
        # publishes on 2025-01-06 (T+2 TSE business days across the New Year break),
        # so a backtest on 2025-01-03 (record+7) must NOT see it.
        with _patch([_margin("2024-12-27", short="9999999")]):
            self.assertEqual(get_margin_balance("9984.T", "2025-01-03"), "")
        with _patch([_margin("2024-12-27", short="9999999")]):
            self.assertIn("2024-12-27", get_margin_balance("9984.T", "2025-01-06"))

    def test_no_visible_weeks_returns_empty(self):
        # Only an unpublished week -> nothing to show -> omit the section.
        with _patch([_margin("2026-06-24")]):
            self.assertEqual(get_margin_balance("9984.T", "2026-06-25"), "")

    def test_caps_to_look_back_weeks(self):
        weeks = [_margin(f"2026-0{m}-05") for m in range(1, 6)]  # 5 published weeks
        with _patch(weeks):
            out = get_margin_balance("9984.T", "2026-06-25", look_back_weeks=3)
        self.assertEqual(out.count("- Week "), 3)

    def test_zero_short_balance_renders_na_ratio(self):
        with _patch([_margin("2026-06-05", short="0")]):
            out = get_margin_balance("9984.T", "2026-06-25")
        self.assertIn("credit ratio N/A", out)

    def test_fetch_error_degrades_to_placeholder(self):
        # Unlike a genuine no-data name (""), an error is surfaced so the LLM can
        # tell a lost official source from one this name lacks.
        with _patch(side_effect=RuntimeError("boom")):
            out = get_margin_balance("9984.T", "2026-06-25")
        self.assertIn("<margin balances unavailable: RuntimeError>", out)

    def test_malformed_curr_date_degrades_to_placeholder(self):
        with _patch([_margin("2026-06-05")]):
            out = get_margin_balance("9984.T", "not-a-date")
        self.assertIn("<margin balances unavailable: ValueError>", out)


def _short(disc, *, seller="Barclays Bank PLC", ratio="0.0052", prev=None):
    rec = {"DiscDate": disc, "Code": "99840", "SSName": seller, "ShrtPosToSO": ratio}
    if prev is not None:
        rec["PrevRptRatio"] = prev
    return rec


@pytest.mark.unit
class ShortPositionTests(unittest.TestCase):
    def test_non_tokyo_ticker_returns_empty(self):
        with mock.patch.object(js, "fetch_records") as fr:
            self.assertEqual(get_short_positions("AAPL", "2026-06-25"), "")
        fr.assert_not_called()

    def test_renders_events_newest_first_with_name_and_ratio(self):
        rows = [
            _short("2026-03-01", seller="Marshall Wace LLP", ratio="0.0061"),
            _short("2026-06-10", seller="Barclays Bank PLC", ratio="0.0052"),
        ]
        with _patch(rows):
            out = get_short_positions("9984.T", "2026-06-25")
        self.assertIn("空売り残高報告", out)
        self.assertIn("Barclays Bank PLC — 0.52% of shares out", out)
        self.assertIn("Marshall Wace LLP", out)
        self.assertLess(out.index("2026-06-10"), out.index("2026-03-01"))  # newest first

    def test_trend_arrow_from_previous_ratio(self):
        with _patch([_short("2026-06-10", ratio="0.0052", prev="0.0048")]):
            out = get_short_positions("9984.T", "2026-06-25")
        self.assertIn("0.52% of shares out (was 0.48% ↑)", out)

    def test_lookahead_excludes_future_disclosures(self):
        rows = [
            _short("2026-06-10", ratio="0.0052"),
            _short("2026-06-30", ratio="0.0099"),  # disclosed after curr_date
        ]
        with _patch(rows):
            out = get_short_positions("9984.T", "2026-06-25")
        self.assertIn("2026-06-10", out)
        self.assertNotIn("2026-06-30", out)
        self.assertNotIn("0.99%", out)

    def test_window_excludes_stale_disclosures(self):
        rows = [
            _short("2024-01-01", ratio="0.0080"),  # older than look_back_days
            _short("2026-06-10", ratio="0.0052"),
        ]
        with _patch(rows):
            out = get_short_positions("9984.T", "2026-06-25")
        self.assertIn("2026-06-10", out)
        self.assertNotIn("2024-01-01", out)

    def test_unpadded_curr_date_does_not_leak_future(self):
        # "2026-7-5" is parseable but unpadded; a lexical compare against the raw
        # string would wrongly admit a 2026-07-30 disclosure. The normalized bound
        # must exclude it.
        rows = [_short("2026-06-10", ratio="0.0052"), _short("2026-07-30", ratio="0.0099")]
        with _patch(rows):
            out = get_short_positions("9984.T", "2026-7-5")
        self.assertIn("2026-06-10", out)
        self.assertNotIn("2026-07-30", out)

    def test_row_without_parseable_ratio_is_dropped(self):
        # A disclosure with no magnitude must not render as a bare bearish row;
        # a covered-to-0.00% position (ratio "0") is real data and must stay.
        rows = [
            _short("2026-06-11", seller="No Ratio LLP", ratio=""),
            _short("2026-06-10", seller="Covered Fund", ratio="0"),
        ]
        with _patch(rows):
            out = get_short_positions("9984.T", "2026-06-25")
        self.assertNotIn("No Ratio LLP", out)
        self.assertIn("Covered Fund — 0.00% of shares out", out)

    def test_no_disclosures_returns_empty(self):
        with _patch([]):
            self.assertEqual(get_short_positions("9984.T", "2026-06-25"), "")

    def test_caps_to_max_rows(self):
        rows = [_short(f"2026-06-{d:02d}") for d in range(1, 12)]  # 11 in window
        with _patch(rows):
            out = get_short_positions("9984.T", "2026-06-25", max_rows=5)
        self.assertEqual(out.count("- 2026-06-"), 5)

    def test_fetch_error_degrades_to_placeholder(self):
        with _patch(side_effect=RuntimeError("boom")):
            out = get_short_positions("9984.T", "2026-06-25")
        self.assertIn("<short positions unavailable: RuntimeError>", out)

    def test_malformed_curr_date_degrades_to_placeholder(self):
        with _patch([_short("2026-06-10")]):
            out = get_short_positions("9984.T", "not-a-date")
        self.assertIn("<short positions unavailable: ValueError>", out)


if __name__ == "__main__":
    unittest.main()
