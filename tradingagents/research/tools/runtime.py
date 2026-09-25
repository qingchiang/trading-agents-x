"""Validated runtime access for model-facing data tools."""

from __future__ import annotations

from collections.abc import Mapping

from langgraph.prebuilt import ToolRuntime

from tradingagents.research.runtime import RunContext
from tradingagents.research.state import AgentState

AnalysisToolRuntime = ToolRuntime[RunContext, AgentState]


def analysis_cutoff(
    runtime: AnalysisToolRuntime,
    injected_date: str,
) -> str:
    """Validate the state-injected cutoff against the immutable runtime request."""
    context = runtime.context
    request = getattr(context, "request", None)
    dataflow_config = getattr(context, "dataflow_config", None)
    analysis_date = getattr(request, "analysis_date", None)
    if analysis_date is None or not isinstance(dataflow_config, Mapping):
        raise ValueError("data tools require an explicit analysis runtime context")
    expected = analysis_date.isoformat()
    if injected_date != expected:
        raise ValueError("tool analysis date does not match immutable runtime context")
    return expected
