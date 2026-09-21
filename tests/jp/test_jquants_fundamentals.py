"""J-Quants /fins/summary -> the four fundamental tools. Network is mocked."""

import copy
import unittest
from unittest import mock

import pytest

import tradingagents.configuration.defaults as default_config
from tests.data_policy import configure_data, request_context
from tradingagents.data import interface
from tradingagents.data.jp import jquants_fundamentals as jf
from tradingagents.domain.data_result import DataResult
from tradingagents.domain.vendor_errors import NoMarketDataError


def _summary(
    disc_date,
    *,
    per_type="FY",
    per_end="2023-03-31",
    ta="1000",
    eq="400",
    sales="500",
    op="80",
    odp="85",
    np_="60",
    eps="12.3",
    bps="250",
    cfo="90",
    cfi="-30",
    cff="-20",
    casheq="200",
    disc_time="15:00:00",
    doc_type=None,
):
    return {
        "Code": "86970",
        "DiscDate": disc_date,
        "DiscTime": disc_time,
        "DocType": doc_type or f"{per_type}FinancialStatements_Consolidated_IFRS",
        "CurPerType": per_type,
        "CurPerEn": per_end,
        "CurFYEn": "2023-03-31",
        "TA": ta,
        "Eq": eq,
        "Sales": sales,
        "OP": op,
        "OdP": odp,
        "NP": np_,
        "EPS": eps,
        "BPS": bps,
        "CFO": cfo,
        "CFI": cfi,
        "CFF": cff,
        "CashEq": casheq,
    }


def _patch(records):
    return mock.patch(
        "tradingagents.data.jp.jquants_common.fetch_records",
        return_value=records,
    )


@pytest.mark.unit
class FundamentalsTests(unittest.TestCase):
    def setUp(self):
        jf._summary_cache.clear()

    def tearDown(self):
        jf._summary_cache.clear()

    def test_summary_fetch_is_memoized_across_tools(self):
        # The four fundamental tools share one /fins/summary fetch per ticker.
        mock_fetch = mock.Mock(return_value=[_summary("2023-05-10")])
        with mock.patch("tradingagents.data.jp.jquants_common.fetch_records", mock_fetch):
            jf.get_fundamentals("9984.T", data_context=request_context())
            jf.get_balance_sheet("9984.T", data_context=request_context())
            jf.get_cashflow("9984.T", data_context=request_context())
            jf.get_income_statement("9984.T", data_context=request_context())
        mock_fetch.assert_called_once()

    def test_undated_record_excluded_under_curr_date(self):
        undated = _summary("2023-05-10", sales="999")
        undated["DiscDate"] = None
        with _patch([_summary("2023-05-10", sales="500"), undated]):
            out = jf.get_fundamentals(
                "9984.T", curr_date="2023-12-31", data_context=request_context()
            )
        self.assertIn("Net sales: 500", out.content)
        self.assertNotIn("999", out.content)

    def test_overview_uses_latest_disclosed_period(self):
        # Input ascending by date (as J-Quants returns); latest must win.
        recs = [_summary("2022-05-10", sales="400"), _summary("2023-05-10", sales="500")]
        with _patch(recs):
            out = jf.get_fundamentals("9984.T", data_context=request_context())
        self.assertIn("9984.T", out.content)
        self.assertIn("Net sales: 500", out.content)
        self.assertIn("EPS: 12.3", out.content)
        self.assertIn("operating: 90", out.content)

    def test_duplicate_period_keeps_latest_visible_disclosure(self):
        recs = [
            _summary("2023-05-10", sales="400"),
            _summary("2023-05-12", sales="500"),
        ]
        with _patch(recs):
            before = jf.get_income_statement(
                "9984.T", "annual", "2023-05-11", data_context=request_context()
            )
            after = jf.get_income_statement(
                "9984.T", "annual", "2023-05-13", data_context=request_context()
            )
        self.assertIn("NetSales=400", before.content)
        self.assertIn("disclosed 2023-05-10", before.content)
        self.assertIn("NetSales=500", after.content)
        self.assertNotIn("NetSales=400", after.content)
        self.assertEqual(after.content.count("FY end 2023-03-31"), 1)

    def test_duplicate_period_same_timestamp_keeps_later_api_record(self):
        recs = [
            _summary("2023-05-12", sales="400"),
            _summary("2023-05-12", sales="500"),
        ]
        with _patch(recs):
            out = jf.get_income_statement(
                "9984.T", "annual", "2023-05-13", data_context=request_context()
            )
        self.assertIn("NetSales=500", out.content)
        self.assertNotIn("NetSales=400", out.content)
        self.assertEqual(out.content.count("FY end 2023-03-31"), 1)

    def test_dedupe_retains_distinct_doc_types_and_incomplete_keys(self):
        consolidated = _summary("2023-05-12", sales="500")
        standalone = _summary(
            "2023-05-11",
            sales="300",
            doc_type="FYFinancialStatements_NonConsolidated_JP",
        )
        incomplete_a = _summary("2023-05-09", sales="200")
        incomplete_b = _summary("2023-05-08", sales="100")
        incomplete_a["DocType"] = None
        incomplete_b["DocType"] = None
        with _patch([consolidated, standalone, incomplete_a, incomplete_b]):
            _, periods = jf.fetch_periods("9984.T", "2023-05-13")
        self.assertEqual([r["Sales"] for r in periods], ["500", "300", "200", "100"])

    def test_balance_sheet_derives_liabilities(self):
        with _patch([_summary("2023-05-10", ta="1000", eq="400")]):
            out = jf.get_balance_sheet("9984.T", data_context=request_context())
        self.assertIn("TotalAssets=1000", out.content)
        self.assertIn("TotalLiabilities=600.0", out.content)  # 1000 - 400
        self.assertIn("NetAssets=400", out.content)

    def test_cashflow_fields(self):
        with _patch([_summary("2023-05-10", cfo="90", cfi="-30", cff="-20", casheq="200")]):
            out = jf.get_cashflow("9984.T", data_context=request_context())
        self.assertIn("Operating=90", out.content)
        self.assertIn("Investing=-30", out.content)
        self.assertIn("Financing=-20", out.content)
        self.assertIn("CashEnd=200", out.content)

    def test_income_statement_fields(self):
        with _patch([_summary("2023-05-10", sales="500", op="80", np_="60", eps="12.3")]):
            out = jf.get_income_statement("9984.T", data_context=request_context())
        self.assertIn("NetSales=500", out.content)
        self.assertIn("OperatingProfit=80", out.content)
        self.assertIn("NetProfit=60", out.content)
        self.assertIn("EPS=12.3", out.content)

    def test_income_statement_explains_ifrs_missing_fields(self):
        with _patch([_summary("2023-05-10", op="", odp="")]):
            out = jf.get_income_statement("9984.T", data_context=request_context())
        self.assertIn("Consolidated, IFRS", out.content)
        self.assertIn("OperatingProfit=not provided in J-Quants summary", out.content)
        self.assertIn("OrdinaryProfit=not applicable (IFRS)", out.content)

    def test_japanese_gaap_missing_ordinary_profit_is_not_called_ifrs_na(self):
        record = _summary(
            "2023-05-10",
            op="",
            odp="",
            doc_type="FYFinancialStatements_NonConsolidated_JP",
        )
        with _patch([record]):
            out = jf.get_income_statement("9984.T", data_context=request_context())
        self.assertIn("Non-consolidated, Japanese GAAP", out.content)
        self.assertIn("OrdinaryProfit=not provided in J-Quants summary", out.content)
        self.assertNotIn("not applicable (IFRS)", out.content)

    def test_lookahead_excludes_future_disclosures(self):
        recs = [_summary("2023-05-10", sales="500"), _summary("2024-05-10", sales="999")]
        with _patch(recs):
            out = jf.get_fundamentals(
                "9984.T", curr_date="2023-12-31", data_context=request_context()
            )
        self.assertIn("Net sales: 500", out.content)
        self.assertNotIn("999", out.content)

    def test_no_disclosure_on_or_before_curr_date_raises(self):
        with _patch([_summary("2024-05-10")]), self.assertRaises(NoMarketDataError):
            jf.get_fundamentals("9984.T", curr_date="2023-12-31", data_context=request_context())

    def test_empty_response_raises(self):
        with _patch([]), self.assertRaises(NoMarketDataError):
            jf.get_fundamentals("9984.T", data_context=request_context())

    def test_annual_freq_narrows_to_full_year(self):
        recs = [
            _summary("2023-02-10", per_type="3Q", per_end="2022-12-31", sales="120"),
            _summary("2023-05-10", per_type="FY", per_end="2023-03-31", sales="500"),
        ]
        with _patch(recs):
            out = jf.get_income_statement("9984.T", freq="annual", data_context=request_context())
        self.assertIn("NetSales=500", out.content)
        self.assertNotIn("NetSales=120", out.content)  # 3Q excluded

    def test_missing_values_render_na(self):
        with _patch([_summary("2023-05-10", ta=None, eq=None)]):
            out = jf.get_balance_sheet("9984.T", data_context=request_context())
        self.assertIn("TotalAssets=N/A", out.content)
        self.assertIn("TotalLiabilities=N/A", out.content)  # cannot derive without TA/Eq


@pytest.mark.unit
class FundamentalsRoutingTests(unittest.TestCase):
    def setUp(self):
        configure_data(copy.deepcopy(default_config.DEFAULT_CONFIG), merge=False)

    def tearDown(self):
        configure_data(copy.deepcopy(default_config.DEFAULT_CONFIG), merge=False)

    def test_jquants_registered_for_all_fundamental_methods(self):
        for method in (
            "get_fundamentals",
            "get_balance_sheet",
            "get_cashflow",
            "get_income_statement",
        ):
            self.assertIn("jquants", interface.VENDOR_METHODS[method])

    def test_tokyo_ticker_routes_fundamentals_to_jquants(self):
        configure_data({"data_vendors_by_market": {".T": {"fundamental_data": "jquants"}}})
        jq = mock.Mock(return_value=DataResult("JQ_FUND"))
        yf = mock.Mock(return_value=DataResult("YF_FUND"))
        with mock.patch.dict(
            interface.VENDOR_METHODS,
            {"get_fundamentals": {"yfinance": yf, "jquants": jq}},
            clear=False,
        ):
            result = interface.route_to_vendor(
                "get_fundamentals", "9984.T", "2026-06-23", data_context=request_context()
            )
        self.assertEqual(result.content, "JQ_FUND")
        yf.assert_not_called()


if __name__ == "__main__":
    unittest.main()
