"""Thread-safe callback metrics independent from any presentation layer."""

from __future__ import annotations

import threading
from time import monotonic
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage
from langchain_core.outputs import LLMResult

from .contracts import RunMetrics


class MetricsCallback(BaseCallbackHandler):
    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self._started_at = monotonic()
        self._node_started: dict[str, float] = {}
        self._node_times: dict[str, float] = {}
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
        with self._lock:
            self.llm_calls += 1

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[Any]],
        **kwargs: Any,
    ) -> None:
        with self._lock:
            self.llm_calls += 1

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        try:
            generation = response.generations[0][0]
        except (IndexError, TypeError):
            return
        message = getattr(generation, "message", None)
        usage = (
            message.usage_metadata
            if isinstance(message, AIMessage)
            and getattr(message, "usage_metadata", None)
            else None
        )
        if usage:
            with self._lock:
                self.input_tokens += int(usage.get("input_tokens", 0))
                self.output_tokens += int(usage.get("output_tokens", 0))

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        **kwargs: Any,
    ) -> None:
        with self._lock:
            self.tool_calls += 1

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
        with self._lock:
            return RunMetrics(
                llm_calls=self.llm_calls,
                tool_calls=self.tool_calls,
                input_tokens=self.input_tokens,
                output_tokens=self.output_tokens,
                wall_time_seconds=max(0.0, monotonic() - self._started_at),
                node_wall_times=dict(self._node_times),
            )
