"""The evidence graph exposes the verified snapshot in the market tool node."""

from unittest.mock import MagicMock

from tradingagents.application.contracts import RunProfile
from tradingagents.graph.research_graph import ResearchGraph


def test_market_toolnode_can_execute_verified_snapshot():
    llm = MagicMock()
    graph = ResearchGraph(
        quick_llm=llm,
        deep_llm=llm,
        profile=RunProfile.FAST,
        selected_analysts=("market",),
    )

    tool_node = graph._analyst_subgraphs["market"].nodes["tools"].bound

    assert set(tool_node.tools_by_name) == {
        "get_stock_data",
        "get_indicators",
        "get_verified_market_snapshot",
    }
