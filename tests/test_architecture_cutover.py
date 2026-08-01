"""Breaking-release boundaries for the independent application architecture."""

from importlib.util import find_spec
from pathlib import Path

import pytest

import tradingagents
from tradingagents import graph
from tradingagents.dataflows import config as dataflow_config

REMOVED_RUNTIME_MODULES = (
    "tradingagents.graph.trading_graph",
    "tradingagents.graph.setup",
    "tradingagents.graph.propagation",
    "tradingagents.graph.conditional_logic",
    "tradingagents.graph.checkpointer",
    "tradingagents.graph.reflection",
    "tradingagents.graph.analyst_execution",
    "tradingagents.reporting",
    "tradingagents.agents.utils.memory",
    "tradingagents.agents.trader.trader",
    "tradingagents.agents.managers.portfolio_manager",
    "tradingagents.agents.managers.research_manager",
    "tradingagents.agents.researchers.bull_researcher",
    "tradingagents.agents.researchers.bear_researcher",
    "tradingagents.agents.risk_mgmt.aggressive_debator",
    "tradingagents.agents.risk_mgmt.neutral_debator",
    "tradingagents.agents.risk_mgmt.conservative_debator",
)

REMOVED_DIRECT_DEPENDENCIES = (
    "backtrader",
    "langchain-experimental",
    "pytz",
    "redis",
    "setuptools",
    "tqdm",
)


@pytest.mark.unit
@pytest.mark.parametrize("module_name", REMOVED_RUNTIME_MODULES)
def test_legacy_runtime_module_is_not_shipped(module_name):
    assert find_spec(module_name) is None


@pytest.mark.unit
def test_public_api_exposes_typed_application_contract():
    assert tradingagents.__all__ == [
        "AnalysisRequest",
        "AnalysisResult",
        "AnalystClaimType",
        "AnalystReport",
        "CalculationRecord",
        "ClaimImportance",
        "DebateAgenda",
        "DebateImportance",
        "DebateIssue",
        "DecisionNumericAuditAppendix",
        "EvidenceBundle",
        "EvidenceItem",
        "EvidenceTable",
        "EvidenceTableCell",
        "EvidenceTableColumn",
        "EvidenceTableRow",
        "EvidenceValueLocator",
        "JudgeDraft",
        "KeyClaim",
        "MarketReferenceBasis",
        "MarketReferenceLevel",
        "MeasurementKind",
        "NumericAuditStatus",
        "NumericAuditAppendixStatus",
        "NumericAuditComponentType",
        "NumericAuditOmission",
        "NumericAuditPhase",
        "NumericAuditSnapshot",
        "RebuttalReview",
        "ReportAuditStatus",
        "ReportLanguage",
        "ReportSection",
        "ResearchArtifact",
        "ResearchCase",
        "ResearchDecision",
        "ResearchScenario",
        "ResearchScenarioKind",
        "ResearchWarning",
        "RiskReview",
        "RiskReviewAdjustment",
        "RiskReviewDisposition",
        "RunProfile",
        "TableDataType",
        "TradingAgents",
        "ValuationAssessment",
        "AuditedRangeEndpoint",
        "NumericTemporalBasis",
        "ScenarioReferenceRange",
        "ScenarioReferenceCategory",
        "StructuredRecoveryNotice",
        "__version__",
    ]
    assert "TradingAgentsGraph" not in tradingagents.__all__


@pytest.mark.unit
def test_graph_package_only_exports_research_graph_contracts():
    assert graph.__all__ == ["GraphExecution", "ResearchGraph"]


@pytest.mark.unit
def test_mutable_global_config_compatibility_api_is_removed():
    assert not hasattr(dataflow_config, "set_config")
    assert not hasattr(dataflow_config, "_config")


@pytest.mark.unit
@pytest.mark.parametrize("dependency", REMOVED_DIRECT_DEPENDENCIES)
def test_unused_dependency_is_not_declared(dependency):
    pyproject = (
        Path(__file__).resolve().parents[1] / "pyproject.toml"
    ).read_text(encoding="utf-8")
    runtime_dependencies = pyproject.split("dependencies = [", 1)[1].split("]", 1)[0]
    assert f'"{dependency}>=' not in runtime_dependencies
