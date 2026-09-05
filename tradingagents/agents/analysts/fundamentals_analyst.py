from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from tradingagents.agents.utils.agent_states import (
    missing_evidence_blocks,
    prefetched_evidence_block,
)
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
from tradingagents.dataflows.financial_inputs import collect_financial_inputs
from tradingagents.dataflows.interface import route_to_vendor
from tradingagents.dataflows.source_observations import SourceObservation
from tradingagents.provenance import extract_provenance


def create_fundamentals_analyst(llm):
    def fundamentals_analyst_node(state):
        current_date = state["trade_date"]
        instrument_context = get_instrument_context_from_state(state)
        inputs = state.get("fundamental_inputs")
        if inputs is None:
            inputs = collect_financial_inputs(
                state["company_of_interest"], current_date, route=route_to_vendor,
            )
        observations = [SourceObservation.load(o) for o in inputs["observations"]]
        core = inputs["responses"].get("get_fundamentals", "")
        core += "\n\n" + "\n\n".join(
            f"{o.content}\nSource: {o.source}; {o.timing}; retrieved: {o.retrieved_at.isoformat()}"
            for o in observations
        )
        if not observations:
            core += "\n\n" + "\n\n".join(
                value for method, value in inputs["responses"].items() if method != "get_fundamentals"
            )

        tools = [
            get_fundamentals_for_analysis,
            get_balance_sheet_for_analysis,
            get_cashflow_for_analysis,
            get_income_statement_for_analysis,
        ]

        system_message = (
            "You are a researcher tasked with analyzing point-in-time fundamental information about a company. Write a comprehensive report covering financial documents, company profile, financial condition, and historical disclosures that were available by the analysis cutoff. Provide specific evidence, uncertainty, risks, catalysts, and invalidation-relevant observations for a research committee. Do not provide account-level sizing, entry, stop, target, or execution instructions."
            + " Make sure to append a Markdown table at the end of the report to organize key points in the report, organized and easy to read."
            + " Use the available tools: `get_fundamentals` for comprehensive company analysis, `get_balance_sheet`, `get_cashflow`, and `get_income_statement` for specific financial statements."
            + " The workflow injects the exact analysis date into every fundamental tool call; do not attempt to supply or override `curr_date`."
            + " Treat missing or unprovided financial fields as unknown, never as zero. Data labelled `not point-in-time historical data` must not be presented as evidence that was available on a historical analysis date; explicitly state that limitation instead. Do not substitute EBIT, pretax income, or another subtotal for operating or ordinary profit unless the source itself defines that mapping."
            + " Preserve source, requested-date, retrieval-time, and point-in-time limitation labels when citing exact figures. Do not create data-quality-warning or provenance sections; the workflow records source metadata separately."
            + " Core financial data has already been fetched below. Use these summaries first; tools can retrieve the cached statement detail. YTD values are cumulative, not standalone quarters.\n\n"
            + core
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
        prefetched_evidence = [
            prefetched_evidence_block(body, extract_provenance(body))
            for body in inputs["responses"].values()
        ]
        prefetched_evidence.extend({
            "content": o.content, "records": [],
            "temporal_scope": "point_in_time" if o.is_pit else "live_only",
            "source_observation": o.dump(),
        } for o in observations)

        if len(result.tool_calls) == 0:
            report = (
                result.content
                if isinstance(result.content, str)
                else str(result.content)
            )
            records = extract_provenance(state["messages"])
            attempted = {
                *inputs["responses"],
                *(record.evidence for record in records),
            }
            prefetched_evidence += missing_evidence_blocks(
                records,
                (
                    (method, label)
                    for method, label in (
                        ("get_fundamentals", "fundamentals overview"),
                        ("get_income_statement", "income statement"),
                        ("get_balance_sheet", "balance sheet"),
                        ("get_cashflow", "cash flow statement"),
                    )
                    if method not in attempted
                ),
                requested_date=current_date,
            )

        return {
            "messages": [result],
            "fundamentals_report": report,
            "prefetched_evidence": prefetched_evidence,
            "fundamental_inputs": inputs,
        }

    return fundamentals_analyst_node
