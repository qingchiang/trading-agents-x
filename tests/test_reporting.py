"""Report parity: the shared writer produces the report tree for the CLI and the
programmatic API alike (#1037)."""

from types import SimpleNamespace

import pytest

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.reporting import write_report_tree


def _state():
    return {
        "market_report": "MKT",
        "news_report": "NEWS",
        "investment_debate_state": {"judge_decision": "RM PLAN"},
        "trader_investment_plan": "TRADE",
        "risk_debate_state": {"judge_decision": "PM DECISION"},
    }


def _complete_state():
    return {
        "market_report": "MKT",
        "sentiment_report": "SENTIMENT",
        "news_report": "NEWS",
        "fundamentals_report": "FUNDAMENTALS",
        "investment_debate_state": {
            "bull_history": "BULL",
            "bear_history": "BEAR",
            "judge_decision": "RM PLAN",
        },
        "trader_investment_plan": "TRADE",
        "risk_debate_state": {
            "aggressive_history": "AGGRESSIVE",
            "conservative_history": "CONSERVATIVE",
            "neutral_history": "NEUTRAL",
            "judge_decision": "PM DECISION",
        },
    }


@pytest.mark.unit
def test_write_report_tree_creates_files(tmp_path):
    out = write_report_tree(_state(), "AAPL", tmp_path)
    assert out.name == "complete_report.md"
    assert (tmp_path / "1_analysts" / "market.md").read_text() == "MKT"
    assert (tmp_path / "1_analysts" / "news.md").read_text() == "NEWS"
    assert (tmp_path / "2_research" / "manager.md").read_text() == "RM PLAN"
    assert (tmp_path / "3_trading" / "trader.md").read_text() == "TRADE"
    assert (tmp_path / "5_portfolio" / "decision.md").read_text() == "PM DECISION"
    complete = out.read_text()
    assert "Trading Analysis Report: AAPL" in complete
    assert "MKT" in complete and "PM DECISION" in complete


@pytest.mark.unit
def test_complete_report_separates_every_section_and_role(tmp_path):
    complete = write_report_tree(_complete_state(), "AAPL", tmp_path).read_text()

    section_titles = (
        "I. Analyst Team Reports",
        "II. Research Team Decision",
        "III. Trading Team Plan",
        "IV. Risk Management Team Decision",
        "V. Portfolio Manager Decision",
    )
    role_titles = (
        "Market Analyst",
        "Sentiment Analyst",
        "News Analyst",
        "Fundamentals Analyst",
        "Bull Researcher",
        "Bear Researcher",
        "Research Manager",
        "Trader",
        "Aggressive Analyst",
        "Conservative Analyst",
        "Neutral Analyst",
        "Portfolio Manager",
    )

    for title in section_titles:
        assert f"---\n\n## {title}\n\n" in complete
    for title in role_titles:
        assert f"---\n\n### {title}\n\n" in complete


@pytest.mark.unit
def test_save_reports_explicit_path(tmp_path):
    # Unbound: with an explicit save_path, the method doesn't touch self/config.
    out = TradingAgentsGraph.save_reports(None, _state(), "AAPL", save_path=tmp_path)
    assert (tmp_path / "complete_report.md").exists()
    assert out == tmp_path / "complete_report.md"


@pytest.mark.unit
def test_save_reports_defaults_under_results_dir(tmp_path):
    mock_self = SimpleNamespace(config={"results_dir": str(tmp_path)})
    out = TradingAgentsGraph.save_reports(mock_self, _state(), "AAPL")
    assert out.exists()
    assert out.parent.parent.name == "reports"  # results_dir/reports/AAPL_<stamp>/...
    assert out.parent.name.startswith("AAPL_")
