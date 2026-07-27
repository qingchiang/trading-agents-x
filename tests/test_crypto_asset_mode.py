from __future__ import annotations

import pytest
from pydantic import ValidationError

from tradingagents.application.contracts import AnalysisRequest, AssetType


@pytest.mark.parametrize("ticker", ["BTC-USD", "eth-usd", "BTCUSDT"])
def test_request_detects_crypto_pair_symbols(ticker: str) -> None:
    request = AnalysisRequest(ticker=ticker, analysis_date="2026-07-24")

    assert request.asset_type is AssetType.CRYPTO


@pytest.mark.parametrize("ticker", ["AAPL", "SPY", "7203.T"])
def test_request_defaults_non_crypto_symbols_to_stock(ticker: str) -> None:
    request = AnalysisRequest(ticker=ticker, analysis_date="2026-07-24")

    assert request.asset_type is AssetType.STOCK


def test_request_filters_unsupported_crypto_fundamentals() -> None:
    request = AnalysisRequest(
        ticker="BTC-USD",
        analysis_date="2026-07-24",
        analysts=("market", "social", "news", "fundamentals"),
    )

    assert request.analysts == ("market", "social", "news")


def test_crypto_cannot_run_with_only_fundamentals() -> None:
    with pytest.raises(ValidationError, match="non-fundamentals"):
        AnalysisRequest(
            ticker="BTC-USD",
            analysis_date="2026-07-24",
            analysts=("fundamentals",),
        )


def test_stock_keeps_all_requested_analysts() -> None:
    analysts = ("market", "social", "news", "fundamentals")

    request = AnalysisRequest(
        ticker="AAPL",
        analysis_date="2026-07-24",
        analysts=analysts,
    )

    assert request.analysts == analysts
