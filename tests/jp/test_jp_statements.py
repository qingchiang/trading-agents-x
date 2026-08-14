"""JP statement assembler: J-Quants summary (authoritative, non-optional) plus a
curated, best-effort yfinance line-item detail block. All J-Quants/yfinance
access is mocked, so these run without network or keys."""
import unittest
from unittest import mock

import pandas as pd
import pytest

from tradingagents.dataflows.jp import jp_statements
from tradingagents.dataflows.symbol_utils import NoMarketDataError
from tradingagents.provenance import extract_provenance, strip_provenance_markers


def _frame(rowmap, dates=("2026-03-31", "2025-03-31")):
    """A yfinance-style statement frame: line items as rows, period ends as cols."""
    cols = [pd.Timestamp(d) for d in dates]
    return pd.DataFrame.from_dict(rowmap, orient="index", columns=cols)


def _live():
    return mock.patch.object(
        jp_statements,
        "is_near_live",
        return_value=True,
    )


@pytest.mark.unit
class JPStatementsTests(unittest.TestCase):
    def test_official_statement_paths_forward_the_information_frontier(self):
        frontier = "2026-08-10T23:59:00+09:00"
        for statement_name in (
            "get_balance_sheet",
            "get_cashflow",
            "get_income_statement",
        ):
            with self.subTest(statement=statement_name), mock.patch.object(
                jp_statements,
                "is_near_live",
                return_value=False,
            ), mock.patch.object(
                jp_statements.jqf,
                statement_name,
                return_value="JQ-STATEMENT",
            ) as jq:
                statement = getattr(jp_statements, statement_name)
                statement(
                    "4568.T",
                    "quarterly",
                    "2026-08-10",
                    information_frontier=frontier,
                )

                jq.assert_called_once_with(
                    "4568.T",
                    "quarterly",
                    "2026-08-10",
                    information_frontier=frontier,
                )

    def test_income_appends_only_curated_yfinance_rows(self):
        # The official J-Quants summary leads; the detail block carries only the
        # curated complement rows that are present — not every row in the frame.
        frame = _frame({"Gross Profit": [1000, 900], "EBITDA": [2000, 1800],
                        "Total Revenue": [9, 9]})  # Total Revenue not in the curated set
        with _live(), \
                mock.patch.object(jp_statements.jqf, "get_income_statement", return_value="JQ-INCOME"), \
                mock.patch.object(jp_statements, "get_statement_frame", return_value=frame):
            out = jp_statements.get_income_statement("7011.T", "annual", "2026-06-26")
        self.assertTrue(strip_provenance_markers(out).startswith("JQ-INCOME"))
        self.assertEqual(
            {record.source for record in extract_provenance(out)},
            {"J-Quants official summary", "yfinance curated detail"},
        )
        self.assertIn("Line-item detail (yfinance, curated", out)
        self.assertIn("Gross Profit", out)
        self.assertIn("EBITDA", out)
        self.assertNotIn("Total Revenue", out)                   # summary already has it
        self.assertIn("Requested analysis date: 2026-06-26", out)
        self.assertIn("Retrieval timestamp:", out)
        self.assertIn("Not point-in-time historical data", out)

    def test_drops_periods_yfinance_has_not_filled(self):
        # yfinance's line items lag ~1 FY, so its latest column is all-blank for
        # the curated rows — that column is dropped, older filled ones stay.
        frame = _frame({"Gross Profit": [None, 900], "EBITDA": [None, 1800]})
        with _live(), \
                mock.patch.object(jp_statements.jqf, "get_income_statement", return_value="JQ-INCOME"), \
                mock.patch.object(jp_statements, "get_statement_frame", return_value=frame):
            out = jp_statements.get_income_statement("7011.T", "annual", "2026-06-26")
        self.assertIn("Line-item detail", out)
        self.assertIn("2025-03-31", out)
        self.assertNotIn("2026-03-31", out)   # all-blank latest period dropped

    def test_omits_detail_when_all_curated_values_missing(self):
        # Curated rows present but every value blank → no useful detail → no block.
        frame = _frame({"Gross Profit": [None, None]})
        with _live(), \
                mock.patch.object(jp_statements.jqf, "get_income_statement", return_value="JQ-INCOME"), \
                mock.patch.object(jp_statements, "get_statement_frame", return_value=frame):
            out = jp_statements.get_income_statement("7011.T", "annual", "2026-06-26")
        self.assertEqual(strip_provenance_markers(out), "JQ-INCOME")

    def test_drops_all_blank_curated_row(self):
        # A curated row present but empty for every kept period is dropped, not
        # rendered as a blank CSV line.
        frame = _frame({"Gross Profit": [1000, 900], "EBITDA": [None, None]})
        with _live(), \
                mock.patch.object(jp_statements.jqf, "get_income_statement", return_value="JQ-INCOME"), \
                mock.patch.object(jp_statements, "get_statement_frame", return_value=frame):
            out = jp_statements.get_income_statement("7011.T", "annual", "2026-06-26")
        self.assertIn("Gross Profit", out)
        self.assertNotIn("EBITDA", out)   # all-blank curated row dropped

    def test_no_date_preserves_labelled_live_retrieval_mode(self):
        frame = _frame({"Gross Profit": [1000, 900]})
        with mock.patch.object(
            jp_statements.jqf,
            "get_income_statement",
            return_value="JQ-INCOME",
        ) as jq, mock.patch.object(
            jp_statements,
            "get_statement_frame",
            return_value=frame,
        ) as gsf:
            out = jp_statements.get_income_statement("7011.T", "annual", None)
        self.assertTrue(strip_provenance_markers(out).startswith("JQ-INCOME"))
        self.assertIn("No analysis date was provided", out)
        self.assertIn("treated as a live retrieval", out)
        self.assertIn("was not filtered to a historical cutoff", out)
        self.assertIn(
            "Requested analysis date: not provided (treated as live retrieval)",
            out,
        )
        self.assertIn("Line-item detail", out)
        jq.assert_called_once_with("7011.T", "annual", None)
        gsf.assert_called_once_with("7011.T", "income", "annual", None)

    def test_historical_date_does_not_request_yfinance_detail(self):
        with mock.patch.object(jp_statements, "is_near_live", return_value=False), \
                mock.patch.object(jp_statements.jqf, "get_cashflow", return_value="JQ-CF"), \
                mock.patch.object(jp_statements, "get_statement_frame") as gsf:
            out = jp_statements.get_cashflow("7011.T", "quarterly", "2024-03-15")
        self.assertIn("Requested analysis date: 2024-03-15", out)
        self.assertIn("do not expose point-in-time filing timestamps", out)
        gsf.assert_not_called()

    def test_omits_detail_when_yfinance_unavailable(self):
        # No yfinance frame (no coverage / fetch failed) → summary only, no block.
        with _live(), \
                mock.patch.object(jp_statements.jqf, "get_income_statement", return_value="JQ-INCOME"), \
                mock.patch.object(jp_statements, "get_statement_frame", return_value=None):
            out = jp_statements.get_income_statement("7011.T", "annual", "2026-06-26")
        self.assertEqual(strip_provenance_markers(out), "JQ-INCOME")

    def test_omits_detail_when_no_curated_rows_present(self):
        # Frame exists but carries none of the curated labels → no empty block.
        frame = _frame({"Totally Other Line": [1, 2]})
        with _live(), \
                mock.patch.object(jp_statements.jqf, "get_balance_sheet", return_value="JQ-BS"), \
                mock.patch.object(jp_statements, "get_statement_frame", return_value=frame):
            out = jp_statements.get_balance_sheet("7011.T", "annual", "2026-06-26")
        self.assertEqual(strip_provenance_markers(out), "JQ-BS")
        self.assertNotIn("Line-item detail", out)

    def test_detail_exception_degrades_to_summary(self):
        # A yfinance-side error must not break the official summary (best-effort).
        with _live(), \
                mock.patch.object(jp_statements.jqf, "get_cashflow", return_value="JQ-CF"), \
                mock.patch.object(jp_statements, "get_statement_frame", side_effect=RuntimeError("boom")):
            out = jp_statements.get_cashflow("7011.T", "quarterly", "2026-06-26")
        self.assertEqual(strip_provenance_markers(out), "JQ-CF")

    def test_jquants_no_data_propagates(self):
        # The J-Quants base is non-optional: NoMarketDataError must propagate so
        # the router can fall through to another vendor (not be swallowed).
        with mock.patch.object(jp_statements.jqf, "get_balance_sheet",
                               side_effect=NoMarketDataError("7011.T", "7011.T", "none")), \
                self.assertRaises(NoMarketDataError):
            jp_statements.get_balance_sheet("7011.T", "annual", "2026-06-26")

    def test_live_kind_freq_and_curr_date_propagate_to_yfinance(self):
        seen = {}

        def fake_frame(ticker, kind, freq, curr_date):
            seen.update(ticker=ticker, kind=kind, freq=freq, curr_date=curr_date)
            return None

        with _live(), \
                mock.patch.object(jp_statements.jqf, "get_cashflow", return_value="JQ-CF"), \
                mock.patch.object(jp_statements, "get_statement_frame", side_effect=fake_frame):
            jp_statements.get_cashflow("7011.T", "quarterly", "2026-06-26")
        self.assertEqual(seen, {"ticker": "7011.T", "kind": "cashflow",
                                "freq": "quarterly", "curr_date": "2026-06-26"})


if __name__ == "__main__":
    unittest.main()
