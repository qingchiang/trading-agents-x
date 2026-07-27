"""Immutable runtime context injected into LangGraph nodes and tools."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .contracts import AnalysisRequest
from .settings import RunSettings


@dataclass(frozen=True)
class RunContext:
    run_id: str
    request: AnalysisRequest
    settings: RunSettings
    dataflow_config: Mapping[str, Any]
    past_context: str
    instrument_context: str
    cancel_requested: Callable[[], bool]


class RunCancelled(RuntimeError):
    """Raised at a node boundary after a cooperative cancellation request."""


def check_cancelled(context: RunContext) -> None:
    if context.cancel_requested():
        raise RunCancelled(f"run {context.run_id} was cancelled")
