"""Public request symbol validation must agree with the data path."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tradingagents.application.contracts import AnalysisRequest, AssetType
from tradingagents.dataflows.symbol_utils import normalize_symbol


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("AAPL", "AAPL"),
        ("GC=F", "GC=F"),
        ("600519", "600519.SS"),
        ("000001", "000001.SZ"),
        ("600519.SH", "600519.SS"),
        ("600519.SS", "600519.SS"),
        ("EURUSD", "EURUSD=X"),
    ],
)
def test_normalize_symbol_supported_aliases_and_passthrough(raw: str, expected: str) -> None:
    assert normalize_symbol(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("BTCUSD", "BTCUSD"),
        ("BTCUSDT", "BTCUSDT"),
        ("BTC-USDT", "BTC-USDT"),
        ("BTC-USDC", "BTC-USDC"),
        ("ethusdt", "ETHUSDT"),
    ],
)
def test_normalize_symbol_does_not_route_crypto_aliases(
    raw: str,
    expected: str,
) -> None:
    assert normalize_symbol(raw) == expected


@pytest.mark.parametrize(
    "value",
    ["AAPL", "BRK-B", "TOTDY", "0700.HK", "7203.T", "600519"],
)
def test_request_accepts_supported_symbols(value: str) -> None:
    request = AnalysisRequest(ticker=value, analysis_date="2026-07-24")

    assert request.ticker == normalize_symbol(value)


@pytest.mark.parametrize(
    "value",
    ["GC=F", "XAUUSD", "EURUSD", "EURUSD=X", "^GSPC", "SPX500"],
)
def test_request_rejects_explicit_non_equity_symbols(value: str) -> None:
    with pytest.raises(ValidationError, match="Only listed equity instruments"):
        AnalysisRequest(ticker=value, analysis_date="2026-07-24")


@pytest.mark.parametrize("value", ["", "bad symbol!", "A" * 65])
def test_request_rejects_invalid_symbols(value: str) -> None:
    with pytest.raises((ValidationError, ValueError)):
        AnalysisRequest(ticker=value, analysis_date="2026-07-24")


@pytest.mark.parametrize("value", ["430001", "430001.BJ", "900001", "123456"])
def test_request_rejects_unsupported_china_symbols(value: str) -> None:
    with pytest.raises(
        (ValidationError, ValueError),
        match="not supported|Cannot infer",
    ):
        AnalysisRequest(ticker=value, analysis_date="2026-07-24")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("AAPL", AssetType.STOCK),
        ("600519.SS", AssetType.STOCK),
    ],
)
def test_request_infers_asset_type(raw: str, expected: AssetType) -> None:
    request = AnalysisRequest(ticker=raw, analysis_date="2026-07-24")

    assert request.asset_type is expected


def test_request_uses_the_data_layer_canonical_symbol() -> None:
    for raw in (
        "600519",
        "600519.SH",
        "000001",
        "AAPL",
    ):
        request = AnalysisRequest(ticker=raw, analysis_date="2026-07-24")
        assert request.ticker == normalize_symbol(raw)
