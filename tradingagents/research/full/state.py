"""Full workflow state reducers and completed graph output."""

import operator
from dataclasses import dataclass
from typing import Annotated, Any, TypedDict

from tradingagents.domain.decision import ResearchDecision
from tradingagents.domain.evidence import EvidenceBundle
from tradingagents.domain.reports import AnalystReport, ResearchWarning


def _merge_dicts(
    left: dict[str, Any] | None,
    right: dict[str, Any] | None,
) -> dict[str, Any]:
    return {**(left or {}), **(right or {})}


class ResearchState(TypedDict, total=False):
    ticker: str
    analysis_date: str
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
    decision_brief: dict[str, Any]
    final_decision: dict[str, Any]
    rebuttal_round: int
    debate_continue: bool
    warnings: Annotated[list[dict[str, Any]], operator.add]


@dataclass(frozen=True)
class GraphExecution:
    state: dict[str, Any]
    evidence: EvidenceBundle
    reports: dict[str, AnalystReport]
    decision: ResearchDecision
    warnings: tuple[ResearchWarning, ...] = ()
