"""Run-context bridge for graph-only tools."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager, nullcontext

from langgraph.prebuilt import ToolRuntime

from tradingagents.agents.utils.agent_states import AgentState
from tradingagents.application.runtime import RunContext
from tradingagents.dataflows.config import use_config

AnalysisToolRuntime = ToolRuntime[RunContext, AgentState]


def evidence_route_kwargs(runtime: AnalysisToolRuntime) -> dict[str, object]:
    """Return provenance options bound to the run's frozen frontier."""

    route_kwargs: dict[str, object] = {"_provenance": True}
    information_frontier = getattr(
        runtime.context,
        "information_frontier",
        None,
    )
    if information_frontier is not None:
        route_kwargs["information_frontier"] = information_frontier.isoformat()
    return route_kwargs


@contextmanager
def tool_runtime_scope(
    runtime: AnalysisToolRuntime,
    injected_date: str,
) -> Iterator[str]:
    """Validate the state-injected cutoff and bind this tool's run config."""
    context = runtime.context
    request = getattr(context, "request", None)
    dataflow_config = getattr(context, "dataflow_config", None)
    analysis_date = getattr(request, "analysis_date", None)
    if analysis_date is None or not isinstance(dataflow_config, Mapping):
        with nullcontext():
            yield injected_date
        return
    expected = analysis_date.isoformat()
    if injected_date != expected:
        raise ValueError(
            "tool analysis date does not match immutable runtime context"
        )
    with use_config(dict(dataflow_config)):
        yield expected
