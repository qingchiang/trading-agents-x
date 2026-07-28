"""Structured sentiment output and deterministic source gating."""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest import mock
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from tradingagents.agents.analysts.sentiment_analyst import (
    create_sentiment_analyst,
)
from tradingagents.agents.schemas import (
    SentimentBand,
    SentimentReport,
    render_sentiment_report,
)
from tradingagents.agents.utils.structured import (
    bind_structured,
    invoke_structured_or_freetext,
)
from tradingagents.dataflows.config import bind_config
from tradingagents.dataflows.market_signals import (
    FetchedSentimentSignal,
    SentimentSignal,
)
from tradingagents.graph.research_graph import _collect_evidence
from tradingagents.provenance import ProvenanceRecord, attach_provenance

_SENTIMENT_MOD = "tradingagents.agents.analysts.sentiment_analyst"
_SIGNALS_MOD = "tradingagents.dataflows.market_signals"


def _state(ticker: str = "NVDA", trade_date: str = "2026-01-15"):
    return {
        "company_of_interest": ticker,
        "trade_date": trade_date,
        "asset_type": "stock",
        "messages": [],
    }


def _structured_llm(
    captured: dict,
    report: SentimentReport | None = None,
):
    report = report or SentimentReport(
        overall_band=SentimentBand.BULLISH,
        overall_score=7.5,
        confidence="high",
        narrative="News and positioning evidence are constructive.",
    )
    structured = MagicMock()
    structured.invoke.side_effect = lambda prompt: (
        captured.__setitem__("prompt", prompt) or report
    )
    llm = MagicMock()
    llm.with_structured_output.return_value = structured
    return llm


@pytest.mark.unit
@pytest.mark.parametrize("band", list(SentimentBand))
def test_all_sentiment_bands_render_deterministically(band):
    report = SentimentReport(
        overall_band=band,
        overall_score=5.0,
        confidence="medium",
        narrative="Evidence narrative.",
    )

    rendered = render_sentiment_report(report)

    assert band.value in rendered
    assert "**Confidence:** Medium" in rendered
    assert rendered.endswith("Evidence narrative.")


@pytest.mark.unit
@pytest.mark.parametrize("score", [-0.1, 10.1])
def test_sentiment_score_must_remain_on_the_declared_scale(score):
    with pytest.raises(ValidationError):
        SentimentReport(
            overall_band=SentimentBand.NEUTRAL,
            overall_score=score,
            confidence="low",
            narrative="Invalid fixture.",
        )


@pytest.mark.unit
def test_structured_none_falls_back_once_to_free_text():
    structured = MagicMock()
    structured.invoke.return_value = None
    plain = MagicMock()
    plain.invoke.return_value = MagicMock(content="FREETEXT")

    output = invoke_structured_or_freetext(
        structured,
        plain,
        "prompt",
        render=lambda value: value.narrative,
        agent_name="sentiment",
        structured_prompt="JSON prompt",
    )

    assert output == "FREETEXT"
    structured.invoke.assert_called_once_with("JSON prompt")
    plain.invoke.assert_called_once_with("prompt")


@pytest.mark.unit
def test_structured_binding_applies_provider_output_limit():
    llm = MagicMock()
    llm.structured_output_max_tokens = 16_384

    bind_structured(llm, SentimentReport, "Sentiment Analyst")

    llm.with_structured_output.assert_called_once_with(
        SentimentReport,
        max_tokens=16_384,
    )


def _run(
    *,
    ticker="NVDA",
    trade_date="2026-01-15",
    routes=None,
    live=True,
    news_side_effect=None,
    signals=(),
    llm=None,
    provenance_appendix=True,
):
    captured = {}
    bind_config({"provenance_appendix": provenance_appendix})
    if routes:
        bind_config({"data_vendors_by_market": routes})
    with (
        mock.patch(
            f"{_SENTIMENT_MOD}.fetch_stocktwits_messages",
            return_value="STOCKTWITS_DATA",
        ) as stocktwits,
        mock.patch(
            f"{_SENTIMENT_MOD}.fetch_reddit_posts",
            return_value="REDDIT_DATA",
        ) as reddit,
        mock.patch(
            f"{_SENTIMENT_MOD}.fetch_sentiment_signals",
            return_value=signals,
        ) as market_signals,
        mock.patch(f"{_SENTIMENT_MOD}.get_news") as news,
        mock.patch(f"{_SENTIMENT_MOD}.is_live", return_value=live),
        mock.patch(f"{_SENTIMENT_MOD}.datetime") as clock,
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
        result = create_sentiment_analyst(
            llm or _structured_llm(captured)
        )(_state(ticker, trade_date))
    return (
        captured,
        stocktwits,
        reddit,
        market_signals,
        news,
        result,
    )


@pytest.mark.unit
def test_structured_report_is_persisted_in_state_and_messages():
    report = SentimentReport(
        overall_band=SentimentBand.MILDLY_BEARISH,
        overall_score=4.0,
        confidence="medium",
        narrative="Mixed source evidence.",
    )
    captured = {}
    with (
        mock.patch(
            f"{_SENTIMENT_MOD}.fetch_stocktwits_messages",
            return_value="STOCKTWITS_DATA",
        ),
        mock.patch(
            f"{_SENTIMENT_MOD}.fetch_reddit_posts",
            return_value="REDDIT_DATA",
        ),
        mock.patch(f"{_SENTIMENT_MOD}.get_news") as news,
        mock.patch(f"{_SENTIMENT_MOD}.is_live", return_value=True),
    ):
        news.func.return_value = "NEWS_DATA"
        result = create_sentiment_analyst(
            _structured_llm(captured, report)
        )(_state())

    assert "Mildly Bearish" in result["sentiment_report"]
    assert result["messages"][0].content == result["sentiment_report"]


@pytest.mark.unit
def test_json_mode_receives_schema_contract_without_changing_fallback_prompt():
    captured = {}
    llm = _structured_llm(captured)
    llm.preferred_structured_output_method = "json_mode"
    llm.structured_output_max_tokens = 16_384

    _run(llm=llm)

    prompt = captured["prompt"]
    contract = str(prompt[-1].content)
    assert "Return exactly one JSON object" in contract
    assert "JSON Schema" in contract
    assert '"overall_band"' in contract
    assert '"narrative"' in contract
    llm.with_structured_output.assert_called_once_with(
        SentimentReport,
        max_tokens=16_384,
    )


@pytest.mark.unit
def test_function_calling_keeps_the_original_sentiment_prompt():
    captured = {}
    llm = _structured_llm(captured)
    llm.preferred_structured_output_method = "function_calling"
    llm.structured_output_max_tokens = None

    _run(llm=llm)

    prompt_text = "\n".join(
        str(getattr(message, "content", message))
        for message in captured["prompt"]
    )
    assert "Return exactly one JSON object" not in prompt_text
    llm.with_structured_output.assert_called_once_with(SentimentReport)


@pytest.mark.unit
def test_us_run_uses_social_sources_and_separate_windows():
    captured, stocktwits, reddit, signals, news, _ = _run()

    news.func.assert_called_once_with(
        "NVDA",
        "2026-01-01",
        "2026-01-15",
    )
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
def test_routed_markets_skip_us_social_and_use_per_name_signals(
    ticker,
    route,
):
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
    assert "## Data Provenance" not in result["sentiment_report"]
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
    prompt = "\n".join(map(str, captured["prompt"]))
    assert "live-only source unavailable for historical trade_date" in prompt
    assert "unavailable for historical date" not in result["sentiment_report"]
    assert result["prefetched_evidence"][1]["content"] is None
    assert (
        result["prefetched_evidence"][1]["records"][0]["timing"]
        == "unavailable for historical date; vendor not queried"
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
def test_provenance_uses_actual_fallback_source():
    spec = SentimentSignal(
        tag="fixture",
        fetch=lambda *_args: "",
        evidence="ownership filings",
        source="configured primary",
        title="Ownership",
        intro="Per-name signal.",
        effective=lambda value: value,
        timing="publication-date filtered",
    )
    body = attach_provenance(
        "FALLBACK_DATA",
        ProvenanceRecord(
            evidence="ownership filings",
            source="actual fallback",
            requested="2026-01-15",
            effective="2026-01-15",
            timing="fallback source used",
        ),
    )
    signal = FetchedSentimentSignal(spec=spec, body=body)

    *_prefix, result = _run(
        ticker="600519.SS",
        routes={".SS": {"news_data": "cn_news"}},
        signals=(signal,),
    )

    assert "Data Provenance" not in result["sentiment_report"]
    fallback_block = next(
        block
        for block in result["prefetched_evidence"]
        if block["content"] == "FALLBACK_DATA"
    )
    assert fallback_block["records"][0]["source"] == "actual fallback"
    assert all(
        record["source"] != "configured primary"
        for record in fallback_block["records"]
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("language", "expected"),
    [
        ("Chinese", "narrative` field in Chinese"),
        ("Japanese", "narrative` field in Japanese"),
        ("English", "narrative, in English"),
    ],
)
def test_report_language_contract_is_explicit(language, expected):
    bind_config({"output_language": language})

    captured, *_ = _run()
    prompt = "\n".join(map(str, captured["prompt"]))

    assert expected in prompt


@pytest.mark.unit
@pytest.mark.parametrize(
    "failure",
    [
        NotImplementedError("provider unsupported"),
        ValueError("invalid structured output"),
    ],
)
def test_structured_output_failures_fall_back_without_provenance_loss(
    failure,
):
    llm = MagicMock()
    if isinstance(failure, NotImplementedError):
        llm.with_structured_output.side_effect = failure
    else:
        structured = MagicMock()
        structured.invoke.side_effect = failure
        llm.with_structured_output.return_value = structured
    llm.invoke.return_value = MagicMock(content="Fallback narrative.")

    *_prefix, result = _run(llm=llm)

    assert result["sentiment_report"].startswith("Fallback narrative.")
    assert "## Data Provenance" not in result["sentiment_report"]
    assert result["prefetched_evidence"]


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
    assert "Data Provenance" not in enabled["sentiment_report"]


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
        item for item in evidence if item.evidence_type == "margin trading balance"
    )
    assert margin.content == "Margin buying balance: JPY 12,345,678."
    assert margin.source == "JPX"
    assert margin.ref.startswith("ev_")
