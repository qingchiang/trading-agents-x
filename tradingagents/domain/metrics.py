"""Aggregation of persisted execution metrics."""

from tradingagents.domain.runs import NodeMetrics, RunMetrics


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
