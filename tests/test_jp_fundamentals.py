"""JP fundamentals assembler: date-safe valuation ratios computed from the
J-Quants summary + as-of price. All J-Quants/price fetches are mocked, so these
run without network or keys."""
import unittest
from unittest import mock

import pandas as pd
import pytest
import requests

from tradingagents.dataflows import jp_fundamentals


def _price_df(close, high, low, date="2026-06-26"):
    """A minimal OHLCV frame whose last Close / High-max / Low-min drive the ratios.

    Only two rows, so it can't satisfy the beta regression's minimum overlap —
    beta renders N/A for these (a dedicated test below exercises a real beta)."""
    return pd.DataFrame({
        "Date": [pd.Timestamp("2025-07-01"), pd.Timestamp(date)],
        "Open": [low, close], "High": [high, close], "Low": [low, low],
        "Close": [low, close], "Volume": [1, 1],
    })


def _returns_frame(returns, start=100.0, origin="2024-01-05", freq="W-FRI"):
    """Build an OHLCV frame (dates on ``freq``) whose closes realize ``returns``.

    Close[i] = start * prod(1 + returns[:i]); resampling to W-FRI and taking
    pct_change() then recovers ``returns`` exactly (the dates are already
    Fridays), so a stock built with stock_ret = beta * index_ret regresses to
    exactly ``beta``. High/Low mirror Close (52-week range irrelevant here)."""
    closes, price = [], start
    for r in returns:
        price *= (1 + r)
        closes.append(price)
    dates = pd.date_range(origin, periods=len(returns), freq=freq)
    return pd.DataFrame({
        "Date": dates, "Open": closes, "High": closes,
        "Low": closes, "Close": closes, "Volume": [1] * len(returns),
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
    def _run(self, records, price_df, bench_df=None):
        # Beta needs the TOPIX frame; default to the same short frame so the
        # regression can't meet its minimum overlap (beta N/A) unless a test
        # supplies a longer aligned pair.
        with mock.patch.object(jp_fundamentals.jqf, "get_fundamentals", return_value="BASE-OVERVIEW"), \
                mock.patch.object(jp_fundamentals.jqf, "fetch_periods", return_value=("7011.T", records)), \
                mock.patch.object(jp_fundamentals, "_fetch_ohlcv_frame", return_value=price_df), \
                mock.patch.object(jp_fundamentals, "fetch_topix_closes",
                                  return_value=bench_df if bench_df is not None else price_df):
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
        self.assertIn("Beta (vs TOPIX, 3yr weekly): N/A", out)   # 2-row frame < min overlap

    def test_beta_regresses_weekly_returns_against_topix(self):
        # Stock weekly returns are exactly 1.3× TOPIX's over a shared window
        # → beta = Cov/Var = 1.30. Frames are on Fridays so W-FRI resampling
        # keeps every week; 80 weeks clears the ~52-week minimum overlap.
        index_ret = [0.010, -0.006, 0.008, -0.004, 0.005, -0.002] * 14  # 84 weeks
        stock_ret = [1.3 * r for r in index_ret]
        idx = _returns_frame(index_ret)
        stk = _returns_frame(stock_ret)
        out = self._run([_FY], stk, bench_df=idx)
        self.assertIn("Beta (vs TOPIX, 3yr weekly): 1.30", out)

    def test_beta_na_when_benchmark_unavailable(self):
        # TOPIX fetch fails (no coverage / plan) → beta N/A, but the rest of the
        # valuation block still renders.
        with mock.patch.object(jp_fundamentals.jqf, "get_fundamentals", return_value="BASE-OVERVIEW"), \
                mock.patch.object(jp_fundamentals.jqf, "fetch_periods", return_value=("7011.T", [_FY])), \
                mock.patch.object(jp_fundamentals, "_fetch_ohlcv_frame",
                                  return_value=_price_df(3567.0, 5208.0, 3171.0)), \
                mock.patch.object(jp_fundamentals, "fetch_topix_closes", side_effect=RuntimeError("no index")):
            out = jp_fundamentals.get_fundamentals("7011.T", "2026-06-26")
        self.assertIn("Beta (vs TOPIX, 3yr weekly): N/A", out)
        self.assertIn("PE: 36.08 (TTM)", out)   # rest of the block intact

    def test_transient_topix_error_only_drops_beta(self):
        # TOPIX is additive: a network/server blip must degrade only beta to N/A,
        # never discard the already-computable PE/PB/dividend/ROE ratios.
        with mock.patch.object(jp_fundamentals.jqf, "get_fundamentals", return_value="BASE-OVERVIEW"), \
                mock.patch.object(jp_fundamentals.jqf, "fetch_periods", return_value=("7011.T", [_FY])), \
                mock.patch.object(jp_fundamentals, "_fetch_ohlcv_frame",
                                  return_value=_price_df(3567.0, 5208.0, 3171.0)), \
                mock.patch.object(jp_fundamentals, "fetch_topix_closes",
                                  side_effect=requests.ConnectionError("reset")):
            out = jp_fundamentals.get_fundamentals("7011.T", "2026-06-26")
        self.assertIn("BASE-OVERVIEW", out)
        self.assertIn("PE: 36.08 (TTM)", out)
        self.assertIn("Beta (vs TOPIX, 3yr weekly): N/A", out)
        self.assertNotIn("ratio computation failed", out)

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
        self.assertIn("Beta (vs TOPIX, 3yr weekly): N/A", out)   # no price frame → no beta

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

        def fake_topix(start, end):
            seen["topix_end"] = end
            return _price_df(3567.0, 5208.0, 3171.0)

        with mock.patch.object(jp_fundamentals.jqf, "get_fundamentals", return_value="BASE"), \
                mock.patch.object(jp_fundamentals.jqf, "fetch_periods", side_effect=fake_periods), \
                mock.patch.object(jp_fundamentals, "_fetch_ohlcv_frame", side_effect=fake_ohlcv), \
                mock.patch.object(jp_fundamentals, "fetch_topix_closes", side_effect=fake_topix):
            jp_fundamentals.get_fundamentals("7011.T", "2024-03-15")
        self.assertEqual(seen["periods_date"], "2024-03-15")
        self.assertEqual(seen["ohlcv_end"], "2024-03-15")  # window ends at curr_date, not today
        self.assertEqual(seen["topix_end"], "2024-03-15")  # TOPIX also date-safe

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
