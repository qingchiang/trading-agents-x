"""Market-specific sentiment signal registry and never-raise prefetch."""

from unittest import mock

import pytest

from tradingagents.agents.analysts import sentiment_analyst
from tradingagents.dataflows import market_signals


@pytest.mark.unit
def test_tokyo_registry_fetches_registered_signals():
    with mock.patch.object(
        market_signals, "get_large_holdings", return_value="HOLDINGS"
    ) as holdings, mock.patch.object(
        market_signals, "get_margin_balance", return_value="MARGIN"
    ) as margin, mock.patch.object(
        market_signals, "get_short_positions", return_value="SHORTS"
    ) as shorts, mock.patch.object(
        market_signals, "get_analyst_ratings_block", return_value="RATINGS"
    ) as ratings:
        results = market_signals.fetch_sentiment_signals("9984.T", "2026-07-18")

    assert {result.spec.tag for result in results} == {
        "large_holdings",
        "margin_balances",
        "short_positions",
        "analyst_ratings",
    }
    holdings.assert_called_once_with("9984.T", "2026-07-18")
    margin.assert_called_once_with("9984.T", "2026-07-18")
    shorts.assert_called_once_with("9984.T", "2026-07-18")
    ratings.assert_called_once_with("9984.T", "2026-07-18")


@pytest.mark.unit
def test_unregistered_market_has_no_market_specific_signals_yet():
    assert market_signals.fetch_sentiment_signals("600519.SS", "2026-07-18") == ()


@pytest.mark.unit
def test_signal_prefetch_never_raises():
    with mock.patch.object(
        market_signals,
        "get_large_holdings",
        side_effect=RuntimeError("temporary failure"),
    ):
        results = market_signals.fetch_sentiment_signals("9984.T", "2026-07-18")

    holdings = next(
        result for result in results if result.spec.tag == "large_holdings"
    )
    assert holdings.body == "<EDINET unavailable: RuntimeError>"


@pytest.mark.unit
def test_registered_signal_metadata_drives_prompt_rendering():
    spec = market_signals.SentimentSignal(
        tag="cn_margin",
        fetch=lambda *_args: "CN_SIGNAL",
        evidence="China margin balances",
        source="SSE",
        title="China margin positioning",
        intro="Read financing and securities-lending balances as positioning.",
        effective=lambda date: date,
        timing="market-date filtered",
    )
    fetched = market_signals.FetchedSentimentSignal(spec, "CN_SIGNAL")

    prompt = sentiment_analyst._build_system_message(
        ticker="600519.SS",
        news_start_date="2026-07-01",
        social_start_date="2026-07-10",
        end_date="2026-07-18",
        output_language="English",
        news_block="NEWS",
        stocktwits_block="<unavailable>",
        reddit_block="<unavailable>",
        market_signals=(fetched,),
    )

    assert "### China margin positioning" in prompt
    assert "<start_of_cn_margin>\nCN_SIGNAL\n<end_of_cn_margin>" in prompt
