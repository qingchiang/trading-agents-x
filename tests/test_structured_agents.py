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
    SentimentSourceAssessment,
    SentimentSourceStatus,
    render_sentiment_report,
    validate_sentiment_sources,
)
from tradingagents.agents.sentiment_sources import (
    SentimentConfidence,
    SentimentSourceInput,
    sentiment_confidence,
)
from tradingagents.agents.utils.structured import (
    bind_structured,
    invoke_structured_or_freetext,
)
from tradingagents.dataflows.config import bind_config
from tradingagents.dataflows.market_context import market_suffix_of
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


def _assessment(
    source_id: str,
    *,
    status: SentimentSourceStatus = SentimentSourceStatus.SUBSTANTIVE,
    direction: SentimentBand | None = SentimentBand.BULLISH,
) -> SentimentSourceAssessment:
    return SentimentSourceAssessment(
        source_id=source_id,
        status=status,
        direction=direction,
        summary=f"{source_id} assessment.",
        key_evidence=(
            (f"{source_id} supplied concrete evidence.",)
            if status is SentimentSourceStatus.SUBSTANTIVE
            else ()
        ),
        limitations=(f"{source_id} coverage is bounded.",),
    )


def _report(
    source_ids: tuple[str, ...] = ("news", "stocktwits", "reddit"),
    *,
    band: SentimentBand = SentimentBand.BULLISH,
    score: float = 7.5,
    statuses: dict[str, SentimentSourceStatus] | None = None,
) -> SentimentReport:
    statuses = statuses or {}
    return SentimentReport(
        overall_band=band,
        overall_score=score,
        executive_summary="The available evidence is constructive overall.",
        source_assessments=tuple(
            _assessment(
                source_id,
                status=statuses.get(
                    source_id,
                    SentimentSourceStatus.SUBSTANTIVE,
                ),
                direction=(
                    SentimentBand.BULLISH
                    if statuses.get(
                        source_id,
                        SentimentSourceStatus.SUBSTANTIVE,
                    )
                    is SentimentSourceStatus.SUBSTANTIVE
                    else None
                ),
            )
            for source_id in source_ids
        ),
        cross_source_consensus=("Several sources point in the same direction.",),
        cross_source_divergences=(),
        dominant_themes=("Positioning is constructive.",),
        catalysts=("A scheduled disclosure could shift sentiment.",),
        risks=("The current narrative may be crowded.",),
        limitations=("Source windows and sample sizes are bounded.",),
    )


def _structured_llm(
    captured: dict,
    report: SentimentReport | None = None,
):
    report = report or _report()
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
    report = _report(
        ("news",),
        band=band,
        score=5.0,
    )

    rendered = render_sentiment_report(
        report,
        confidence="medium",
        confidence_score=0.55,
        source_labels={"news": "Routed ticker news"},
    )

    assert band.value in rendered
    assert "**Confidence:** Medium (0.55)" in rendered
    assert "## Source Assessments" in rendered
    assert "## Cross-source Divergences\n- —" in rendered
    assert "## Dominant Themes" in rendered
    assert "## Catalysts" in rendered
    assert "## Risks" in rendered
    assert rendered.endswith(
        "- Source windows and sample sizes are bounded."
    )


@pytest.mark.unit
@pytest.mark.parametrize("score", [-0.1, 10.1])
def test_sentiment_score_must_remain_on_the_declared_scale(score):
    with pytest.raises(ValidationError):
        _report(("news",), band=SentimentBand.NEUTRAL, score=score)


@pytest.mark.unit
def test_empty_catalysts_are_valid_and_render_explicitly():
    report = _report(("news",)).model_copy(update={"catalysts": ()})

    rendered = render_sentiment_report(
        report,
        confidence="medium",
        confidence_score=0.55,
        source_labels={"news": "Routed ticker news"},
    )

    assert "## Catalysts\n- —" in rendered


@pytest.mark.unit
def test_substantive_source_requires_direction_and_key_evidence():
    with pytest.raises(ValidationError):
        SentimentSourceAssessment(
            source_id="news",
            status=SentimentSourceStatus.SUBSTANTIVE,
            direction=SentimentBand.BULLISH,
            summary="Constructive news flow.",
            key_evidence=(),
        )


@pytest.mark.unit
def test_non_substantive_source_cannot_invent_direction_or_evidence():
    with pytest.raises(ValidationError):
        SentimentSourceAssessment(
            source_id="reddit",
            status=SentimentSourceStatus.UNAVAILABLE,
            direction=SentimentBand.BEARISH,
            summary="The source is unavailable.",
            key_evidence=("Invented evidence.",),
        )


@pytest.mark.unit
def test_sentiment_band_casing_is_normalized_without_broad_aliases():
    assessment = SentimentSourceAssessment(
        source_id="news",
        status=SentimentSourceStatus.SUBSTANTIVE,
        direction="neutral",
        key_evidence=("The supplied signals are balanced.",),
    )
    payload = _report(("news",)).model_dump(mode="json")
    payload["overall_band"] = "mildly bullish"
    payload["source_assessments"] = [
        assessment.model_dump(mode="json")
    ]

    report = SentimentReport.model_validate(payload)

    assert report.overall_band is SentimentBand.MILDLY_BULLISH
    assert report.source_assessments[0].direction is SentimentBand.NEUTRAL


@pytest.mark.unit
def test_missing_source_summary_reuses_validated_key_evidence_in_renderer():
    report = SentimentReport(
        overall_band=SentimentBand.NEUTRAL,
        overall_score=5.0,
        executive_summary="The evidence is balanced.",
        source_assessments=(
            SentimentSourceAssessment(
                source_id="news",
                status=SentimentSourceStatus.SUBSTANTIVE,
                direction="neutral",
                key_evidence=("The supplied signals are balanced.",),
            ),
        ),
        dominant_themes=("No directional theme dominates.",),
        risks=("Coverage remains limited.",),
        limitations=("Only one source was substantive.",),
    )

    rendered = render_sentiment_report(
        report,
        confidence="medium",
        confidence_score=0.55,
        source_labels={"news": "Routed ticker news"},
    )

    assert report.source_assessments[0].summary is None
    assert (
        "| substantive | Neutral | The supplied signals are balanced. |"
        in rendered
    )


@pytest.mark.unit
def test_sentiment_source_contract_rejects_missing_and_unknown_ids():
    report = _report(("news", "invented"))

    with pytest.raises(ValueError, match="missing=.*stocktwits"):
        validate_sentiment_sources(
            report,
            {
                "news": SentimentSourceStatus.SUBSTANTIVE,
                "stocktwits": SentimentSourceStatus.SUBSTANTIVE,
            },
        )


@pytest.mark.unit
def test_sentiment_source_ids_must_be_unique():
    with pytest.raises(ValidationError, match="must be unique"):
        SentimentReport(
            overall_band=SentimentBand.NEUTRAL,
            overall_score=5.0,
            executive_summary="The evidence is balanced.",
            source_assessments=(
                _assessment("news"),
                _assessment("news"),
            ),
            dominant_themes=("No theme dominates.",),
            risks=("Coverage is thin.",),
            limitations=("Only one source type was supplied.",),
        )


@pytest.mark.unit
def test_model_cannot_self_report_confidence():
    payload = _report(("news",)).model_dump(mode="json")
    payload["confidence"] = "high"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        SentimentReport.model_validate(payload)


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
                SentimentSourceInput(
                    "reddit",
                    "Reddit",
                    SentimentSourceStatus.SUBSTANTIVE,
                    True,
                    True,
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
        (
            (
                SentimentSourceInput(
                    "stocktwits",
                    "StockTwits",
                    SentimentSourceStatus.UNAVAILABLE,
                    False,
                    True,
                ),
                SentimentSourceInput(
                    "reddit",
                    "Reddit",
                    SentimentSourceStatus.UNAVAILABLE,
                    False,
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
def test_structured_none_falls_back_once_to_free_text():
    structured = MagicMock()
    structured.invoke.return_value = None
    plain = MagicMock()
    plain.invoke.return_value = MagicMock(content="FREETEXT")

    output = invoke_structured_or_freetext(
        structured,
        plain,
        "prompt",
        render=lambda value: value.executive_summary,
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
        mock.patch(f"{_SENTIMENT_MOD}.is_near_live", return_value=live),
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
        if llm is None:
            source_ids = ["news"]
            statuses = {
                "news": (
                    SentimentSourceStatus.UNAVAILABLE
                    if news_side_effect
                    else SentimentSourceStatus.SUBSTANTIVE
                )
            }
            if market_suffix_of(ticker):
                for signal in signals:
                    if signal.spec.live_only and not live:
                        continue
                    source_id = f"signal.{signal.spec.tag}"
                    source_ids.append(source_id)
                    if not signal.body:
                        status = SentimentSourceStatus.NO_SIGNAL
                    elif "unavailable" in signal.body.casefold():
                        status = SentimentSourceStatus.UNAVAILABLE
                    else:
                        status = SentimentSourceStatus.SUBSTANTIVE
                    statuses[source_id] = status
            elif live:
                source_ids.extend(("stocktwits", "reddit"))
            llm = _structured_llm(
                captured,
                _report(tuple(source_ids), statuses=statuses),
            )
        result = create_sentiment_analyst(
            llm
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
    report = _report(
        band=SentimentBand.MILDLY_BEARISH,
        score=4.0,
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
        mock.patch(f"{_SENTIMENT_MOD}.is_near_live", return_value=True),
    ):
        news.func.return_value = "NEWS_DATA"
        result = create_sentiment_analyst(
            _structured_llm(captured, report)
        )(_state())

    assert "Mildly Bearish" in result["sentiment_report"]
    assert "**Confidence:** Medium (0.55)" in result["sentiment_report"]
    assert "## Source Assessments" in result["sentiment_report"]
    assert "## Dominant Themes" in result["sentiment_report"]
    assert "## Risks" in result["sentiment_report"]
    assert "## Limitations" in result["sentiment_report"]
    assert result["sentiment_confidence"] == 0.55
    assert result["sentiment_output_warning"] is None
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
    assert '"source_assessments"' in contract
    assert '"dominant_themes"' in contract
    assert '"confidence"' not in contract
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
def test_unknown_or_missing_source_ids_trigger_free_text_fallback():
    captured = {}
    llm = _structured_llm(
        captured,
        _report(("news", "invented")),
    )
    llm.invoke.return_value = MagicMock(
        content="Fallback narrative after source validation failed."
    )

    result = _run(llm=llm)[-1]

    assert result["sentiment_report"].startswith("Fallback narrative")
    assert (
        result["sentiment_output_warning"]
        == "structured_output_failed"
    )
    llm.invoke.assert_called_once()


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
    assert "Do not return assessments for these non-applicable sources" in prompt
    assert "`stocktwits`" in prompt
    assert "`reddit`" in prompt
    assert "## Data Provenance" not in result["sentiment_report"]
    assert "`signal.fixture`" in result["sentiment_report"]
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
    prompt = "\n".join(map(str, captured["prompt"]))
    assert (
        "live-only source unavailable for historical or future trade_date"
        in prompt
    )
    assert "unavailable for historical or future date" not in result[
        "sentiment_report"
    ]
    assert result["prefetched_evidence"][1]["content"] is None
    assert (
        result["prefetched_evidence"][1]["records"][0]["timing"]
        == "live-only; unavailable for historical or future date; vendor not queried"
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
        ("Chinese", "structured text field in Chinese"),
        ("Japanese", "structured text field in Japanese"),
        ("English", "structured text field in English"),
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
    assert result["sentiment_output_warning"] in {
        "structured_output_failed",
        "structured_output_unavailable",
    }
    assert result["sentiment_confidence"] == 0.55


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
