"""Thread-safe callback metrics independent from any presentation layer."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from time import monotonic
from typing import Any
from uuid import UUID, uuid4

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage
from langchain_core.outputs import LLMResult

from .contracts import NodeMetrics, RunMetrics


def merge_run_metrics(*metrics: RunMetrics) -> RunMetrics:
    """Add independently observed execution segments without losing node detail."""

    node_names = {node for snapshot in metrics for node in snapshot.node_metrics}

    def node_values(node: str) -> list[NodeMetrics]:
        return [
            snapshot.node_metrics[node]
            for snapshot in metrics
            if node in snapshot.node_metrics
        ]

    node_metrics = {
        node: NodeMetrics(
            llm_calls=sum(value.llm_calls for value in node_values(node)),
            tool_calls=sum(value.tool_calls for value in node_values(node)),
            input_tokens=sum(value.input_tokens for value in node_values(node)),
            output_tokens=sum(value.output_tokens for value in node_values(node)),
            cache_hit_input_tokens=sum(
                value.cache_hit_input_tokens for value in node_values(node)
            ),
            cache_miss_input_tokens=sum(
                value.cache_miss_input_tokens for value in node_values(node)
            ),
            reasoning_output_tokens=sum(
                value.reasoning_output_tokens for value in node_values(node)
            ),
            detailed_usage_calls=sum(
                value.detailed_usage_calls for value in node_values(node)
            ),
            wall_time_seconds=sum(
                value.wall_time_seconds for value in node_values(node)
            ),
        )
        for node in sorted(node_names)
    }
    return RunMetrics(
        llm_calls=sum(snapshot.llm_calls for snapshot in metrics),
        tool_calls=sum(snapshot.tool_calls for snapshot in metrics),
        input_tokens=sum(snapshot.input_tokens for snapshot in metrics),
        output_tokens=sum(snapshot.output_tokens for snapshot in metrics),
        cache_hit_input_tokens=sum(
            snapshot.cache_hit_input_tokens for snapshot in metrics
        ),
        cache_miss_input_tokens=sum(
            snapshot.cache_miss_input_tokens for snapshot in metrics
        ),
        reasoning_output_tokens=sum(
            snapshot.reasoning_output_tokens for snapshot in metrics
        ),
        detailed_usage_calls=sum(
            snapshot.detailed_usage_calls for snapshot in metrics
        ),
        wall_time_seconds=sum(
            snapshot.wall_time_seconds for snapshot in metrics
        ),
        node_metrics=node_metrics,
    )


@dataclass
class _NodeAccumulator:
    llm_calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_hit_input_tokens: int = 0
    cache_miss_input_tokens: int = 0
    reasoning_output_tokens: int = 0
    detailed_usage_calls: int = 0


@dataclass(frozen=True)
class _TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_hit_input_tokens: int = 0
    cache_miss_input_tokens: int = 0
    reasoning_output_tokens: int = 0
    has_details: bool = False


class MetricsCallback(BaseCallbackHandler):
    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self._started_at = monotonic()
        self._active_spans: dict[UUID, tuple[str, float]] = {}
        self._legacy_spans: dict[str, list[UUID]] = {}
        self._node_times: dict[str, float] = {}
        self._node_usage: dict[str, _NodeAccumulator] = {}
        self._llm_runs: dict[Any, str] = {}
        self._tool_runs: dict[Any, str] = {}
        self.llm_calls = 0
        self.tool_calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_hit_input_tokens = 0
        self.cache_miss_input_tokens = 0
        self.reasoning_output_tokens = 0
        self.detailed_usage_calls = 0

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
        token_usage = _token_usage(response)
        if not (
            token_usage.input_tokens
            or token_usage.output_tokens
            or token_usage.has_details
        ):
            return
        with self._lock:
            self.input_tokens += token_usage.input_tokens
            self.output_tokens += token_usage.output_tokens
            self.cache_hit_input_tokens += token_usage.cache_hit_input_tokens
            self.cache_miss_input_tokens += token_usage.cache_miss_input_tokens
            self.reasoning_output_tokens += token_usage.reasoning_output_tokens
            self.detailed_usage_calls += int(token_usage.has_details)
            usage = self._node_usage.setdefault(node, _NodeAccumulator())
            usage.input_tokens += token_usage.input_tokens
            usage.output_tokens += token_usage.output_tokens
            usage.cache_hit_input_tokens += token_usage.cache_hit_input_tokens
            usage.cache_miss_input_tokens += token_usage.cache_miss_input_tokens
            usage.reasoning_output_tokens += token_usage.reasoning_output_tokens
            usage.detailed_usage_calls += int(token_usage.has_details)

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

    def start_span(self, node: str) -> UUID:
        """Start one independently tracked phase, including parallel repeats."""

        token = uuid4()
        with self._lock:
            self._active_spans[token] = (node, monotonic())
        return token

    def finish_span(self, token: UUID) -> float:
        """Finish a phase span and return its measured elapsed seconds."""

        with self._lock:
            active = self._active_spans.pop(token, None)
            if active is None:
                return 0.0
            node, started = active
            elapsed = max(0.0, monotonic() - started)
            self._node_times[node] = (
                self._node_times.get(node, 0.0) + elapsed
            )
            return elapsed

    def node_started(self, node: str) -> None:
        """Compatibility wrapper for graph nodes not yet split into phases."""

        token = self.start_span(node)
        with self._lock:
            self._legacy_spans.setdefault(node, []).append(token)

    def node_finished(self, node: str) -> float:
        """Finish the most recent compatibility span for one graph node."""

        with self._lock:
            tokens = self._legacy_spans.get(node)
            token = tokens.pop() if tokens else None
            if tokens == []:
                self._legacy_spans.pop(node, None)
        return self.finish_span(token) if token is not None else 0.0

    def record_tool_calls(self, node: str, count: int = 1) -> None:
        """Record deterministic local tool executions outside callback plumbing."""

        if count <= 0:
            return
        with self._lock:
            self.tool_calls += count
            self._node_usage.setdefault(
                node,
                _NodeAccumulator(),
            ).tool_calls += count

    @contextmanager
    def phase(
        self,
        node: str,
        *,
        event_writer: Callable[[dict[str, Any]], None] | None = None,
    ) -> Iterator[None]:
        """Measure one phase and optionally expose its durable timeline events."""

        token = self.start_span(node)
        if event_writer is not None:
            event_writer(
                {
                    "event_type": "phase.started",
                    "node": node,
                    "payload": {},
                }
            )
        try:
            yield
        finally:
            elapsed = self.finish_span(token)
            if event_writer is not None:
                event_writer(
                    {
                        "event_type": "phase.completed",
                        "node": node,
                        "payload": {"wall_time_seconds": elapsed},
                    }
                )

    def snapshot(self) -> RunMetrics:
        now = monotonic()
        with self._lock:
            node_times = dict(self._node_times)
            for node, started in self._active_spans.values():
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
                    cache_hit_input_tokens=usage.cache_hit_input_tokens,
                    cache_miss_input_tokens=usage.cache_miss_input_tokens,
                    reasoning_output_tokens=usage.reasoning_output_tokens,
                    detailed_usage_calls=usage.detailed_usage_calls,
                    wall_time_seconds=node_times.get(node, 0.0),
                )
            return RunMetrics(
                llm_calls=self.llm_calls,
                tool_calls=self.tool_calls,
                input_tokens=self.input_tokens,
                output_tokens=self.output_tokens,
                cache_hit_input_tokens=self.cache_hit_input_tokens,
                cache_miss_input_tokens=self.cache_miss_input_tokens,
                reasoning_output_tokens=self.reasoning_output_tokens,
                detailed_usage_calls=self.detailed_usage_calls,
                wall_time_seconds=max(0.0, now - self._started_at),
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


def _token_usage(response: LLMResult) -> _TokenUsage:
    try:
        generation = response.generations[0][0]
    except (IndexError, TypeError):
        generation = None
    message = getattr(generation, "message", None)
    normalized_usage = (
        message.usage_metadata
        if isinstance(message, AIMessage)
        and getattr(message, "usage_metadata", None)
        else None
    )
    response_metadata = (
        message.response_metadata
        if isinstance(message, AIMessage)
        and isinstance(getattr(message, "response_metadata", None), dict)
        else {}
    )
    raw_usage = response_metadata.get("token_usage")
    if not isinstance(raw_usage, dict):
        raw_usage = (response.llm_output or {}).get("token_usage", {})
    if not isinstance(raw_usage, dict):
        raw_usage = {}

    input_tokens = _first_int(
        normalized_usage,
        ("input_tokens",),
        fallback=_first_int(raw_usage, ("input_tokens", "prompt_tokens")),
    )
    output_tokens = _first_int(
        normalized_usage,
        ("output_tokens",),
        fallback=_first_int(raw_usage, ("output_tokens", "completion_tokens")),
    )
    hit, hit_present = _detail_int(
        raw_usage,
        ("prompt_cache_hit_tokens", "cache_hit_input_tokens"),
    )
    miss, miss_present = _detail_int(
        raw_usage,
        ("prompt_cache_miss_tokens", "cache_miss_input_tokens"),
    )
    reasoning, reasoning_present = _detail_int(
        raw_usage,
        ("reasoning_tokens",),
    )

    input_details = (
        normalized_usage.get("input_token_details", {})
        if isinstance(normalized_usage, dict)
        else {}
    )
    output_details = (
        normalized_usage.get("output_token_details", {})
        if isinstance(normalized_usage, dict)
        else {}
    )
    completion_details = raw_usage.get("completion_tokens_details", {})
    if not isinstance(input_details, dict):
        input_details = {}
    if not isinstance(output_details, dict):
        output_details = {}
    if not isinstance(completion_details, dict):
        completion_details = {}
    if not hit_present:
        hit, hit_present = _detail_int(
            input_details,
            ("cache_read", "cache_hit"),
        )
    if not reasoning_present:
        reasoning, reasoning_present = _detail_int(
            output_details,
            ("reasoning",),
        )
    if not reasoning_present:
        reasoning, reasoning_present = _detail_int(
            completion_details,
            ("reasoning_tokens",),
        )
    if hit_present and not miss_present and input_tokens >= hit:
        miss = input_tokens - hit
        miss_present = True
    if hit_present and miss_present:
        input_tokens = hit + miss

    return _TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_hit_input_tokens=hit,
        cache_miss_input_tokens=miss,
        reasoning_output_tokens=reasoning,
        has_details=hit_present or miss_present or reasoning_present,
    )


def _first_int(
    values: Any,
    keys: tuple[str, ...],
    *,
    fallback: int = 0,
) -> int:
    if not isinstance(values, dict):
        return fallback
    for key in keys:
        value = values.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return max(0, int(value))
    return fallback


def _detail_int(
    values: Any,
    keys: tuple[str, ...],
) -> tuple[int, bool]:
    if not isinstance(values, dict):
        return 0, False
    for key in keys:
        if key not in values:
            continue
        value = values[key]
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return max(0, int(value)), True
    return 0, False
