"""Markdown-first sentiment collection and deterministic source gating."""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest import mock
from unittest.mock import MagicMock

import pytest

from tradingagents.agents.analysts.sentiment_analyst import (
    create_sentiment_analyst,
)
from tradingagents.agents.sentiment_sources import (
    SentimentConfidence,
    SentimentSourceInput,
    SentimentSourceStatus,
    sentiment_confidence,
)
from tradingagents.dataflows.config import bind_config
from tradingagents.dataflows.market_signals import (
    FetchedSentimentSignal,
    SentimentSignal,
)
from tradingagents.graph.research_graph import _collect_evidence
from tradingagents.provenance import ProvenanceRecord, attach_provenance

_MODULE = "tradingagents.agents.analysts.sentiment_analyst"


def _state(ticker: str = "NVDA", trade_date: str = "2026-01-15"):
    return {
        "company_of_interest": ticker,
        "trade_date": trade_date,
        "asset_type": "stock",
        "messages": [],
    }


def _capturing_llm(captured: dict, text: str = "# Sentiment\n\nA rich draft."):
    llm = MagicMock()

    def invoke(prompt):
        captured["prompt"] = prompt
        return MagicMock(content=text)

    llm.invoke.side_effect = invoke
    return llm


def _run(
    *,
    ticker: str = "NVDA",
    trade_date: str = "2026-01-15",
    routes=None,
    live: bool = True,
    news_side_effect: Exception | None = None,
    signals: tuple[FetchedSentimentSignal, ...] = (),
    llm=None,
    provenance_appendix: bool = True,
):
    captured: dict = {}
    bind_config({"provenance_appendix": provenance_appendix})
    if routes:
        bind_config({"data_vendors_by_market": routes})
    with (
        mock.patch(
            f"{_MODULE}.fetch_stocktwits_messages",
            return_value="STOCKTWITS_DATA",
        ) as stocktwits,
        mock.patch(
            f"{_MODULE}.fetch_reddit_posts",
            return_value="REDDIT_DATA",
        ) as reddit,
        mock.patch(
            f"{_MODULE}.fetch_sentiment_signals",
            return_value=signals,
        ) as market_signals,
        mock.patch(f"{_MODULE}.get_news") as news,
        mock.patch(f"{_MODULE}.is_near_live", return_value=live),
        mock.patch(f"{_MODULE}.datetime") as clock,
    ):
        clock.now.return_value = datetime(
            2026,
            1,
            15,
            12,
            tzinfo=timezone.utc,
        )
        if news_side_effect:
            news.func.side_effect = news_side_effect
        else:
            news.func.return_value = "NEWS_DATA"
        llm = llm or _capturing_llm(captured)
        result = create_sentiment_analyst(llm)(_state(ticker, trade_date))
    return captured, stocktwits, reddit, market_signals, news, result


@pytest.mark.unit
@pytest.mark.parametrize(
    ("sources", "expected"),
    [
        (
            (
                SentimentSourceInput(
                    "news",
                    "News",
                    SentimentSourceStatus.SUBSTANTIVE,
                    True,
                    False,
                ),
                SentimentSourceInput(
                    "positioning",
                    "Positioning",
                    SentimentSourceStatus.SUBSTANTIVE,
                    True,
                    False,
                ),
            ),
            SentimentConfidence("high", 0.8),
        ),
        (
            (
                SentimentSourceInput(
                    "news",
                    "News",
                    SentimentSourceStatus.SUBSTANTIVE,
                    True,
                    False,
                ),
            ),
            SentimentConfidence("medium", 0.55),
        ),
        (
            (
                SentimentSourceInput(
                    "stocktwits",
                    "StockTwits",
                    SentimentSourceStatus.SUBSTANTIVE,
                    True,
                    True,
                ),
            ),
            SentimentConfidence("low", 0.25),
        ),
    ],
)
def test_sentiment_confidence_uses_fixed_coverage_rules(sources, expected):
    assert sentiment_confidence(sources) == expected


@pytest.mark.unit
def test_markdown_draft_is_persisted_with_local_confidence():
    captured, *_prefix, result = _run()

    assert result["sentiment_report"] == "# Sentiment\n\nA rich draft."
    assert result["messages"][0].content == result["sentiment_report"]
    assert result["sentiment_confidence"] == 0.55
    assert result["prefetched_evidence"]
    assert "comprehensive sentiment report" in "\n".join(
        str(message.content) for message in captured["prompt"]
    )


@pytest.mark.unit
def test_us_run_uses_social_sources_and_separate_windows():
    captured, stocktwits, reddit, signals, news, _ = _run()

    news.func.assert_called_once_with("NVDA", "2026-01-01", "2026-01-15")
    stocktwits.assert_called_once_with(
        "NVDA",
        limit=30,
        start_date="2026-01-08",
        end_date="2026-01-15",
    )
    reddit.assert_called_once_with(
        "NVDA",
        start_date="2026-01-08",
        end_date="2026-01-15",
    )
    signals.assert_not_called()
    prompt = "\n".join(map(str, captured["prompt"]))
    assert "requested window 2026-01-01 to 2026-01-15" in prompt


@pytest.mark.unit
@pytest.mark.parametrize(
    ("ticker", "route"),
    [
        ("9984.T", {".T": {"news_data": "jp_news"}}),
        ("600519.SS", {".SS": {"news_data": "cn_news"}}),
        ("000001.SZ", {".SZ": {"news_data": "cn_news"}}),
    ],
)
def test_routed_markets_skip_us_social_and_use_per_name_signals(ticker, route):
    spec = SentimentSignal(
        tag="fixture",
        fetch=lambda *_args: "",
        evidence="positioning",
        source="official source",
        title="Positioning",
        intro="Per-name signal.",
        effective=lambda value: value,
        timing="publication-date filtered",
    )
    signal = FetchedSentimentSignal(spec=spec, body="SIGNAL_DATA")

    captured, stocktwits, reddit, signals, _, result = _run(
        ticker=ticker,
        routes=route,
        signals=(signal,),
    )

    stocktwits.assert_not_called()
    reddit.assert_not_called()
    signals.assert_called_once_with(ticker, "2026-01-15")
    prompt = "\n".join(map(str, captured["prompt"]))
    assert "unavailable: no coverage for this market" in prompt
    assert "SIGNAL_DATA" in prompt
    assert "`stocktwits`" in prompt
    assert "`reddit`" in prompt
    assert result["sentiment_confidence"] == 0.55
    signal_block = next(
        block
        for block in result["prefetched_evidence"]
        if block["content"] == "SIGNAL_DATA"
    )
    assert signal_block["records"][0]["source"] == "official source"


@pytest.mark.unit
def test_historical_us_run_never_queries_live_social_sources():
    captured, stocktwits, reddit, signals, _, result = _run(
        trade_date="2020-01-15",
        live=False,
    )

    stocktwits.assert_not_called()
    reddit.assert_not_called()
    signals.assert_not_called()
    assert "live-only source unavailable for historical or future" in "\n".join(
        map(str, captured["prompt"])
    )
    assert result["prefetched_evidence"][1]["content"] is None
    assert "vendor not queried" in (
        result["prefetched_evidence"][1]["records"][0]["timing"]
    )


@pytest.mark.unit
def test_news_error_degrades_to_a_redacted_type_marker():
    captured, *_rest, result = _run(
        news_side_effect=RuntimeError("private provider detail"),
    )

    prompt = "\n".join(map(str, captured["prompt"]))
    assert "<news unavailable: RuntimeError>" in prompt
    assert "private provider detail" not in prompt
    assert result["sentiment_report"]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("language", "expected"),
    [
        ("Chinese", "structured text field in Chinese"),
        ("Japanese", "structured text field in Japanese"),
        ("English", "structured text field in English"),
    ],
)
def test_report_language_contract_is_explicit(language, expected):
    bind_config({"output_language": language})
    captured, *_ = _run()
    assert expected in "\n".join(map(str, captured["prompt"]))


@pytest.mark.unit
def test_markdown_generation_failure_is_not_silently_fabricated():
    llm = MagicMock()
    llm.invoke.side_effect = RuntimeError("provider failed")

    with pytest.raises(RuntimeError, match="provider failed"):
        _run(llm=llm)


@pytest.mark.unit
def test_prefetched_evidence_is_independent_of_appendix_display_setting():
    enabled = _run(provenance_appendix=True)[-1]
    disabled = _run(provenance_appendix=False)[-1]
    enabled_evidence = _collect_evidence(
        enabled["messages"],
        enabled["sentiment_report"],
        requested_date=date(2026, 1, 15),
        analyst="social",
        prefetched_blocks=enabled["prefetched_evidence"],
    )
    disabled_evidence = _collect_evidence(
        disabled["messages"],
        disabled["sentiment_report"],
        requested_date=date(2026, 1, 15),
        analyst="social",
        prefetched_blocks=disabled["prefetched_evidence"],
    )

    assert enabled["sentiment_report"] == disabled["sentiment_report"]
    assert enabled["prefetched_evidence"] == disabled["prefetched_evidence"]
    assert enabled_evidence == disabled_evidence


@pytest.mark.unit
def test_japan_margin_figure_becomes_resolvable_evidence():
    spec = SentimentSignal(
        tag="margin",
        fetch=lambda *_args: "",
        evidence="margin trading balance",
        source="JPX",
        title="Margin balance",
        intro="Per-name margin signal.",
        effective=lambda value: value,
        timing="publication-date filtered",
    )
    body = attach_provenance(
        "Margin buying balance: JPY 12,345,678.",
        ProvenanceRecord(
            evidence="margin trading balance",
            source="JPX",
            requested="2026-01-15",
            effective="2026-01-14",
            timing="publication-date filtered",
        ),
    )
    result = _run(
        ticker="2802.T",
        routes={".T": {"news_data": "jp_news"}},
        signals=(FetchedSentimentSignal(spec=spec, body=body),),
    )[-1]

    evidence = _collect_evidence(
        result["messages"],
        result["sentiment_report"],
        requested_date=date(2026, 1, 15),
        analyst="social",
        prefetched_blocks=result["prefetched_evidence"],
    )
    margin = next(
        item
        for item in evidence
        if item.evidence_type == "margin trading balance"
    )
    assert margin.content == "Margin buying balance: JPY 12,345,678."
    assert margin.source == "JPX"
