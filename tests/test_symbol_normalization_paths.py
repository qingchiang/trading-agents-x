"""Symbol normalization must apply on every yfinance path, not just price fetch.

Regression tests for #983 (instrument identity), #984 (reflection returns), and
the news path: a broker symbol like XAUUSD must resolve to the same Yahoo symbol
(GC=F) that the price path uses, so identity, realized-return, and news lookups
hit the right instrument instead of failing/mismatching.
"""
from datetime import date

import pandas as pd
import pytest

import tradingagents.agents.utils.agent_utils as au
import tradingagents.dataflows.instrument_identity as identity_dataflow
import tradingagents.dataflows.yfinance_news as ynews
from tradingagents.application.outcomes import OutcomeSettlement
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


def test_outcome_settlement_normalizes_symbol(
    monkeypatch,
    app_settings,
    repository,
):
    queried = []

    class FakeTicker:
        def __init__(self, symbol):
            queried.append(symbol)

        def history(self, *args, **kwargs):
            return pd.DataFrame(
                {"Close": [100.0, 101.0, 102.0, 103.0, 104.0, 105.0]},
                index=pd.date_range("2025-01-02", periods=6, freq="B"),
            )

    monkeypatch.setattr(
        "tradingagents.application.outcomes.market_today",
        lambda symbol: date(2025, 1, 20),
    )
    history = type("History", (), {"Ticker": FakeTicker})()
    settlement = OutcomeSettlement(
        app_settings,
        repository,
        history_provider=history,
    )

    outcome = settlement.observe(
        "XAUUSD",
        date(2025, 1, 2),
        holding_intervals=5,
        benchmark="SPY",
    )

    assert queried[0] == "GC=F"  # stock symbol normalized (#984)
    assert queried[1] == "SPY"   # benchmark left as the canonical symbol
    assert outcome is not None
    assert outcome.holding_intervals == 5


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
