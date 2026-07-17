from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from tradingagents.agents.utils.agent_utils import (
    get_instrument_context_from_state,
    get_language_instruction,
)
from tradingagents.agents.utils.fundamental_data_tools import (
    get_balance_sheet_for_analysis,
    get_cashflow_for_analysis,
    get_fundamentals_for_analysis,
    get_income_statement_for_analysis,
)
from tradingagents.dataflows.config import get_config
from tradingagents.provenance import append_provenance_appendix, extract_provenance


def create_fundamentals_analyst(llm):
    def fundamentals_analyst_node(state):
        current_date = state["trade_date"]
        instrument_context = get_instrument_context_from_state(state)

        tools = [
            get_fundamentals_for_analysis,
            get_balance_sheet_for_analysis,
            get_cashflow_for_analysis,
            get_income_statement_for_analysis,
        ]

        system_message = (
            "You are a researcher tasked with analyzing fundamental information over the past week about a company. Please write a comprehensive report of the company's fundamental information such as financial documents, company profile, basic company financials, and company financial history to gain a full view of the company's fundamental information to inform traders. Make sure to include as much detail as possible. Provide specific, actionable insights with supporting evidence to help traders make informed decisions."
            + " Make sure to append a Markdown table at the end of the report to organize key points in the report, organized and easy to read."
            + " Use the available tools: `get_fundamentals` for comprehensive company analysis, `get_balance_sheet`, `get_cashflow`, and `get_income_statement` for specific financial statements."
            + " The workflow injects the exact analysis date into every fundamental tool call; do not attempt to supply or override `curr_date`."
            + " Treat missing or unprovided financial fields as unknown, never as zero. Data labelled `not point-in-time historical data` must not be presented as evidence that was available on a historical analysis date; explicitly state that limitation instead. Do not substitute EBIT, pretax income, or another subtotal for operating or ordinary profit unless the source itself defines that mapping."
            + " Preserve source, requested-date, retrieval-time, and point-in-time limitation labels when citing exact figures. Do not create a data-provenance appendix yourself; the workflow may append one in audit mode."
            + get_language_instruction(),
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
            report = append_provenance_appendix(
                result.content,
                extract_provenance(state["messages"]),
                expected=(
                    ("get_fundamentals", "fundamentals overview"),
                    ("get_income_statement", "income statement"),
                    ("get_balance_sheet", "balance sheet"),
                    ("get_cashflow", "cash flow statement"),
                ),
                requested_date=current_date,
                enabled=get_config()["provenance_appendix"],
            )
            result = result.model_copy(update={"content": report})

        return {
            "messages": [result],
            "fundamentals_report": report,
        }

    return fundamentals_analyst_node
