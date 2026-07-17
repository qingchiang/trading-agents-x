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
the sentiment header (band + score + confidence) is deterministic across
runs and providers instead of free-form per-model prose.

See: https://github.com/TauricResearch/TradingAgents/issues/557
See: https://github.com/TauricResearch/TradingAgents/issues/796
"""

import logging
from datetime import datetime, timezone

from langchain_core.messages import AIMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from tradingagents.agents.schemas import SentimentReport, render_sentiment_report
from tradingagents.agents.utils.agent_utils import (
    get_instrument_context_from_state,
    get_language_instruction,
    get_news,
)
from tradingagents.agents.utils.structured import (
    bind_structured,
    invoke_structured_or_freetext,
)
from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.jp.edinet_holdings import get_large_holdings
from tradingagents.dataflows.jp.jquants_sentiment import (
    get_margin_balance,
    get_short_positions,
)
from tradingagents.dataflows.jp.market import is_tokyo_ticker
from tradingagents.dataflows.jp.yfinance_sentiment import get_analyst_ratings_block
from tradingagents.dataflows.lookahead import is_live, lookback_start_date
from tradingagents.dataflows.market_context import market_suffix_of
from tradingagents.dataflows.reddit import fetch_reddit_posts
from tradingagents.dataflows.stocktwits import fetch_stocktwits_messages
from tradingagents.provenance import (
    ProvenanceRecord,
    append_provenance_appendix,
    extract_provenance,
)

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
        live_run = is_live(end_date)
        stocktwits_retrieved_at = None
        reddit_retrieved_at = None
        ratings_retrieved_at = None

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
        # rule 6 then lowers confidence rather than reading noise as signal.
        # Per-name official positioning signals replace the unavailable social
        # sources. Exchange-section investor flows belong to the News Analyst's
        # market context and must never appear as ticker sentiment here.
        if market_suffix_of(ticker):
            placeholder = "<unavailable: no coverage for this market>"
            stocktwits_block = placeholder
            reddit_block = placeholder
            if is_tokyo_ticker(ticker):
                ratings_block = get_analyst_ratings_block(ticker, end_date)
                ratings_retrieved_at = (
                    datetime.now(timezone.utc).isoformat(timespec="seconds")
                    if ratings_block
                    else None
                )
                # Optional Tokyo-specific signals keyed by _OPTIONAL_SECTIONS tag.
                optional_blocks = {
                    "large_holdings": get_large_holdings(ticker, end_date),
                    "margin_balances": get_margin_balance(ticker, end_date),
                    "short_positions": get_short_positions(ticker, end_date),
                    "analyst_ratings": ratings_block,
                }
            else:
                optional_blocks = {}
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
                    f"<live-only source unavailable for historical trade_date {end_date}>"
                )
                stocktwits_block = historical
                reddit_block = historical
            optional_blocks = {}

        system_message = _build_system_message(
            ticker=ticker,
            news_start_date=news_start_date,
            social_start_date=social_start_date,
            end_date=end_date,
            news_block=news_block,
            stocktwits_block=stocktwits_block,
            reddit_block=reddit_block,
            optional_blocks=optional_blocks,
        )

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants."
                    " If you or any other assistant has the FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** or deliverable,"
                    " prefix your response with FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** so the team knows to stop."
                    " Today's date is {current_date}; treat it as 'now' for all analysis and tool-call date ranges. {instrument_context}"
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

        report_text = invoke_structured_or_freetext(
            structured_llm,
            llm,
            formatted_messages,
            render_sentiment_report,
            "Sentiment Analyst",
        )

        records = extract_provenance(news_block)
        if not records:
            records.append(
                ProvenanceRecord(
                    evidence="routed ticker news",
                    source="unknown",
                    requested=f"{news_start_date} to {end_date}",
                    effective="unknown",
                    timing=(
                        "unavailable"
                        if "unavailable" in news_block.lower()
                        else "no auditable source metadata captured"
                    ),
                )
            )

        market_suffix = market_suffix_of(ticker)

        def social_status(
            body: str, retrieved_at: str | None
        ) -> tuple[str, str, str | None]:
            if market_suffix:
                return "—", "unavailable: no coverage for this market", None
            if not live_run:
                return "—", "unavailable for historical date; vendor not queried", None
            lowered = body.casefold()
            if "unavailable" in lowered:
                return "—", "retrieval unavailable", retrieved_at
            if lowered.startswith("<no "):
                return (
                    f"{social_start_date} to {end_date}",
                    "available; no messages in current public-feed window",
                    retrieved_at,
                )
            return (
                f"{social_start_date} to {end_date}",
                "live source; market-calendar window filtered",
                retrieved_at,
            )

        stocktwits_effective, stocktwits_timing, stocktwits_retrieved = social_status(
            stocktwits_block, stocktwits_retrieved_at
        )
        reddit_effective, reddit_timing, reddit_retrieved = social_status(
            reddit_block, reddit_retrieved_at
        )
        records.extend(
            (
                ProvenanceRecord(
                    evidence="retail social messages",
                    source="StockTwits",
                    requested=f"{social_start_date} to {end_date}",
                    effective=stocktwits_effective,
                    timing=stocktwits_timing,
                    retrieved_at=stocktwits_retrieved,
                ),
                ProvenanceRecord(
                    evidence="community discussion",
                    source="Reddit public feeds",
                    requested=f"{social_start_date} to {end_date}",
                    effective=reddit_effective,
                    timing=reddit_timing,
                    retrieved_at=reddit_retrieved,
                ),
            )
        )
        if optional_blocks:
            holdings_start = lookback_start_date(end_date, 89)
            shorts_start = lookback_start_date(end_date, 365)
            optional_specs = (
                ("ownership and control filings", "EDINET", "large_holdings", f"{holdings_start} to {end_date}", "disclosure-date filtered"),
                ("margin balances", "J-Quants", "margin_balances", f"published weeks <= {end_date}", "publication-date filtered"),
                ("large short positions", "J-Quants", "short_positions", f"{shorts_start} to {end_date}", "disclosure-date filtered"),
            )
            for evidence, source, key, effective, timing in optional_specs:
                body = optional_blocks.get(key, "")
                lowered = body.casefold()
                if "unavailable" in lowered:
                    record_timing = "unavailable"
                    record_effective = "—"
                elif "skipped" in lowered or "no edinet code" in lowered:
                    record_timing = "not queried; identifier unavailable"
                    record_effective = "—"
                elif body:
                    record_timing = timing
                    record_effective = effective
                else:
                    record_timing = "available; no qualifying records"
                    record_effective = effective
                records.append(
                    ProvenanceRecord(
                        evidence=evidence,
                        source=source,
                        requested=end_date,
                        effective=record_effective,
                        timing=record_timing,
                    )
                )
            ratings = optional_blocks.get("analyst_ratings", "")
            records.append(
                ProvenanceRecord(
                    evidence="analyst consensus",
                    source="yfinance",
                    requested=end_date,
                    effective="retrieval-time snapshot" if ratings else "—",
                    timing=(
                        "live non-point-in-time"
                        if ratings
                        else (
                            "unavailable for historical date; vendor not queried"
                            if not live_run
                            else "no analyst snapshot returned; retrieval success unknown"
                        )
                    ),
                    retrieved_at=ratings_retrieved_at if ratings else None,
                )
            )

        report_text = append_provenance_appendix(
            report_text,
            records,
            requested_date=end_date,
            enabled=config["provenance_appendix"],
        )

        return {
            "messages": [AIMessage(content=report_text)],
            "sentiment_report": report_text,
        }

    return sentiment_analyst_node


# Each intro owns its block's interpretation (rendered directly above the data);
# prompt rule 7 stays block-agnostic so a new signal is one intro, not two edits.
_HOLDINGS_INTRO = (
    "Per-name EDINET filings about the company, of two kinds — read each row's label.\n"
    "大量保有 (5%+ stakes): an investor crossing/adjusting a 5% stake; the row shows the\n"
    "filer and report type, not the exact %, so read frequency and who is filing — a\n"
    "cluster of new 5%+ reports suggests institutional accumulation (mildly bullish).\n"
    "公開買付 (TOB / tender offer): a takeover event that dominates routine accumulation —\n"
    "a launch is a premium bid (strongly bullish for the target), a withdrawal cancels it\n"
    "(bearish), a result concludes it, and a target-board opinion signals support or\n"
    "opposition. Weigh a takeover by its label, not as a 5% stake."
)
_MARGIN_INTRO = (
    "Per-name weekly margin-trading balances (信用取引): 信用買残 are shares bought on\n"
    "margin (latent future selling), 信用売残 shares sold short on margin. A rising\n"
    "credit ratio (買残/売残) means growing long overhang — a contrarian/bearish tilt,\n"
    "a falling one is supportive. Read the trend across weeks, not a single week."
)
_SHORT_INTRO = (
    "Per-name disclosed large short positions (空売り残高報告, ≥0.5% of shares out),\n"
    "each naming the short seller. New or rising positions are professional bearish\n"
    "positioning; falling/covered ones are bullish. Weigh by how large and how many."
)
_RATINGS_INTRO = (
    "Per-name sell-side view: the analyst-consensus rating (its mean is a 1–5 scale\n"
    "where 1 is most bullish) and the 12-month price-target implied upside. A\n"
    "professional-opinion signal, distinct from the flow/accumulation blocks, which are\n"
    "positioning. LIVE snapshot — present only on live runs, absent in backtests."
)


def _optional_section(title: str, intro: str, tag: str, body: str) -> str:
    """Render an optional ``### title / intro / <start_of_tag>…<end_of_tag>`` block.

    Returns "" when ``body`` is empty, so a market lacking the signal (e.g. US)
    leaves the prompt byte-for-byte unchanged.
    """
    if not body:
        return ""
    return f"\n### {title}\n{intro}\n\n<start_of_{tag}>\n{body}\n<end_of_{tag}>\n"


# Optional market-specific signal sections, in render order: (title, intro, tag).
# The caller passes an ``optional_blocks`` mapping keyed by tag; an absent/empty
# block omits its section, so the US prompt is unchanged. Adding another market's
# signal is one row here plus one entry at the call site — no concat to forget.
_OPTIONAL_SECTIONS = (
    ("Ownership & control — official 大量保有 / 公開買付 (TOB)", _HOLDINGS_INTRO, "large_holdings"),
    ("Margin-trading balances — official weekly 信用取引", _MARGIN_INTRO, "margin_balances"),
    ("Short-position disclosures — official 空売り残高報告", _SHORT_INTRO, "short_positions"),
    ("Analyst consensus — sell-side rating & price target", _RATINGS_INTRO, "analyst_ratings"),
)


def _build_system_message(
    *,
    ticker: str,
    news_start_date: str,
    social_start_date: str,
    end_date: str,
    news_block: str,
    stocktwits_block: str,
    reddit_block: str,
    optional_blocks: dict | None = None,
) -> str:
    """Assemble the sentiment-analyst system message with structured data blocks.

    ``optional_blocks`` maps an ``_OPTIONAL_SECTIONS`` tag (e.g.
    ``large_holdings``, ``analyst_ratings``) to its rendered body — optional
    Tokyo-market signals. A missing or empty block omits its section entirely,
    leaving the US prompt unchanged.
    """
    optional_blocks = optional_blocks or {}
    optional_sections = "".join(
        _optional_section(title, intro, tag, optional_blocks.get(tag, ""))
        for title, intro, tag in _OPTIONAL_SECTIONS
    )
    return f"""You are a financial market sentiment analyst. Your task is to produce a comprehensive sentiment report for {ticker} ending on {end_date}, drawing on the complementary data sources and source-specific windows that have already been collected for you.

## Data sources (pre-fetched, in this prompt)

### Routed ticker news — requested window {news_start_date} to {end_date}
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

### StockTwits messages — retail-trader social platform indexed by cashtag ({social_start_date} to {end_date})
Fast-moving signal. Each message carries a user-labeled sentiment tag (Bullish / Bearish / no-label) plus the message body.

<start_of_stocktwits>
{stocktwits_block}
<end_of_stocktwits>

### Reddit posts — r/wallstreetbets, r/stocks, r/investing ({social_start_date} to {end_date})
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

6. **Be honest about data limits, and never invent data for an unavailable source.** If a block contains an "<unavailable>" / "<no ...>" placeholder (e.g. StockTwits and Reddit have no coverage outside US markets), treat that source as absent: do NOT infer a Bullish/Bearish ratio, divergence, or engagement from it — rules 1–3 simply do not apply to it. Lean on the sources that ARE present and lower the `confidence` field accordingly, stating which sources were missing.

7. **When per-name official exchange/disclosure blocks are present, treat them as the primary sentiment signal.** Margin balances, short disclosures, ownership/control filings, and analyst ratings refer to this company; broad exchange-section flows are deliberately excluded because they are not ticker order flow. Weight the per-name blocks above any thin or placeholder social block, and read each one exactly as the one-line note printed directly above its data explains. These are positioning and professional opinion, not retail chatter — do not force the StockTwits/Reddit Bullish/Bearish-ratio framing (rules 1–3) onto them. A live-snapshot block (analyst consensus) is often absent in backtests; that absence is normal and not itself bearish.

8. **Identify catalysts and risks** that emerge across sources — news of upcoming earnings, product launches, competitive threats, macro headlines, etc.

9. **Past sentiment is not predictive.** Frame your conclusions as signal for the trader to weigh alongside fundamentals and technicals, not as a price call.

10. **Preserve source and date boundaries.** Keep supplied source/window labels for exact claims. Do not create a data-provenance appendix yourself; the workflow may append one in audit mode.

## Output fields

Fill the following fields:

- **overall_band**: Exactly one of Bullish / Mildly Bullish / Neutral / Mixed / Mildly Bearish / Bearish. Use Mixed when sources point in clearly different directions; Neutral only when all sources are genuinely silent.
- **overall_score**: A number from 0 (maximally bearish) to 10 (maximally bullish); 5 is neutral. Keep it consistent with overall_band.
- **confidence**: low / medium / high, based on data quality and sample size.
- **narrative**: Full source-by-source breakdown, divergences, dominant narrative themes, catalysts and risks, and a markdown summary table of key sentiment signals (direction, source, supporting evidence).

{get_language_instruction()}"""


# ---------------------------------------------------------------------------
# Backwards-compatibility shim
# ---------------------------------------------------------------------------
def create_social_media_analyst(llm):
    """Deprecated alias for :func:`create_sentiment_analyst`.

    Kept so existing code that imports ``create_social_media_analyst``
    continues to work.

    .. deprecated::
        Import :func:`create_sentiment_analyst` directly instead.
    """
    import warnings
    warnings.warn(
        "create_social_media_analyst is deprecated and will be removed in a "
        "future version. Use create_sentiment_analyst instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return create_sentiment_analyst(llm)
