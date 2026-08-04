from __future__ import annotations

import pandas as pd
import pytest

from tradingagents.application import instrument_names


@pytest.mark.unit
def test_japan_name_uses_configured_current_metadata(monkeypatch) -> None:
    observed: list[str] = []
    monkeypatch.setattr(
        instrument_names,
        "get_company_name",
        lambda ticker: observed.append(ticker) or "トヨタ自動車",
    )

    result = instrument_names.resolve_local_instrument_name(
        "7203.T",
        "2024-03-31",
        {"data_vendors_by_market": {".T": {"news_data": "jp_news,yfinance"}}},
    )

    assert result == "トヨタ自動車"
    assert observed == ["7203.T"]


@pytest.mark.unit
def test_japan_name_does_not_use_an_unconfigured_source(monkeypatch) -> None:
    called = False

    def unexpected(*_args):
        nonlocal called
        called = True

    monkeypatch.setattr(instrument_names, "get_company_name", unexpected)

    result = instrument_names.resolve_local_instrument_name(
        "7203.T",
        "2024-03-31",
        {"data_vendors_by_market": {".T": {"news_data": "yfinance"}}},
    )

    assert result is None
    assert not called


@pytest.mark.unit
def test_china_name_uses_current_metadata_for_historical_run(monkeypatch) -> None:
    monkeypatch.setattr(
        instrument_names,
        "get_company_profile",
        lambda _ticker: pd.DataFrame([{"A股简称": "贵州茅台"}]),
    )

    result = instrument_names.resolve_local_instrument_name(
        "600519.SS",
        "2020-03-31",
        {"data_vendors_by_market": {".SS": {"news_data": "cn_news,yfinance"}}},
    )

    assert result == "贵州茅台"


@pytest.mark.unit
def test_china_current_name_uses_a_share_short_name(monkeypatch) -> None:
    monkeypatch.setattr(
        instrument_names,
        "get_company_profile",
        lambda _ticker: pd.DataFrame([{"A股简称": "贵州茅台"}]),
    )

    result = instrument_names.resolve_local_instrument_name(
        "600519.SS",
        "2026-08-04",
        {"data_vendors_by_market": {".SS": {"news_data": "cn_news,yfinance"}}},
    )

    assert result == "贵州茅台"
