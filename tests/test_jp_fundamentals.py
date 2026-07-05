"""JP fundamentals assembler: date-safe valuation ratios computed from the
J-Quants summary + as-of price. All J-Quants/price fetches are mocked, so these
run without network or keys."""
import unittest
from unittest import mock

import pandas as pd
import pytest

from tradingagents.dataflows import jp_fundamentals


def _price_df(close, high, low, date="2026-06-26"):
    """A minimal OHLCV frame whose last Close / High-max / Low-min drive the ratios."""
    return pd.DataFrame({
        "Date": [pd.Timestamp("2025-07-01"), pd.Timestamp(date)],
        "Open": [low, close], "High": [high, close], "Low": [low, low],
        "Close": [low, close], "Volume": [1, 1],
    })


# A full-year (FY) disclosure like 7011.T's latest — TTM == FY here.
_FY = {
    "CurPerType": "FY", "CurFYEn": "2026-03-31", "CurPerEn": "2026-03-31",
    "Sales": "4974168000000", "NP": "332129000000", "EPS": "98.86", "BPS": "919.16",
    "Eq": "3228400000000", "TA": "8269711000000",
    "ShOutFY": "3373647810", "TrShFY": "13438470",
    "DivAnn": "25.0", "PayoutRatioAnn": "0.253", "EqAR": "0.373", "NxFEPS": "113.09",
}


@pytest.mark.unit
class JPFundamentalsTests(unittest.TestCase):
    def _run(self, records, price_df):
        with mock.patch.object(jp_fundamentals.jqf, "get_fundamentals", return_value="BASE-OVERVIEW"), \
                mock.patch.object(jp_fundamentals.jqf, "fetch_periods", return_value=("7011.T", records)), \
                mock.patch.object(jp_fundamentals, "_fetch_ohlcv_frame", return_value=price_df):
            return jp_fundamentals.get_fundamentals("7011.T", "2026-06-26")

    def test_fy_case_matches_golden_ratios(self):
        # FY disclosure (7011.T @ 2026-06-26): every ratio checked against the
        # hand-computed golden values in the plan.
        out = self._run([_FY], _price_df(3567.0, 5208.0, 3171.0))
        self.assertIn("BASE-OVERVIEW", out)                       # official summary preserved
        self.assertIn("Market cap: ¥11.99T", out)                 # 3567 × (ShOutFY−TrShFY)
        self.assertIn("shares 3,360,209,340", out)
        self.assertIn("PE: 36.08 (TTM)", out)                     # 3567/98.86
        self.assertIn("PB: 3.88", out)                            # 3567/919.16
        self.assertIn("Dividend yield: 0.70% (DivAnn 25.00)", out)
        self.assertIn("Payout: 25.30%", out)
        self.assertIn("Forward PE: 31.54 (company guidance / 会社予想, EPS 113.09)", out)
        self.assertIn("PEG: 2.19", out)
        self.assertIn("1yr growth +14.4%", out)
        self.assertIn("Net margin: 6.68% (TTM)", out)
        self.assertIn("ROE: 10.76%", out)   # EPS/BPS (owners' basis), consistent with PB
        self.assertIn("ROA: 4.02%", out)
        self.assertIn("Equity ratio: 0.37", out)
        self.assertIn("52-week range: 3171.00 – 5208.00", out)

    def test_ttm_rolls_cumulative_quarters(self):
        # Mid-year: latest disclosure is a cumulative 3Q. TTM = 3Q_cum + prior_FY
        # − prior_year_3Q_cum. EPS TTM = 62.81 + 73.04 − 53.00 = 82.85.
        q3_2026 = {"CurPerType": "3Q", "CurFYEn": "2026-03-31", "CurPerEn": "2025-12-31",
                   "Sales": "3326976000000", "NP": "210996000000", "EPS": "62.81"}
        fy_2025 = {"CurPerType": "FY", "CurFYEn": "2025-03-31", "CurPerEn": "2025-03-31",
                   "Sales": "5027176000000", "NP": "245447000000", "EPS": "73.04",
                   "BPS": "698.91", "Eq": "2469823000000", "TA": "6658924000000",
                   "ShOutFY": "3373647810", "TrShFY": "13438470", "EqAR": "0.371"}
        q3_2025 = {"CurPerType": "3Q", "CurFYEn": "2025-03-31", "CurPerEn": "2024-12-31",
                   "Sales": "3000000000000", "NP": "180000000000", "EPS": "53.00"}
        out = self._run([q3_2026, fy_2025, q3_2025], _price_df(3567.0, 5208.0, 3171.0))
        self.assertIn("PE: 43.05 (TTM)", out)          # 3567 / 82.85
        self.assertNotIn("TTM unavailable", out)       # rolling succeeded

    def test_ttm_degrades_to_fy_when_rolling_inputs_missing(self):
        # Latest is a quarter but the prior-year same quarter is absent → cannot
        # roll → fall back to the latest full FY figure, labelled.
        q3_2026 = {"CurPerType": "3Q", "CurFYEn": "2026-03-31", "CurPerEn": "2025-12-31",
                   "Sales": "3326976000000", "NP": "210996000000", "EPS": "62.81"}
        fy_2025 = {"CurPerType": "FY", "CurFYEn": "2025-03-31", "CurPerEn": "2025-03-31",
                   "Sales": "5027176000000", "NP": "245447000000", "EPS": "73.04",
                   "BPS": "698.91", "Eq": "2469823000000", "TA": "6658924000000",
                   "ShOutFY": "3373647810", "TrShFY": "13438470", "EqAR": "0.371"}
        out = self._run([q3_2026, fy_2025], _price_df(3567.0, 5208.0, 3171.0))
        self.assertIn("PE: 48.84 (FY (TTM unavailable))", out)   # 3567 / 73.04

    def test_price_fetch_failure_degrades_price_ratios_but_keeps_summary(self):
        # No price (halted / no coverage): price-based ratios go N/A, but the
        # official summary and price-free ratios (margins/ROE/ROA) still render.
        with mock.patch.object(jp_fundamentals.jqf, "get_fundamentals", return_value="BASE-OVERVIEW"), \
                mock.patch.object(jp_fundamentals.jqf, "fetch_periods", return_value=("7011.T", [_FY])), \
                mock.patch.object(jp_fundamentals, "_fetch_ohlcv_frame", side_effect=RuntimeError("halted")):
            out = jp_fundamentals.get_fundamentals("7011.T", "2026-06-26")
        self.assertIn("BASE-OVERVIEW", out)
        self.assertIn("Price: N/A", out)
        self.assertIn("PE: N/A", out)
        self.assertIn("Market cap: N/A", out)
        self.assertIn("Net margin: 6.68% (TTM)", out)   # price-free ratio still computed
        self.assertIn("ROE: 10.76%", out)

    def test_look_ahead_date_is_propagated_to_both_sources(self):
        # Date-safety is the module's whole point: curr_date must reach BOTH the
        # record fetch and the price-window end. A regression that dropped it would
        # leak the future and the golden tests (arg-independent mocks) wouldn't
        # notice — so assert the received date args here.
        seen = {}

        def fake_periods(ticker, curr_date):
            seen["periods_date"] = curr_date
            return ("7011.T", [_FY])

        def fake_ohlcv(ticker, start, end):
            seen["ohlcv_end"] = end
            return _price_df(3567.0, 5208.0, 3171.0)

        with mock.patch.object(jp_fundamentals.jqf, "get_fundamentals", return_value="BASE"), \
                mock.patch.object(jp_fundamentals.jqf, "fetch_periods", side_effect=fake_periods), \
                mock.patch.object(jp_fundamentals, "_fetch_ohlcv_frame", side_effect=fake_ohlcv):
            jp_fundamentals.get_fundamentals("7011.T", "2024-03-15")
        self.assertEqual(seen["periods_date"], "2024-03-15")
        self.assertEqual(seen["ohlcv_end"], "2024-03-15")  # window ends at curr_date, not today

    def test_forecast_decline_suppresses_negative_peg(self):
        # Guidance below trailing → negative growth. A negative PEG is not
        # interpretable, so it must render N/A (forward PE still shows).
        fy_decline = {**_FY, "NxFEPS": "90.0"}   # 90.0 < EPS 98.86 → decline
        out = self._run([fy_decline], _price_df(3567.0, 5208.0, 3171.0))
        self.assertIn("PEG: N/A", out)
        self.assertIn("1yr growth -9.0%", out)
        self.assertNotIn("PEG: -", out)

    def test_no_statements_notes_unavailable_but_keeps_summary(self):
        # Only a forecast-revision row (no actual results) → valuation note, but
        # the base overview is preserved.
        revision = {"CurPerType": "FY", "CurFYEn": "2026-03-31", "DocType": "EarnForecastRevision"}
        out = self._run([revision], _price_df(3567.0, 5208.0, 3171.0))
        self.assertIn("BASE-OVERVIEW", out)
        self.assertIn("no statement disclosures", out)


if __name__ == "__main__":
    unittest.main()
