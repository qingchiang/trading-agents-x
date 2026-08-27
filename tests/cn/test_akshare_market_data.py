"""AkShare A-share qfq prices, indicators, snapshots, and source fallback."""

import copy
from datetime import date
from types import SimpleNamespace
from unittest import mock

import pandas as pd
import pytest
import requests

import tradingagents.default_config as default_config
from tradingagents.dataflows import interface, stockstats_utils, y_finance
from tradingagents.dataflows.cn import akshare_indicator, akshare_stock, calendar, common
from tradingagents.dataflows.config import bind_config
from tradingagents.dataflows.errors import NoMarketDataError
from tradingagents.dataflows.rate_limit import stop_on_rate_limit_scope
from tradingagents.provenance import extract_provenance, provenance_quality_issues


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
def test_tencent_qfq_is_primary_and_records_adjustment(monkeypatch):
    ak, em, _tx = _fake_ak(eastmoney=_eastmoney_frame())
    tencent = mock.Mock(return_value=_tencent_frame())
    monkeypatch.setattr(akshare_stock, "load_akshare", lambda: ak)
    monkeypatch.setattr(akshare_stock, "_fetch_tencent", tencent)

    output = akshare_stock.get_stock(
        "600519.SS", "2026-07-01", "2026-07-19"
    )

    assert "# Actual data source: AkShare / Tencent" in output
    assert "# Price adjustment: qfq (forward-adjusted)" in output
    assert "# Volume unit: shares" in output
    assert "# Requested end date: 2026-07-19" in output
    assert "# Effective trading date: 2026-07-17" in output
    assert "Amount,AmplitudePct,PctChange,PriceChange,TurnoverPct" not in output
    em.assert_not_called()
    tencent.assert_called_once_with("sh600519", "2026-07-01", "2026-07-17")
    provenance = extract_provenance(output)[0]
    assert provenance.source == "AkShare / Tencent"
    assert "fallback" not in provenance.timing
    cached = akshare_stock.fetch_ohlcv(
        "600519.SS", "2026-07-01", "2026-07-19"
    )
    assert cached.frame["Volume"].tolist() == [100_000, 120_000]


@pytest.mark.unit
def test_eastmoney_cold_fallback_preserves_extended_fields(monkeypatch):
    ak, em, _tx = _fake_ak(eastmoney=_eastmoney_frame())
    tencent = mock.Mock(side_effect=common.AkShareRequestError("slow"))
    monkeypatch.setattr(akshare_stock, "load_akshare", lambda: ak)
    monkeypatch.setattr(akshare_stock, "_fetch_tencent", tencent)

    output = akshare_stock.get_stock(
        "000001.SZ", "2026-07-01", "2026-07-19"
    )
    result = akshare_stock.fetch_ohlcv("000001.SZ", "2026-07-01", "2026-07-19")

    assert result.source == "AkShare / Eastmoney"
    assert result.fallback_reason == "Tencent primary retrieval unavailable"
    assert "Amount,AmplitudePct,PctChange,PriceChange,TurnoverPct" in output
    assert result.frame["Volume"].tolist() == [100_000, 120_000]
    assert em.call_args.kwargs["adjust"] == "qfq"
    assert em.call_args.kwargs["timeout"] == common.REQUEST_TIMEOUT
    assert "fallback: Tencent primary retrieval unavailable" in extract_provenance(
        output
    )[0].timing
    reasons = {
        issue.reason for issue in provenance_quality_issues(extract_provenance(output))
    }
    assert "fallback source used" in reasons
    assert "adjustment provider changed; technical indicators may differ" in reasons


@pytest.mark.unit
def test_negative_qfq_prices_are_preserved(monkeypatch):
    frame = pd.DataFrame(
        {
            "date": ["2026-07-16", "2026-07-17"],
            "open": [-0.50, 1.00],
            "high": [-0.20, 1.20],
            "low": [-0.70, 0.90],
            "close": [-0.30, 1.10],
            "amount": [1000, 1200],
        }
    )
    ak, em, _tx = _fake_ak(eastmoney=_eastmoney_frame())
    monkeypatch.setattr(akshare_stock, "load_akshare", lambda: ak)
    monkeypatch.setattr(akshare_stock, "_fetch_tencent", lambda *_args: frame)

    result = akshare_stock.fetch_ohlcv(
        "600519.SS", "2026-07-01", "2026-07-19"
    )

    assert result.frame["Close"].tolist() == [-0.3, 1.1]
    assert result.frame["Volume"].tolist() == [100_000, 120_000]
    em.assert_not_called()


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
    assert get.call_count == 1
    assert get.call_args.kwargs["timeout"] == common.REQUEST_TIMEOUT
    assert (
        get.call_args.kwargs["params"]["param"]
        == "sz000001,day,2026-07-01,2026-07-17,640,qfq"
    )


@pytest.mark.unit
def test_tencent_paginates_only_after_a_full_640_row_page(monkeypatch):
    newest_dates = pd.date_range(end="2026-07-17", periods=640, freq="D")
    older_dates = pd.date_range(end=newest_dates.min() - pd.Timedelta(days=1), periods=5)

    def page(dates):
        return pd.DataFrame(
            {
                "date": dates,
                "open": 10,
                "close": 11,
                "high": 12,
                "low": 9,
                "amount": 100,
            }
        )

    fetch_page = mock.Mock(side_effect=[page(newest_dates), page(older_dates)])
    monkeypatch.setattr(akshare_stock, "load_akshare", lambda: object())
    monkeypatch.setattr(akshare_stock, "_fetch_tencent_page", fetch_page)

    frame = akshare_stock._fetch_tencent(
        "sh600519", "2024-01-01", "2026-07-17"
    )

    assert fetch_page.call_count == 2
    assert fetch_page.call_args_list[0].args[:3] == (
        "sh600519",
        "2024-01-01",
        "2026-07-17",
    )
    assert fetch_page.call_args_list[1].args[2] == (
        newest_dates.min() - pd.Timedelta(days=1)
    ).strftime("%Y-%m-%d")
    assert len(frame) == 645
    assert frame["date"].is_monotonic_increasing


@pytest.mark.unit
def test_tencent_short_listing_stops_after_one_page(monkeypatch):
    short_history = _tencent_frame()
    short_history["date"] = pd.to_datetime(short_history["date"])
    fetch_page = mock.Mock(return_value=short_history)
    monkeypatch.setattr(akshare_stock, "load_akshare", lambda: object())
    monkeypatch.setattr(akshare_stock, "_fetch_tencent_page", fetch_page)

    frame = akshare_stock._fetch_tencent(
        "sh688981", "2020-01-01", "2026-07-17"
    )

    assert len(frame) == 2
    assert fetch_page.call_count == 1


@pytest.mark.unit
def test_tencent_backfill_before_start_is_trimmed_without_another_request(monkeypatch):
    dates = pd.date_range(end="2026-07-17", periods=640, freq="D")
    page = pd.DataFrame(
        {
            "date": dates,
            "open": 10,
            "close": 11,
            "high": 12,
            "low": 9,
            "amount": 100,
        }
    )
    fetch_page = mock.Mock(return_value=page)
    monkeypatch.setattr(akshare_stock, "load_akshare", lambda: object())
    monkeypatch.setattr(akshare_stock, "_fetch_tencent_page", fetch_page)

    frame = akshare_stock._fetch_tencent(
        "sh600519", "2026-01-01", "2026-07-17"
    )

    assert fetch_page.call_count == 1
    assert frame["date"].min() == pd.Timestamp("2026-01-01")
    assert frame["date"].max() == pd.Timestamp("2026-07-17")


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
def test_tencent_page_deduplicates_identical_dates_but_rejects_conflicts(monkeypatch):
    response = mock.Mock()
    response.text = "payload={}"
    response.raise_for_status.return_value = None
    monkeypatch.setattr(akshare_stock.requests, "get", lambda *_args, **_kwargs: response)
    decoder = mock.Mock()
    base = ["2026-07-17", "10", "11", "12", "9", "1234"]
    decoder.decode.return_value = {
        "data": {"sz000001": {"qfqday": [base, list(base)]}}
    }

    frame = akshare_stock._fetch_tencent_page(
        "sz000001", "2026-07-01", "2026-07-17", decoder=decoder
    )
    assert len(frame) == 1
    assert frame.attrs["raw_count"] == 2

    conflicting = list(base)
    conflicting[2] = "99"
    decoder.decode.return_value = {
        "data": {"sz000001": {"qfqday": [base, conflicting]}}
    }
    with pytest.raises(common.AkShareSchemaError, match="conflicting duplicate"):
        akshare_stock._fetch_tencent_page(
            "sz000001", "2026-07-01", "2026-07-17", decoder=decoder
        )


@pytest.mark.unit
def test_empty_or_changed_tencent_schema_falls_to_eastmoney(monkeypatch):
    changed = pd.DataFrame({"日期": ["2026-07-17"], "收盘价": [100]})
    ak, em, _tx = _fake_ak(eastmoney=_eastmoney_frame())
    monkeypatch.setattr(akshare_stock, "load_akshare", lambda: ak)
    monkeypatch.setattr(akshare_stock, "_fetch_tencent", lambda *_args: changed)

    result = akshare_stock.fetch_ohlcv(
        "600519.SS", "2026-07-01", "2026-07-19"
    )

    assert result.source == "AkShare / Eastmoney"
    assert result.fallback_reason == "Tencent primary retrieval unavailable"
    assert em.call_count == 1


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
    ak, _em, _tx = _fake_ak(eastmoney=stale)
    monkeypatch.setattr(akshare_stock, "load_akshare", lambda: ak)
    monkeypatch.setattr(
        akshare_stock,
        "_fetch_tencent",
        lambda *_args: _tencent_frame(latest="2026-07-16"),
    )
    with pytest.raises(NoMarketDataError, match="suspended, delisted, or stale"):
        akshare_stock.fetch_ohlcv(
            "600519.SS", "2026-07-01", "2026-07-19"
        )


@pytest.mark.unit
def test_successful_frame_is_cached_and_returned_as_copy(monkeypatch):
    ak, em, _tx = _fake_ak(eastmoney=_eastmoney_frame())
    tencent = mock.Mock(return_value=_tencent_frame())
    monkeypatch.setattr(akshare_stock, "load_akshare", lambda: ak)
    monkeypatch.setattr(akshare_stock, "_fetch_tencent", tencent)
    first = akshare_stock.fetch_ohlcv(
        "600519.SS", "2026-07-01", "2026-07-19"
    )
    first.frame.loc[0, "Close"] = 999
    second = akshare_stock.fetch_ohlcv(
        "600519.SS", "2026-07-01", "2026-07-19"
    )
    assert tencent.call_count == 1
    em.assert_not_called()
    assert second.frame.loc[0, "Close"] == 101


@pytest.mark.unit
def test_rows_after_analysis_date_are_excluded(monkeypatch):
    frame = pd.DataFrame(
        {
            "date": ["2026-07-16", "2026-07-17", "2026-07-20"],
            "open": [100, 101, 999],
            "high": [102, 103, 1000],
            "low": [99, 100, 998],
            "close": [101, 102, 999],
            "amount": [1000, 1200, 9999],
        }
    )
    ak, em, _tx = _fake_ak(eastmoney=_eastmoney_frame())
    monkeypatch.setattr(akshare_stock, "load_akshare", lambda: ak)
    monkeypatch.setattr(akshare_stock, "_fetch_tencent", lambda *_args: frame)
    result = akshare_stock.fetch_ohlcv(
        "600519.SS", "2026-07-01", "2026-07-19"
    )
    assert result.frame["Date"].max() == pd.Timestamp("2026-07-17")
    assert 999 not in result.frame["Close"].tolist()
    em.assert_not_called()


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
            "date": dates,
            "open": range(100, 360),
            "high": range(102, 362),
            "low": range(99, 359),
            "close": range(101, 361),
            "amount": [1000] * 260,
        }
    )
    ak, em, _tx = _fake_ak(eastmoney=_eastmoney_frame())
    tencent = mock.Mock(return_value=frame)
    monkeypatch.setattr(akshare_stock, "load_akshare", lambda: ak)
    monkeypatch.setattr(akshare_stock, "_fetch_tencent", tencent)

    indicator = akshare_indicator.get_indicator(
        "600519.SS", "rsi", "2026-07-19", 5
    )
    snapshot = akshare_indicator.get_verified_market_snapshot(
        "600519.SS", "2026-07-19", 30
    )

    assert tencent.call_count == 1
    em.assert_not_called()
    assert "# Actual data source: AkShare / Tencent" in indicator
    assert "Effective trading date: 2026-07-17" in indicator
    assert "Latest valid indicator observation: 2026-07-17" in indicator
    assert "Data source: AkShare / Tencent" in snapshot
    assert "Price adjustment: qfq (forward-adjusted)" in snapshot
    assert "Latest trading row used: 2026-07-17" in snapshot


@pytest.mark.unit
def test_eastmoney_fallback_is_auditable_in_indicator_and_snapshot(monkeypatch):
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
    ak, _em, _tx = _fake_ak(eastmoney=frame)
    monkeypatch.setattr(akshare_stock, "load_akshare", lambda: ak)
    monkeypatch.setattr(
        akshare_stock,
        "_fetch_tencent",
        mock.Mock(side_effect=common.AkShareRequestError("down")),
    )

    indicator = akshare_indicator.get_indicator(
        "600519.SS", "rsi", "2026-07-19", 5
    )
    snapshot = akshare_indicator.get_verified_market_snapshot(
        "600519.SS", "2026-07-19", 30
    )

    indicator_record = extract_provenance(indicator)[0]
    snapshot_record = extract_provenance(snapshot)[0]
    assert indicator_record.source == "AkShare / Eastmoney"
    assert snapshot_record.source == "AkShare / Eastmoney"
    assert "fallback: Tencent primary retrieval unavailable" in indicator_record.timing
    assert "fallback: Tencent primary retrieval unavailable" in snapshot_record.timing
    assert "adjustment provider changed" in indicator_record.timing
    assert "adjustment provider changed" in snapshot_record.timing


@pytest.mark.unit
def test_endpoint_health_log_records_source_schema_freshness_and_latency(
    monkeypatch, caplog
):
    ak, em, _tx = _fake_ak(eastmoney=_eastmoney_frame())
    monkeypatch.setattr(akshare_stock, "load_akshare", lambda: ak)
    monkeypatch.setattr(
        akshare_stock,
        "_fetch_tencent",
        mock.Mock(side_effect=common.AkShareSchemaError("changed")),
    )

    with caplog.at_level("INFO", logger=akshare_stock.__name__):
        result = akshare_stock.fetch_ohlcv(
            "600519.SS", "2026-07-01", "2026-07-19"
        )

    assert result.source == "AkShare / Eastmoney"
    assert em.call_count == 1
    messages = [record.getMessage() for record in caplog.records]
    assert any(
        "AkShare / Tencent unhealthy" in message
        and "status=schema_error" in message
        and "latest=n/a" in message
        and "error=AkShareSchemaError" in message
        and "detail=changed" in message
        and "latency_ms=" in message
        for message in messages
    )
    assert any(
        "AkShare / Eastmoney healthy" in message
        and "schema=valid" in message
        and "latest=2026-07-17" in message
        and "latency_ms=" in message
        for message in messages
    )


@pytest.mark.unit
def test_endpoint_health_log_preserves_stale_latest_date(monkeypatch, caplog):
    ak, _em, _tx = _fake_ak(eastmoney=_eastmoney_frame())
    monkeypatch.setattr(akshare_stock, "load_akshare", lambda: ak)
    monkeypatch.setattr(
        akshare_stock,
        "_fetch_tencent",
        lambda *_args: _tencent_frame(latest="2026-07-16"),
    )

    with caplog.at_level("INFO", logger=akshare_stock.__name__):
        result = akshare_stock.fetch_ohlcv(
            "600519.SS", "2026-07-01", "2026-07-19"
        )

    assert result.source == "AkShare / Eastmoney"
    message = next(
        record.getMessage()
        for record in caplog.records
        if "AkShare / Tencent unhealthy" in record.getMessage()
    )
    assert "status=stale" in message
    assert "latest=2026-07-16" in message
    assert "before expected mainland trading date 2026-07-17" in message


@pytest.mark.unit
def test_endpoint_health_log_sanitizes_request_error_detail(monkeypatch, caplog):
    ak, _em, _tx = _fake_ak(eastmoney=_eastmoney_frame())
    monkeypatch.setattr(akshare_stock, "load_akshare", lambda: ak)
    monkeypatch.setattr(
        akshare_stock,
        "_fetch_tencent",
        mock.Mock(
            side_effect=common.AkShareRequestError(
                "timeout\nhttps://example.test/?token=private-value"
            )
        ),
    )

    with caplog.at_level("INFO", logger=akshare_stock.__name__):
        result = akshare_stock.fetch_ohlcv(
            "600519.SS", "2026-07-01", "2026-07-19"
        )

    assert result.source == "AkShare / Eastmoney"
    message = next(
        record.getMessage()
        for record in caplog.records
        if "AkShare / Tencent unhealthy" in record.getMessage()
    )
    assert "status=request_error" in message
    assert "detail=timeout https://example.test/?token=<redacted>" in message
    assert "private-value" not in message


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
def test_incremental_scope_does_not_retry_a_rate_limit(monkeypatch):
    response = mock.Mock(status_code=429)
    call = mock.Mock(side_effect=requests.HTTPError("429", response=response))
    sleep = mock.Mock()
    monkeypatch.setattr(common.time, "sleep", sleep)

    with (
        stop_on_rate_limit_scope(True),
        pytest.raises(common.AkShareRateLimitError, match="rate limited"),
    ):
        common.call_with_retry(call, label="AkShare incremental test")

    call.assert_called_once_with()
    sleep.assert_not_called()


@pytest.mark.unit
def test_incremental_market_stops_before_eastmoney_after_tencent_rate_limit(
    monkeypatch,
):
    akshare_stock.clear_cache()
    later = mock.Mock(side_effect=AssertionError("Eastmoney queried after 429"))
    monkeypatch.setattr(
        akshare_stock,
        "_fetch_tencent",
        mock.Mock(side_effect=common.AkShareRateLimitError("Tencent 429")),
    )
    monkeypatch.setattr(akshare_stock, "_fetch_eastmoney", later)
    monkeypatch.setattr(
        calendar,
        "effective_trade_date",
        lambda *_args, **_kwargs: date(2026, 7, 17),
    )

    with (
        stop_on_rate_limit_scope(True),
        pytest.raises(common.AkShareRateLimitError, match="Tencent 429"),
    ):
        akshare_stock.fetch_ohlcv(
            "600519.SS", "2026-07-01", "2026-07-17"
        )

    later.assert_not_called()


@pytest.mark.unit
def test_router_falls_back_to_yfinance_after_akshare_no_data():
    bind_config(copy.deepcopy(default_config.DEFAULT_CONFIG), merge=False)
    bind_config(
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
    with mock.patch.dict(
        interface.VENDOR_METHODS,
        {"get_stock_data": {"akshare": ak, "yfinance": yf}},
        clear=False,
    ):
        output = interface.route_to_vendor(
            "get_stock_data", "600519", "2026-07-01", "2026-07-17"
        )
    assert output == "YFINANCE_FALLBACK"
    ak.assert_called_once_with("600519.SS", "2026-07-01", "2026-07-17")
    yf.assert_called_once_with("600519.SS", "2026-07-01", "2026-07-17")


@pytest.mark.unit
def test_router_yfinance_fallback_records_adjustment_provider_change():
    bind_config(copy.deepcopy(default_config.DEFAULT_CONFIG), merge=False)
    bind_config(
        {
            "data_vendors_by_market": {
                ".SS": {"core_stock_apis": "akshare,yfinance"}
            }
        }
    )
    ak = mock.Mock(
        side_effect=NoMarketDataError("600519.SS", "600519.SS", "empty")
    )
    yf = mock.Mock(
        return_value=(
            "# Price adjustment: auto-adjusted prices "
            "(yfinance auto_adjust=True)\n2026-07-17,100"
        )
    )
    with mock.patch.dict(
        interface.VENDOR_METHODS,
        {"get_stock_data": {"akshare": ak, "yfinance": yf}},
        clear=False,
    ):
        output = interface.route_to_vendor(
            "get_stock_data",
            "600519",
            "2026-07-01",
            "2026-07-17",
            _provenance=True,
        )

    record = extract_provenance(output)[0]
    assert record.source == "yfinance"
    assert "fallback vendor selected" in record.timing
    assert "adjustment provider changed" in record.timing
    assert "auto-adjusted prices" in output


@pytest.mark.unit
@pytest.mark.parametrize("symbol", ["600519.SZ", "510300.SS"])
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
