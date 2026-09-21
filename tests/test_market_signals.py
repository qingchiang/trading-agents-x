"""Market-specific sentiment signal registry and never-raise prefetch."""

from unittest import mock

import pytest

from tests.data_policy import request_context
from tradingagents.data import market_signals
from tradingagents.domain.data_result import DataResult
from tradingagents.research.analysts import sentiment_analyst
from tradingagents.research.analysts.sentiment_sources import (
    SentimentSourceInput,
    SentimentSourceStatus,
)


@pytest.fixture(autouse=True)
def isolate_external_signals(monkeypatch):
    for name in (
        "get_large_holdings", "get_margin_balance", "get_short_positions",
        "get_analyst_ratings_result", "get_cn_margin_signal", "get_cn_holding_changes",
        "get_cn_research_signal", "get_cn_important_announcements",
    ):
        monkeypatch.setattr(market_signals, name, mock.Mock(return_value=DataResult("")))


@pytest.mark.unit
def test_tokyo_registry_fetches_registered_signals():
    fact = {
        "key": "target_mean_price",
        "label": "target mean price",
        "value": 6000,
        "measurement_kind": "currency",
        "unit": "JPY",
        "effective_date": "2026-07-18",
    }
    with mock.patch.object(
        market_signals, "is_near_live", return_value=True
    ), mock.patch.object(
        market_signals, "get_large_holdings", return_value=DataResult("HOLDINGS")
    ) as holdings, mock.patch.object(
        market_signals, "get_margin_balance", return_value=DataResult("MARGIN")
    ) as margin, mock.patch.object(
        market_signals, "get_short_positions", return_value=DataResult("SHORTS")
    ) as shorts, mock.patch.object(
        market_signals,
        "get_analyst_ratings_result",
        return_value=DataResult("RATINGS", numeric_facts=(fact,)),
    ) as ratings:
        results = market_signals.fetch_sentiment_signals("9984.T", "2026-07-18", data_context=request_context())

    assert {result.spec.tag for result in results} == {
        "large_holdings",
        "margin_balances",
        "short_positions",
        "analyst_ratings",
    }
    holdings.assert_called_once_with("9984.T", "2026-07-18", data_context=request_context())
    margin.assert_called_once_with("9984.T", "2026-07-18", data_context=request_context())
    shorts.assert_called_once_with("9984.T", "2026-07-18", data_context=request_context())
    ratings.assert_called_once_with("9984.T", "2026-07-18", data_context=request_context())
    analyst = next(
        result for result in results if result.spec.tag == "analyst_ratings"
    )
    assert analyst.result.numeric_facts == (fact,)


@pytest.mark.unit
def test_historical_tokyo_registry_does_not_query_live_only_signal():
    with mock.patch.object(
        market_signals, "is_near_live", return_value=False
    ), mock.patch.object(
        market_signals, "get_analyst_ratings_result"
    ) as ratings:
        results = market_signals.fetch_sentiment_signals(
            "9984.T",
            "2020-01-15",
            data_context=request_context(),
        )

    ratings.assert_not_called()
    analyst = next(
        result for result in results if result.spec.tag == "analyst_ratings"
    )
    assert "vendor not queried" in analyst.result.content
    assert analyst.retrieved_at is None


@pytest.mark.unit
def test_mainland_registry_fetches_registered_signals():
    patches = (
        mock.patch.object(market_signals, "get_cn_margin_signal", return_value=DataResult("MARGIN")),
        mock.patch.object(market_signals, "get_cn_holding_changes", return_value=DataResult("HOLDINGS")),
        mock.patch.object(
            market_signals,
            "get_cn_research_signal",
            return_value=DataResult("RESEARCH"),
        ),
        mock.patch.object(
            market_signals, "get_cn_important_announcements", return_value=DataResult("ANNOUNCEMENTS")
        ),
    )
    with (
        patches[0] as margin,
        patches[1] as holdings,
        patches[2] as research,
        patches[3] as announcements,
    ):
        results = market_signals.fetch_sentiment_signals("600519.SS", "2026-07-18", data_context=request_context())

    assert {result.spec.tag for result in results} == {
        "cn_margin",
        "cn_holding_changes",
        "cn_research",
        "cn_announcements",
    }
    for fetch in (margin, holdings, research, announcements):
        fetch.assert_called_once_with("600519.SS", "2026-07-18", data_context=request_context())


@pytest.mark.unit
def test_signal_prefetch_never_raises():
    with mock.patch.object(
        market_signals,
        "get_large_holdings",
        side_effect=RuntimeError("temporary failure"),
    ):
        results = market_signals.fetch_sentiment_signals("9984.T", "2026-07-18", data_context=request_context())

    holdings = next(
        result for result in results if result.spec.tag == "large_holdings"
    )
    assert holdings.result.content == "<EDINET unavailable: RuntimeError>"


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
    fetched = market_signals.FetchedSentimentSignal(spec, DataResult("CN_SIGNAL"))

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
