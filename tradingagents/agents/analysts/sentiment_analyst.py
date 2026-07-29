"""Sentiment analyst — multi-source sentiment analysis for a target ticker.

Previously named ``social_media_analyst``. Renamed and redesigned because
the old version had a prompt that demanded social-media analysis but the
only tool available was Yahoo Finance news — which led LLMs to fabricate
Reddit/X/StockTwits content under prompt pressure (verified live).

The redesigned agent pre-fetches complementary data sources before the LLM
is invoked and injects them into the prompt as structured blocks:

  1. News headlines     — routed by ticker (Yahoo Finance; EDINET for .T)
  2. StockTwits messages — retail-trader posts indexed by cashtag, with
                           user-labeled Bullish/Bearish sentiment tags
  3. Reddit posts        — r/wallstreetbets, r/stocks, r/investing

StockTwits and Reddit are US-retail platforms, so routed non-US markets receive
clear unavailable placeholders plus any supported per-name official signals.
Exchange-section investor flows are deliberately excluded: they belong to the
News Analyst as regional context and cannot be attributed to a target ticker.

The agent does not use tool-calling; the data is in the prompt from
turn 0. Output uses the structured-output pattern (json_schema for
OpenAI/xAI, response_schema for Gemini, tool-use for Anthropic), falling
back to free-text generation for providers that lack native support, so
the report structure is stable and confidence is calculated locally from
source coverage instead of being self-rated by the model.

See: https://github.com/TauricResearch/TradingAgents/issues/557
See: https://github.com/TauricResearch/TradingAgents/issues/796
"""

import logging
from datetime import datetime, timezone

from langchain_core.messages import AIMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from tradingagents.agents.schemas import (
    SentimentReport,
    render_sentiment_report,
    validate_sentiment_sources,
)
from tradingagents.agents.sentiment_sources import (
    SentimentSourceInput,
    prepare_sentiment_sources,
    sentiment_confidence,
)
from tradingagents.agents.utils.agent_utils import (
    get_instrument_context_from_state,
    get_language_instruction,
    get_news,
)
from tradingagents.agents.utils.structured import (
    NO_EXTERNAL_TOOLS,
    bind_structured,
    invoke_structured_or_freetext,
    structured_prompt_for,
)
from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.lookahead import is_near_live, lookback_start_date
from tradingagents.dataflows.market_context import market_suffix_of
from tradingagents.dataflows.market_signals import (
    FetchedSentimentSignal,
    fetch_sentiment_signals,
)
from tradingagents.dataflows.reddit import fetch_reddit_posts
from tradingagents.dataflows.stocktwits import fetch_stocktwits_messages

logger = logging.getLogger(__name__)


def create_sentiment_analyst(llm):
    """Create a sentiment analyst node for the trading graph.

    Pre-fetches news + StockTwits + Reddit data, injects them into the
    prompt as structured blocks, and produces a deterministic sentiment
    report via structured output (with a free-text fallback for providers
    that do not support it).
    """
    structured_llm = bind_structured(llm, SentimentReport, "Sentiment Analyst")

    def sentiment_analyst_node(state):
        ticker = state["company_of_interest"]
        end_date = state["trade_date"]
        config = get_config()
        news_start_date = lookback_start_date(
            end_date,
            config["ticker_news_lookback_days"],
        )
        social_start_date = lookback_start_date(
            end_date,
            config["social_lookback_days"],
        )
        instrument_context = get_instrument_context_from_state(state)
        live_run = is_near_live(end_date, ticker)
        stocktwits_retrieved_at = None
        reddit_retrieved_at = None
        fetched_market_signals = ()

        # Pre-fetch all three sources. Each must degrade to a string so the LLM
        # always sees something — either real data or a clear placeholder — and
        # no exception escapes this node. News auto-routes by ticker suffix
        # (e.g. EDINET for .T); unlike the two social fetchers, get_news goes
        # through route_to_vendor, which re-raises for a misconfigured/unset
        # vendor (news_data isn't optional), so we catch and degrade here.
        try:
            news_block = get_news.func(ticker, news_start_date, end_date)
        except Exception as exc:
            logger.warning("News fetch failed for %s: %s", ticker, exc)
            news_block = f"<news unavailable: {type(exc).__name__}>"
        # StockTwits and Reddit are US-retail platforms with no coverage of
        # other markets, so for a routed market (e.g. .T, future .SS) skip the
        # pointless network calls and hand the LLM a clear placeholder — prompt
        # rule 6 then excludes them rather than reading noise as signal.
        # Per-name official positioning signals replace the unavailable social
        # sources. Exchange-section investor flows belong to the News Analyst's
        # market context and must never appear as ticker sentiment here.
        if market_suffix_of(ticker):
            placeholder = "<unavailable: no coverage for this market>"
            stocktwits_block = placeholder
            reddit_block = placeholder
            fetched_market_signals = fetch_sentiment_signals(ticker, end_date)
        else:
            if live_run:
                stocktwits_block = fetch_stocktwits_messages(
                    ticker,
                    limit=30,
                    start_date=social_start_date,
                    end_date=end_date,
                )
                stocktwits_retrieved_at = datetime.now(timezone.utc).isoformat(
                    timespec="seconds"
                )
                reddit_block = fetch_reddit_posts(
                    ticker,
                    start_date=social_start_date,
                    end_date=end_date,
                )
                reddit_retrieved_at = datetime.now(timezone.utc).isoformat(
                    timespec="seconds"
                )
            else:
                historical = (
                    "<live-only source unavailable for historical or future "
                    f"trade_date {end_date}>"
                )
                stocktwits_block = historical
                reddit_block = historical

        sentiment_sources, prefetched_evidence = prepare_sentiment_sources(
            ticker=ticker,
            end_date=end_date,
            news_start_date=news_start_date,
            social_start_date=social_start_date,
            live_run=live_run,
            news_block=news_block,
            stocktwits_block=stocktwits_block,
            reddit_block=reddit_block,
            stocktwits_retrieved_at=stocktwits_retrieved_at,
            reddit_retrieved_at=reddit_retrieved_at,
            market_signals=fetched_market_signals,
        )
        confidence = sentiment_confidence(sentiment_sources)
        system_message = _build_system_message(
            ticker=ticker,
            news_start_date=news_start_date,
            social_start_date=social_start_date,
            end_date=end_date,
            output_language=config["output_language"],
            news_block=news_block,
            stocktwits_block=stocktwits_block,
            reddit_block=reddit_block,
            market_signals=fetched_market_signals,
            sentiment_sources=sentiment_sources,
        )

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants."
                    # No tool-calling here: the data is pre-fetched into the
                    # prompt, so tool-range wording would only invite a
                    # hallucinated tool call (#1130).
                    " Today's date is {current_date}; treat it as 'now' for all analysis. {instrument_context}"
                    " " + NO_EXTERNAL_TOOLS +
                    "\n{system_message}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(current_date=end_date)
        prompt = prompt.partial(instrument_context=instrument_context)

        # Format the template into a concrete message list so the structured
        # and free-text paths receive the same input. No bind_tools — the
        # data is already in the prompt.
        formatted_messages = prompt.format_messages(messages=state["messages"])
        structured_messages = structured_prompt_for(
            llm,
            SentimentReport,
            formatted_messages,
        )

        applicable_sources = tuple(
            source for source in sentiment_sources if source.applicable
        )
        expected_statuses = {
            source.source_id: source.status
            for source in applicable_sources
        }
        source_labels = {
            source.source_id: source.label
            for source in applicable_sources
        }

        def render_structured(report: SentimentReport) -> str:
            validate_sentiment_sources(report, expected_statuses)
            return render_sentiment_report(
                report,
                confidence=confidence.level,
                confidence_score=confidence.score,
                source_labels=source_labels,
            )

        fallback_reason: str | None = None

        def record_fallback(reason: str) -> None:
            nonlocal fallback_reason
            fallback_reason = reason

        report_text = invoke_structured_or_freetext(
            structured_llm,
            llm,
            formatted_messages,
            render_structured,
            "Sentiment Analyst",
            structured_prompt=structured_messages,
            on_fallback=record_fallback,
        )

        return {
            "messages": [AIMessage(content=report_text)],
            "sentiment_report": report_text,
            "sentiment_confidence": confidence.score,
            "sentiment_output_warning": fallback_reason,
            "prefetched_evidence": prefetched_evidence,
        }

    return sentiment_analyst_node


def _optional_section(
    source_id: str,
    title: str,
    intro: str,
    tag: str,
    body: str,
) -> str:
    """Render an optional ``### title / intro / <start_of_tag>…<end_of_tag>`` block.

    Returns "" when ``body`` is empty, so a market lacking the signal (e.g. US)
    leaves the prompt byte-for-byte unchanged.
    """
    if not body:
        return ""
    return (
        f"\n### {title} — source_id `{source_id}`\n"
        f"{intro}\n\n<start_of_{tag}>\n{body}\n<end_of_{tag}>\n"
    )


def _build_system_message(
    *,
    ticker: str,
    news_start_date: str,
    social_start_date: str,
    end_date: str,
    output_language: str,
    news_block: str,
    stocktwits_block: str,
    reddit_block: str,
    market_signals: tuple[FetchedSentimentSignal, ...] = (),
    sentiment_sources: tuple[SentimentSourceInput, ...],
) -> str:
    """Assemble the sentiment-analyst system message with structured data blocks.

    Market-specific blocks carry their own presentation metadata from the same
    registry that fetched them. Empty bodies omit their section, leaving the US
    prompt unchanged.
    """
    optional_sections = "".join(
        _optional_section(
            f"signal.{result.spec.tag}",
            result.spec.title,
            result.spec.intro,
            result.spec.tag,
            result.body,
        )
        for result in market_signals
    )
    applicable_sources = tuple(
        source for source in sentiment_sources if source.applicable
    )
    excluded_sources = tuple(
        source for source in sentiment_sources if not source.applicable
    )
    source_contract = "\n".join(
        (
            "Return exactly one `source_assessments` item for each applicable "
            "source below. Copy both `source_id` and `status` exactly; the "
            "application validates omissions, duplicates, unknown IDs, and "
            "status changes.",
            *(
                f"- `{source.source_id}` — {source.label}; "
                f"status=`{source.status.value}`"
                for source in applicable_sources
            ),
            (
                "Do not return assessments for these non-applicable sources: "
                + ", ".join(
                    f"`{source.source_id}`" for source in excluded_sources
                )
                if excluded_sources
                else "There are no non-applicable sources in this run."
            ),
        )
    )
    # Unlike tool-calling analysts, Sentiment receives a large body of source
    # text in its initial prompt. Native-language JP disclosures can therefore
    # overpower a short language reminder placed after the data. Put an
    # explicit contract before every source block, including for English (for
    # which the shared helper intentionally returns an empty string).
    language_instruction = get_language_instruction(
        "all explanatory prose in every structured text field"
    ).strip()
    if not language_instruction:
        language_instruction = (
            "Write all explanatory prose in every structured text field "
            f"in {output_language}."
        )
    return f"""You are a financial market sentiment analyst. Your task is to produce a comprehensive sentiment report for {ticker} ending on {end_date}, drawing on the complementary data sources and source-specific windows that have already been collected for you.

## Mandatory output-language contract

{language_instruction}
Write all explanatory prose in structured text fields in {output_language},
regardless of the language used by EDINET, TDnet, news media, or any other
source material. Do not imitate or switch to a source language. Translate or
summarize foreign-language evidence into {output_language}, retaining only
proper names, tickers, source names, and necessary original-language terms.
Keep the structured field names, fixed report headings, and required English
enum values (such as Bullish / Bearish and substantive / no_signal /
unavailable) unchanged. The same rules apply if structured output is
unavailable and you must return a free-text report.

## Source assessment contract

{source_contract}

## Data sources (pre-fetched, in this prompt)

### Routed ticker news — source_id `news` — requested window {news_start_date} to {end_date}
The inner block header identifies the actual routed source(s). Fact-driven,
slower-moving signal; do not assume Yahoo Finance when another source is named.
`[direct]` has explicit ticker or full-name evidence and may be treated as a
company event. `[candidate]` has an ambiguous ticker/name or summary-only
mention: verify its concrete relationship from the supplied text and ignore it
when unclear; ticker-endpoint provenance alone is not evidence. `[context]` is
only an external driver or industry/market backdrop. Never rewrite candidate or
context material as an action taken by, or event confirmed for, {ticker}.

<start_of_news>
{news_block}
<end_of_news>

### StockTwits messages — source_id `stocktwits` — retail-trader social platform indexed by cashtag ({social_start_date} to {end_date})
Fast-moving signal. Each message carries a user-labeled sentiment tag (Bullish / Bearish / no-label) plus the message body.

<start_of_stocktwits>
{stocktwits_block}
<end_of_stocktwits>

### Reddit posts — source_id `reddit` — r/wallstreetbets, r/stocks, r/investing ({social_start_date} to {end_date})
Community discussion. Engagement signal via upvote score and comment count. Subreddit character matters (r/wallstreetbets is often contrarian/exuberant; r/stocks more measured; r/investing longer-term).

<start_of_reddit>
{reddit_block}
<end_of_reddit>
{optional_sections}
## How to analyze this data (best practices)

1. **Read the StockTwits Bullish/Bearish ratio as a leading retail-sentiment signal.** A 70/30 bullish/bearish split is moderately bullish; ≥90/10 may indicate over-extension and contrarian risk; 50/50 is uncertainty. Sample size matters — base rates on the actual message count, not percentages alone.

2. **Look for cross-source divergences.** If news framing is bearish but StockTwits is overwhelmingly bullish, that mismatch is itself a signal — it can mean retail is leaning into a thesis the news flow hasn't caught up to (or vice versa, that retail is chasing while institutions are cautious).

3. **Weight Reddit posts by engagement only when those metrics are present.** A 400-upvote / 200-comment thread reflects community attention; a 3-upvote post is noise. RSS results explicitly lack scores/comments, so never invent them. Read the body excerpts for context — the title alone often misleads.

4. **Distinguish opinion from event.** A news headline ("Nvidia announces $500M Corning deal") is an event; a StockTwits post ("buying NVDA, this is going to moon") is opinion. Both are inputs but should be weighted differently in your conclusions.

5. **Identify recurring narrative themes.** What topic keeps coming up across sources? That's the dominant narrative driving current sentiment.

6. **Be honest about data limits, and never invent data for an unavailable source.** If a block contains an "<unavailable>" / "<no ...>" placeholder (e.g. StockTwits and Reddit have no coverage outside US markets), do NOT infer a Bullish/Bearish ratio, divergence, engagement, direction, or key evidence from it — rules 1–3 simply do not apply. Preserve the application-supplied `status`, use `direction: null` and an empty `key_evidence`, and explain the limitation.

7. **When per-name official exchange/disclosure blocks are present, treat them as the primary sentiment signal.** Margin balances, short disclosures, ownership/control filings, and analyst ratings refer to this company; broad exchange-section flows are deliberately excluded because they are not ticker order flow. Weight the per-name blocks above any thin or placeholder social block, and read each one exactly as the one-line note printed directly above its data explains. These are positioning and professional opinion, not retail chatter — do not force the StockTwits/Reddit Bullish/Bearish-ratio framing (rules 1–3) onto them. A live-snapshot block (analyst consensus) is often absent in backtests; that absence is normal and not itself bearish.

8. **Identify catalysts and risks** that emerge across sources — news of upcoming earnings, product launches, competitive threats, macro headlines, etc.

9. **Past sentiment is not predictive.** Frame conclusions as evidence for the research committee to weigh alongside fundamentals and market data, not as a price call or account instruction.

10. **Preserve source and date boundaries.** Keep supplied source/window labels for exact claims. Do not create data-quality-warning or provenance sections; the workflow records source metadata separately.

## Output fields

Fill the following fields:

- **overall_band**: Exactly one of Bullish / Mildly Bullish / Neutral / Mixed / Mildly Bearish / Bearish. Use Mixed when sources point in clearly different directions; Neutral only when all sources are genuinely silent.
- **overall_score**: A number from 0 (maximally bearish) to 10 (maximally bullish); 5 is neutral. Keep it consistent with overall_band.
- **executive_summary**: A substantive synthesis of the overall direction, strongest evidence, and why conflicting signals matter.
- **source_assessments**: Exactly one item for every applicable source_id. A substantive source requires a direction and at least one concrete key-evidence statement. A no-signal or unavailable source must use null direction and no key evidence.
- **cross_source_consensus**: Concrete points on which independent sources agree; may be empty when only one source is substantive.
- **cross_source_divergences**: Material conflicts between sources; may be empty when none exist.
- **dominant_themes**: One or more recurring narratives supported by the supplied evidence.
- **catalysts**: Evidence-backed sentiment catalysts; may be empty when none are identified.
- **risks**: One or more risks or contrarian signals surfaced by the evidence.
- **limitations**: One or more coverage, timing, sample-size, or interpretation constraints.

Do not return a confidence field or a preformatted Markdown table. The application computes confidence from source coverage and quality, and renders the summary table locally.
"""
