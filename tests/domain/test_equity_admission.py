from __future__ import annotations

import pytest
from pydantic import ValidationError

from tradingagents.domain.runs import AnalysisRequest


@pytest.mark.parametrize("ticker", ["BTC-USD", "eth-usd", "DOGE-SHIB", "BTCUSDT"])
def test_request_rejects_crypto_pair_symbols(ticker: str) -> None:
    with pytest.raises(
        ValidationError,
        match="Crypto instruments are not supported|Only listed equity instruments",
    ):
        AnalysisRequest(ticker=ticker, analysis_date="2026-07-24")
