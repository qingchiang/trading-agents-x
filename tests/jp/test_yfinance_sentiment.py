"""JP sentiment overlay: yfinance analyst-consensus rating block. It is a LIVE
snapshot, so it is gated to live/near-live runs and self-gated to ``.T``; all
yfinance access is mocked, so these run without network."""
import unittest
from unittest import mock

import pytest

from tradingagents.dataflows.jp import yfinance_sentiment as ys

_LIVE = "2026-06-26"  # gating is patched, so the literal value is irrelevant
_FULL = {
    "recommendationKey": "buy",
    "recommendationMean": 1.69,
    "numberOfAnalystOpinions": 16,
    "targetMeanPrice": 5323.078,
    "targetHighPrice": 6200.0,
    "targetLowPrice": 4100.0,
    "currentPrice": 3832.0,
}


@pytest.mark.unit
class AnalystRatingsBlockTests(unittest.TestCase):
    def _block(self, ratings, ticker="7011.T", live=True):
        with mock.patch.object(ys, "is_live", return_value=live), \
                mock.patch.object(ys, "get_analyst_ratings", return_value=ratings) as gar:
            out = ys.get_analyst_ratings_block(ticker, _LIVE)
        return out, gar

    def test_full_block_has_rating_target_and_implied_upside(self):
        out, _ = self._block(_FULL)
        self.assertIn("yfinance analyst consensus", out)
        self.assertIn("Rating: buy (mean 1.69 on 1=Strong Buy", out)
        self.assertIn("16 analysts", out)
        self.assertIn("12-month price target (mean): 5,323", out)
        self.assertIn("high 6,200 / low 4,100", out)
        self.assertIn("implied +38.9% vs current 3,832", out)

    def test_non_jp_ticker_returns_empty(self):
        # yfinance-sourced but injected as a JP fill; a US name uses StockTwits/Reddit.
        out, gar = self._block(_FULL, ticker="AAPL")
        self.assertEqual(out, "")
        gar.assert_not_called()  # self-gate short-circuits before any fetch

    def test_backtest_date_returns_empty_even_with_data(self):
        # Live-only: a non-live run must omit the snapshot (look-ahead), not render it.
        out, gar = self._block(_FULL, live=False)
        self.assertEqual(out, "")
        gar.assert_not_called()  # live-gate short-circuits before any fetch

    def test_empty_ratings_returns_empty(self):
        out, _ = self._block({})
        self.assertEqual(out, "")

    def test_no_coverage_returns_empty(self):
        # A name with a "none" rating and zero analysts carries no signal.
        out, _ = self._block({"recommendationKey": "none", "numberOfAnalystOpinions": 0})
        self.assertEqual(out, "")

    def test_rating_without_target_omits_target_line(self):
        out, _ = self._block({"recommendationKey": "hold", "recommendationMean": 3.0,
                              "numberOfAnalystOpinions": 5})
        self.assertIn("Rating: hold", out)
        self.assertNotIn("price target", out)

    def test_rating_without_analyst_count_still_renders(self):
        # yfinance can report a rating with numberOfAnalystOpinions None/0; the
        # count is optional and must not suppress the whole block.
        out, _ = self._block({"recommendationKey": "buy", "recommendationMean": 1.5,
                              "numberOfAnalystOpinions": None})
        self.assertIn("Rating: buy (mean 1.50", out)
        self.assertNotIn("analysts", out)   # count clause omitted, not the block

    def test_currentprice_falls_back_to_regular_market_price(self):
        # currentPrice is sometimes absent for .T; regularMarketPrice backs the
        # implied-upside so it isn't silently dropped.
        r = dict(_FULL, currentPrice=None, regularMarketPrice=3800.0)
        out, _ = self._block(r)
        self.assertIn("implied +40.1% vs current 3,800", out)  # 5323/3800 − 1

    def test_single_bound_band_shows_available_bound(self):
        r = dict(_FULL, targetLowPrice=None)  # only the high bound present
        out, _ = self._block(r)
        self.assertIn("(high 6,200)", out)
        self.assertNotIn("low", out)

    def test_target_without_price_omits_implied_upside(self):
        r = dict(_FULL, currentPrice=None)
        out, _ = self._block(r)
        self.assertIn("12-month price target (mean): 5,323", out)
        self.assertNotIn("implied", out)

    def test_target_without_band_omits_band(self):
        r = dict(_FULL, targetHighPrice=None, targetLowPrice=None)
        out, _ = self._block(r)
        self.assertIn("12-month price target (mean): 5,323", out)
        self.assertNotIn("high", out)
        self.assertIn("implied +38.9%", out)  # upside still shown

    def test_underscore_rating_is_humanized(self):
        r = dict(_FULL, recommendationKey="strong_buy", recommendationMean=1.2)
        out, _ = self._block(r)
        self.assertIn("Rating: strong buy", out)

    def test_fetch_error_degrades_to_empty(self):
        # Defensive: the getter degrades to {}, but a raise must not escape the prefetch.
        with mock.patch.object(ys, "is_live", return_value=True), \
                mock.patch.object(ys, "get_analyst_ratings", side_effect=RuntimeError("boom")):
            out = ys.get_analyst_ratings_block("7011.T", _LIVE)
        self.assertEqual(out, "")


if __name__ == "__main__":
    unittest.main()
