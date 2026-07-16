"""Point-in-time guards for yfinance's live-only ``.info`` overview."""

from unittest import mock

import pytest

from tradingagents.dataflows import y_finance as yf_data


@pytest.mark.unit
class TestYFinanceFundamentalsLookahead:
    def test_historical_date_does_not_request_live_info(self):
        with mock.patch.object(yf_data, "is_live", return_value=False), mock.patch.object(
            yf_data.yf, "Ticker"
        ) as ticker:
            out = yf_data.get_fundamentals("NVDA", "2020-01-15")
        ticker.assert_not_called()
        assert "LIVE_DATA_UNAVAILABLE" in out
        assert "not point-in-time historical data" in out
        assert "2020-01-15" in out

    def test_live_overview_labels_requested_and_retrieval_times(self):
        ticker_obj = mock.MagicMock()
        ticker_obj.info = {"longName": "NVIDIA Corporation", "marketCap": 123}
        with mock.patch.object(yf_data, "is_live", return_value=True), mock.patch.object(
            yf_data.yf, "Ticker", return_value=ticker_obj
        ):
            out = yf_data.get_fundamentals("NVDA", "2026-07-15")
        assert "live yfinance snapshot" in out
        assert "Requested analysis date: 2026-07-15" in out
        assert "Retrieved at:" in out
        assert "Not point-in-time historical data" in out
        assert "Market Cap: 123" in out
