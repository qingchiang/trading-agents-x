"""Market-specific sentiment signal registry and never-raise prefetch."""

from unittest import mock

import pytest

from tradingagents.agents.analysts import sentiment_analyst
from tradingagents.agents.sentiment_sources import (
    SentimentSourceInput,
    SentimentSourceStatus,
)
from tradingagents.dataflows import market_signals


@pytest.mark.unit
def test_tokyo_registry_fetches_registered_signals():
    with mock.patch.object(
        market_signals, "is_near_live", return_value=True
    ), mock.patch.object(
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
def test_historical_tokyo_registry_does_not_query_live_only_signal():
    with mock.patch.object(
        market_signals, "is_near_live", return_value=False
    ), mock.patch.object(
        market_signals, "get_analyst_ratings_block"
    ) as ratings:
        results = market_signals.fetch_sentiment_signals(
            "9984.T",
            "2020-01-15",
        )

    ratings.assert_not_called()
    analyst = next(
        result for result in results if result.spec.tag == "analyst_ratings"
    )
    assert "vendor not queried" in analyst.body
    assert analyst.retrieved_at is None


@pytest.mark.unit
def test_mainland_registry_fetches_registered_signals():
    patches = (
        mock.patch.object(market_signals, "get_cn_margin_signal", return_value="MARGIN"),
        mock.patch.object(market_signals, "get_cn_holding_changes", return_value="HOLDINGS"),
        mock.patch.object(market_signals, "get_cn_research_signal", return_value="RESEARCH"),
        mock.patch.object(
            market_signals, "get_cn_important_announcements", return_value="ANNOUNCEMENTS"
        ),
    )
    with (
        patches[0] as margin,
        patches[1] as holdings,
        patches[2] as research,
        patches[3] as announcements,
    ):
        results = market_signals.fetch_sentiment_signals("600519.SS", "2026-07-18")

    assert {result.spec.tag for result in results} == {
        "cn_margin",
        "cn_holding_changes",
        "cn_research",
        "cn_announcements",
    }
    for fetch in (margin, holdings, research, announcements):
        fetch.assert_called_once_with("600519.SS", "2026-07-18")


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
        sentiment_sources=(
            SentimentSourceInput(
                source_id="news",
                label="Routed ticker news",
                status=SentimentSourceStatus.SUBSTANTIVE,
                applicable=True,
                degraded=False,
            ),
            SentimentSourceInput(
                source_id="signal.cn_margin",
                label="China margin positioning",
                status=SentimentSourceStatus.SUBSTANTIVE,
                applicable=True,
                degraded=False,
            ),
        ),
    )

    assert "### China margin positioning" in prompt
    assert "source_id `signal.cn_margin`" in prompt
    assert "<start_of_cn_margin>\nCN_SIGNAL\n<end_of_cn_margin>" in prompt
