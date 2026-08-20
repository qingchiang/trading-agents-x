"""Public request symbols use the positive listed-equity boundary."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tradingagents.application.contracts import AnalysisRequest, AssetType
from tradingagents.dataflows.symbol_utils import normalize_symbol


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("AAPL", "AAPL"),
        ("BRK.B", "BRK.B"),
        ("BRK-b", "BRK-B"),
        ("TOTDY", "TOTDY"),
        ("DOW", "DOW"),
        ("7203.T", "7203.T"),
        ("130A.T", "130A.T"),
        ("600519", "600519.SS"),
        ("000651", "000651.SZ"),
        ("600519.SH", "600519.SS"),
    ],
)
def test_request_accepts_supported_symbols(raw: str, expected: str) -> None:
    request = AnalysisRequest(ticker=raw, analysis_date="2026-07-24")

    assert request.ticker == expected
    assert request.asset_type is AssetType.STOCK


@pytest.mark.parametrize(
    "value",
    [
        "GC=F",
        "XAUUSD",
        "EURUSD",
        "EURUSD=X",
        "^GSPC",
        "SPX500",
        "DJI",
        "GSPC",
        "IXIC",
        "NDX",
        "RUT",
        "VIX",
        "NSEI",
        "BSESN",
        "N225",
        "HSI",
        "FTSE",
        "AXJO",
        "GDAXI",
        "FCHI",
        "0700.HK",
        "CNC.TO",
        "BHP.AX",
        "AAPL.SS",
        "000001.SZ",
        "399001.SZ",
    ],
)
def test_request_rejects_non_product_symbols(value: str) -> None:
    with pytest.raises(ValidationError, match="Only listed equity instruments"):
        AnalysisRequest(ticker=value, analysis_date="2026-07-24")


@pytest.mark.parametrize(
    "value",
    [
        "BTC-USD",
        "BTCUSDT",
        "DOGE-SHIB",
        "PEPE-USD",
        "ETHBTC",
    ],
)
def test_request_rejects_crypto_like_symbols(value: str) -> None:
    with pytest.raises(ValidationError, match="Crypto instruments are not supported"):
        AnalysisRequest(ticker=value, analysis_date="2026-07-24")


@pytest.mark.parametrize("value", ["", "bad symbol!", "A" * 65])
def test_request_rejects_invalid_symbols(value: str) -> None:
    with pytest.raises((ValidationError, ValueError)):
        AnalysisRequest(ticker=value, analysis_date="2026-07-24")


@pytest.mark.parametrize(
    "value",
    [
        "430001",
        "430001.BJ",
        "900001",
        "123456",
        "510300.SS",
        "399006.SZ",
        "000016",
        "000300",
        "000688",
        "000905",
        "000852",
        "000300.SZ",
    ],
)
def test_request_rejects_unsupported_china_symbols(value: str) -> None:
    with pytest.raises(
        (ValidationError, ValueError),
        match="not supported|Cannot infer|Only listed equity",
    ):
        AnalysisRequest(ticker=value, analysis_date="2026-07-24")


def test_explicit_crypto_asset_type_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AnalysisRequest(
            ticker="AAPL",
            analysis_date="2026-07-24",
            asset_type="crypto",
        )


def test_request_uses_the_data_layer_canonical_symbol() -> None:
    for raw in ("600519", "600519.SH", "000651", "AAPL"):
        request = AnalysisRequest(ticker=raw, analysis_date="2026-07-24")
        assert request.ticker == normalize_symbol(raw)
