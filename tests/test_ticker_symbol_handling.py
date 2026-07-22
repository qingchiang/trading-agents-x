import unittest
from unittest.mock import MagicMock

import pytest

from cli.utils import normalize_ticker_symbol
from tradingagents.agents.utils.agent_utils import build_instrument_context
from tradingagents.graph.trading_graph import TradingAgentsGraph


@pytest.mark.unit
class TickerSymbolHandlingTests(unittest.TestCase):
    def test_normalize_ticker_symbol_preserves_exchange_suffix(self):
        self.assertEqual(normalize_ticker_symbol(" cnc.to "), "CNC.TO")

    def test_normalize_ticker_symbol_infers_mainland_exchange(self):
        self.assertEqual(normalize_ticker_symbol("600519"), "600519.SS")
        self.assertEqual(normalize_ticker_symbol("000001"), "000001.SZ")
        self.assertEqual(normalize_ticker_symbol("600519.SH"), "600519.SS")

    def test_build_instrument_context_mentions_exact_symbol(self):
        context = build_instrument_context("7203.T")
        self.assertIn("7203.T", context)
        self.assertIn("exchange suffix", context)

    def test_programmatic_graph_entry_normalizes_bare_a_share(self):
        graph = MagicMock()
        graph.config = {"checkpoint_enabled": False}
        graph._checkpointer_ctx = None
        graph._run_graph.return_value = ("STATE", "DECISION")

        result = TradingAgentsGraph.propagate(graph, "600519", "2026-07-18")

        self.assertEqual(result, ("STATE", "DECISION"))
        self.assertEqual(graph.ticker, "600519.SS")
        graph._resolve_pending_entries.assert_called_once_with("600519.SS")
        graph._run_graph.assert_called_once_with(
            "600519.SS", "2026-07-18", asset_type="stock"
        )

    def test_programmatic_graph_entry_normalizes_shanghai_alias(self):
        graph = MagicMock()
        graph.config = {"checkpoint_enabled": False}
        graph._checkpointer_ctx = None
        graph._run_graph.return_value = ("STATE", "DECISION")

        result = TradingAgentsGraph.propagate(graph, "600519.SH", "2026-07-18")

        self.assertEqual(result, ("STATE", "DECISION"))
        self.assertEqual(graph.ticker, "600519.SS")
        graph._resolve_pending_entries.assert_called_once_with("600519.SS")
        graph._run_graph.assert_called_once_with(
            "600519.SS", "2026-07-18", asset_type="stock"
        )

    def test_single_get_ticker_no_shadow(self):
        # Regression: cli/main.py had a duplicate get_ticker with an empty
        # questionary prompt (rendered as a bare "?") that shadowed the
        # descriptive one in cli/utils. Keep a single canonical definition.
        import cli.main
        import cli.utils
        self.assertIs(cli.main.get_ticker, cli.utils.get_ticker)


if __name__ == "__main__":
    unittest.main()
