"""Parallel, evidence-first research graph with Fast/Standard/Deep profiles."""

from __future__ import annotations

import json
import operator
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Any, Literal

from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.runtime import Runtime
from typing_extensions import TypedDict

from tradingagents.agents import (
    create_fundamentals_analyst,
    create_market_analyst,
    create_news_analyst,
    create_sentiment_analyst,
)
from tradingagents.agents.utils.agent_states import AgentState
from tradingagents.agents.utils.agent_utils import get_news
from tradingagents.agents.utils.core_stock_tools import get_stock_data_for_analysis
from tradingagents.agents.utils.fundamental_data_tools import (
    get_balance_sheet_for_analysis,
    get_cashflow_for_analysis,
    get_fundamentals_for_analysis,
    get_income_statement_for_analysis,
)
from tradingagents.agents.utils.macro_data_tools import (
    get_macro_indicators_for_analysis,
)
from tradingagents.agents.utils.market_data_validation_tools import (
    get_verified_market_snapshot_for_analysis,
)
from tradingagents.agents.utils.news_data_tools import (
    get_global_news_for_analysis,
    get_news_for_analysis,
)
from tradingagents.agents.utils.prediction_markets_tools import (
    get_prediction_markets_for_analysis,
)
from tradingagents.agents.utils.technical_indicators_tools import (
    get_indicators_for_analysis,
)
from tradingagents.application.contracts import (
    AnalystReport,
    ArtifactGenerationMethod,
    DecisionNumericAuditAppendix,
    EvidenceBundle,
    EvidenceItem,
    EvidenceOrigin,
    EvidenceQuality,
    EvidenceTemporalScope,
    ResearchArtifactContent,
    ResearchArtifactDraft,
    ResearchDecision,
    ResearchWarning,
    RunProfile,
    report_language_prompt_label,
)
from tradingagents.application.evidence import (
    extract_evidence_tables,
)
from tradingagents.application.evidence_workset import (
    artifact_records,
    is_evidence_tool_artifact,
)
from tradingagents.application.metrics import MetricsCallback
from tradingagents.application.reporting import order_reports
from tradingagents.application.runtime import RunContext, check_cancelled
from tradingagents.dataflows.config import use_config
from tradingagents.graph.analyst_synthesis import (
    evidence_warnings as _evidence_warnings,
    invoke_analyst_report as _invoke_analyst_report,
)
from tradingagents.graph.deliberation import (
    debate_round_has_material_progress,
    invoke_debate_agenda,
    invoke_judge_draft,
    invoke_rebuttal,
    invoke_research_case,
    invoke_research_decision,
    invoke_risk_review,
    write_research_markdown,
)
from tradingagents.graph.evidence_context import (
    build_analyst_evidence_context,
)
from tradingagents.graph.output_validation import (
    require_valid_refs as _require_valid_refs,
)
from tradingagents.graph.role_context import RoleContext, RoleContextBuilder
from tradingagents.graph.structured_output import (
    StructuredOutputResult,
)
from tradingagents.provenance import (
    ProvenanceRecord,
    extract_evidence_spans,
    extract_provenance,
    strip_provenance_markers,
    temporal_scope_from_records,
)

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_CONTROL_COMMENT_RE = re.compile(r"<!--\s*tradingagents-data-provenance:(?:start|end)\s*-->")


def _merge_dicts(
    left: dict[str, Any] | None,
    right: dict[str, Any] | None,
) -> dict[str, Any]:
    return {**(left or {}), **(right or {})}


class ResearchState(TypedDict, total=False):
    ticker: str
    analysis_date: str
    asset_type: str
    profile: str
    output_language: str
    analysts: list[str]
    analyst_collection_memos: Annotated[dict[str, str], _merge_dicts]
    analyst_evidence_items: Annotated[
        dict[str, list[dict[str, Any]]],
        _merge_dicts,
    ]
    analyst_collection_metadata: Annotated[
        dict[str, dict[str, Any]],
        _merge_dicts,
    ]
    analyst_reports: Annotated[dict[str, dict[str, Any]], _merge_dicts]
    evidence_bundle: dict[str, Any]
    cases: Annotated[dict[str, dict[str, Any]], _merge_dicts]
    debate_agenda: dict[str, Any]
    rebuttals: Annotated[list[dict[str, Any]], operator.add]
    risk_reviews: Annotated[dict[str, dict[str, Any]], _merge_dicts]
    judge_draft: dict[str, Any]
    final_decision: dict[str, Any]
    numeric_audit: dict[str, Any] | None
    rebuttal_round: int
    debate_continue: bool
    warnings: Annotated[list[dict[str, Any]], operator.add]


@dataclass(frozen=True)
class RoleSpec:
    key: str
    label: str
    objective: str
    model: Literal["quick", "deep"] = "quick"


@dataclass(frozen=True)
class GraphExecution:
    state: dict[str, Any]
    evidence: EvidenceBundle
    reports: dict[str, AnalystReport]
    decision: ResearchDecision
    numeric_audit: DecisionNumericAuditAppendix | None = None
    warnings: tuple[ResearchWarning, ...] = ()


_PERSPECTIVE_SPECS = {
    "bull": RoleSpec(
        key="bull",
        label="Bull Researcher",
        objective=(
            "Build the strongest evidence-grounded constructive case from the "
            "typed analyst claims. Explain causal mechanisms, identify the "
            "strongest opposing argument, and expose fragile assumptions."
        ),
    ),
    "bear": RoleSpec(
        key="bear",
        label="Bear Researcher",
        objective=(
            "Build the strongest evidence-grounded skeptical case. Separate "
            "real downside mechanisms from data gaps and mere unknowns, while "
            "acknowledging the strongest constructive evidence."
        ),
    ),
    "risk": RoleSpec(
        key="integrated",
        label="Integrated Risk Reviewer",
        objective=(
            "Review upside omissions, base-case consistency, downside paths, "
            "data quality, and invalidation. Recommend explicit changes to the "
            "judge draft without proposing account or execution instructions."
        ),
    ),
    "aggressive": RoleSpec(
        key="aggressive",
        label="Aggressive Risk Lens",
        objective=(
            "Stress-test whether the draft underweights asymmetric upside and "
            "opportunity cost, while explicitly identifying failure conditions."
        ),
    ),
    "neutral": RoleSpec(
        key="neutral",
        label="Neutral Risk Lens",
        objective=(
            "Balance upside and downside mechanisms, surface uncertainty, and "
            "challenge overconfident claims on either side."
        ),
    ),
    "conservative": RoleSpec(
        key="conservative",
        label="Conservative Risk Lens",
        objective=(
            "Stress-test downside, data quality, regime shifts, and thesis "
            "invalidation without giving account-level trading instructions."
        ),
    ),
}


class ResearchGraph:
    """Build and execute a graph for one immutable request/configuration."""

    def __init__(
        self,
        *,
        quick_llm: Any,
        deep_llm: Any,
        quick_serializer_llm: Any | None = None,
        deep_serializer_llm: Any | None = None,
        profile: RunProfile,
        selected_analysts: Iterable[str],
        metrics: MetricsCallback | None = None,
    ):
        self.quick_llm = quick_llm
        self.deep_llm = deep_llm
        self.quick_serializer_llm = quick_serializer_llm or quick_llm
        self.deep_serializer_llm = deep_serializer_llm or deep_llm
        self.profile = profile
        self.selected_analysts = tuple(selected_analysts)
        self.metrics = metrics or MetricsCallback()
        self._analyst_subgraphs = self._build_analyst_subgraphs()
        self.workflow = self._build_workflow()

    def execute(
        self,
        context: RunContext,
        *,
        checkpointer: Any = None,
        checkpoint_thread_id: str,
        resume: bool = False,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> GraphExecution:
        graph = self.workflow.compile(checkpointer=checkpointer)
        config: dict[str, Any] = {
            "configurable": {"thread_id": checkpoint_thread_id},
            "recursion_limit": 120,
            "callbacks": [self.metrics],
        }
        graph_input = None if resume else self._initial_state(context)
        final_state: dict[str, Any] | None = None
        for mode, chunk in graph.stream(
            graph_input,
            config=config,
            context=context,
            stream_mode=["values", "custom"],
        ):
            if mode == "custom":
                if on_event:
                    on_event(dict(chunk))
            elif mode == "values":
                final_state = dict(chunk)
        if final_state is None:
            snapshot = graph.get_state(config)
            final_state = dict(snapshot.values)
        evidence = EvidenceBundle.model_validate(final_state["evidence_bundle"])
        reports = order_reports(
            {
                key: AnalystReport.model_validate(value)
                for key, value in final_state["analyst_reports"].items()
            }
        )
        decision = ResearchDecision.model_validate(final_state["final_decision"])
        numeric_audit = (
            DecisionNumericAuditAppendix.model_validate(final_state["numeric_audit"])
            if final_state.get("numeric_audit")
            else None
        )
        warnings = tuple(
            ResearchWarning.model_validate(value) for value in final_state.get("warnings", [])
        )
        return GraphExecution(
            state=final_state,
            evidence=evidence,
            reports=reports,
            decision=decision,
            numeric_audit=numeric_audit,
            warnings=tuple(dict.fromkeys(warnings)),
        )

    def execute_frozen(
        self,
        context: RunContext,
        *,
        evidence: EvidenceBundle,
        reports: dict[str, AnalystReport],
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> GraphExecution:
        """Run production deliberation prompts against approved frozen inputs.

        This evaluation boundary intentionally skips data tools and Analyst
        generation. It is used to compare research topologies with the exact
        same sealed EvidenceBundle and validated AnalystReports.
        """

        if evidence.instrument != context.request.ticker:
            raise ValueError("frozen evidence instrument does not match request")
        if evidence.analysis_date != context.request.analysis_date:
            raise ValueError("frozen evidence cutoff does not match request")
        selected = set(reports)
        expected = set(context.request.analysts)
        if selected != expected:
            raise ValueError("frozen reports do not match selected analysts")
        reports = order_reports(reports)
        valid_refs = {item.ref for item in evidence.items}
        for analyst, report in reports.items():
            if report.analyst != analyst:
                raise ValueError("frozen report key does not match analyst")
            _require_valid_refs(
                report.source_refs,
                valid_refs,
                required=False,
            )

        workflow = StateGraph(ResearchState, context_schema=RunContext)
        self._attach_research_workflow(workflow, START)
        graph = workflow.compile()
        final_state: dict[str, Any] | None = None
        for mode, chunk in graph.stream(
            self._frozen_state(context, evidence=evidence, reports=reports),
            config={
                "recursion_limit": 120,
                "callbacks": [self.metrics],
            },
            context=context,
            stream_mode=["values", "custom"],
        ):
            if mode == "custom":
                if on_event:
                    on_event(dict(chunk))
            elif mode == "values":
                final_state = dict(chunk)
        if final_state is None:
            raise RuntimeError("frozen research graph produced no state")
        decision = ResearchDecision.model_validate(final_state["final_decision"])
        numeric_audit = (
            DecisionNumericAuditAppendix.model_validate(final_state["numeric_audit"])
            if final_state.get("numeric_audit")
            else None
        )
        warnings = tuple(
            ResearchWarning.model_validate(value) for value in final_state.get("warnings", [])
        )
        return GraphExecution(
            state=final_state,
            evidence=evidence,
            reports=order_reports(reports),
            decision=decision,
            numeric_audit=numeric_audit,
            warnings=tuple(dict.fromkeys(warnings)),
        )

    def _initial_state(self, context: RunContext) -> ResearchState:
        request = context.request
        return {
            "ticker": request.ticker,
            "analysis_date": request.analysis_date.isoformat(),
            "asset_type": request.asset_type.value,
            "profile": request.profile.value,
            "output_language": report_language_prompt_label(context.settings.output_language),
            "analysts": list(request.analysts),
            "analyst_collection_memos": {},
            "analyst_evidence_items": {},
            "analyst_collection_metadata": {},
            "analyst_reports": {},
            "cases": {},
            "rebuttals": [],
            "risk_reviews": {},
            "rebuttal_round": 0,
            "debate_continue": False,
            "warnings": [],
        }

    def _frozen_state(
        self,
        context: RunContext,
        *,
        evidence: EvidenceBundle,
        reports: dict[str, AnalystReport],
    ) -> ResearchState:
        state = self._initial_state(context)
        state["analyst_reports"] = {
            key: report.model_dump(mode="json") for key, report in reports.items()
        }
        state["evidence_bundle"] = evidence.model_dump(mode="json")
        return state

    def _build_analyst_subgraphs(self) -> dict[str, Any]:
        factories = {
            "market": lambda: create_market_analyst(self.quick_llm),
            "social": lambda: create_sentiment_analyst(self.quick_llm),
            "news": lambda: create_news_analyst(self.quick_llm),
            "fundamentals": lambda: create_fundamentals_analyst(self.quick_llm),
        }
        tool_nodes = {
            "market": ToolNode(
                [
                    get_stock_data_for_analysis,
                    get_indicators_for_analysis,
                    get_verified_market_snapshot_for_analysis,
                ]
            ),
            "social": ToolNode([get_news]),
            "news": ToolNode(
                [
                    get_news_for_analysis,
                    get_global_news_for_analysis,
                    get_macro_indicators_for_analysis,
                    get_prediction_markets_for_analysis,
                ]
            ),
            "fundamentals": ToolNode(
                [
                    get_fundamentals_for_analysis,
                    get_balance_sheet_for_analysis,
                    get_cashflow_for_analysis,
                    get_income_statement_for_analysis,
                ]
            ),
        }
        subgraphs: dict[str, Any] = {}
        for analyst in self.selected_analysts:
            builder = StateGraph(AgentState, context_schema=RunContext)
            builder.add_node("agent", factories[analyst]())
            builder.add_node("tools", tool_nodes[analyst])
            builder.add_edge(START, "agent")
            builder.add_conditional_edges(
                "agent",
                _analyst_route,
                {"tools": "tools", "done": END},
            )
            builder.add_edge("tools", "agent")
            subgraphs[analyst] = builder.compile()
        return subgraphs

    def _build_workflow(self) -> StateGraph:
        workflow = StateGraph(ResearchState, context_schema=RunContext)
        for analyst in self.selected_analysts:
            node_name = f"analyst.{analyst}"
            collect_name = f"{node_name}.collect"
            workflow.add_node(
                collect_name,
                self._create_analyst_collect_node(analyst),
            )
            workflow.add_node(
                node_name,
                self._create_analyst_synthesis_node(analyst),
            )
            workflow.add_edge(START, collect_name)
            workflow.add_edge(collect_name, "evidence.seal")
        workflow.add_node("evidence.seal", self._seal_evidence)
        workflow.add_node("reports.ready", self._reports_ready)
        for analyst in self.selected_analysts:
            node_name = f"analyst.{analyst}"
            workflow.add_edge("evidence.seal", node_name)
            workflow.add_edge(node_name, "reports.ready")
        self._attach_research_workflow(workflow, "reports.ready")
        return workflow

    def _attach_research_workflow(
        self,
        workflow: StateGraph,
        source: str,
    ) -> None:
        if self.profile is RunProfile.FAST:
            workflow.add_node("committee.final", self._create_final_committee(fast=True))
            workflow.add_edge(source, "committee.final")
            workflow.add_edge("committee.final", END)
            return

        workflow.add_node(
            "case.bull",
            self._create_case_node(_PERSPECTIVE_SPECS["bull"]),
        )
        workflow.add_node(
            "case.bear",
            self._create_case_node(_PERSPECTIVE_SPECS["bear"]),
        )
        workflow.add_node("debate.agenda", self._create_debate_agenda_node())
        workflow.add_edge(source, "case.bull")
        workflow.add_edge(source, "case.bear")
        workflow.add_edge("case.bull", "debate.agenda")
        workflow.add_edge("case.bear", "debate.agenda")

        workflow.add_node("debate.control", self._debate_control)
        workflow.add_node(
            "rebuttal.bull",
            self._create_rebuttal_node(_PERSPECTIVE_SPECS["bull"]),
        )
        workflow.add_node(
            "rebuttal.bear",
            self._create_rebuttal_node(_PERSPECTIVE_SPECS["bear"]),
        )
        workflow.add_edge("debate.agenda", "debate.control")
        workflow.add_conditional_edges(
            "debate.control",
            self._route_debate,
            {
                "bull_rebuttal": "rebuttal.bull",
                "bear_rebuttal": "rebuttal.bear",
                "judge": "judge.research",
            },
        )
        workflow.add_edge("rebuttal.bull", "debate.control")
        workflow.add_edge("rebuttal.bear", "debate.control")

        workflow.add_node("judge.research", self._research_judge)

        if self.profile is RunProfile.STANDARD:
            workflow.add_node(
                "risk.review",
                self._create_risk_node(_PERSPECTIVE_SPECS["risk"]),
            )
            workflow.add_edge("judge.research", "risk.review")
            workflow.add_node("committee.final", self._create_final_committee(fast=False))
            workflow.add_edge("risk.review", "committee.final")
        else:
            for key in ("aggressive", "neutral", "conservative"):
                name = f"risk.{key}"
                workflow.add_node(
                    name,
                    self._create_risk_node(_PERSPECTIVE_SPECS[key]),
                )
                workflow.add_edge("judge.research", name)
                workflow.add_edge(name, "committee.final")
            workflow.add_node("committee.final", self._create_final_committee(fast=False))

        workflow.add_edge("committee.final", END)

    def _create_analyst_collect_node(self, analyst: str):
        report_key = {
            "market": "market_report",
            "social": "sentiment_report",
            "news": "news_report",
            "fundamentals": "fundamentals_report",
        }[analyst]

        def collect_node(
            state: ResearchState,
            runtime: Runtime[RunContext],
        ) -> dict[str, Any]:
            context = runtime.context
            node_name = f"analyst.{analyst}.collect"
            self._start_node(runtime, node_name)
            check_cancelled(context)
            local_state: dict[str, Any] = {
                "messages": [HumanMessage(content=context.request.ticker)],
                "company_of_interest": context.request.ticker,
                "asset_type": context.request.asset_type.value,
                "instrument_context": context.instrument_context,
                "trade_date": context.request.analysis_date.isoformat(),
                "past_context": "",
                "market_report": "",
                "sentiment_report": "",
                "news_report": "",
                "fundamentals_report": "",
                "sentiment_confidence": None,
                "prefetched_evidence": [],
            }
            with use_config(dict(context.dataflow_config)):
                result = self._analyst_subgraphs[analyst].invoke(
                    local_state,
                    config={
                        "recursion_limit": 40,
                        "callbacks": [self.metrics],
                        "metadata": {"research_node": node_name},
                    },
                    context=context,
                )
            narrative = _clean_narrative(str(result.get(report_key, "")))
            evidence = _collect_evidence(
                result.get("messages", []),
                narrative,
                requested_date=context.request.analysis_date,
                analyst=analyst,
                prefetched_blocks=result.get("prefetched_evidence", []),
            )
            evidence_warnings = _evidence_warnings(evidence)
            synthesis_metadata = {
                "confidence_override": (
                    result.get("sentiment_confidence") if analyst == "social" else None
                ),
                "warnings": [warning.model_dump(mode="json") for warning in evidence_warnings],
            }
            self._finish_node(
                runtime,
                node_name,
                {
                    "evidence_count": len(evidence),
                    "draft_characters": len(narrative),
                },
            )
            return {
                "analyst_collection_memos": {analyst: narrative},
                "analyst_evidence_items": {
                    analyst: [item.model_dump(mode="json") for item in evidence]
                },
                "analyst_collection_metadata": {analyst: synthesis_metadata},
            }

        return collect_node

    def _create_analyst_synthesis_node(self, analyst: str):
        def synthesis_node(
            state: ResearchState,
            runtime: Runtime[RunContext],
        ) -> dict[str, Any]:
            context = runtime.context
            node_name = f"analyst.{analyst}"
            self._start_node(runtime, node_name, measure=False)
            check_cancelled(context)
            narrative = state["analyst_collection_memos"][analyst]
            evidence_refs = tuple(
                EvidenceItem.model_validate(item).ref
                for item in state["analyst_evidence_items"][analyst]
            )
            synthesis_metadata = state["analyst_collection_metadata"][analyst]
            confidence_override = synthesis_metadata.get("confidence_override")
            warnings = tuple(
                ResearchWarning.model_validate(warning)
                for warning in synthesis_metadata.get("warnings", [])
            )
            sealed_bundle = EvidenceBundle.model_validate(state["evidence_bundle"])
            evidence_ref_set = set(evidence_refs)
            analyst_items = tuple(
                item for item in sealed_bundle.items if item.ref in evidence_ref_set
            )
            analyst_bundle = EvidenceBundle(
                instrument=sealed_bundle.instrument,
                analysis_date=sealed_bundle.analysis_date,
                items=analyst_items,
                tables=tuple(
                    table
                    for table in sealed_bundle.tables
                    if evidence_ref_set.intersection(table.evidence_refs)
                ),
            )
            output_language = report_language_prompt_label(context.settings.output_language)
            prepared_evidence = build_analyst_evidence_context(
                bundle=analyst_bundle,
                evidence_refs=evidence_refs,
            )
            runtime.stream_writer(
                {
                    "event_type": "node.context_prepared",
                    "node": f"{node_name}.context",
                    "payload": {
                        "inline_characters": (prepared_evidence.inline_characters),
                        "catalog_items": len(prepared_evidence.catalog.get("items", [])),
                        "catalog_tables": len(prepared_evidence.catalog.get("tables", [])),
                        "lookup_count": len(prepared_evidence.lookups),
                        "returned_rows": sum(
                            lookup.returned_rows for lookup in prepared_evidence.lookups
                        ),
                    },
                }
            )
            output = _invoke_analyst_report(
                self.quick_llm,
                self.quick_serializer_llm,
                analyst=analyst,
                draft_narrative=narrative,
                bundle=analyst_bundle,
                output_language=output_language,
                confidence_override=confidence_override,
                warnings=warnings,
                node=node_name,
                prepared_evidence=prepared_evidence,
                event_writer=runtime.stream_writer,
                metrics=self.metrics,
            )
            typed = output.value
            check_cancelled(context)
            self._write_artifact(
                runtime,
                node=node_name,
                stage="analyst",
                role=analyst,
                content=typed,
                generation_method=output.generation_method,
                prompt_version=f"analyst-{analyst}-v6-sealed-context",
            )
            self._finish_node(
                runtime,
                node_name,
                {
                    "evidence_count": len(analyst_items),
                    "confidence": typed.confidence,
                    "warnings": len(typed.warnings),
                },
                measure=False,
            )
            return {
                "analyst_reports": {analyst: typed.model_dump(mode="json")},
                "warnings": [warning.model_dump(mode="json") for warning in typed.warnings]
                + _structured_recovery_warnings(node_name, output),
            }

        return synthesis_node

    def _seal_evidence(
        self,
        state: ResearchState,
        runtime: Runtime[RunContext],
    ) -> dict[str, Any]:
        node = "evidence.seal"
        self._start_node(runtime, node)
        check_cancelled(runtime.context)
        deduped: dict[str, EvidenceItem] = {}
        for analyst_items in state.get("analyst_evidence_items", {}).values():
            for raw in analyst_items:
                item = EvidenceItem.model_validate(raw)
                deduped[item.ref] = item
        bundle = EvidenceBundle(
            instrument=state["ticker"],
            analysis_date=date.fromisoformat(state["analysis_date"]),
            items=tuple(deduped.values()),
            tables=extract_evidence_tables(tuple(deduped.values())),
        )
        runtime.context.evidence_writer(bundle)
        self._finish_node(
            runtime,
            node,
            {
                "items": len(bundle.items),
                "tables": len(bundle.tables),
                "digest": bundle.digest,
            },
        )
        return {
            "evidence_bundle": bundle.model_dump(mode="json"),
        }

    def _reports_ready(
        self,
        state: ResearchState,
        runtime: Runtime[RunContext],
    ) -> dict[str, Any]:
        node = "reports.ready"
        self._start_node(runtime, node)
        check_cancelled(runtime.context)
        bundle = EvidenceBundle.model_validate(state["evidence_bundle"])
        valid_refs = {item.ref for item in bundle.items}
        reports: dict[str, dict[str, Any]] = {}
        for analyst in state["analysts"]:
            report = AnalystReport.model_validate(state["analyst_reports"][analyst])
            _require_valid_refs(
                report.source_refs,
                valid_refs,
                required=False,
            )
            reports[analyst] = report.model_dump(mode="json")
        self._finish_node(
            runtime,
            node,
            {"reports": len(reports)},
        )
        return {"analyst_reports": reports}

    def _create_case_node(self, spec: RoleSpec):
        llm = self._deliberation_llm(spec)

        def case_node(
            state: ResearchState,
            runtime: Runtime[RunContext],
        ) -> dict[str, Any]:
            node = f"case.{spec.key}"
            self._start_node(runtime, node, measure=False)
            check_cancelled(runtime.context)
            context = self._role_context(
                state=state,
                runtime=runtime,
                node=node,
                title=spec.label,
                objective=spec.objective,
                stage="opening_case",
                report_mode="full",
                evidence_refs=RoleContextBuilder(state).primary_evidence_refs(),
                instructions=(
                    "Build an independent complete case before seeing the "
                    "opposing case. For every material argument, identify the "
                    "analyst conclusion it relies on, cite the relevant evidence, "
                    "and explain the causal mechanism connecting the evidence to "
                    "its implication. Do not expose internal claim or section IDs."
                ),
            )
            with self.metrics.phase(
                f"{node}.write",
                event_writer=runtime.stream_writer,
            ):
                written = write_research_markdown(
                    llm,
                    prompt=context.prompt,
                    node=f"{node}.write",
                    allowed_evidence_refs=_state_evidence_refs(state),
                    output_language=state["output_language"],
                    invoke_config={
                        "callbacks": [self.metrics],
                        "metadata": {"research_node": f"{node}.write"},
                    },
                )
            with self.metrics.phase(
                f"{node}.audit",
                event_writer=runtime.stream_writer,
            ):
                output = invoke_research_case(
                    self._deliberation_serializer_llm(spec),
                    role=spec.key,
                    markdown=written.markdown,
                    state=state,
                    node=f"{node}.audit",
                    event_writer=runtime.stream_writer,
                )
            case = output.value
            self._write_artifact(
                runtime,
                node=node,
                stage="case",
                role=spec.key,
                content=case,
                generation_method=output.generation_method,
                prompt_version=f"research-case-{spec.key}-v6-readable",
            )
            self._finish_node(
                runtime,
                node,
                {"role": case.role},
                measure=False,
            )
            return {
                "cases": {spec.key: case.model_dump(mode="json")},
                "warnings": [
                    *_structured_recovery_warnings(node, output),
                    *_research_markdown_warnings(written),
                ],
            }

        return case_node

    def _create_debate_agenda_node(self):
        def agenda_node(
            state: ResearchState,
            runtime: Runtime[RunContext],
        ) -> dict[str, Any]:
            node = "debate.agenda"
            self._start_node(runtime, node, measure=False)
            check_cancelled(runtime.context)
            context = RoleContextBuilder(state).build_agenda(
                title="Research Debate Moderator",
                objective=(
                    "Compare the independent bull and bear cases and produce a "
                    "prioritized agenda of genuine material disagreements."
                ),
                instructions=(
                    "Create one issue per distinct material question. "
                    "Describe the analyst conclusions at stake in natural "
                    "language. Do not expose internal claim or section IDs. Do "
                    "not turn mere missing data into a bearish assertion."
                ),
            )
            self._record_role_context(runtime, node=node, context=context)
            with self.metrics.phase(
                f"{node}.serialize",
                event_writer=runtime.stream_writer,
            ):
                output = invoke_debate_agenda(
                    self._deliberation_serializer_llm(),
                    prompt=context.prompt,
                    state=state,
                    node=f"{node}.serialize",
                    output_language=state["output_language"],
                    event_writer=runtime.stream_writer,
                )
            agenda = output.value
            self._write_artifact(
                runtime,
                node=node,
                stage="agenda",
                role="moderator",
                content=agenda,
                generation_method=output.generation_method,
                prompt_version="debate-agenda-v8-compact-context",
            )
            self._finish_node(
                runtime,
                node,
                {
                    "issues": len(agenda.issues),
                    "critical": sum(
                        issue.importance.value == "critical" for issue in agenda.issues
                    ),
                },
                measure=False,
            )
            return {
                "debate_agenda": agenda.model_dump(mode="json"),
                "warnings": _structured_recovery_warnings(node, output),
            }

        return agenda_node

    def _create_rebuttal_node(self, spec: RoleSpec):
        llm = self._deliberation_llm(spec)

        def rebuttal_node(
            state: ResearchState,
            runtime: Runtime[RunContext],
        ) -> dict[str, Any]:
            node = f"rebuttal.{spec.key}"
            round_number = int(state.get("rebuttal_round", 0))
            self._start_node(runtime, node, measure=False)
            check_cancelled(runtime.context)
            context = self._role_context(
                state=state,
                runtime=runtime,
                node=node,
                title=f"{spec.label} Rebuttal",
                objective=(
                    "Answer the DebateAgenda issue by issue. Challenge exact "
                    "claims and mechanisms rather than repeating the opening case."
                ),
                stage="rebuttal",
                artifacts={
                    "cases": state.get("cases", {}),
                    "debate_agenda": state.get("debate_agenda", {}),
                    "prior_rebuttals": state.get("rebuttals", []),
                    "current_round": round_number,
                },
                instructions=(
                    "Respond only to listed agenda issues. Mark evidence as new "
                    "only if this role did not cite it in its opening case or "
                    "an earlier rebuttal. State the causal mechanism and any "
                    "remaining question for each response."
                ),
            )
            with self.metrics.phase(
                f"{node}.write",
                event_writer=runtime.stream_writer,
            ):
                written = write_research_markdown(
                    llm,
                    prompt=context.prompt,
                    node=f"{node}.write",
                    allowed_evidence_refs=_state_evidence_refs(state),
                    output_language=state["output_language"],
                    invoke_config={
                        "callbacks": [self.metrics],
                        "metadata": {"research_node": f"{node}.write"},
                    },
                )
            with self.metrics.phase(
                f"{node}.audit",
                event_writer=runtime.stream_writer,
            ):
                output = invoke_rebuttal(
                    self._deliberation_serializer_llm(spec),
                    role=spec.key,
                    round_number=round_number,
                    markdown=written.markdown,
                    state=state,
                    node=f"{node}.audit",
                    conservative_open=self.profile is RunProfile.DEEP,
                    event_writer=runtime.stream_writer,
                )
            rebuttal = output.value
            self._write_artifact(
                runtime,
                node=node,
                stage="rebuttal",
                role=spec.key,
                round=round_number,
                content=rebuttal,
                generation_method=output.generation_method,
                prompt_version=f"rebuttal-{spec.key}-v5-compact",
            )
            self._finish_node(
                runtime,
                node,
                {
                    "round": round_number,
                    "addressed_issues": len(rebuttal.addressed_issue_ids),
                    "open_issues": len(rebuttal.open_issue_ids),
                },
                measure=False,
            )
            return {
                "rebuttals": [rebuttal.model_dump(mode="json")],
                "warnings": [
                    *_structured_recovery_warnings(node, output),
                    *_research_markdown_warnings(written),
                ],
            }

        return rebuttal_node

    def _debate_control(
        self,
        state: ResearchState,
        runtime: Runtime[RunContext],
    ) -> dict[str, Any]:
        node = "debate.control"
        self._start_node(runtime, node)
        check_cancelled(runtime.context)
        round_number = int(state.get("rebuttal_round", 0))
        if round_number == 0:
            should_continue = True
        else:
            max_rounds = 3 if self.profile is RunProfile.DEEP else 1
            should_continue = round_number < max_rounds and debate_round_has_material_progress(
                state,
                round_number=round_number,
            )
        next_round = round_number + 1 if should_continue else round_number
        self._finish_node(
            runtime,
            node,
            {"round": round_number, "continue": should_continue},
        )
        return {
            "rebuttal_round": next_round,
            "debate_continue": should_continue,
        }

    @staticmethod
    def _route_debate(state: ResearchState) -> list[str]:
        if state.get("debate_continue", False):
            return ["bull_rebuttal", "bear_rebuttal"]
        return ["judge"]

    def _research_judge(
        self,
        state: ResearchState,
        runtime: Runtime[RunContext],
    ) -> dict[str, Any]:
        node = "judge.research"
        self._start_node(runtime, node, measure=False)
        check_cancelled(runtime.context)
        context = self._role_context(
            state=state,
            runtime=runtime,
            node=node,
            title="Research Judge",
            objective=(
                "Rule on every DebateAgenda issue, explain why specific findings "
                "are accepted or rejected, preserve unresolved questions, and "
                "form a preliminary research conclusion."
            ),
            stage="research_judge",
            artifacts={
                "cases": state.get("cases", {}),
                "debate_agenda": state.get("debate_agenda", {}),
                "rebuttals": state.get("rebuttals", []),
            },
            report_mode="full",
            include_memory=True,
            instructions=(
                "Rule on every agenda issue even when the correct ruling "
                "is unresolved. Refer to analyst findings in natural language, "
                "cite relevant evidence refs, and do not expose internal claim "
                "or section IDs."
            ),
        )
        with self.metrics.phase(
            f"{node}.write",
            event_writer=runtime.stream_writer,
        ):
            written = write_research_markdown(
                self.deep_llm,
                prompt=context.prompt,
                node=f"{node}.write",
                allowed_evidence_refs=_state_evidence_refs(state),
                output_language=state["output_language"],
                invoke_config={
                    "callbacks": [self.metrics],
                    "metadata": {"research_node": f"{node}.write"},
                },
            )
        with self.metrics.phase(
            f"{node}.audit",
            event_writer=runtime.stream_writer,
        ):
            output = invoke_judge_draft(
                self.deep_serializer_llm,
                markdown=written.markdown,
                state=state,
                node=f"{node}.audit",
                event_writer=runtime.stream_writer,
                memory=runtime.context.memory,
            )
        draft = output.value
        self._write_artifact(
            runtime,
            node=node,
            stage="judge",
            role="research_judge",
            content=draft,
            generation_method=output.generation_method,
            prompt_version="research-judge-v6-readable",
        )
        self._finish_node(
            runtime,
            node,
            {
                "rating": (
                    draft.preliminary_rating.value if draft.preliminary_rating is not None else None
                ),
                "confidence": draft.confidence,
            },
            measure=False,
        )
        return {
            "judge_draft": draft.model_dump(mode="json"),
            "warnings": [
                *_structured_recovery_warnings(node, output),
                *_research_markdown_warnings(written),
            ],
        }

    def _create_risk_node(self, spec: RoleSpec):
        def risk_node(
            state: ResearchState,
            runtime: Runtime[RunContext],
        ) -> dict[str, Any]:
            node = "risk.review" if spec.key == "integrated" else f"risk.{spec.key}"
            self._start_node(runtime, node, measure=False)
            check_cancelled(runtime.context)
            llm = self._deliberation_llm(spec)
            context = self._role_context(
                state=state,
                runtime=runtime,
                node=node,
                title=spec.label,
                objective=spec.objective,
                stage="risk_review",
                artifacts={
                    "judge_draft": state.get("judge_draft", {}),
                    "debate_agenda": state.get("debate_agenda", {}),
                },
                report_mode="risk",
                instructions=(
                    "Identify explicit findings, their mechanisms and "
                    "severity, the analyst conclusions they challenge, concrete "
                    "invalidation paths, and changes the final committee should "
                    "make. Do not expose internal claim or section IDs. Unknowns "
                    "are not automatically downside."
                ),
            )
            with self.metrics.phase(
                f"{node}.write",
                event_writer=runtime.stream_writer,
            ):
                written = write_research_markdown(
                    llm,
                    prompt=context.prompt,
                    node=f"{node}.write",
                    allowed_evidence_refs=_state_evidence_refs(state),
                    output_language=state["output_language"],
                    invoke_config={
                        "callbacks": [self.metrics],
                        "metadata": {"research_node": f"{node}.write"},
                    },
                )
            with self.metrics.phase(
                f"{node}.audit",
                event_writer=runtime.stream_writer,
            ):
                output = invoke_risk_review(
                    self._deliberation_serializer_llm(spec),
                    role=spec.key,
                    markdown=written.markdown,
                    state=state,
                    node=f"{node}.audit",
                    event_writer=runtime.stream_writer,
                )
            review = output.value
            self._write_artifact(
                runtime,
                node=node,
                stage="risk",
                role=spec.key,
                content=review,
                generation_method=output.generation_method,
                prompt_version=f"risk-review-{spec.key}-v6-readable",
            )
            self._finish_node(
                runtime,
                node,
                {"challenged_issues": len(review.challenged_issue_ids)},
                measure=False,
            )
            return {
                "risk_reviews": {spec.key: review.model_dump(mode="json")},
                "warnings": [
                    *_structured_recovery_warnings(node, output),
                    *_research_markdown_warnings(written),
                ],
            }

        return risk_node

    def _create_final_committee(self, *, fast: bool):
        def final_node(
            state: ResearchState,
            runtime: Runtime[RunContext],
        ) -> dict[str, Any]:
            node = "committee.final"
            self._start_node(runtime, node, measure=False)
            check_cancelled(runtime.context)
            if fast:
                objective = (
                    "Directly synthesize the typed analyst reports into one "
                    "research-only decision. Explicitly preserve uncertainty and "
                    "cite evidence refs."
                )
                artifacts: dict[str, Any] = {}
            else:
                objective = (
                    "Produce the final research decision after considering the "
                    "judge draft and every risk review. State which risk findings "
                    "were retained, modified, or rejected and why."
                )
                artifacts = {
                    "judge_draft": state.get("judge_draft", {}),
                    "risk_reviews": state.get("risk_reviews", {}),
                    "unresolved_issue_ids": RoleContextBuilder(state).unresolved_issue_ids(),
                }
            context = self._role_context(
                state=state,
                runtime=runtime,
                node=node,
                title="Final Research Committee",
                objective=objective,
                stage="final_committee",
                artifacts=artifacts,
                report_mode="full",
                evidence_refs=RoleContextBuilder(state).primary_evidence_refs(),
                include_memory=True,
                instructions=(
                    "For every risk-review role, add at least one structured "
                    "risk_review_adjustment. Keep the rating, thesis, scenarios, "
                    "risks, and invalidation conditions internally consistent."
                    if not fast
                    else (
                        "Preserve material uncertainty and ground the direct "
                        "decision in primary analyst evidence."
                    )
                ),
            )
            with self.metrics.phase(
                f"{node}.reason",
                event_writer=runtime.stream_writer,
            ):
                written = write_research_markdown(
                    self.deep_llm,
                    prompt=(
                        context.prompt + "\n\nForm a complete decision synthesis brief. "
                        "Cover the rating rationale, thesis, three scenarios, "
                        "catalysts, risks, invalidation, unresolved questions, "
                        "time horizon, any valuation or market-reference "
                        "calculations, and risk-review dispositions."
                    ),
                    node=f"{node}.reason",
                    allowed_evidence_refs=_state_evidence_refs(state),
                    output_language=state["output_language"],
                    invoke_config={
                        "callbacks": [self.metrics],
                        "metadata": {"research_node": f"{node}.reason"},
                    },
                )
            output = invoke_research_decision(
                self.deep_serializer_llm,
                prompt=(
                    "Map the completed decision synthesis brief to the "
                    "strict final decision contract. Preserve its research "
                    "judgment; do not add unsupported facts. Use localized "
                    "reader-facing labels for market reference levels.\n\n"
                    f"DECISION SYNTHESIS BRIEF:\n{written.markdown}\n\n"
                    "ALLOWED EVIDENCE REFS:\n"
                    f"{json.dumps(_state_evidence_refs(state))}\n\n"
                    "ALLOWED MEMORY REFS:\n"
                    f"{json.dumps(tuple(runtime.context.memory.refs))}\n\n"
                    "REQUIRED RISK REVIEW ROLES:\n"
                    f"{json.dumps(tuple(state.get('risk_reviews', {})))}"
                ),
                state=state,
                node=f"{node}.serialize",
                event_writer=runtime.stream_writer,
                memory=runtime.context.memory,
                require_risk_adjustments=not fast,
                output_language=state["output_language"],
                metrics=self.metrics,
            )
            decision = output.value
            self._write_artifact(
                runtime,
                node=node,
                stage="decision",
                role="final_committee",
                content=decision,
                generation_method=output.generation_method,
                prompt_version="final-committee-v8-language-contract",
            )
            self._finish_node(
                runtime,
                node,
                {
                    "rating": decision.rating.value,
                    "confidence": decision.confidence,
                },
                measure=False,
            )
            return {
                "final_decision": decision.model_dump(mode="json"),
                "numeric_audit": (
                    output.numeric_audit.model_dump(mode="json")
                    if output.numeric_audit is not None
                    else None
                ),
                "warnings": [
                    *_structured_recovery_warnings(node, output),
                    *_research_markdown_warnings(written),
                    *(warning.model_dump(mode="json") for warning in output.warnings),
                ],
            }

        return final_node

    def _deliberation_llm(self, spec: RoleSpec | None = None) -> Any:
        """Resolve role models according to the quality-first profile contract."""

        return self.deep_llm if self._deliberation_model_tier(spec) == "deep" else self.quick_llm

    def _deliberation_serializer_llm(
        self,
        spec: RoleSpec | None = None,
    ) -> Any:
        return (
            self.deep_serializer_llm
            if self._deliberation_model_tier(spec) == "deep"
            else self.quick_serializer_llm
        )

    def _deliberation_model_tier(
        self,
        spec: RoleSpec | None = None,
    ) -> Literal["quick", "deep"]:
        """Keep reasoning and serializer clients on the same profile tier."""

        if self.profile is RunProfile.DEEP:
            return "deep"
        return spec.model if spec is not None else "quick"

    def _role_context(
        self,
        *,
        state: ResearchState,
        runtime: Runtime[RunContext],
        node: str,
        title: str,
        objective: str,
        stage: str,
        artifacts: dict[str, Any] | None = None,
        report_mode: Literal["none", "full", "risk"] = "none",
        evidence_refs: tuple[str, ...] = (),
        include_memory: bool = False,
        instructions: str = "",
    ) -> RoleContext:
        context = RoleContextBuilder(
            state,
            memory=runtime.context.memory if include_memory else None,
        ).build(
            title=title,
            objective=objective,
            stage=stage,
            artifacts=artifacts,
            report_mode=report_mode,
            evidence_refs=evidence_refs,
            include_memory=include_memory,
            instructions=instructions,
        )
        self._record_role_context(runtime, node=node, context=context)
        return context

    @staticmethod
    def _record_role_context(
        runtime: Runtime[RunContext],
        *,
        node: str,
        context: RoleContext,
    ) -> None:
        runtime.stream_writer(
            {
                "event_type": "node.context_prepared",
                "node": f"{node}.context",
                "payload": {
                    "inline_characters": context.inline_characters,
                    "catalog_items": context.catalog_items,
                    "catalog_tables": context.catalog_tables,
                    "reference_count": len(context.evidence_refs),
                    "table_summary_count": context.table_summary_count,
                    "lookup_count": 0,
                    "returned_rows": 0,
                },
            }
        )

    @staticmethod
    def _write_artifact(
        runtime: Runtime[RunContext],
        *,
        node: str,
        stage: str,
        role: str,
        content: ResearchArtifactContent,
        generation_method: ArtifactGenerationMethod,
        round: int = 0,
        prompt_version: str = "research-v1",
    ) -> None:
        runtime.context.artifact_writer(
            ResearchArtifactDraft(
                node=node,
                stage=stage,
                role=role,
                round=round,
                prompt_version=prompt_version,
                generation_method=generation_method,
                content=content,
            )
        )

    def _start_node(
        self,
        runtime: Runtime[RunContext],
        node: str,
        *,
        measure: bool = True,
    ) -> None:
        if measure:
            self.metrics.node_started(node)
            runtime.stream_writer(
                {
                    "event_type": "phase.started",
                    "node": node,
                    "payload": {},
                }
            )
        runtime.stream_writer(
            {
                "event_type": "node.started",
                "node": node,
                "payload": {},
            }
        )

    def _finish_node(
        self,
        runtime: Runtime[RunContext],
        node: str,
        payload: dict[str, Any],
        *,
        measure: bool = True,
    ) -> None:
        if measure:
            elapsed = self.metrics.node_finished(node)
            runtime.stream_writer(
                {
                    "event_type": "phase.completed",
                    "node": node,
                    "payload": {"wall_time_seconds": elapsed},
                }
            )
        runtime.stream_writer(
            {
                "event_type": "node.completed",
                "node": node,
                "payload": payload,
            }
        )


def _analyst_route(state: AgentState) -> str:
    messages = state.get("messages", [])
    if messages and getattr(messages[-1], "tool_calls", None):
        return "tools"
    return "done"


def _collect_evidence(
    messages: Iterable[Any],
    _narrative: str,
    *,
    requested_date: date,
    analyst: str,
    prefetched_blocks: Iterable[dict[str, Any]] = (),
) -> list[EvidenceItem]:
    items: dict[str, EvidenceItem] = {}
    content_groups: dict[
        tuple[str, EvidenceTemporalScope],
        list[ProvenanceRecord],
    ] = {}
    content_metadata: dict[
        tuple[str, EvidenceTemporalScope],
        dict[str, Any],
    ] = {}
    content_order: list[tuple[str, EvidenceTemporalScope]] = []
    empty_payloads: list[tuple[tuple[ProvenanceRecord, ...], EvidenceTemporalScope]] = []

    def collect_payload(
        records: Iterable[ProvenanceRecord],
        content: str | None,
        temporal_scope: str | EvidenceTemporalScope | None = None,
        provenance_metadata: dict[str, Any] | None = None,
    ) -> None:
        records = tuple(dict.fromkeys(records))
        if not records:
            return
        scope = _coerce_temporal_scope(temporal_scope, records)
        if content:
            key = (content, scope)
            if key not in content_groups:
                content_groups[key] = []
                content_order.append(key)
                content_metadata[key] = dict(provenance_metadata or {})
            elif provenance_metadata:
                content_metadata[key].update(provenance_metadata)
            existing = content_groups[key]
            for record in records:
                if record not in existing:
                    existing.append(record)
        else:
            empty_payloads.append((records, scope))

    tool_messages = [message for message in messages if isinstance(message, ToolMessage)]
    for message in tool_messages:
        artifact = getattr(message, "artifact", None)
        if is_evidence_tool_artifact(artifact):
            records = artifact_records(artifact)
            if not records:
                records = (
                    ProvenanceRecord(
                        evidence=str(
                            artifact.get("evidence_type")
                            or getattr(message, "name", None)
                            or f"{analyst} tool"
                        ),
                        source="unknown",
                        requested=requested_date.isoformat(),
                        effective="unknown",
                        timing="no auditable source metadata captured",
                    ),
                )
            collect_payload(
                records,
                str(artifact["source_content"]).strip() or None,
                artifact.get("temporal_scope"),
                {
                    "dataset_id": artifact.get("dataset_id"),
                    "analytical_views": artifact.get("analytical_views", {}),
                },
            )
            continue
        content = message.content if isinstance(message.content, str) else str(message.content)
        spans = extract_evidence_spans(content)
        if spans:
            for span in spans:
                records = list(span.records)
                if not records:
                    records = [
                        ProvenanceRecord(
                            evidence=getattr(message, "name", None) or f"{analyst} tool",
                            source="unknown",
                            requested=requested_date.isoformat(),
                            effective="unknown",
                            timing=(
                                f"{span.temporal_scope} span without auditable source metadata"
                            ),
                        )
                    ]
                collect_payload(
                    records,
                    span.content,
                    span.temporal_scope,
                )
            continue
        records = extract_provenance(content)
        if not records:
            records = [
                ProvenanceRecord(
                    evidence=getattr(message, "name", None) or f"{analyst} tool",
                    source="unknown",
                    requested=requested_date.isoformat(),
                    effective="unknown",
                    timing="no auditable source metadata captured",
                )
            ]
        collect_payload(
            records,
            strip_provenance_markers(content).strip() or None,
        )

    for block in prefetched_blocks:
        raw_records = block.get("records", [])
        records = []
        for raw in raw_records:
            try:
                records.append(ProvenanceRecord(**raw))
            except (TypeError, ValueError):
                continue
        collect_payload(
            records,
            block.get("content"),
            block.get("temporal_scope"),
        )

    for content, scope in content_order:
        item = _evidence_from_records(
            content_groups[(content, scope)],
            requested_date=requested_date,
            content=content,
            temporal_scope=scope,
            provenance_metadata=content_metadata[(content, scope)],
        )
        items[item.ref] = item
    for records, scope in empty_payloads:
        item = _evidence_from_records(
            records,
            requested_date=requested_date,
            content=None,
            temporal_scope=scope,
        )
        items[item.ref] = item
    if not items:
        item = _evidence_from_record(
            ProvenanceRecord(
                evidence=f"{analyst} analyst evidence",
                source="unknown",
                requested=requested_date.isoformat(),
                effective="unknown",
                timing="no auditable source metadata captured",
            ),
            requested_date=requested_date,
            content=None,
        )
        items[item.ref] = item
    return list(items.values())


def _evidence_from_record(
    record: ProvenanceRecord,
    *,
    requested_date: date,
    content: str | None,
) -> EvidenceItem:
    return _evidence_from_records(
        (record,),
        requested_date=requested_date,
        content=content,
    )


def _evidence_from_records(
    records: Iterable[ProvenanceRecord],
    *,
    requested_date: date,
    content: str | None,
    temporal_scope: str | EvidenceTemporalScope | None = None,
    provenance_metadata: dict[str, Any] | None = None,
) -> EvidenceItem:
    records = tuple(records)
    if not records:
        raise ValueError("at least one provenance record is required")
    origin_pairs = tuple(
        _origin_from_record(
            record,
            requested_date=requested_date,
            temporal_scope=temporal_scope,
        )
        for record in records
    )
    origins = tuple(origin for origin, _future in origin_pairs)
    future_dated = any(future for _origin, future in origin_pairs)
    valid_effective_dates = [
        origin.effective_date
        for origin in origins
        if origin.effective_date is not None and origin.effective_date <= requested_date
    ]
    effective = max(valid_effective_dates) if valid_effective_dates else None
    all_unavailable = all(origin.quality is EvidenceQuality.UNAVAILABLE for origin in origins)
    all_reliable = all(
        origin.quality is EvidenceQuality.HIGH and not origin.fallback for origin in origins
    )
    temporal_scopes = tuple(dict.fromkeys(origin.temporal_scope for origin in origins))
    mixed_temporal_scope = len(temporal_scopes) > 1
    quality = (
        EvidenceQuality.UNAVAILABLE
        if all_unavailable
        else EvidenceQuality.HIGH
        if all_reliable
        else EvidenceQuality.LOW
    )
    sources = tuple(dict.fromkeys(origin.source for origin in origins))
    evidence_types = tuple(dict.fromkeys(origin.evidence_type for origin in origins))
    composite = len(origins) > 1
    source = sources[0] if not composite else "composite"
    evidence_type = evidence_types[0] if len(evidence_types) == 1 else "composite tool response"
    fallback = any(origin.fallback for origin in origins)
    provenance = (
        {
            "requested": origins[0].requested,
            "effective": origins[0].effective,
            "timing": origins[0].timing,
            "retrieved_at": origins[0].retrieved_at,
        }
        if not composite
        else {
            "composite": True,
            "origin_count": len(origins),
            "temporal_scopes": [scope.value for scope in temporal_scopes],
            "mixed_temporal_scope_unseparated": mixed_temporal_scope,
        }
    )
    provenance.update(provenance_metadata or {})
    return EvidenceItem.create(
        source=source,
        evidence_type=evidence_type,
        requested_date=requested_date,
        effective_date=effective,
        content=(None if future_dated or all_unavailable or mixed_temporal_scope else content),
        quality=quality,
        fallback=fallback,
        origins=origins,
        provenance=provenance,
    )


def _origin_from_record(
    record: ProvenanceRecord,
    *,
    requested_date: date,
    temporal_scope: str | EvidenceTemporalScope | None = None,
) -> tuple[EvidenceOrigin, bool]:
    effective = _last_date(record.effective)
    future_dated = bool(effective and effective > requested_date)
    timing = record.timing.casefold()
    scope = _coerce_temporal_scope(temporal_scope, (record,))
    unavailable = (
        any(
            token in timing
            for token in (
                "unavailable",
                "failed",
                "not requested",
                "not queried",
                "no usable data",
                "no auditable source metadata",
            )
        )
        or future_dated
    )
    degraded = scope is EvidenceTemporalScope.LIVE_ONLY or any(
        token in timing
        for token in (
            "fallback",
            "partial",
            "stale",
            "truncated",
            "non-point-in-time",
            "non-vintage",
        )
    )
    successful_empty = timing.startswith("available;") and (
        "; no " in timing or "contained no values" in timing
    )
    missing_effective = effective is None and not successful_empty
    quality = (
        EvidenceQuality.UNAVAILABLE
        if unavailable
        else EvidenceQuality.LOW
        if (degraded or missing_effective or record.source.casefold() in {"unknown", "—", ""})
        else EvidenceQuality.HIGH
    )
    display_timing = (
        f"{record.timing}; future-dated evidence withheld" if future_dated else record.timing
    )
    return (
        EvidenceOrigin(
            source=record.source or "unknown",
            evidence_type=record.evidence or "unknown evidence",
            requested=record.requested or "unknown",
            effective=record.effective or "unknown",
            effective_date=effective,
            timing=display_timing or "unknown",
            retrieved_at=record.retrieved_at,
            quality=quality,
            fallback="fallback" in timing,
            temporal_scope=scope,
        ),
        future_dated,
    )


def _coerce_temporal_scope(
    value: str | EvidenceTemporalScope | None,
    records: Iterable[ProvenanceRecord],
) -> EvidenceTemporalScope:
    if isinstance(value, EvidenceTemporalScope):
        return (
            EvidenceTemporalScope(temporal_scope_from_records(records))
            if value is EvidenceTemporalScope.UNKNOWN
            else value
        )
    raw = value or temporal_scope_from_records(records)
    if raw == EvidenceTemporalScope.UNKNOWN.value:
        raw = temporal_scope_from_records(records)
    try:
        return EvidenceTemporalScope(raw)
    except ValueError:
        return EvidenceTemporalScope.UNKNOWN


def _last_date(value: str | None) -> date | None:
    matches = _DATE_RE.findall(value or "")
    if not matches:
        return None
    try:
        return max(date.fromisoformat(raw) for raw in matches)
    except ValueError:
        return None


def _clean_narrative(value: str) -> str:
    return _CONTROL_COMMENT_RE.sub(
        "",
        strip_provenance_markers(value),
    ).strip()


def _structured_recovery_warnings(
    node: str,
    output: StructuredOutputResult[Any],
) -> list[dict[str, Any]]:
    if output.generation_method is ArtifactGenerationMethod.MARKDOWN_AUDIT_INCOMPLETE:
        if node.startswith("analyst."):
            return []
        warning = ResearchWarning(
            code="deliberation.audit_incomplete",
            message=(
                "The readable Markdown was preserved, but shallow navigation "
                "metadata could not be fully validated."
            ),
            source=node,
        )
        return [warning.model_dump(mode="json")]
    return []


def _state_evidence_refs(state: Mapping[str, Any]) -> tuple[str, ...]:
    bundle = EvidenceBundle.model_validate(state["evidence_bundle"])
    return tuple(item.ref for item in bundle.items)


def _research_markdown_warnings(output: Any) -> list[dict[str, Any]]:
    return [warning.model_dump(mode="json") for warning in output.warnings]
