"""Stale OHLCV guard (#1021): a vendor returning a year-old partial frame must
be rejected, not fed into the report as if it were current.

The guard raises NoMarketDataError with a stale-specific detail, so the router's
existing try-next-vendor + single-sentinel handling applies and the sentinel
surfaces the reason.
"""
import copy
import unittest
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

import tradingagents.dataflows.stockstats_utils as stockstats_utils
import tradingagents.dataflows.y_finance as y_finance
import tradingagents.default_config as default_config
from tradingagents.dataflows import interface
from tradingagents.dataflows.config import bind_config
from tradingagents.dataflows.stockstats_utils import _assert_ohlcv_not_stale
from tradingagents.dataflows.symbol_utils import NoMarketDataError


def _frame(date):
    return pd.DataFrame(
        {
            "Date": [pd.Timestamp(date)],
            "Open": [330.0],
            "High": [332.0],
            "Low": [328.0],
            "Close": [330.58],
            "Volume": [1_000_000],
        }
    )


@pytest.mark.unit
class StaleGuardUnitTests(unittest.TestCase):
    def test_recent_prior_trading_day_is_accepted(self):
        # 1 day before curr_date — well within the freshness window.
        _assert_ohlcv_not_stale(_frame("2026-06-10"), "2026-06-11", "CB")

    def test_year_old_row_is_rejected_with_detail(self):
        with self.assertRaises(NoMarketDataError) as ctx:
            _assert_ohlcv_not_stale(_frame("2025-06-11"), "2026-06-11", "CB", "CB")
        msg = str(ctx.exception)
        self.assertIn("2025-06-11", msg)
        self.assertIn("2026-06-11", msg)
        self.assertIn("stale", msg)

    def test_empty_frame_is_left_to_caller(self):
        # Empty is a no-data condition handled elsewhere, not a staleness one.
        _assert_ohlcv_not_stale(
            pd.DataFrame(columns=["Date", "Close"]), "2026-06-11", "X"
        )

    def test_long_holiday_gap_within_threshold_is_accepted(self):
        _assert_ohlcv_not_stale(_frame("2026-06-02"), "2026-06-11", "X")  # 9 days

    def test_historical_mainland_request_has_no_live_cache_phase(self):
        phase = stockstats_utils._mainland_live_cache_phase(
            "2026-06-10",
            "600519.SS",
            "600519.SS",
            now=datetime(2026, 6, 11, 16, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        )
        self.assertIsNone(phase)


@pytest.mark.unit
def test_mainland_yfinance_cache_refreshes_when_completed_session_changes(
    monkeypatch, tmp_path
):
    """A pre-close raw candle must not become the post-close verified candle."""
    state = {"completed": "2026-06-10", "downloads": 0}

    def fake_download(*args, **kwargs):
        state["downloads"] += 1
        current_close = 111.0 if state["downloads"] == 1 else 120.0
        return pd.DataFrame(
            {
                "Open": [100.0, current_close - 1],
                "High": [102.0, current_close + 1],
                "Low": [99.0, current_close - 2],
                "Close": [101.0, current_close],
                "Volume": [1_000_000, 2_000_000],
            },
            index=pd.DatetimeIndex(
                [pd.Timestamp("2026-06-10"), pd.Timestamp("2026-06-11")],
                name="Date",
            ),
        )

    monkeypatch.setattr(
        stockstats_utils,
        "get_config",
        lambda: {"data_cache_dir": str(tmp_path)},
    )
    monkeypatch.setattr(stockstats_utils.yf, "download", fake_download)
    monkeypatch.setattr(
        stockstats_utils,
        "_mainland_live_cache_phase",
        lambda *_args, **_kwargs: state["completed"],
    )
    monkeypatch.setattr(
        stockstats_utils,
        "_mainland_effective_ohlcv_date",
        lambda *_args, **_kwargs: pd.Timestamp(state["completed"]),
    )

    before_close = stockstats_utils.load_ohlcv("600519.SS", "2026-06-11")
    assert before_close["Date"].max() == pd.Timestamp("2026-06-10")

    state["completed"] = "2026-06-11"
    after_close = stockstats_utils.load_ohlcv("600519.SS", "2026-06-11")

    assert state["downloads"] == 2
    assert after_close.iloc[-1]["Close"] == 120.0


@pytest.mark.unit
class StaleGuardPropagationTests(unittest.TestCase):
    def test_get_yfin_data_online_raises_on_stale_frame(self):
        stale = pd.DataFrame(
            {
                "Open": [280.0], "High": [286.0], "Low": [278.0],
                "Close": [284.45], "Volume": [1_000_000],
            },
            index=pd.DatetimeIndex([pd.Timestamp("2025-06-11")], name="Date"),
        )

        class DummyTicker:
            def __init__(self, symbol):
                pass

            def history(self, start, end, auto_adjust):
                assert auto_adjust is True
                return stale

        with mock.patch.object(y_finance.yf, "Ticker", DummyTicker), \
                self.assertRaises(NoMarketDataError):
            y_finance.get_YFin_data_online("CB", "2026-06-01", "2026-06-11")


@pytest.mark.unit
class StaleGuardRoutingTests(unittest.TestCase):
    def setUp(self):
        bind_config(copy.deepcopy(default_config.DEFAULT_CONFIG), merge=False)

    def tearDown(self):
        bind_config(copy.deepcopy(default_config.DEFAULT_CONFIG), merge=False)

    def test_router_sentinel_surfaces_stale_reason(self):
        bind_config({"data_vendors": {"core_stock_apis": "yfinance"}})

        def _stale(symbol, *a, **k):
            raise NoMarketDataError(
                symbol, symbol, "latest row is 2025-06-11, 365 days before ... (stale)"
            )

        with mock.patch.dict(
            interface.VENDOR_METHODS,
            {"get_stock_data": {"yfinance": _stale}},
            clear=False,
        ):
            out = interface.route_to_vendor(
                "get_stock_data", "CB", "2026-06-01", "2026-06-11"
            )
        self.assertIn("NO_DATA_AVAILABLE", out)
        self.assertIn("stale", out)  # the typed detail is surfaced to the agent


if __name__ == "__main__":
    unittest.main()
