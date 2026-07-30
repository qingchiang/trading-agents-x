from __future__ import annotations

from uuid import uuid4

import pytest
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


def _deepseek_result(
    *,
    cache_hit: int,
    cache_miss: int,
    output_tokens: int,
    reasoning_tokens: int,
) -> LLMResult:
    return LLMResult(
        generations=[
            [
                ChatGeneration(
                    message=AIMessage(
                        content="fixture",
                        usage_metadata={
                            "input_tokens": cache_hit + cache_miss,
                            "output_tokens": output_tokens,
                            "total_tokens": cache_hit
                            + cache_miss
                            + output_tokens,
                        },
                        response_metadata={
                            "token_usage": {
                                "prompt_tokens": cache_hit + cache_miss,
                                "completion_tokens": output_tokens,
                                "prompt_cache_hit_tokens": cache_hit,
                                "prompt_cache_miss_tokens": cache_miss,
                                "completion_tokens_details": {
                                    "reasoning_tokens": reasoning_tokens,
                                },
                            }
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
        metadata={"research_node": "case.bear"},
    )
    metrics.on_llm_error(RuntimeError("fixture"), run_id=reused_after_error)
    metrics.on_chat_model_start(
        {},
        [[]],
        run_id=reused_after_error,
        metadata={"research_node": "case.bull"},
    )

    snapshot = metrics.snapshot()

    assert snapshot.node_metrics["committee.final"].llm_calls == 2
    assert snapshot.node_metrics["committee.final"].input_tokens == 100
    assert snapshot.node_metrics["case.bear"].llm_calls == 1
    assert snapshot.node_metrics["case.bull"].llm_calls == 1


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


def test_deepseek_cache_and_reasoning_usage_are_attributed_with_coverage() -> None:
    metrics = MetricsCallback()
    detailed_run = uuid4()
    basic_run = uuid4()

    metrics.on_chat_model_start(
        {},
        [[]],
        run_id=detailed_run,
        metadata={"research_node": "analyst.market"},
    )
    metrics.on_llm_end(
        _deepseek_result(
            cache_hit=95_744,
            cache_miss=1_928_396,
            output_tokens=140_794,
            reasoning_tokens=120_000,
        ),
        run_id=detailed_run,
    )
    metrics.on_chat_model_start(
        {},
        [[]],
        run_id=basic_run,
        metadata={"research_node": "analyst.market"},
    )
    metrics.on_llm_end(_result(100, 10), run_id=basic_run)

    snapshot = metrics.snapshot()
    node = snapshot.node_metrics["analyst.market"]

    assert snapshot.input_tokens == 95_744 + 1_928_396 + 100
    assert snapshot.cache_hit_input_tokens == 95_744
    assert snapshot.cache_miss_input_tokens == 1_928_396
    assert snapshot.reasoning_output_tokens == 120_000
    assert snapshot.detailed_usage_calls == 1
    assert node.llm_calls == 2
    assert node.detailed_usage_calls == 1


def test_metrics_reject_removed_duplicate_node_wall_times() -> None:
    with pytest.raises(ValueError, match="node_wall_times"):
        RunMetrics.model_validate(
            {
                "llm_calls": 2,
                "input_tokens": 100,
                "node_wall_times": {"analyst.market": 1.25},
            }
        )
