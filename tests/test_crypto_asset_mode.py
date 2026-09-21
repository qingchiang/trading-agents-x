from __future__ import annotations

import pytest
from pydantic import ValidationError

from tradingagents.domain.common import AssetType
from tradingagents.domain.runs import AnalysisRequest


@pytest.mark.parametrize("ticker", ["BTC-USD", "eth-usd", "DOGE-SHIB", "BTCUSDT"])
def test_request_rejects_crypto_pair_symbols(ticker: str) -> None:
    with pytest.raises(
        ValidationError,
        match="Crypto instruments are not supported|Only listed equity instruments",
    ):
        AnalysisRequest(ticker=ticker, analysis_date="2026-07-24")


@pytest.mark.parametrize("ticker", ["AAPL", "SPY", "7203.T"])
def test_request_defaults_supported_symbols_to_stock(ticker: str) -> None:
    request = AnalysisRequest(ticker=ticker, analysis_date="2026-07-24")

    assert request.asset_type is AssetType.STOCK
