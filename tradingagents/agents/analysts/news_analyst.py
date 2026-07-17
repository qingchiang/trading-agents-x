from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from tradingagents.agents.utils.agent_utils import (
    get_instrument_context_from_state,
    get_language_instruction,
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


def create_news_analyst(llm):
    def news_analyst_node(state):
        current_date = state["trade_date"]
        ticker = state["company_of_interest"]
        asset_type = state.get("asset_type", "stock")
        asset_label = "company" if asset_type == "stock" else "asset"
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
        market_flow_context = (
            get_market_investor_flows(ticker, current_date)
            if is_tokyo_ticker(ticker)
            else ""
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
            f"You are a news researcher tasked with analyzing recent news and trends. Please write a comprehensive report of the current state of the world that is relevant for trading and macroeconomics. Use the available tools: get_news(ticker, window) for {asset_label}-specific news by ticker symbol, get_global_news(look_back_days, limit) for broader macroeconomic news, get_macro_indicators(indicator, look_back_days) to drill into a SPECIFIC macro series beyond the panel below (a US FRED alias such as 'yield_curve', 'initial_claims', 'retail_sales', 'm2', '2y_treasury', a raw FRED series ID like 'CPILFESL', or a Japanese official series 'jp_cpi'/'jp_core_cpi'/'jp_policy_rate'/'jp_tankan'), and get_prediction_markets(topic, limit) for current market-implied probabilities of forward-looking events (e.g. 'Fed rate cut', 'recession 2026', geopolitical or sector events). The workflow injects the immutable analysis date into every tool; do not attempt to supply or override any date argument. Always call get_news with window='recent' first; it covers {ticker_news_start_date} through {current_date} (configured lookback offset {ticker_news_lookback_days}, inclusive at both ends). Call window='extended' only when recent evidence is absent/insufficient or an unresolved earnings warning, M&A, regulatory, management, financing, or capital-allocation catalyst may predate that window. Extended covers {extended_news_start_date} through {current_date} ({extended_news_lookback_days + 1} calendar dates), includes the recent results, and must replace rather than duplicate them in the analysis. Never expand merely to increase article count. Prediction markets are live-only and return an explicit unavailable result for historical analysis. Provide specific, actionable insights with supporting evidence to help traders make informed decisions."
            "\n\nTicker-news relevance labels are evidence boundaries: `[direct]` has explicit ticker or full-name evidence and may be described as a company event. `[candidate]` contains an ambiguous ticker/name or summary-only mention; independently verify the concrete relationship from the supplied title/summary and ignore the item if that relationship is not clear. Never assume relevance merely because Yahoo returned an item for the ticker. `[context]` is only an external driver or industry/market backdrop. Never rewrite `[candidate]` or `[context]` as an action taken by, financing raised by, or event confirmed for the target company without explicit evidence in the item."
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
                    " If you or any other assistant has the FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** or deliverable,"
                    " prefix your response with FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** so the team knows to stop."
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

        if len(result.tool_calls) == 0:
            report = result.content

        return {
            "messages": [result],
            "news_report": report,
        }

    return news_analyst_node
