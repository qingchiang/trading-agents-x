from __future__ import annotations

from tradingagents.agents.utils.agent_utils import build_instrument_context
from tradingagents.application.contracts import AnalysisRequest


def test_request_preserves_exchange_suffix() -> None:
    request = AnalysisRequest(ticker=" 7203.t ", analysis_date="2026-07-24")

    assert request.ticker == "7203.T"


def test_request_infers_mainland_exchange() -> None:
    assert (
        AnalysisRequest(ticker="600519", analysis_date="2026-07-24").ticker
        == "600519.SS"
    )
    assert (
        AnalysisRequest(ticker="000001", analysis_date="2026-07-24").ticker
        == "000001.SZ"
    )
    assert (
        AnalysisRequest(ticker="600519.SH", analysis_date="2026-07-24").ticker
        == "600519.SS"
    )


def test_build_instrument_context_mentions_exact_symbol() -> None:
    context = build_instrument_context("7203.T")

    assert "7203.T" in context
    assert "exchange suffix" in context
