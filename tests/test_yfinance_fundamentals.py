"""Point-in-time guards for yfinance's live-only ``.info`` overview."""

from unittest import mock

import pandas as pd
import pytest

from tradingagents.dataflows import y_finance as yf_data
from tradingagents.dataflows.errors import VendorRateLimitError
from tradingagents.dataflows.rate_limit import stop_on_rate_limit_scope


@pytest.mark.unit
class TestYFinanceFundamentalsLookahead:
    def test_historical_date_does_not_request_live_info(self):
        with mock.patch.object(
            yf_data, "is_near_live", return_value=False
        ), mock.patch.object(yf_data.yf, "Ticker") as ticker:
            out = yf_data.get_fundamentals("NVDA", "2020-01-15")
        ticker.assert_not_called()
        assert "LIVE_DATA_UNAVAILABLE" in out
        assert "not point-in-time historical data" in out
        assert "2020-01-15" in out

    def test_live_overview_labels_requested_and_retrieval_times(self):
        ticker_obj = mock.MagicMock()
        ticker_obj.info = {"longName": "NVIDIA Corporation", "marketCap": 123}
        with mock.patch.object(
            yf_data, "is_near_live", return_value=True
        ), mock.patch.object(yf_data.yf, "Ticker", return_value=ticker_obj):
            out = yf_data.get_fundamentals("NVDA", "2026-07-15")
        assert "live yfinance snapshot" in out
        assert "Requested analysis date: 2026-07-15" in out
        assert "Retrieved at:" in out
        assert "Not point-in-time historical data" in out
        assert "Market Cap: 123" in out

    def test_unscoped_rate_limit_remains_a_sanitized_unavailable_result(self):
        with mock.patch.object(
            yf_data, "is_near_live", return_value=True
        ), mock.patch.object(
            yf_data, "yf_retry", side_effect=VendorRateLimitError("Yahoo Finance rate limited")
        ):
            out = yf_data.get_fundamentals("NVDA", "2026-07-15")

        assert out == "Error retrieving fundamentals for NVDA: Yahoo Finance rate limited"

    def test_focused_rate_limit_bubbles_from_the_real_info_adapter_without_retry(self):
        calls = []

        class RateLimitedTicker:
            @property
            def info(self):
                calls.append("info")
                from yfinance.exceptions import YFRateLimitError

                raise YFRateLimitError()

        with mock.patch.object(
            yf_data, "is_near_live", return_value=True
        ), mock.patch.object(yf_data.yf, "Ticker", return_value=RateLimitedTicker()), stop_on_rate_limit_scope(
            True
        ), pytest.raises(VendorRateLimitError, match="Yahoo Finance rate limited"):
            yf_data.get_fundamentals("NVDA", "2026-07-15")

        assert calls == ["info"]

    @pytest.mark.parametrize(
        ("ticker_symbol", "method_name"),
        [
            ("NVDA", "get_income_statement"),
            ("9984.T", "get_balance_sheet"),
            ("600519.SS", "get_cashflow"),
        ],
    )
    def test_historical_statement_does_not_request_current_yfinance_frame(
        self, ticker_symbol, method_name
    ):
        with mock.patch.object(
            yf_data, "is_near_live", return_value=False
        ), mock.patch.object(yf_data.yf, "Ticker") as ticker:
            out = getattr(yf_data, method_name)(ticker_symbol, "quarterly", "2020-01-15")
        ticker.assert_not_called()
        assert "HISTORICAL_DATA_UNAVAILABLE" in out
        assert "without filing timestamps" in out
        assert "2020-01-15" in out

    def test_historical_statement_frame_helper_does_not_request_yfinance(self):
        with mock.patch.object(
            yf_data, "is_near_live", return_value=False
        ), mock.patch.object(yf_data.yf, "Ticker") as ticker:
            frame = yf_data.get_statement_frame(
                "600519.SS", "income", "quarterly", "2020-01-15"
            )
        ticker.assert_not_called()
        assert frame is None

    def test_no_date_statement_is_labelled_as_live_retrieval(self):
        ticker_obj = mock.MagicMock()
        ticker_obj.income_stmt = pd.DataFrame(
            {pd.Timestamp("2026-01-31"): [100]},
            index=["Total Revenue"],
        )
        with mock.patch.object(yf_data.yf, "Ticker", return_value=ticker_obj):
            out = yf_data.get_income_statement("9984.T", "annual", None)
        assert "not provided (treated as live retrieval)" in out
        assert "Not point-in-time historical data" in out
