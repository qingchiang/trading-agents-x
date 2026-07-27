"""Tests for deterministic point-in-time instrument identity."""

import unittest
from unittest.mock import patch

import pytest

from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    get_instrument_context_from_state,
    resolve_instrument_identity,
)
from tradingagents.dataflows import instrument_identity as identity_dataflow


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
if __name__ == "__main__":
    unittest.main()
