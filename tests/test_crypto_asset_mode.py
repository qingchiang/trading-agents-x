from __future__ import annotations

import pytest
from pydantic import ValidationError

from tradingagents.application.contracts import AnalysisRequest, AssetType


@pytest.mark.parametrize(
    "ticker",
    ["BTC-USD", "eth-usd", "BTCUSDT", "PEPE-USD", "BTC-JPY"],
)
def test_request_rejects_crypto_pair_symbols(ticker: str) -> None:
    with pytest.raises(ValidationError, match="Crypto instruments are not supported"):
        AnalysisRequest(ticker=ticker, analysis_date="2026-07-24")


@pytest.mark.parametrize("ticker", ["AAPL", "SPY", "7203.T"])
def test_request_defaults_non_crypto_symbols_to_stock(ticker: str) -> None:
    request = AnalysisRequest(ticker=ticker, analysis_date="2026-07-24")

    assert request.asset_type is AssetType.STOCK


def test_request_rejects_explicit_crypto_asset_type() -> None:
    with pytest.raises(ValidationError):
        AnalysisRequest(
            ticker="AAPL",
            analysis_date="2026-07-24",
            asset_type="crypto",
        )


def test_stock_keeps_all_requested_analysts() -> None:
    analysts = ("market", "social", "news", "fundamentals")

    request = AnalysisRequest(
        ticker="AAPL",
        analysis_date="2026-07-24",
        analysts=analysts,
    )

    assert request.analysts == analysts
