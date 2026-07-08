"""Suffix-based market routing: a ticker's exchange suffix (e.g. ``.T``) selects
a market-specific vendor chain via ``data_vendors_by_market``, while US /
unsuffixed tickers stay on the default chain untouched.

Stage 0 wiring for Japanese-market support: the mechanism is exercised here with
mocked vendors; the real jquants/edinet/boj implementations land later.
"""
import copy
import unittest
from unittest import mock

import pytest

import tradingagents.dataflows.config as config_module
import tradingagents.default_config as default_config
from tradingagents.dataflows import interface, market_context
from tradingagents.dataflows.config import set_config


def _reset_config():
    # Hard reset: set_config() merges, so empty DEFAULT dicts don't clear keys
    # leaked by other tests. Replace the global outright.
    config_module._config = copy.deepcopy(default_config.DEFAULT_CONFIG)


def _returns(value):
    def impl(symbol, *a, **k):
        return value
    return impl


@pytest.mark.unit
class MarketRoutingTests(unittest.TestCase):
    def setUp(self):
        _reset_config()

    def tearDown(self):
        _reset_config()

    def _route(self, method, vendors):
        return mock.patch.dict(interface.VENDOR_METHODS, {method: vendors}, clear=False)

    def test_jp_suffix_routes_to_market_vendor(self):
        # A ".T" ticker uses the market-specific vendor, not the default one.
        set_config({"data_vendors_by_market": {".T": {"core_stock_apis": "jquants"}}})
        yf = mock.Mock(side_effect=_returns("YF"))
        with self._route("get_stock_data", {"yfinance": yf, "jquants": _returns("JP")}):
            result = interface.route_to_vendor(
                "get_stock_data", "9984.T", "2026-01-01", "2026-01-10"
            )
        self.assertEqual(result, "JP")
        yf.assert_not_called()  # default vendor never tried for a routed market

    def test_us_ticker_unaffected_by_market_routes(self):
        # Configuring a ".T" route must not change US-ticker routing at all.
        set_config({"data_vendors_by_market": {".T": {"core_stock_apis": "jquants"}}})
        jp = mock.Mock(side_effect=_returns("JP"))
        with self._route("get_stock_data", {"yfinance": _returns("YF"), "jquants": jp}):
            result = interface.route_to_vendor(
                "get_stock_data", "AAPL", "2026-01-01", "2026-01-10"
            )
        self.assertEqual(result, "YF")
        jp.assert_not_called()

    def test_empty_market_map_preserves_default_routing(self):
        # With no configured routes, even a ".T" ticker stays on the default chain
        # (byte-for-byte pre-feature behavior). Set the dict directly rather than
        # via set_config, whose one-level-deep dict merge cannot clear keys.
        config_module._config["data_vendors_by_market"] = {}
        with self._route("get_stock_data", {"yfinance": _returns("YF"), "jquants": _returns("JP")}):
            result = interface.route_to_vendor(
                "get_stock_data", "9984.T", "2026-01-01", "2026-01-10"
            )
        self.assertEqual(result, "YF")

    def test_market_without_category_falls_back_to_default(self):
        # ".T" routes core_stock_apis only; fundamental_data must use the default.
        set_config({"data_vendors_by_market": {".T": {"core_stock_apis": "jquants"}}})
        with self._route("get_fundamentals", {"yfinance": _returns("YF_F"), "jquants": _returns("JP_F")}):
            result = interface.route_to_vendor("get_fundamentals", "9984.T", "2026-01-01")
        self.assertEqual(result, "YF_F")

    def test_tool_vendor_overrides_market_route(self):
        # Tool-level config wins over a market route (documented precedence).
        set_config({
            "data_vendors_by_market": {".T": {"core_stock_apis": "jquants"}},
            "tool_vendors": {"get_stock_data": "alpha_vantage"},
        })
        with self._route("get_stock_data", {"jquants": _returns("JP"), "alpha_vantage": _returns("AV")}):
            result = interface.route_to_vendor(
                "get_stock_data", "9984.T", "2026-01-01", "2026-01-10"
            )
        self.assertEqual(result, "AV")

    def test_macro_is_market_agnostic_even_if_routed(self):
        # Macro is cross-border: it stays on the default chain regardless of any
        # configured market route (analyzed across all markets at once). Pin the
        # default chain to fred so the assertion targets the market-route bypass,
        # not the real default chain (which now also lists boj as a macro vendor).
        set_config({
            "data_vendors": {"macro_data": "fred"},
            "data_vendors_by_market": {".T": {"macro_data": "boj"}},
        })
        boj = mock.Mock(side_effect=_returns("BOJ"))
        with self._route("get_macro_indicators", {"fred": _returns("FRED"), "boj": boj}):
            result = interface.route_to_vendor("get_macro_indicators", "cpi", "2026-01-01")
        self.assertEqual(result, "FRED")
        boj.assert_not_called()

    def test_global_news_stays_global_when_ticker_news_is_routed(self):
        # Routing news_data for ".T" sends per-ticker news to the JP vendor, but
        # get_global_news is ticker-less and must stay on the default source.
        set_config({"data_vendors_by_market": {".T": {"news_data": "edinet_news"}}})
        jp = mock.Mock(side_effect=_returns("JP_NEWS"))
        with self._route("get_global_news", {"yfinance": _returns("GLOBAL"), "edinet_news": jp}):
            result = interface.route_to_vendor("get_global_news", "2026-01-01", 7, 10)
        self.assertEqual(result, "GLOBAL")
        jp.assert_not_called()

    def test_ticker_news_is_routed_while_global_is_not(self):
        # The complement of the above: get_news (ticker-bearing) for a ".T" ticker
        # DOES route to the JP vendor under the same news_data route.
        set_config({"data_vendors_by_market": {".T": {"news_data": "edinet_news"}}})
        with self._route("get_news", {"yfinance": _returns("YF_NEWS"), "edinet_news": _returns("JP_NEWS")}):
            result = interface.route_to_vendor("get_news", "9984.T", "2026-01-01", "2026-01-10")
        self.assertEqual(result, "JP_NEWS")

    def test_default_config_routes_dot_t_to_japanese_vendors(self):
        # The shipped DEFAULT_CONFIG wires ".T" to JP vendors (first in each chain)
        # with yfinance as a keyless fallback. This is a regression guard: a config
        # edit that drops or renames a category would break JP routing silently.
        routes = default_config.DEFAULT_CONFIG["data_vendors_by_market"][".T"]
        self.assertEqual(routes["core_stock_apis"], "jquants,yfinance")
        self.assertEqual(routes["technical_indicators"], "jquants,yfinance")
        # get_fundamentals goes to jp_fundamentals; the three statement methods go
        # to jp_statements (each serves only its own methods, so the router picks
        # per method) — guarded by test_dot_t_chains_serve_every_ticker_bearing_method.
        self.assertEqual(routes["fundamental_data"], "jp_fundamentals,jp_statements,jquants,yfinance")
        self.assertEqual(routes["news_data"], "edinet_news,yfinance")
        # Macro stays market-agnostic — must not appear in the per-market block.
        self.assertNotIn("macro_data", routes)

    def test_dot_t_chains_serve_every_ticker_bearing_method(self):
        # Routing a category to a vendor chain applies it to EVERY ticker-bearing
        # method in that category. If a chained vendor implements only a subset
        # (e.g. edinet_news serves get_news but not get_insider_transactions),
        # route_to_vendor raises "Configured vendor(s) not available" the moment
        # the LLM calls the unserved method. This guards that, for every ".T"
        # category, at least one vendor in the chain implements each of the
        # category's ticker-bearing methods. (Ticker-less methods like
        # get_global_news stay market-agnostic and never use the .T chain.)
        routes = default_config.DEFAULT_CONFIG["data_vendors_by_market"][".T"]
        for category, chain in routes.items():
            vendors = [v.strip() for v in chain.split(",")]
            for method in interface.TOOLS_CATEGORIES[category]["tools"]:
                if method in market_context.TICKERLESS_METHODS:
                    continue
                servers = interface.VENDOR_METHODS[method]
                self.assertTrue(
                    any(v in servers for v in vendors),
                    f"No vendor in {chain!r} implements {method!r} "
                    f"(category {category!r}); a .T call would crash.",
                )


if __name__ == "__main__":
    unittest.main()
