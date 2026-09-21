"""Breaking-release boundaries for the independent application architecture."""

import tomllib
from pathlib import Path

import pytest

import tradingagents
from tradingagents.client import TradingAgents
from tradingagents.data import config as dataflow_config
from tradingagents.domain.common import RunProfile
from tradingagents.domain.decision import ResearchDecision
from tradingagents.domain.runs import AnalysisRequest, AnalysisResult

REMOVED_DIRECT_DEPENDENCIES = (
    "backtrader",
    "langchain-experimental",
    "pytz",
    "redis",
    "setuptools",
    "tqdm",
    "typing-extensions",
)


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
