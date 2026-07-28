from __future__ import annotations

from uuid import uuid4

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from tradingagents.application.contracts import RunMetrics
from tradingagents.application.metrics import MetricsCallback


def _result(input_tokens: int, output_tokens: int) -> LLMResult:
    return LLMResult(
        generations=[
            [
                ChatGeneration(
                    message=AIMessage(
                        content="fixture",
                        usage_metadata={
                            "input_tokens": input_tokens,
                            "output_tokens": output_tokens,
                            "total_tokens": input_tokens + output_tokens,
                        },
                    )
                )
            ]
        ]
    )


def test_parallel_callbacks_are_attributed_by_run_id() -> None:
    metrics = MetricsCallback()
    market_run = uuid4()
    news_run = uuid4()
    tool_run = uuid4()

    metrics.on_chat_model_start(
        {},
        [[]],
        run_id=market_run,
        metadata={"research_node": "analyst.market"},
    )
    metrics.on_chat_model_start(
        {},
        [[]],
        run_id=news_run,
        metadata={"research_node": "analyst.news"},
    )
    metrics.on_tool_start(
        {},
        "{}",
        run_id=tool_run,
        metadata={"research_node": "analyst.news"},
    )
    metrics.on_llm_end(_result(200, 20), run_id=news_run)
    metrics.on_llm_end(_result(100, 10), run_id=market_run)
    metrics.on_tool_end("done", run_id=tool_run)

    snapshot = metrics.snapshot()

    assert snapshot.llm_calls == 2
    assert snapshot.tool_calls == 1
    assert snapshot.input_tokens == 300
    assert snapshot.output_tokens == 30
    assert snapshot.node_metrics["analyst.market"].llm_calls == 1
    assert snapshot.node_metrics["analyst.market"].input_tokens == 100
    assert snapshot.node_metrics["analyst.news"].llm_calls == 1
    assert snapshot.node_metrics["analyst.news"].tool_calls == 1
    assert snapshot.node_metrics["analyst.news"].input_tokens == 200


def test_recovery_calls_stay_on_the_same_node_and_errors_release_run_ids() -> None:
    metrics = MetricsCallback()
    first = uuid4()
    recovery = uuid4()
    reused_after_error = uuid4()

    for run_id in (first, recovery):
        metrics.on_chat_model_start(
            {},
            [[]],
            run_id=run_id,
            metadata={"langgraph_node": "committee.final"},
        )
        metrics.on_llm_end(_result(50, 5), run_id=run_id)

    metrics.on_chat_model_start(
        {},
        [[]],
        run_id=reused_after_error,
        metadata={"research_node": "review.bear"},
    )
    metrics.on_llm_error(RuntimeError("fixture"), run_id=reused_after_error)
    metrics.on_chat_model_start(
        {},
        [[]],
        run_id=reused_after_error,
        metadata={"research_node": "review.bull"},
    )

    snapshot = metrics.snapshot()

    assert snapshot.node_metrics["committee.final"].llm_calls == 2
    assert snapshot.node_metrics["committee.final"].input_tokens == 100
    assert snapshot.node_metrics["review.bear"].llm_calls == 1
    assert snapshot.node_metrics["review.bull"].llm_calls == 1


def test_missing_metadata_and_provider_usage_are_explicit() -> None:
    metrics = MetricsCallback()
    run_id = uuid4()

    metrics.on_llm_start({}, ["prompt"], run_id=run_id)
    metrics.on_llm_end(
        LLMResult(generations=[], llm_output={}),
        run_id=run_id,
    )

    snapshot = metrics.snapshot()

    assert snapshot.llm_calls == 1
    assert snapshot.input_tokens == 0
    assert snapshot.node_metrics["unattributed"].llm_calls == 1
    assert snapshot.node_metrics["unattributed"].input_tokens == 0


def test_legacy_metrics_parse_without_node_usage() -> None:
    metrics = RunMetrics.model_validate(
        {
            "llm_calls": 2,
            "input_tokens": 100,
            "node_wall_times": {"analyst.market": 1.25},
        }
    )

    assert metrics.node_metrics == {}
    assert metrics.node_wall_times == {"analyst.market": 1.25}
