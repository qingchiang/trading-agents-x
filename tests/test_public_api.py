"""The official top-level Python entry point exposes its current typed contract."""

import pytest

import tradingagents
from tradingagents.client import TradingAgents
from tradingagents.domain.common import RunProfile
from tradingagents.domain.decision import ResearchDecision
from tradingagents.domain.runs import AnalysisRequest, AnalysisResult


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
    assert tradingagents.AnalysisRequest is AnalysisRequest
    assert tradingagents.AnalysisResult is AnalysisResult
    assert tradingagents.ResearchDecision is ResearchDecision
    assert tradingagents.RunProfile is RunProfile
    assert tradingagents.TradingAgents is TradingAgents
