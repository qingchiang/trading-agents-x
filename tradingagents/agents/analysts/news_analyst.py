from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from tradingagents.agents.utils.agent_states import (
    missing_evidence_blocks,
    prefetched_evidence_block,
)
from tradingagents.agents.utils.agent_utils import (
    get_instrument_context_from_state,
    get_language_instruction,
)
from tradingagents.agents.utils.information_frontier import (
    filter_evidence_content_at_information_frontier,
    information_frontier_from_state,
)
from tradingagents.agents.utils.macro_data_tools import (
    get_macro_indicators_for_analysis,
)
from tradingagents.agents.utils.news_data_tools import (
    EXTENDED_TICKER_NEWS_LOOKBACK_DAYS,
    get_global_news_for_analysis,
    get_news_for_analysis,
)
from tradingagents.agents.utils.prediction_markets_tools import (
    get_prediction_markets_for_analysis,
)
from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.jp.jquants_sentiment import get_market_investor_flows
from tradingagents.dataflows.jp.market import is_tokyo_ticker
from tradingagents.dataflows.lookahead import lookback_start_date
from tradingagents.dataflows.macro_panel import get_global_macro_panel
from tradingagents.provenance import (
    ProvenanceRecord,
    extract_provenance,
)


def create_news_analyst(llm):
    def news_analyst_node(state):
        current_date = state["trade_date"]
        information_frontier = information_frontier_from_state(state)
        ticker = state["company_of_interest"]
        instrument_context = get_instrument_context_from_state(state)
        ticker_news_lookback_days = get_config()["ticker_news_lookback_days"]
        ticker_news_start_date = lookback_start_date(
            current_date,
            ticker_news_lookback_days,
        )
        extended_news_lookback_days = max(
            ticker_news_lookback_days,
            EXTENDED_TICKER_NEWS_LOOKBACK_DAYS,
        )
        extended_news_start_date = lookback_start_date(
            current_date,
            extended_news_lookback_days,
        )

        tools = [
            get_news_for_analysis,
            get_global_news_for_analysis,
            get_macro_indicators_for_analysis,
            get_prediction_markets_for_analysis,
        ]

        # Cross-region macro backdrop is prefetched and injected (not left to the
        # LLM to tool-call): it's context every analysis needs and macro is
        # market-agnostic. get_macro_indicators stays available as a microscope
        # for drilling into a specific series beyond the panel. Never raises.
        macro_panel = get_global_macro_panel(current_date)
        macro_panel, _ = filter_evidence_content_at_information_frontier(
            macro_panel,
            information_frontier,
            fallback_source="global macro panel",
        )
        market_flow_context = (
            get_market_investor_flows(ticker, current_date)
            if is_tokyo_ticker(ticker)
            else ""
        )
        market_flow_context, _ = filter_evidence_content_at_information_frontier(
            market_flow_context,
            information_frontier,
            fallback_source="J-Quants investor-types",
        )
        market_flow_section = ""
        if market_flow_context:
            market_flow_section = (
                "\n\nA target-market capital-flow block is also prefetched below. "
                "It is aggregate exchange-section context, NOT company order flow. "
                f"Never claim that any investor category bought or sold {ticker} from "
                "this block, and do not use it as ticker-specific sentiment:\n\n"
                f"{market_flow_context}\n"
            )

        system_message = (
            f"You are a news researcher tasked with analyzing recent news, disclosures, and macro trends relevant to the instrument. Use the available tools: get_news(ticker, window) for instrument-specific news by ticker symbol, get_global_news(look_back_days, limit) for broader macroeconomic news, get_macro_indicators(indicator, look_back_days) to drill into a SPECIFIC macro series beyond the panel below (a US FRED alias such as 'yield_curve', 'initial_claims', 'retail_sales', 'm2', '2y_treasury', a raw FRED series ID like 'CPILFESL', a Japanese official series 'jp_cpi'/'jp_core_cpi'/'jp_policy_rate'/'jp_tankan', or a China series 'cn_lpr'/'cn_10y_yield'/'cn_cpi'/'cn_gdp'/'cn_unemployment'/'cn_pmi'/'usd_cny'), and get_prediction_markets(topic, limit) for current market-implied probabilities of forward-looking events (e.g. 'Fed rate cut', 'recession 2026', geopolitical or sector events). The workflow injects the immutable analysis date into every tool; do not attempt to supply or override any date argument. Always call get_news with window='recent' first; it covers {ticker_news_start_date} through {current_date} (configured lookback offset {ticker_news_lookback_days}, inclusive at both ends). Call window='extended' only when recent evidence is absent/insufficient or an unresolved earnings warning, M&A, regulatory, management, financing, or capital-allocation catalyst may predate that window. Extended covers {extended_news_start_date} through {current_date} ({extended_news_lookback_days + 1} calendar dates), includes the recent results, and must replace rather than duplicate them in the analysis. Never expand merely to increase article count. Prediction markets are live-only and return an explicit unavailable result for historical analysis. Provide specific evidence, uncertainty, catalysts, risks, and invalidation-relevant observations for the research committee. Do not provide account-level sizing, entry, stop, target, or execution instructions."
            "\n\nTicker-news relevance labels are evidence boundaries: `[direct]` has explicit ticker or full-name evidence and may be described as a company event. `[candidate]` contains an ambiguous ticker/name or summary-only mention; independently verify the concrete relationship from the supplied title/summary and ignore the item if that relationship is not clear. Never assume relevance merely because Yahoo returned an item for the ticker. `[context]` is only an external driver or industry/market backdrop. Never rewrite `[candidate]` or `[context]` as an action taken by, financing raised by, or event confirmed for the target company without explicit evidence in the item."
            " Preserve each material news item's supplied publisher and publication/disclosure date, and preserve source/observation dates for exact macro claims. Do not create data-quality-warning or provenance sections; the workflow records source metadata separately."
            "\n\nA cross-region macro panel has already been prefetched for you — use it as the macro backdrop (no tool call needed for these baseline indicators):\n\n"
            f"{macro_panel}\n"
            f"{market_flow_section}"
            + """ Make sure to append a Markdown table at the end of the report to organize key points in the report, organized and easy to read."""
            + get_language_instruction()
        )

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants."
                    " Use the provided tools to progress towards answering the question."
                    " If you are unable to fully answer, that's OK; another assistant with different tools"
                    " will help where you left off. Execute what you can to make progress."
                    " You have access to the following tools: {tool_names}."
                    " Today's date is {current_date}; treat it as 'now' for all analysis and tool-call date ranges. {instrument_context}\n"
                    "{system_message}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(tool_names=", ".join([tool.name for tool in tools]))
        prompt = prompt.partial(current_date=current_date)
        prompt = prompt.partial(instrument_context=instrument_context)

        chain = prompt | llm.bind_tools(tools)
        result = chain.invoke(state["messages"])

        report = ""
        prefetched_evidence = []

        if len(result.tool_calls) == 0:
            macro_records = extract_provenance(macro_panel)
            if not macro_records:
                macro_records.append(
                    ProvenanceRecord(
                        evidence="global macro panel",
                        source="unknown",
                        requested=current_date,
                        effective="unknown",
                        timing="no auditable source metadata captured",
                    )
                )
            flow_records = []
            if market_flow_context:
                lowered_flow = market_flow_context.casefold().lstrip()
                if "unavailable" in lowered_flow:
                    flow_effective = "—"
                    flow_timing = "unavailable"
                elif lowered_flow.startswith("<no "):
                    flow_effective = "—"
                    flow_timing = "available; no published records"
                else:
                    flow_effective = f"published market-section data <= {current_date}"
                    flow_timing = "market context only; not ticker order flow"
                flow_records.append(
                    ProvenanceRecord(
                        evidence="regional investor flows",
                        source="J-Quants investor-types",
                        requested=current_date,
                        effective=flow_effective,
                        timing=flow_timing,
                    )
                )
            prefetched_evidence.append(
                prefetched_evidence_block(macro_panel, macro_records)
            )
            if market_flow_context:
                prefetched_evidence.append(
                    prefetched_evidence_block(
                        market_flow_context,
                        flow_records,
                    )
                )
            all_records = [
                *extract_provenance(state["messages"]),
                *macro_records,
                *flow_records,
            ]
            prefetched_evidence.extend(
                missing_evidence_blocks(
                    all_records,
                    (("get_news", "routed ticker news"),),
                    requested_date=current_date,
                )
            )
            report = (
                result.content
                if isinstance(result.content, str)
                else str(result.content)
            )

        return {
            "messages": [result],
            "news_report": report,
            "prefetched_evidence": prefetched_evidence,
        }

    return news_analyst_node
