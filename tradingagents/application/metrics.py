"""Thread-safe callback metrics independent from any presentation layer."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from time import monotonic
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage
from langchain_core.outputs import LLMResult

from .contracts import NodeMetrics, RunMetrics


@dataclass
class _NodeAccumulator:
    llm_calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


class MetricsCallback(BaseCallbackHandler):
    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self._started_at = monotonic()
        self._node_started: dict[str, float] = {}
        self._node_times: dict[str, float] = {}
        self._node_usage: dict[str, _NodeAccumulator] = {}
        self._llm_runs: dict[Any, str] = {}
        self._tool_runs: dict[Any, str] = {}
        self.llm_calls = 0
        self.tool_calls = 0
        self.input_tokens = 0
        self.output_tokens = 0

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        **kwargs: Any,
    ) -> None:
        self._llm_started(kwargs)

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[Any]],
        **kwargs: Any,
    ) -> None:
        self._llm_started(kwargs)

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        node = self._finish_callback(self._llm_runs, kwargs)
        input_tokens, output_tokens = _token_usage(response)
        if not input_tokens and not output_tokens:
            return
        with self._lock:
            self.input_tokens += input_tokens
            self.output_tokens += output_tokens
            usage = self._node_usage.setdefault(node, _NodeAccumulator())
            usage.input_tokens += input_tokens
            usage.output_tokens += output_tokens

    def on_llm_error(self, error: BaseException, **kwargs: Any) -> None:
        self._finish_callback(self._llm_runs, kwargs)

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        **kwargs: Any,
    ) -> None:
        node = _callback_node(kwargs)
        run_id = kwargs.get("run_id")
        with self._lock:
            if run_id is not None and run_id in self._tool_runs:
                return
            if run_id is not None:
                self._tool_runs[run_id] = node
            self.tool_calls += 1
            self._node_usage.setdefault(
                node,
                _NodeAccumulator(),
            ).tool_calls += 1

    def on_tool_end(self, output: Any, **kwargs: Any) -> None:
        self._finish_callback(self._tool_runs, kwargs)

    def on_tool_error(self, error: BaseException, **kwargs: Any) -> None:
        self._finish_callback(self._tool_runs, kwargs)

    def _llm_started(self, kwargs: dict[str, Any]) -> None:
        node = _callback_node(kwargs)
        run_id = kwargs.get("run_id")
        with self._lock:
            if run_id is not None and run_id in self._llm_runs:
                return
            if run_id is not None:
                self._llm_runs[run_id] = node
            self.llm_calls += 1
            self._node_usage.setdefault(
                node,
                _NodeAccumulator(),
            ).llm_calls += 1

    def _finish_callback(
        self,
        active: dict[Any, str],
        kwargs: dict[str, Any],
    ) -> str:
        run_id = kwargs.get("run_id")
        with self._lock:
            node = active.pop(run_id, None) if run_id is not None else None
        return node or _callback_node(kwargs)

    def node_started(self, node: str) -> None:
        with self._lock:
            self._node_started.setdefault(node, monotonic())

    def node_finished(self, node: str) -> None:
        with self._lock:
            started = self._node_started.pop(node, None)
            if started is not None:
                self._node_times[node] = self._node_times.get(node, 0.0) + (
                    monotonic() - started
                )

    def snapshot(self) -> RunMetrics:
        now = monotonic()
        with self._lock:
            node_times = dict(self._node_times)
            for node, started in self._node_started.items():
                node_times[node] = node_times.get(node, 0.0) + max(
                    0.0,
                    now - started,
                )
            nodes = set(node_times) | set(self._node_usage)
            node_metrics = {}
            for node in sorted(nodes):
                usage = self._node_usage.get(node, _NodeAccumulator())
                node_metrics[node] = NodeMetrics(
                    llm_calls=usage.llm_calls,
                    tool_calls=usage.tool_calls,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    wall_time_seconds=node_times.get(node, 0.0),
                )
            return RunMetrics(
                llm_calls=self.llm_calls,
                tool_calls=self.tool_calls,
                input_tokens=self.input_tokens,
                output_tokens=self.output_tokens,
                wall_time_seconds=max(0.0, now - self._started_at),
                node_wall_times=node_times,
                node_metrics=node_metrics,
            )


def _callback_node(kwargs: dict[str, Any]) -> str:
    metadata = kwargs.get("metadata")
    if isinstance(metadata, dict):
        node = metadata.get("research_node") or metadata.get(
            "langgraph_node"
        )
        if node:
            return str(node)
    return "unattributed"


def _token_usage(response: LLMResult) -> tuple[int, int]:
    try:
        generation = response.generations[0][0]
    except (IndexError, TypeError):
        generation = None
    message = getattr(generation, "message", None)
    usage = (
        message.usage_metadata
        if isinstance(message, AIMessage)
        and getattr(message, "usage_metadata", None)
        else None
    )
    if usage:
        return (
            int(usage.get("input_tokens", 0)),
            int(usage.get("output_tokens", 0)),
        )
    token_usage = (response.llm_output or {}).get("token_usage", {})
    if not isinstance(token_usage, dict):
        return 0, 0
    return (
        int(
            token_usage.get(
                "input_tokens",
                token_usage.get("prompt_tokens", 0),
            )
        ),
        int(
            token_usage.get(
                "output_tokens",
                token_usage.get("completion_tokens", 0),
            )
        ),
    )
