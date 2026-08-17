"""Breaking-release boundaries for the independent application architecture."""

import tomllib
from importlib.util import find_spec
from pathlib import Path

import pytest

import tradingagents
from tradingagents import graph
from tradingagents.application.contracts import (
    AnalysisRequest,
    AnalysisResult,
    ResearchDecision,
    RunProfile,
)
from tradingagents.client import TradingAgents
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
    "tradingagents.application.legacy",
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
    "typing-extensions",
)


def _find_module_spec(module_name: str):
    """Resolve nested modules without asking importlib to load a missing parent."""
    parent_name, separator, _ = module_name.rpartition(".")
    if separator and _find_module_spec(parent_name) is None:
        return None
    return find_spec(module_name)


@pytest.mark.unit
@pytest.mark.parametrize("module_name", REMOVED_RUNTIME_MODULES)
def test_legacy_runtime_module_is_not_shipped(module_name):
    assert _find_module_spec(module_name) is None


@pytest.mark.unit
def test_public_api_exposes_typed_application_contract():
    assert tradingagents.__all__ == [
        "AnalysisRequest",
        "AnalysisResult",
        "ArtifactGenerationObservation",
        "ResearchDecision",
        "RunProfile",
        "TradingAgents",
        "__version__",
    ]
    assert "TradingAgentsGraph" not in tradingagents.__all__
    assert not hasattr(tradingagents, "EvidenceBundle")
    assert not hasattr(tradingagents, "DebateAgenda")
    assert not hasattr(tradingagents, "NumericAuditSnapshot")
    assert tradingagents.AnalysisRequest is AnalysisRequest
    assert tradingagents.AnalysisResult is AnalysisResult
    assert tradingagents.ResearchDecision is ResearchDecision
    assert tradingagents.RunProfile is RunProfile
    assert tradingagents.TradingAgents is TradingAgents


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


@pytest.mark.unit
def test_ci_builds_wheel_from_an_isolated_sdist():
    workflow = (
        Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml"
    ).read_text(encoding="utf-8")
    assert "uv build --out-dir wheelhouse" in workflow
    assert "uv build --wheel" not in workflow


@pytest.mark.unit
def test_python_support_contract_is_closed_at_312_through_314():
    root = Path(__file__).resolve().parents[1]
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    workflow = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert pyproject["project"]["requires-python"] == ">=3.12,<3.15"
    assert pyproject["tool"]["ruff"]["target-version"] == "py312"
    assert 'python-version: ["3.12", "3.13", "3.14"]' in workflow
    assert 'python-version: ["3.10", "3.11", "3.12", "3.13"]' not in workflow
