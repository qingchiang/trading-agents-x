"""Tests for structured-output agents (Trader, Research Manager, Sentiment Analyst).

The Portfolio Manager has its own coverage in tests/test_memory_log.py
(which exercises the full memory-log → PM injection cycle).  This file
covers the parallel schemas, render functions, and graceful-fallback
behavior we added for the Trader, Research Manager, and Sentiment Analyst
so they share the same deterministic output shape.
"""

import copy
from unittest import mock
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

import tradingagents.dataflows.config as config_module
import tradingagents.default_config as default_config
from tradingagents.agents.analysts.sentiment_analyst import create_sentiment_analyst
from tradingagents.agents.managers.research_manager import create_research_manager
from tradingagents.agents.schemas import (
    PortfolioDecision,
    PortfolioRating,
    ResearchPlan,
    SentimentBand,
    SentimentReport,
    TraderAction,
    TraderProposal,
    render_research_plan,
    render_sentiment_report,
    render_trader_proposal,
)
from tradingagents.agents.trader.trader import create_trader
from tradingagents.dataflows.config import set_config
from tradingagents.dataflows.errors import VendorNotConfiguredError
from tradingagents.dataflows.market_signals import FetchedSentimentSignal, SentimentSignal
from tradingagents.provenance import ProvenanceRecord, attach_provenance

# ---------------------------------------------------------------------------
# Render functions
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRenderTraderProposal:
    def test_minimal_required_fields(self):
        p = TraderProposal(action=TraderAction.HOLD, reasoning="Balanced setup; no edge.")
        md = render_trader_proposal(p)
        assert "**Action**: Hold" in md
        assert "**Reasoning**: Balanced setup; no edge." in md
        # The trailing FINAL TRANSACTION PROPOSAL line is preserved for the
        # analyst stop-signal text and any external code that greps for it.
        assert "FINAL TRANSACTION PROPOSAL: **HOLD**" in md

    def test_optional_fields_included_when_present(self):
        p = TraderProposal(
            action=TraderAction.BUY,
            reasoning="Strong technicals + fundamentals.",
            entry_price=189.5,
            stop_loss=178.0,
            position_sizing="6% of portfolio",
        )
        md = render_trader_proposal(p)
        assert "**Action**: Buy" in md
        assert "**Entry Price**: 189.5" in md
        assert "**Stop Loss**: 178.0" in md
        assert "**Position Sizing**: 6% of portfolio" in md
        assert "FINAL TRANSACTION PROPOSAL: **BUY**" in md

    def test_optional_fields_omitted_when_absent(self):
        p = TraderProposal(action=TraderAction.SELL, reasoning="Guidance cut.")
        md = render_trader_proposal(p)
        assert "Entry Price" not in md
        assert "Stop Loss" not in md
        assert "Position Sizing" not in md
        assert "FINAL TRANSACTION PROPOSAL: **SELL**" in md


@pytest.mark.unit
class TestNullishFloatCoercion:
    """A weak LLM may write "None"/"N/A" into an optional float field (#1058);
    coerce those to None so the structured call validates instead of erroring."""

    def test_trader_nullish_strings_coerce_to_none(self):
        for sentinel in ("None", "N/A", "null", "-", "", "TBD"):
            p = TraderProposal(
                action=TraderAction.HOLD,
                reasoning="x",
                entry_price=sentinel,
                stop_loss=sentinel,
            )
            assert p.entry_price is None
            assert p.stop_loss is None

    def test_trader_real_numeric_string_still_parses(self):
        p = TraderProposal(action=TraderAction.BUY, reasoning="x", entry_price="189.5")
        assert p.entry_price == 189.5

    def test_pm_nullish_price_target_coerces_to_none(self):
        d = PortfolioDecision(
            rating=PortfolioRating.OVERWEIGHT,
            executive_summary="s",
            investment_thesis="t",
            price_target="N/A",
        )
        assert d.price_target is None


@pytest.mark.unit
class TestRenderResearchPlan:
    def test_required_fields(self):
        p = ResearchPlan(
            recommendation=PortfolioRating.OVERWEIGHT,
            rationale="Bull case carried; tailwinds intact.",
            strategic_actions="Build position over two weeks; cap at 5%.",
        )
        md = render_research_plan(p)
        assert "**Recommendation**: Overweight" in md
        assert "**Rationale**: Bull case carried" in md
        assert "**Strategic Actions**: Build position" in md

    def test_all_5_tier_ratings_render(self):
        for rating in PortfolioRating:
            p = ResearchPlan(
                recommendation=rating,
                rationale="r",
                strategic_actions="s",
            )
            md = render_research_plan(p)
            assert f"**Recommendation**: {rating.value}" in md


# ---------------------------------------------------------------------------
# Trader agent: structured happy path + fallback
# ---------------------------------------------------------------------------


def _make_trader_state():
    return {
        "company_of_interest": "NVDA",
        "investment_plan": "**Recommendation**: Buy\n**Rationale**: ...\n**Strategic Actions**: ...",
    }


def _structured_trader_llm(captured: dict, proposal: TraderProposal | None = None):
    """Build a MagicMock LLM whose with_structured_output binding captures the
    prompt and returns a real TraderProposal so render_trader_proposal works.
    """
    if proposal is None:
        proposal = TraderProposal(
            action=TraderAction.BUY,
            reasoning="Strong setup.",
        )
    structured = MagicMock()
    structured.invoke.side_effect = lambda prompt: (
        captured.__setitem__("prompt", prompt) or proposal
    )
    llm = MagicMock()
    llm.with_structured_output.return_value = structured
    return llm


@pytest.mark.unit
def test_invoke_structured_falls_back_when_result_is_none():
    # A thinking model can answer in plain text, leaving the parser with None.
    # That must fall back to free text, not crash on render(None) (#1051).
    from tradingagents.agents.utils.structured import invoke_structured_or_freetext

    structured = MagicMock()
    structured.invoke.return_value = None
    plain = MagicMock()
    plain.invoke.return_value = MagicMock(content="FREETEXT")

    out = invoke_structured_or_freetext(
        structured, plain, "prompt", render=lambda r: r.rating, agent_name="t"
    )
    assert out == "FREETEXT"
    plain.invoke.assert_called_once()


@pytest.mark.unit
class TestTraderAgent:
    def test_structured_path_produces_rendered_markdown(self):
        captured = {}
        proposal = TraderProposal(
            action=TraderAction.BUY,
            reasoning="AI capex cycle intact; institutional flows constructive.",
            entry_price=189.5,
            stop_loss=178.0,
            position_sizing="6% of portfolio",
        )
        llm = _structured_trader_llm(captured, proposal)
        trader = create_trader(llm)
        result = trader(_make_trader_state())
        plan = result["trader_investment_plan"]
        assert "**Action**: Buy" in plan
        assert "**Entry Price**: 189.5" in plan
        assert "FINAL TRANSACTION PROPOSAL: **BUY**" in plan
        # The same rendered markdown is also added to messages for downstream agents.
        assert plan in result["messages"][0].content

    def test_prompt_includes_investment_plan(self):
        captured = {}
        llm = _structured_trader_llm(captured)
        trader = create_trader(llm)
        trader(_make_trader_state())
        # The investment plan is in the user message of the captured prompt.
        prompt = captured["prompt"]
        assert any("Proposed Investment Plan" in m["content"] for m in prompt)

    def test_falls_back_to_freetext_when_structured_unavailable(self):
        plain_response = (
            "**Action**: Sell\n\nGuidance cut hits margins.\n\n"
            "FINAL TRANSACTION PROPOSAL: **SELL**"
        )
        llm = MagicMock()
        llm.with_structured_output.side_effect = NotImplementedError("provider unsupported")
        llm.invoke.return_value = MagicMock(content=plain_response)
        trader = create_trader(llm)
        result = trader(_make_trader_state())
        assert result["trader_investment_plan"] == plain_response


# ---------------------------------------------------------------------------
# Research Manager agent: structured happy path + fallback
# ---------------------------------------------------------------------------


def _make_rm_state():
    return {
        "company_of_interest": "NVDA",
        "investment_debate_state": {
            "history": "Bull and bear arguments here.",
            "bull_history": "Bull says...",
            "bear_history": "Bear says...",
            "current_response": "",
            "judge_decision": "",
            "count": 1,
        },
    }


def _structured_rm_llm(captured: dict, plan: ResearchPlan | None = None):
    if plan is None:
        plan = ResearchPlan(
            recommendation=PortfolioRating.HOLD,
            rationale="Balanced view across both sides.",
            strategic_actions="Hold current position; reassess after earnings.",
        )
    structured = MagicMock()
    structured.invoke.side_effect = lambda prompt: (
        captured.__setitem__("prompt", prompt) or plan
    )
    llm = MagicMock()
    llm.with_structured_output.return_value = structured
    return llm


@pytest.mark.unit
class TestResearchManagerAgent:
    def test_structured_path_produces_rendered_markdown(self):
        captured = {}
        plan = ResearchPlan(
            recommendation=PortfolioRating.OVERWEIGHT,
            rationale="Bull case is stronger; AI tailwind intact.",
            strategic_actions="Build position gradually over two weeks.",
        )
        llm = _structured_rm_llm(captured, plan)
        rm = create_research_manager(llm)
        result = rm(_make_rm_state())
        ip = result["investment_plan"]
        assert "**Recommendation**: Overweight" in ip
        assert "**Rationale**: Bull case" in ip
        assert "**Strategic Actions**: Build position" in ip

    def test_prompt_uses_5_tier_rating_scale(self):
        """The RM prompt must list all five tiers so the schema enum matches user expectations."""
        captured = {}
        llm = _structured_rm_llm(captured)
        rm = create_research_manager(llm)
        rm(_make_rm_state())
        prompt = captured["prompt"]
        for tier in ("Buy", "Overweight", "Hold", "Underweight", "Sell"):
            assert f"**{tier}**" in prompt, f"missing {tier} in prompt"

    def test_falls_back_to_freetext_when_structured_unavailable(self):
        plain_response = "**Recommendation**: Sell\n\n**Rationale**: ...\n\n**Strategic Actions**: ..."
        llm = MagicMock()
        llm.with_structured_output.side_effect = NotImplementedError("provider unsupported")
        llm.invoke.return_value = MagicMock(content=plain_response)
        rm = create_research_manager(llm)
        result = rm(_make_rm_state())
        assert result["investment_plan"] == plain_response


# ---------------------------------------------------------------------------
# Sentiment Analyst: schema, render, structured happy path + fallback
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRenderSentimentReport:
    def test_header_contains_band_and_score(self):
        report = SentimentReport(
            overall_band=SentimentBand.BULLISH,
            overall_score=7.2,
            confidence="high",
            narrative="Source breakdown here.",
        )
        md = render_sentiment_report(report)
        assert "**Overall Sentiment:** **Bullish**" in md
        assert "(Score: 7.2/10)" in md

    def test_header_contains_confidence(self):
        report = SentimentReport(
            overall_band=SentimentBand.NEUTRAL,
            overall_score=5.0,
            confidence="low",
            narrative="Limited data.",
        )
        assert "**Confidence:** Low" in render_sentiment_report(report)

    def test_narrative_preserved_in_output(self):
        narrative = "## Breakdown\n\nStockTwits: 70% bullish.\n\n| Signal | Direction |\n|---|---|\n| News | Neutral |"
        report = SentimentReport(
            overall_band=SentimentBand.MILDLY_BULLISH,
            overall_score=6.0,
            confidence="medium",
            narrative=narrative,
        )
        assert narrative in render_sentiment_report(report)

    def test_all_six_bands_render(self):
        for band in SentimentBand:
            report = SentimentReport(
                overall_band=band, overall_score=5.0,
                confidence="medium", narrative="n",
            )
            assert band.value in render_sentiment_report(report)

    def test_score_out_of_range_rejected(self):
        with pytest.raises(ValidationError):
            SentimentReport(
                overall_band=SentimentBand.BULLISH, overall_score=11.0,
                confidence="high", narrative="n",
            )


def _make_sentiment_state():
    return {
        "company_of_interest": "NVDA",
        "trade_date": "2026-01-15",
        "asset_type": "stock",
        "messages": [],
    }


def _structured_sentiment_llm(captured: dict, report: SentimentReport | None = None):
    """MagicMock LLM whose structured binding captures the prompt and returns
    a real SentimentReport so render_sentiment_report works."""
    if report is None:
        report = SentimentReport(
            overall_band=SentimentBand.BULLISH, overall_score=7.5,
            confidence="high",
            narrative="StockTwits 75% bullish. News constructive. Reddit upbeat.",
        )
    structured = MagicMock()
    structured.invoke.side_effect = lambda prompt: (
        captured.__setitem__("prompt", prompt) or report
    )
    llm = MagicMock()
    llm.with_structured_output.return_value = structured
    return llm


@pytest.mark.unit
class TestSentimentAnalystAgent:
    def test_structured_path_produces_rendered_markdown(self):
        captured = {}
        report = SentimentReport(
            overall_band=SentimentBand.MILDLY_BEARISH, overall_score=4.0,
            confidence="medium", narrative="Mixed signals across sources.",
        )
        analyst = create_sentiment_analyst(_structured_sentiment_llm(captured, report))
        sr = analyst(_make_sentiment_state())["sentiment_report"]
        assert "**Overall Sentiment:** **Mildly Bearish**" in sr
        assert "(Score: 4.0/10)" in sr
        assert "Mixed signals across sources." in sr

    def test_sentiment_report_also_in_messages(self):
        captured = {}
        analyst = create_sentiment_analyst(_structured_sentiment_llm(captured))
        result = analyst(_make_sentiment_state())
        assert len(result["messages"]) == 1
        assert result["sentiment_report"] == result["messages"][0].content

    def test_market_signal_embedded_provenance_uses_actual_fallback_source(self):
        config_module._config = copy.deepcopy(default_config.DEFAULT_CONFIG)
        set_config({"provenance_appendix": True})
        captured = {}
        spec = SentimentSignal(
            tag="test_signal",
            fetch=lambda *_args: "",
            evidence="registry evidence",
            source="configured primary",
            title="Test signal",
            intro="Test source.",
            effective=lambda date: date,
            timing="publication-date filtered",
        )
        body = attach_provenance(
            "FALLBACK_DATA",
            ProvenanceRecord(
                evidence="registry evidence",
                source="actual fallback",
                requested="2026-01-15",
                effective="2026-01-15",
                timing="fallback source used",
            ),
        )
        fetched = (FetchedSentimentSignal(spec=spec, body=body),)
        state = {**_make_sentiment_state(), "company_of_interest": "600519.SS"}
        try:
            with mock.patch(f"{_SENTIMENT_MOD}.get_news") as news, mock.patch(
                f"{_SENTIMENT_MOD}.fetch_sentiment_signals", return_value=fetched
            ):
                news.func.return_value = "NEWS_DATA"
                result = create_sentiment_analyst(_structured_sentiment_llm(captured))(state)
        finally:
            config_module._config = copy.deepcopy(default_config.DEFAULT_CONFIG)

        report = result["sentiment_report"]
        assert "| registry evidence | actual fallback |" in report
        assert "| registry evidence | configured primary |" not in report

    def test_prompt_contains_ticker(self):
        captured = {}
        create_sentiment_analyst(_structured_sentiment_llm(captured))(_make_sentiment_state())
        assert any("NVDA" in str(m) for m in captured["prompt"])

    def test_chinese_language_contract_precedes_japanese_source_data(self):
        set_config({"output_language": "Chinese"})
        captured = {}
        with mock.patch(f"{_SENTIMENT_MOD}.get_news") as news:
            news.func.return_value = "日立製作所が適時開示を発表"
            create_sentiment_analyst(_structured_sentiment_llm(captured))(
                _make_sentiment_state()
            )

        prompt_text = "\n".join(str(message) for message in captured["prompt"])
        contract_start = prompt_text.index("## Mandatory output-language contract")
        source_start = prompt_text.index("<start_of_news>")
        assert contract_start < source_start
        assert "prose in the `narrative` field in Chinese" in prompt_text
        assert "Do not imitate or switch to a source language" in prompt_text
        assert "日立製作所が適時開示を発表" in prompt_text
        assert "required English" in prompt_text
        assert "enum values" in prompt_text
        assert "entire response in Chinese" not in prompt_text

    def test_english_language_contract_is_explicit(self):
        set_config({"output_language": "English"})
        captured = {}
        create_sentiment_analyst(_structured_sentiment_llm(captured))(
            _make_sentiment_state()
        )

        prompt_text = "\n".join(str(message) for message in captured["prompt"])
        assert (
            "Write all explanatory prose, including the narrative, in English."
            in prompt_text
        )
        assert "prose in the `narrative` field in English" in prompt_text
        assert "fixed report headings" in prompt_text

    def test_falls_back_to_freetext_when_structured_unavailable(self):
        config_module._config = copy.deepcopy(default_config.DEFAULT_CONFIG)
        plain = "**Overall Sentiment:** **Bearish** (Score: 3.0/10)\n**Confidence:** Low\n\nLimited data."
        llm = MagicMock()
        llm.with_structured_output.side_effect = NotImplementedError("provider unsupported")
        llm.invoke.return_value = MagicMock(content=plain)
        result = create_sentiment_analyst(llm)(_make_sentiment_state())["sentiment_report"]
        assert result.startswith(plain)
        assert "## Data Provenance" not in result

    def test_falls_back_to_freetext_when_structured_call_fails(self):
        config_module._config = copy.deepcopy(default_config.DEFAULT_CONFIG)
        plain = "Fallback free-text sentiment."
        structured = MagicMock()
        structured.invoke.side_effect = ValueError("bad JSON from model")
        llm = MagicMock()
        llm.with_structured_output.return_value = structured
        llm.invoke.return_value = MagicMock(content=plain)
        result = create_sentiment_analyst(llm)(_make_sentiment_state())["sentiment_report"]
        assert result.startswith(plain)
        assert "## Data Provenance" not in result


_SENTIMENT_MOD = "tradingagents.agents.analysts.sentiment_analyst"
_SIGNALS_MOD = "tradingagents.dataflows.market_signals"


@pytest.mark.unit
class TestSentimentMarketGating:
    """The sentiment node skips US-only social fetchers for routed markets and
    never lets a news-fetch error escape (both new in the EDINET/JP change)."""

    def teardown_method(self):
        config_module._config = copy.deepcopy(default_config.DEFAULT_CONFIG)

    def _run(
        self,
        ticker,
        routes=None,
        news_side_effect=None,
        live=True,
        holdings_value="LARGE_HOLDINGS_DATA",
        llm=None,
    ):
        captured = {}
        config_module._config = copy.deepcopy(default_config.DEFAULT_CONFIG)
        set_config({"provenance_appendix": True})
        if routes:
            set_config({"data_vendors_by_market": routes})
        with mock.patch(f"{_SENTIMENT_MOD}.fetch_stocktwits_messages") as st, \
                mock.patch(f"{_SENTIMENT_MOD}.fetch_reddit_posts") as rd, \
                mock.patch(f"{_SIGNALS_MOD}.get_large_holdings") as holdings, \
                mock.patch(f"{_SIGNALS_MOD}.get_margin_balance") as margin, \
                mock.patch(f"{_SIGNALS_MOD}.get_short_positions") as shorts, \
                mock.patch(f"{_SIGNALS_MOD}.get_analyst_ratings_block") as ratings, \
                mock.patch(f"{_SENTIMENT_MOD}.get_news") as news, \
                mock.patch(f"{_SENTIMENT_MOD}.is_live", return_value=live):
            st.return_value = "STOCKTWITS_DATA"
            rd.return_value = "REDDIT_DATA"
            holdings.return_value = holdings_value
            margin.return_value = "MARGIN_BALANCE_DATA"
            shorts.return_value = "SHORT_POSITION_DATA"
            ratings.return_value = "ANALYST_RATINGS_DATA"
            if news_side_effect is not None:
                news.func.side_effect = news_side_effect
            else:
                news.func.return_value = "NEWS_DATA"
            captured["news_mock"] = news
            state = {**_make_sentiment_state(), "company_of_interest": ticker}
            result = create_sentiment_analyst(
                llm or _structured_sentiment_llm(captured)
            )(state)
        return captured, st, rd, holdings, margin, shorts, ratings, result

    def test_us_ticker_calls_social_fetchers(self):
        _c, st, rd, holdings, margin, shorts, ratings, _ = self._run("NVDA")
        st.assert_called_once()
        rd.assert_called_once()
        holdings.assert_not_called()  # large-holding signal is for routed markets only
        margin.assert_not_called()  # margin-balance signal is for routed markets only
        shorts.assert_not_called()  # short-position signal is for routed markets only
        ratings.assert_not_called()  # analyst-rating overlay is for routed markets only

    def test_ticker_news_uses_14_days_while_social_stays_at_7(self):
        captured, st, rd, *_ = self._run("NVDA")
        captured["news_mock"].func.assert_called_once_with(
            "NVDA", "2026-01-01", "2026-01-15"
        )
        st.assert_called_once_with(
            "NVDA",
            limit=30,
            start_date="2026-01-08",
            end_date="2026-01-15",
        )
        rd.assert_called_once_with(
            "NVDA",
            start_date="2026-01-08",
            end_date="2026-01-15",
        )
        prompt_text = "".join(str(message) for message in captured["prompt"])
        assert "requested window 2026-01-01 to 2026-01-15" in prompt_text
        assert "(2026-01-08 to 2026-01-15)" in prompt_text

    def test_routed_market_skips_social_and_injects_per_name_signals(self):
        captured, st, rd, holdings, margin, shorts, ratings, result = self._run(
            "9984.T", routes={".T": {"news_data": "edinet_news"}}
        )
        st.assert_not_called()
        rd.assert_not_called()
        holdings.assert_called_once()
        margin.assert_called_once()
        shorts.assert_called_once()
        ratings.assert_called_once()
        prompt_text = "".join(str(m) for m in captured["prompt"])
        assert "unavailable: no coverage for this market" in prompt_text
        assert "INVESTOR_FLOWS_DATA" not in prompt_text
        assert "Market-wide investor flows" not in prompt_text
        assert "LARGE_HOLDINGS_DATA" in prompt_text
        assert "MARGIN_BALANCE_DATA" in prompt_text
        assert "SHORT_POSITION_DATA" in prompt_text
        assert "ANALYST_RATINGS_DATA" in prompt_text
        assert "Routed ticker news" in prompt_text
        assert "News headlines — Yahoo Finance" not in prompt_text
        assert "[direct]` has explicit ticker or full-name evidence" in prompt_text
        assert "[candidate]` has an ambiguous ticker/name" in prompt_text
        assert "ticker-endpoint provenance alone is not evidence" in prompt_text
        assert "[context]` is" in prompt_text
        report = result["sentiment_report"]
        assert "## Data Provenance" in report
        assert "| ownership and control filings | EDINET |" in report
        assert "| margin balances | J-Quants |" in report
        assert "| large short positions | J-Quants |" in report
        assert "| analyst consensus | yfinance |" in report
        assert "market context only" not in report

    def test_missing_edinet_identity_is_reported_as_not_queried(self):
        *_rest, result = self._run(
            "9984.T",
            routes={".T": {"news_data": "edinet_news"}},
            holdings_value=(
                "<no EDINET code on file for 9984.T; "
                "large-shareholding lookup skipped>"
            ),
        )
        report = result["sentiment_report"]
        row = next(
            line
            for line in report.splitlines()
            if "| ownership and control filings |" in line
        )
        assert "| — | not queried; identifier unavailable |" in row

    def test_non_tokyo_routed_market_does_not_claim_jp_sources(self):
        _captured, _st, _rd, holdings, margin, shorts, ratings, result = self._run(
            "0700.HK",
            routes={".HK": {"news_data": "yfinance"}},
        )
        holdings.assert_not_called()
        margin.assert_not_called()
        shorts.assert_not_called()
        ratings.assert_not_called()
        report = result["sentiment_report"]
        assert "| EDINET |" not in report
        assert "| J-Quants |" not in report
        assert "| analyst consensus | yfinance |" not in report

    def test_default_a_share_route_skips_us_social_without_jp_signals(self):
        _captured, st, rd, holdings, margin, shorts, ratings, result = self._run(
            "600519.SS"
        )
        st.assert_not_called()
        rd.assert_not_called()
        holdings.assert_not_called()
        margin.assert_not_called()
        shorts.assert_not_called()
        ratings.assert_not_called()
        report = result["sentiment_report"]
        assert "| EDINET |" not in report
        assert "| J-Quants |" not in report

    def test_historical_us_run_skips_live_social_fetchers(self):
        captured, st, rd, *_rest, result = self._run("NVDA", live=False)
        st.assert_not_called()
        rd.assert_not_called()
        assert "live-only source unavailable for historical trade_date" in str(captured)
        assert result["sentiment_report"].count(
            "unavailable for historical date; vendor not queried"
        ) == 2

    def test_social_retrieval_times_are_captured_before_llm_completion(self):
        clock = MagicMock()
        first = MagicMock()
        first.isoformat.return_value = "2026-01-15T01:00:00+00:00"
        second = MagicMock()
        second.isoformat.return_value = "2026-01-15T01:00:01+00:00"
        clock.now.side_effect = [first, second]

        structured = MagicMock()

        def invoke(_prompt):
            assert clock.now.call_count == 2
            return SentimentReport(
                overall_band=SentimentBand.NEUTRAL,
                overall_score=5.0,
                confidence="medium",
                narrative="Mixed.",
            )

        structured.invoke.side_effect = invoke
        llm = MagicMock()
        llm.with_structured_output.return_value = structured
        with mock.patch(f"{_SENTIMENT_MOD}.datetime", clock):
            *_rest, result = self._run("NVDA", llm=llm)
        report = result["sentiment_report"]
        assert "retrieved 2026-01-15T01:00:00+00:00" in report
        assert "retrieved 2026-01-15T01:00:01+00:00" in report

    def test_news_fetch_error_degrades_instead_of_crashing(self):
        captured, *_rest, result = self._run(
            "9984.T",
            routes={".T": {"news_data": "edinet_news"}},
            news_side_effect=VendorNotConfiguredError("EDINET_API_KEY unset"),
        )
        # Node returns a report rather than propagating the vendor error.
        assert result["sentiment_report"]
        assert "news unavailable" in "".join(str(m) for m in captured["prompt"])
