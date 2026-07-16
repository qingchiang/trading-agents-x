"""Tests for deterministic instrument-identity resolution (#814) and the
context-anchored message placeholder (#888)."""

import unittest
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage

from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    create_msg_delete,
    get_instrument_context_from_state,
    resolve_instrument_identity,
)
from tradingagents.dataflows import instrument_identity as identity_dataflow
from tradingagents.graph.trading_graph import TradingAgentsGraph


@pytest.mark.unit
class ResolveInstrumentIdentityTests(unittest.TestCase):
    def setUp(self):
        resolve_instrument_identity.cache_clear()

    def test_resolves_company_metadata_from_yfinance(self):
        with patch.object(identity_dataflow.yf, "Ticker") as mock:
            mock.return_value.info = {
                "longName": "TOTO LTD.",
                "shortName": "TOTO",
                "sector": "Industrials",
                "industry": "Building Products & Equipment",
                "exchange": "PNK",
                "quoteType": "EQUITY",
            }
            identity = resolve_instrument_identity("totdy")
        mock.assert_called_once_with("TOTDY")
        self.assertEqual(identity["company_name"], "TOTO LTD.")
        self.assertEqual(identity["sector"], "Industrials")
        self.assertEqual(identity["industry"], "Building Products & Equipment")
        self.assertEqual(identity["exchange"], "PNK")

    def test_falls_back_to_short_name(self):
        with patch.object(identity_dataflow.yf, "Ticker") as mock:
            mock.return_value.info = {"shortName": "TOTO", "sector": "Industrials"}
            identity = resolve_instrument_identity("TOTDY")
        self.assertEqual(identity["company_name"], "TOTO")

    def test_skips_placeholder_values(self):
        with patch.object(identity_dataflow.yf, "Ticker") as mock:
            mock.return_value.info = {"longName": "  ", "sector": "None", "industry": "n/a"}
            identity = resolve_instrument_identity("TOTDY")
        self.assertEqual(identity, {})

    def test_fails_open_on_exception(self):
        with patch.object(
            identity_dataflow.yf, "Ticker", side_effect=RuntimeError("rate limited")
        ):
            self.assertEqual(resolve_instrument_identity("TOTDY"), {})

    def test_result_is_cached(self):
        with patch.object(identity_dataflow.yf, "Ticker") as mock:
            mock.return_value.info = {"longName": "TOTO LTD."}
            first = resolve_instrument_identity("TOTDY")
            second = resolve_instrument_identity("TOTDY")
        mock.assert_called_once()  # second call served from cache
        self.assertEqual(first, second)

    def test_historical_identity_uses_exact_search_without_info(self):
        search = type("SearchResult", (), {
            "quotes": [
                {"symbol": "TOTO", "longName": "Wrong symbol"},
                {
                    "symbol": "TOTDY",
                    "longname": "TOTO LTD.",
                    "shortname": "TOTO",
                    "exchange": "PNK",
                    "quoteType": "EQUITY",
                    "sector": "Current sector must not leak",
                },
            ]
        })()
        with patch.object(identity_dataflow.yf, "Search", return_value=search) as search_mock, \
                patch.object(identity_dataflow.yf, "Ticker") as ticker_mock:
            identity = resolve_instrument_identity("totdy", "2020-01-02")

        ticker_mock.assert_not_called()
        search_mock.assert_called_once()
        self.assertEqual(identity["company_name"], "TOTO LTD.")
        self.assertEqual(identity["short_name"], "TOTO")
        self.assertEqual(identity["exchange"], "PNK")
        self.assertNotIn("sector", identity)

    def test_historical_search_failure_does_not_fall_back_to_info(self):
        with patch.object(
            identity_dataflow.yf, "Search", side_effect=RuntimeError("search failed")
        ), patch.object(identity_dataflow.yf, "Ticker") as ticker_mock:
            identity = resolve_instrument_identity("TOTDY", "2020-01-02")
        ticker_mock.assert_not_called()
        self.assertEqual(identity, {})

    def test_cache_collapses_historical_dates_but_separates_live_mode(self):
        search = type("SearchResult", (), {
            "quotes": [{"symbol": "TOTDY", "longName": "Historical TOTO"}]
        })()
        with patch.object(identity_dataflow.yf, "Search", return_value=search) as search_mock, \
                patch.object(identity_dataflow.yf, "Ticker") as ticker_mock:
            ticker_mock.return_value.info = {"longName": "Live TOTO"}
            first = resolve_instrument_identity("TOTDY", "2020-01-02")
            second = resolve_instrument_identity("TOTDY", "2021-02-03")
            live = resolve_instrument_identity("TOTDY")

        search_mock.assert_called_once()
        ticker_mock.assert_called_once_with("TOTDY")
        self.assertEqual(first, second)
        self.assertNotEqual(first, live)


@pytest.mark.unit
class BuildInstrumentContextTests(unittest.TestCase):
    def test_mentions_exact_symbol_without_identity(self):
        context = build_instrument_context("7203.T")
        self.assertIn("7203.T", context)
        self.assertIn("exchange suffix", context)
        self.assertNotIn("Resolved identity", context)

    def test_injects_resolved_identity(self):
        context = build_instrument_context(
            "TOTDY", "stock",
            {
                "company_name": "TOTO LTD.",
                "sector": "Industrials",
                "industry": "Building Products & Equipment",
                "exchange": "PNK",
            },
        )
        self.assertIn("Company: TOTO LTD.", context)
        self.assertIn("Industrials / Building Products & Equipment", context)
        self.assertIn("Exchange: PNK", context)
        self.assertIn("Do not substitute a different company", context)

    def test_crypto_uses_name_label_and_keeps_hint(self):
        context = build_instrument_context(
            "BTC-USD", "crypto", {"company_name": "Bitcoin USD"}
        )
        self.assertIn("Name: Bitcoin USD", context)
        self.assertIn("crypto asset rather than a company", context)


@pytest.mark.unit
def test_graph_context_resolver_passes_analysis_date():
    with patch("tradingagents.graph.trading_graph.resolve_instrument_identity") as resolver:
        resolver.return_value = {"company_name": "NVIDIA Corporation"}
        context = TradingAgentsGraph.resolve_instrument_context(
            None, "NVDA", "stock", "2020-01-02"
        )
    resolver.assert_called_once_with("NVDA", "2020-01-02")
    assert "NVIDIA Corporation" in context


@pytest.mark.unit
def test_graph_startup_forwards_trade_date_to_context_resolver():
    graph = object.__new__(TradingAgentsGraph)
    graph.memory_log = MagicMock()
    graph.memory_log.get_past_context.return_value = ""
    graph.resolve_instrument_context = MagicMock(return_value="IDENTITY CONTEXT")
    graph.propagator = MagicMock()
    graph.propagator.create_initial_state.return_value = {"initial": True}
    graph.propagator.get_graph_args.return_value = {}
    graph.config = {"checkpoint_enabled": False}
    graph.debug = False
    graph.graph = MagicMock()
    graph.graph.invoke.return_value = {"final_trade_decision": "HOLD"}
    graph._log_state = MagicMock()
    graph.process_signal = MagicMock(return_value="HOLD")

    graph._run_graph("NVDA", "2020-01-02")

    graph.resolve_instrument_context.assert_called_once_with(
        "NVDA", "stock", "2020-01-02"
    )


@pytest.mark.unit
def test_cli_context_resolver_forwards_selected_analysis_date():
    from cli.main import _resolve_cli_instrument_context

    graph = MagicMock()
    graph.resolve_instrument_context.return_value = "IDENTITY CONTEXT"
    selections = {
        "ticker": "NVDA",
        "asset_type": "stock",
        "analysis_date": "2020-01-02",
    }

    context = _resolve_cli_instrument_context(graph, selections)

    assert context == "IDENTITY CONTEXT"
    graph.resolve_instrument_context.assert_called_once_with(
        "NVDA", "stock", "2020-01-02"
    )


@pytest.mark.unit
class GetInstrumentContextFromStateTests(unittest.TestCase):
    def test_prefers_precomputed_context(self):
        state = {"company_of_interest": "TOTDY", "instrument_context": "PRECOMPUTED"}
        self.assertEqual(get_instrument_context_from_state(state), "PRECOMPUTED")

    def test_fallback_is_network_free_ticker_only(self):
        # No instrument_context and no yfinance call — must not hit the network.
        with patch.object(identity_dataflow.yf, "Ticker") as mock:
            context = get_instrument_context_from_state(
                {"company_of_interest": "NVDA", "asset_type": "stock"}
            )
        mock.assert_not_called()
        self.assertIn("NVDA", context)

    def test_fallback_respects_asset_type(self):
        context = get_instrument_context_from_state(
            {"company_of_interest": "BTC-USD", "asset_type": "crypto"}
        )
        self.assertIn("crypto asset", context)


@pytest.mark.unit
class ContextAnchoredPlaceholderTests(unittest.TestCase):
    """#888 — the message-clear placeholder must not be a bare 'Continue'."""

    def _run(self, state_extra):
        state = {
            "messages": [
                HumanMessage(content="old", id="h1"),
                AIMessage(content="reply", id="a1"),
            ],
            **state_extra,
        }
        return create_msg_delete()(state)

    def test_placeholder_is_not_bare_continue(self):
        result = self._run(
            {"company_of_interest": "EC", "asset_type": "stock", "trade_date": "2026-05-28"}
        )
        placeholder = result["messages"][-1]
        self.assertIsInstance(placeholder, HumanMessage)
        self.assertNotEqual(placeholder.content.strip(), "Continue")

    def test_placeholder_carries_resolved_identity(self):
        result = self._run(
            {
                "company_of_interest": "EC",
                "instrument_context": "The instrument to analyze is `EC`. Resolved identity: Company: Ecopetrol.",
                "trade_date": "2026-05-28",
            }
        )
        content = result["messages"][-1].content
        self.assertIn("Ecopetrol", content)
        self.assertIn("2026-05-28", content)

    def test_old_messages_are_removed(self):
        result = self._run({"company_of_interest": "EC", "trade_date": "2026-05-28"})
        removals = [m for m in result["messages"] if isinstance(m, RemoveMessage)]
        humans = [m for m in result["messages"] if isinstance(m, HumanMessage)]
        self.assertEqual(len(removals), 2)
        self.assertEqual(len(humans), 1)

    def test_safe_defaults_when_state_minimal(self):
        result = create_msg_delete()({"messages": [], "company_of_interest": "EC"})
        placeholder = result["messages"][-1]
        self.assertNotEqual(placeholder.content.strip(), "Continue")
        self.assertIn("EC", placeholder.content)


if __name__ == "__main__":
    unittest.main()
