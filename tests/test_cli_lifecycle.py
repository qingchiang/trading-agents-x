from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from typer.testing import CliRunner

import cli.main as m
from tradingagents.graph.analyst_execution import (
    AnalystWallTimeTracker,
    build_analyst_execution_plan,
)


def _completed_state():
    return {
        "messages": [],
        "company_of_interest": "NVDA",
        "trade_date": "2026-07-22",
        "market_report": "market done",
        "sentiment_report": "",
        "news_report": "",
        "fundamentals_report": "",
        "investment_debate_state": {
            "bull_history": "bull",
            "bear_history": "bear",
            "judge_decision": "research decision",
        },
        "investment_plan": "research decision",
        "trader_investment_plan": "trade plan",
        "risk_debate_state": {
            "aggressive_history": "aggressive",
            "conservative_history": "conservative",
            "neutral_history": "neutral",
            "judge_decision": "portfolio decision",
        },
        "final_trade_decision": "Rating: Hold",
    }


def test_resume_snapshot_hydrates_without_replaying_messages_or_tool_calls():
    buffer = m.MessageBuffer()
    buffer.init_for_analysis(["market", "news"])
    tracker = AnalystWallTimeTracker(
        build_analyst_execution_plan(["market", "news"])
    )
    tracker.mark_started("market", started_at=10.0)

    old_human = HumanMessage(content="NVDA", id="old-human")
    old_call = AIMessage(
        content="old tool request",
        id="old-ai",
        tool_calls=[
            {"name": "lookup", "args": {"ticker": "NVDA"}, "id": "call-1"}
        ],
    )
    restored = {
        "messages": [old_human, old_call],
        "market_report": "restored market report",
        "news_report": "",
    }

    m._process_analysis_chunk(
        buffer,
        restored,
        tracker,
        restored_step=3,
        ticker="NVDA",
        analysis_date="2026-07-22",
    )

    assert [kind for _, kind, _ in buffer.messages] == ["System"]
    assert "checkpoint step 3" in buffer.messages[-1][2]
    assert list(buffer.tool_calls) == []
    assert buffer.report_sections["market_report"] == "restored market report"
    assert buffer.agent_status["Market Analyst"] == "resumed"
    assert buffer.agent_status["News Analyst"] == "in_progress"
    assert buffer.get_completed_reports_count() == 1
    assert tracker.format_summary() == "Analyst wall time: Market resumed"

    new_message = AIMessage(content="new work", id="new-ai")
    continued = {
        "messages": [old_human, old_call, new_message],
        "market_report": "restored market report",
        "news_report": "new news report",
    }
    m._process_analysis_chunk(buffer, continued, tracker)
    m._process_analysis_chunk(buffer, continued, tracker)

    agent_messages = [content for _, kind, content in buffer.messages if kind == "Agent"]
    assert agent_messages == ["new work"]
    assert list(buffer.tool_calls) == []
    assert buffer.report_sections["news_report"] == "new news report"
    assert buffer.agent_status["Market Analyst"] == "resumed"
    assert "Market resumed" in tracker.format_summary()
    assert "News " in tracker.format_summary()


class _FakeLive:
    def __init__(self, *_args, **_kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_run_analysis_uses_propagate_lifecycle(monkeypatch, tmp_path):
    selections = {
        "ticker": "NVDA",
        "asset_type": "stock",
        "analysis_date": "2026-07-22",
        "analysts": [SimpleNamespace(value="market")],
        "research_depth": 1,
        "shallow_thinker": "quick",
        "deep_thinker": "deep",
        "backend_url": None,
        "llm_provider": "openai",
        "quick_reasoning_effort": None,
        "deep_reasoning_effort": None,
        "google_thinking_level": None,
        "openai_reasoning_effort": None,
        "anthropic_effort": None,
        "output_language": "English",
    }
    config = dict(
        m.DEFAULT_CONFIG,
        results_dir=str(tmp_path / "results"),
        data_cache_dir=str(tmp_path / "cache"),
        memory_log_path=str(tmp_path / "memory.md"),
    )
    graph = MagicMock()
    final_state = _completed_state()
    trackers = []

    real_tracker_type = m.AnalystWallTimeTracker

    def make_tracker(plan):
        tracker = real_tracker_type(plan)
        trackers.append(tracker)
        return tracker

    def propagate(ticker, date, *, asset_type, on_chunk):
        assert trackers[0]._started_at == {}
        initial_state = dict(final_state, market_report="")
        on_chunk(initial_state, None)
        assert "market" in trackers[0]._started_at
        on_chunk(final_state, None)
        return final_state, "Hold"

    graph.propagate.side_effect = propagate
    graph_type = MagicMock(return_value=graph)
    monkeypatch.setattr(m, "DEFAULT_CONFIG", config)
    monkeypatch.setattr(m, "get_user_selections", lambda: selections)
    monkeypatch.setattr(m, "TradingAgentsGraph", graph_type)
    monkeypatch.setattr(m, "AnalystWallTimeTracker", make_tracker)
    monkeypatch.setattr(m, "message_buffer", m.MessageBuffer())
    monkeypatch.setattr(m, "Live", _FakeLive)
    monkeypatch.setattr(m, "update_display", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(m, "console", MagicMock())
    monkeypatch.setattr(m.typer, "prompt", lambda *_args, **_kwargs: "N")

    m.run_analysis(checkpoint=True)

    graph_type.assert_called_once()
    assert graph_type.call_args.kwargs["debug"] is False
    assert graph_type.call_args.kwargs["config"]["checkpoint_enabled"] is True
    graph.propagate.assert_called_once()
    assert graph.propagate.call_args.args == ("NVDA", "2026-07-22")
    assert graph.propagate.call_args.kwargs["asset_type"] == "stock"
    assert callable(graph.propagate.call_args.kwargs["on_chunk"])
    graph.graph.stream.assert_not_called()


@pytest.mark.parametrize(
    ("flag", "expected"),
    [(["--checkpoint"], True), (["--no-checkpoint"], False)],
)
def test_root_checkpoint_flags_reach_run_analysis(monkeypatch, flag, expected):
    called = []
    monkeypatch.setattr(m, "run_analysis", lambda checkpoint=None: called.append(checkpoint))

    result = CliRunner().invoke(m.app, flag)

    assert result.exit_code == 0
    assert called == [expected]


def test_root_clear_checkpoints_deletes_before_analysis(monkeypatch, tmp_path):
    from tradingagents.graph import checkpointer

    events = []
    config = dict(m.DEFAULT_CONFIG, data_cache_dir=str(tmp_path))
    monkeypatch.setattr(m, "DEFAULT_CONFIG", config)
    monkeypatch.setattr(
        checkpointer,
        "clear_all_checkpoints",
        lambda data_dir: events.append(("clear", data_dir)) or 2,
    )
    monkeypatch.setattr(
        m,
        "run_analysis",
        lambda checkpoint=None: events.append(("run", checkpoint)),
    )

    result = CliRunner().invoke(m.app, ["--clear-checkpoints"])

    assert result.exit_code == 0
    assert events == [("clear", str(tmp_path)), ("run", None)]
    assert "Cleared 2 checkpoint(s)" in result.output
