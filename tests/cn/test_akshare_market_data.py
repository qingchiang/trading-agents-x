"""AkShare A-share qfq prices, indicators, snapshots, and source fallback."""

import copy
from datetime import date
from types import SimpleNamespace
from unittest import mock

import pandas as pd
import pytest
import requests

import tradingagents.dataflows.config as config_module
import tradingagents.default_config as default_config
from tradingagents.dataflows import interface, stockstats_utils, y_finance
from tradingagents.dataflows.cn import akshare_indicator, akshare_stock, calendar, common
from tradingagents.dataflows.config import set_config
from tradingagents.dataflows.errors import NoMarketDataError
from tradingagents.provenance import extract_provenance


def _eastmoney_frame(*, latest="2026-07-17", close=102.0):
    return pd.DataFrame(
        {
            "日期": ["2026-07-16", latest],
            "开盘": [100.0, 101.0],
            "最高": [102.0, close + 1],
            "最低": [99.0, 100.0],
            "收盘": [101.0, close],
            "成交量": [1000, 1200],
            "成交额": [1_000_000, 1_200_000],
            "振幅": [3.0, 2.0],
            "涨跌幅": [1.0, 0.9],
            "涨跌额": [1.0, 1.0],
            "换手率": [0.5, 0.6],
        }
    )


def _tencent_frame(*, latest="2026-07-17"):
    return pd.DataFrame(
        {
            "date": ["2026-07-16", latest],
            "open": [100.0, 101.0],
            "high": [102.0, 103.0],
            "low": [99.0, 100.0],
            "close": [101.0, 102.0],
            "amount": [1000, 1200],
        }
    )


@pytest.fixture(autouse=True)
def _isolate_cache(monkeypatch):
    akshare_stock.clear_cache()
    monkeypatch.setattr(
        calendar,
        "effective_trade_date",
        lambda value: date(2026, 7, 17),
    )
    monkeypatch.setattr(
        akshare_stock,
        "effective_trade_date",
        lambda value: date(2026, 7, 17),
    )
    yield
    akshare_stock.clear_cache()


def _fake_ak(*, eastmoney=None, tencent=None):
    em = eastmoney if callable(eastmoney) else mock.Mock(return_value=eastmoney)
    tx = tencent if callable(tencent) else mock.Mock(return_value=tencent)
    return SimpleNamespace(stock_zh_a_hist=em, stock_zh_a_hist_tx=tx), em, tx


@pytest.mark.unit
def test_eastmoney_qfq_output_preserves_extended_fields(monkeypatch):
    ak, em, tx = _fake_ak(
        eastmoney=_eastmoney_frame(), tencent=_tencent_frame()
    )
    monkeypatch.setattr(akshare_stock, "load_akshare", lambda: ak)

    output = akshare_stock.get_stock(
        "600519.SS", "2026-07-01", "2026-07-19"
    )

    assert "# Actual data source: AkShare / Eastmoney" in output
    assert "# Price adjustment: qfq (forward-adjusted)" in output
    assert "# Volume unit: shares" in output
    assert "# Requested end date: 2026-07-19" in output
    assert "# Effective trading date: 2026-07-17" in output
    assert "Amount,AmplitudePct,PctChange,PriceChange,TurnoverPct" in output
    assert em.call_args.kwargs["adjust"] == "qfq"
    assert em.call_args.kwargs["timeout"] == common.REQUEST_TIMEOUT
    tx.assert_not_called()
    assert extract_provenance(output)[0].source == "AkShare / Eastmoney"
    cached = akshare_stock.fetch_ohlcv(
        "600519.SS", "2026-07-01", "2026-07-19"
    )
    assert cached.frame["Volume"].tolist() == [100_000, 120_000]


@pytest.mark.unit
def test_tencent_is_used_after_eastmoney_timeout(monkeypatch):
    em = mock.Mock(side_effect=requests.Timeout("slow"))
    ak, _, _tx = _fake_ak(eastmoney=em, tencent=_tencent_frame())
    tencent = mock.Mock(return_value=_tencent_frame())
    monkeypatch.setattr(akshare_stock, "load_akshare", lambda: ak)
    monkeypatch.setattr(akshare_stock, "_fetch_tencent", tencent)
    monkeypatch.setattr(common.time, "sleep", lambda _delay: None)

    result = akshare_stock.fetch_ohlcv(
        "000001.SZ", "2026-07-01", "2026-07-19"
    )

    assert result.source == "AkShare / Tencent"
    assert list(result.frame.columns) == [
        "Date",
        "Open",
        "High",
        "Low",
        "Close",
        "Volume",
    ]
    assert result.frame["Volume"].tolist() == [100_000, 120_000]
    assert em.call_count == common.MAX_ATTEMPTS
    tencent.assert_called_once_with("sz000001", "2026-07-01", "2026-07-17")


@pytest.mark.unit
def test_negative_qfq_prices_are_preserved(monkeypatch):
    frame = pd.DataFrame(
        {
            "日期": ["2026-07-16", "2026-07-17"],
            "开盘": [-0.50, 1.00],
            "最高": [-0.20, 1.20],
            "最低": [-0.70, 0.90],
            "收盘": [-0.30, 1.10],
            "成交量": [1000, 1200],
        }
    )
    ak, _em, _tx = _fake_ak(eastmoney=frame, tencent=pd.DataFrame())
    monkeypatch.setattr(akshare_stock, "load_akshare", lambda: ak)

    result = akshare_stock.fetch_ohlcv(
        "600519.SS", "2026-07-01", "2026-07-19"
    )

    assert result.frame["Close"].tolist() == [-0.3, 1.1]
    assert result.frame["Volume"].tolist() == [100_000, 120_000]


@pytest.mark.unit
def test_tencent_equivalent_path_sets_timeout_and_parses_qfq(monkeypatch):
    response = mock.Mock()
    response.text = (
        'kline_dayqfq2026={"data":{"sz000001":{"qfqday":'
        '[["2026-07-17","10","11","12","9","1234"]]}}}'
    )
    response.raise_for_status.return_value = None
    get = mock.Mock(return_value=response)
    monkeypatch.setattr(akshare_stock, "load_akshare", lambda: object())
    monkeypatch.setattr(akshare_stock.requests, "get", get)

    frame = akshare_stock._fetch_tencent(
        "sz000001", "2026-07-01", "2026-07-17"
    )

    assert frame.iloc[0]["close"] == "11"
    assert get.call_args.kwargs["timeout"] == common.REQUEST_TIMEOUT
    assert "qfq" in get.call_args.kwargs["params"]["param"]


@pytest.mark.unit
@pytest.mark.parametrize("wrong_key", ["day", "hfqday"])
def test_tencent_rejects_non_qfq_payloads(monkeypatch, wrong_key):
    response = mock.Mock()
    response.text = (
        'kline_dayqfq2026={"data":{"sz000001":{"'
        + wrong_key
        + '":[["2026-07-17","10","11","12","9","1234"]]}}}'
    )
    response.raise_for_status.return_value = None
    monkeypatch.setattr(akshare_stock, "load_akshare", lambda: object())
    monkeypatch.setattr(akshare_stock.requests, "get", lambda *_args, **_kwargs: response)

    with pytest.raises(common.AkShareSchemaError, match="missing qfqday"):
        akshare_stock._fetch_tencent(
            "sz000001", "2026-07-01", "2026-07-17"
        )


@pytest.mark.unit
def test_empty_or_changed_primary_schema_falls_to_tencent(monkeypatch):
    changed = pd.DataFrame({"日期": ["2026-07-17"], "收盘价": [100]})
    ak, _em, _tx = _fake_ak(eastmoney=changed, tencent=_tencent_frame())
    monkeypatch.setattr(akshare_stock, "load_akshare", lambda: ak)
    monkeypatch.setattr(
        akshare_stock, "_fetch_tencent", lambda *_args: _tencent_frame()
    )
    assert (
        akshare_stock.fetch_ohlcv(
            "600519.SS", "2026-07-01", "2026-07-19"
        ).source
        == "AkShare / Tencent"
    )


@pytest.mark.unit
def test_both_empty_sources_raise_typed_no_data(monkeypatch):
    ak, _em, _tx = _fake_ak(
        eastmoney=pd.DataFrame(), tencent=pd.DataFrame()
    )
    monkeypatch.setattr(akshare_stock, "load_akshare", lambda: ak)
    monkeypatch.setattr(
        akshare_stock, "_fetch_tencent", lambda *_args: pd.DataFrame()
    )
    with pytest.raises(NoMarketDataError, match="returned no qfq rows"):
        akshare_stock.fetch_ohlcv(
            "600519.SS", "2026-07-01", "2026-07-19"
        )


@pytest.mark.unit
def test_stale_suspended_rows_are_rejected(monkeypatch):
    stale = _eastmoney_frame(latest="2026-07-16")
    ak, _em, _tx = _fake_ak(eastmoney=stale, tencent=stale)
    monkeypatch.setattr(akshare_stock, "load_akshare", lambda: ak)
    monkeypatch.setattr(akshare_stock, "_fetch_tencent", lambda *_args: stale)
    with pytest.raises(NoMarketDataError, match="suspended, delisted, or stale"):
        akshare_stock.fetch_ohlcv(
            "600519.SS", "2026-07-01", "2026-07-19"
        )


@pytest.mark.unit
def test_successful_frame_is_cached_and_returned_as_copy(monkeypatch):
    ak, em, _tx = _fake_ak(
        eastmoney=_eastmoney_frame(), tencent=_tencent_frame()
    )
    monkeypatch.setattr(akshare_stock, "load_akshare", lambda: ak)
    first = akshare_stock.fetch_ohlcv(
        "600519.SS", "2026-07-01", "2026-07-19"
    )
    first.frame.loc[0, "Close"] = 999
    second = akshare_stock.fetch_ohlcv(
        "600519.SS", "2026-07-01", "2026-07-19"
    )
    assert em.call_count == 1
    assert second.frame.loc[0, "Close"] == 101


@pytest.mark.unit
def test_rows_after_analysis_date_are_excluded(monkeypatch):
    frame = pd.DataFrame(
        {
            "日期": ["2026-07-16", "2026-07-17", "2026-07-20"],
            "开盘": [100, 101, 999],
            "最高": [102, 103, 1000],
            "最低": [99, 100, 998],
            "收盘": [101, 102, 999],
            "成交量": [1000, 1200, 9999],
        }
    )
    ak, _em, _tx = _fake_ak(eastmoney=frame, tencent=pd.DataFrame())
    monkeypatch.setattr(akshare_stock, "load_akshare", lambda: ak)
    result = akshare_stock.fetch_ohlcv(
        "600519.SS", "2026-07-01", "2026-07-19"
    )
    assert result.frame["Date"].max() == pd.Timestamp("2026-07-17")
    assert 999 not in result.frame["Close"].tolist()


@pytest.mark.unit
def test_reversed_date_range_fails_loud():
    with pytest.raises(ValueError, match="start_date.*after end_date"):
        akshare_stock.fetch_ohlcv(
            "600519.SS", "2026-07-20", "2026-07-19"
        )


@pytest.mark.unit
def test_yfinance_fallback_uses_exact_mainland_freshness(monkeypatch):
    from tradingagents.dataflows.cn import calendar

    monkeypatch.setattr(
        calendar, "effective_trade_date", lambda _value: date(2026, 7, 17)
    )
    stale = pd.DataFrame(
        {"Date": [pd.Timestamp("2026-07-16")], "Close": [100.0]}
    )
    with pytest.raises(NoMarketDataError, match="expected mainland trading date"):
        stockstats_utils._assert_ohlcv_not_stale(
            stale, "2026-07-19", "600519.SS", "600519.SS"
        )


@pytest.mark.unit
def test_yfinance_fallback_excludes_incomplete_mainland_daily_bar(monkeypatch):
    monkeypatch.setattr(
        calendar, "effective_trade_date", lambda _value: date(2026, 7, 16)
    )
    frame = pd.DataFrame(
        {
            "Open": [100.0, 101.0],
            "High": [102.0, 103.0],
            "Low": [99.0, 100.0],
            "Close": [101.0, 102.0],
            "Volume": [1000, 1200],
        },
        index=pd.DatetimeIndex(["2026-07-16", "2026-07-17"], name="Date"),
    )

    class FakeTicker:
        def __init__(self, _symbol):
            pass

        def history(self, **_kwargs):
            return frame

    monkeypatch.setattr(y_finance.yf, "Ticker", FakeTicker)
    output = y_finance.get_YFin_data_online(
        "600519.SS", "2026-07-01", "2026-07-17"
    )

    assert "# Effective trading date: 2026-07-16" in output
    assert "\n2026-07-16," in output
    assert "\n2026-07-17," not in output


@pytest.mark.unit
def test_mainland_freshness_rejects_a_row_after_completed_session(monkeypatch):
    monkeypatch.setattr(
        calendar, "effective_trade_date", lambda _value: date(2026, 7, 16)
    )
    incomplete = pd.DataFrame(
        {"Date": [pd.Timestamp("2026-07-17")], "Close": [100.0]}
    )
    with pytest.raises(NoMarketDataError, match="expected mainland trading date"):
        stockstats_utils._assert_ohlcv_not_stale(
            incomplete, "2026-07-17", "600519.SS", "600519.SS"
        )


@pytest.mark.unit
def test_yfinance_fallback_fails_closed_when_calendar_is_unavailable(monkeypatch):
    from tradingagents.dataflows.cn import calendar

    monkeypatch.setattr(
        calendar,
        "effective_trade_date",
        mock.Mock(side_effect=common.AkShareRequestError("calendar offline")),
    )
    recent_but_unverified = pd.DataFrame(
        {"Date": [pd.Timestamp("2026-07-16")], "Close": [100.0]}
    )
    with pytest.raises(NoMarketDataError, match="cannot verify mainland freshness"):
        stockstats_utils._assert_ohlcv_not_stale(
            recent_but_unverified, "2026-07-17", "600519.SS", "600519.SS"
        )


@pytest.mark.unit
def test_indicator_and_snapshot_reuse_same_qfq_fetch(monkeypatch):
    dates = pd.bdate_range(end="2026-07-17", periods=260)
    frame = pd.DataFrame(
        {
            "日期": dates,
            "开盘": range(100, 360),
            "最高": range(102, 362),
            "最低": range(99, 359),
            "收盘": range(101, 361),
            "成交量": [1000] * 260,
        }
    )
    ak, em, _tx = _fake_ak(eastmoney=frame, tencent=pd.DataFrame())
    monkeypatch.setattr(akshare_stock, "load_akshare", lambda: ak)

    indicator = akshare_indicator.get_indicator(
        "600519.SS", "rsi", "2026-07-19", 5
    )
    snapshot = akshare_indicator.get_verified_market_snapshot(
        "600519.SS", "2026-07-19", 30
    )

    assert em.call_count == 1
    assert "# Actual data source: AkShare / Eastmoney" in indicator
    assert "Effective trading date: 2026-07-17" in indicator
    assert "Data source: AkShare / Eastmoney" in snapshot
    assert "Price adjustment: qfq (forward-adjusted)" in snapshot
    assert "Latest trading row used: 2026-07-17" in snapshot


@pytest.mark.unit
def test_broken_akshare_import_is_typed(monkeypatch):
    monkeypatch.setattr(
        common.importlib,
        "import_module",
        mock.Mock(side_effect=ImportError("broken binary")),
    )
    with pytest.raises(common.AkShareUnavailableError, match="could not be imported"):
        common.load_akshare()


@pytest.mark.unit
@pytest.mark.parametrize(
    "symbol",
    ["600519.SZ", "000001.SS", "000001.SH", "510300.SS", "AAPL.SS"],
)
def test_exchange_mismatch_and_non_equities_fail_loud(symbol):
    with pytest.raises(ValueError, match="suffix mismatch|out of scope"):
        common.canonical_a_share(symbol)


@pytest.mark.unit
def test_fetch_preserves_typed_broken_install_error(monkeypatch):
    unavailable = common.AkShareUnavailableError("broken install")
    monkeypatch.setattr(
        akshare_stock, "load_akshare", mock.Mock(side_effect=unavailable)
    )
    with pytest.raises(common.AkShareUnavailableError, match="broken install"):
        akshare_stock.fetch_ohlcv(
            "600519.SS", "2026-07-01", "2026-07-19"
        )


@pytest.mark.unit
def test_rate_limit_is_retried_then_typed(monkeypatch):
    response = mock.Mock(status_code=429)
    error = requests.HTTPError("429", response=response)
    call = mock.Mock(side_effect=error)
    monkeypatch.setattr(common.time, "sleep", lambda _delay: None)
    with pytest.raises(common.AkShareRateLimitError, match="rate limited"):
        common.call_with_retry(call, label="AkShare test")
    assert call.call_count == common.MAX_ATTEMPTS


@pytest.mark.unit
def test_router_falls_back_to_yfinance_after_akshare_no_data():
    original = config_module._config
    config_module._config = copy.deepcopy(default_config.DEFAULT_CONFIG)
    # Avoid relying on the module-global config left by another test.
    set_config(
        {
            "data_vendors_by_market": {
                ".SS": {"core_stock_apis": "akshare,yfinance"}
            }
        }
    )
    ak = mock.Mock(
        side_effect=NoMarketDataError("600519.SS", "600519.SS", "empty")
    )
    yf = mock.Mock(return_value="YFINANCE_FALLBACK")
    try:
        with mock.patch.dict(
            interface.VENDOR_METHODS,
            {"get_stock_data": {"akshare": ak, "yfinance": yf}},
            clear=False,
        ):
            output = interface.route_to_vendor(
                "get_stock_data", "600519", "2026-07-01", "2026-07-17"
            )
    finally:
        config_module._config = original
    assert output == "YFINANCE_FALLBACK"
    ak.assert_called_once_with("600519.SS", "2026-07-01", "2026-07-17")
    yf.assert_called_once_with("600519.SS", "2026-07-01", "2026-07-17")


@pytest.mark.unit
@pytest.mark.parametrize("symbol", ["600519.SZ", "510300.SS", "399006.SZ"])
def test_router_rejects_unsupported_mainland_symbol_before_any_vendor(symbol):
    ak = mock.Mock(return_value="AKSHARE")
    yf = mock.Mock(return_value="YFINANCE")
    with mock.patch.dict(
        interface.VENDOR_METHODS,
        {"get_stock_data": {"akshare": ak, "yfinance": yf}},
        clear=False,
    ), pytest.raises(ValueError, match="suffix mismatch|not supported"):
        interface.route_to_vendor(
            "get_stock_data", symbol, "2026-07-01", "2026-07-17"
        )
    ak.assert_not_called()
    yf.assert_not_called()
