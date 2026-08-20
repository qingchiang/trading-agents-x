"""Symbol normalization must apply on every supported yfinance path.

Regression tests for instrument identity and the news path: a broker symbol like
XAUUSD must resolve to the same Yahoo symbol
(GC=F) that the price path uses, so identity, realized-return, and news lookups
hit the right instrument instead of failing/mismatching.
"""
import pytest

import tradingagents.agents.utils.agent_utils as au
import tradingagents.dataflows.instrument_identity as identity_dataflow
import tradingagents.dataflows.yfinance_news as ynews
from tradingagents.dataflows.symbol_utils import market_timezone


@pytest.mark.parametrize(
    ("symbol", "timezone_name"),
    (
        ("JP225", "Asia/Tokyo"),
        ("^N225", "Asia/Tokyo"),
        ("HK50", "Asia/Hong_Kong"),
        ("^HSI", "Asia/Hong_Kong"),
        ("UK100", "Europe/London"),
        ("^FTSE", "Europe/London"),
        ("GER40", "Europe/Berlin"),
        ("^GDAXI", "Europe/Berlin"),
        ("FRA40", "Europe/Paris"),
        ("EU50", "Europe/Paris"),
        ("^NSEI", "Asia/Kolkata"),
        ("^BSESN", "Asia/Kolkata"),
        ("^GSPTSE", "America/Toronto"),
        ("^AXJO", "Australia/Sydney"),
    ),
)
def test_market_timezone_preserves_suffixless_index_identity(symbol, timezone_name):
    assert market_timezone(symbol).key == timezone_name


def test_identity_lookup_normalizes_symbol(monkeypatch):
    seen = {}

    class FakeTicker:
        def __init__(self, symbol):
            seen["symbol"] = symbol

        @property
        def info(self):
            return {"longName": "Gold Futures", "quoteType": "FUTURE"}

    monkeypatch.setattr(identity_dataflow.yf, "Ticker", FakeTicker)
    au.resolve_instrument_identity.cache_clear()

    identity = au.resolve_instrument_identity("XAUUSD")

    assert seen["symbol"] == "GC=F"  # normalized, not the raw broker symbol
    assert identity.get("company_name") == "Gold Futures"


def test_news_lookup_normalizes_symbol(monkeypatch):
    seen = {}

    class FakeTicker:
        def __init__(self, symbol):
            seen["symbol"] = symbol

        def get_news(self, count):
            return []

    monkeypatch.setattr(ynews.yf, "Ticker", FakeTicker)
    monkeypatch.setattr(ynews, "yf_retry", lambda fn: fn())

    out = ynews.get_news_yfinance("XAUUSD", "2025-01-01", "2025-01-10")

    assert seen["symbol"] == "GC=F"   # news queried with the canonical symbol
    assert "XAUUSD" in out            # the user's ticker stays in the report
    assert "GC=F" in out              # provenance noted
