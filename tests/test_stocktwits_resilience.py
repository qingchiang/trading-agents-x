"""StockTwits fetch: transport-error resilience (#1024) and crypto symbol
mapping (#1113).

StockTwits lists crypto under ``<BASE>.X`` (Yahoo's ``BTC-USD`` 404s), and any
transport error must degrade to a placeholder rather than raise.
"""

from __future__ import annotations

import http.client
import json
from unittest.mock import patch
from urllib.error import HTTPError

import pytest

from tradingagents.dataflows import stocktwits


def _raise(exc):
    class _Resp:
        def __enter__(self_inner):
            return self_inner

        def __exit__(self_inner, *a):
            return False

        def read(self_inner):
            raise exc
    return _Resp()


def _json_response(payload):
    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(payload).encode()

    return _Resp()


@pytest.mark.unit
class TestStockTwitsResilience:
    @pytest.mark.parametrize(
        "exc",
        [
            http.client.IncompleteRead(b""),
            HTTPError("url", 503, "down", {}, None),
            TimeoutError("slow"),
        ],
    )
    def test_transport_errors_return_placeholder(self, exc):
        with patch.object(stocktwits, "urlopen", return_value=_raise(exc)):
            out = stocktwits.fetch_stocktwits_messages("NVDA")
        assert "unavailable" in out.lower()
        assert out.startswith("<stocktwits unavailable")

    def test_requested_window_uses_us_market_date_and_excludes_future(self):
        payload = {
            "messages": [
                {
                    "created_at": "2026-07-16T02:00:00Z",  # Jul 15 in New York
                    "user": {"username": "inside"},
                    "entities": {"sentiment": {"basic": "Bullish"}},
                    "body": "inside window",
                },
                {
                    "created_at": "2026-07-16T14:00:00Z",  # Jul 16 in New York
                    "user": {"username": "future"},
                    "entities": {"sentiment": {"basic": "Bearish"}},
                    "body": "future message",
                },
            ]
        }
        with patch.object(stocktwits, "urlopen", return_value=_json_response(payload)):
            out = stocktwits.fetch_stocktwits_messages(
                "NVDA", start_date="2026-07-15", end_date="2026-07-15"
            )
        assert "inside window" in out
        assert "future message" not in out
        assert "Total: 1 messages in 2026-07-15..2026-07-15" in out
        assert "[2026-07-15 22:00:00 EDT" in out
        assert "[2026-07-16T02:00:00Z" not in out

    def test_empty_filtered_sample_warns_it_is_not_historical_proof(self):
        payload = {
            "messages": [
                {"created_at": "2026-07-16T14:00:00Z", "body": "future"}
            ]
        }
        with patch.object(stocktwits, "urlopen", return_value=_json_response(payload)):
            out = stocktwits.fetch_stocktwits_messages(
                "NVDA", start_date="2026-07-15", end_date="2026-07-15"
            )
        assert "not evidence of no historical discussion" in out


@pytest.mark.unit
class TestStockTwitsCryptoSymbols:
    @pytest.mark.parametrize(
        ("ticker", "expected"),
        [
            ("BTC-USD", "BTC.X"),
            ("eth-usd", "ETH.X"),
            ("SOL-USD", "SOL.X"),
            ("BTCUSD", "BTC.X"),      # undashed broker form
            ("BTC-USDT", "BTC.X"),    # stablecoin quote
            ("AMD", "AMD"),
            ("BRK-B", "BRK-B"),       # dashed class share: untouched
            ("GOLD", "GOLD"),         # real equity (aliases elsewhere): untouched here
            ("XYZ-USD", "XYZ-USD"),   # unknown base: not treated as crypto
        ],
    )
    def test_symbol_mapping(self, ticker, expected):
        assert stocktwits._stocktwits_symbol(ticker) == expected

    def test_crypto_pair_requests_dot_x_endpoint(self):
        seen = {}

        def fake_urlopen(req, timeout=None):
            seen["url"] = req.full_url
            raise TimeoutError("stop after capturing the URL")

        with patch.object(stocktwits, "urlopen", side_effect=fake_urlopen):
            stocktwits.fetch_stocktwits_messages("BTC-USD")
        assert "/symbol/BTC.X.json" in seen["url"]
