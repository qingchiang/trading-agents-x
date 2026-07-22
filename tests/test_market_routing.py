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
from tradingagents.dataflows.errors import NoMarketDataError, VendorNotConfiguredError


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

    def test_bare_a_share_is_normalized_before_market_routing(self):
        cn = mock.Mock(side_effect=_returns("CN"))
        with self._route("get_stock_data", {"yfinance": cn}):
            result = interface.route_to_vendor(
                "get_stock_data", "600519", "2026-01-01", "2026-01-10"
            )
        self.assertEqual(result, "CN")
        self.assertEqual(cn.call_args.args[0], "600519.SS")

    def test_shanghai_alias_is_normalized_before_market_routing(self):
        cn = mock.Mock(side_effect=_returns("CN"))
        with self._route("get_stock_data", {"yfinance": cn}):
            result = interface.route_to_vendor(
                "get_stock_data", "600519.SH", "2026-01-01", "2026-01-10"
            )
        self.assertEqual(result, "CN")
        self.assertEqual(cn.call_args.args[0], "600519.SS")

    def test_beijing_suffix_is_rejected_before_vendor_routing(self):
        yf = mock.Mock(side_effect=_returns("YF"))
        with (
            self._route("get_stock_data", {"yfinance": yf}),
            self.assertRaisesRegex(ValueError, "Beijing Stock Exchange symbol"),
        ):
            interface.route_to_vendor(
                "get_stock_data", "430001.BJ", "2026-01-01", "2026-01-10"
            )
        yf.assert_not_called()

    def test_verified_snapshot_uses_jp_technical_vendor(self):
        with self._route(
            "get_verified_market_snapshot",
            {"yfinance": _returns("YF SNAPSHOT"), "jquants": _returns("JQ SNAPSHOT")},
        ):
            result = interface.route_to_vendor(
                "get_verified_market_snapshot", "9984.T", "2026-07-15", 30
            )
        self.assertEqual(result, "JQ SNAPSHOT")

    def test_verified_snapshot_us_uses_default_yfinance(self):
        with self._route(
            "get_verified_market_snapshot",
            {"yfinance": _returns("YF SNAPSHOT"), "jquants": _returns("JQ SNAPSHOT")},
        ):
            result = interface.route_to_vendor(
                "get_verified_market_snapshot", "NVDA", "2026-07-15", 30
            )
        self.assertEqual(result, "YF SNAPSHOT")

    def test_verified_snapshot_falls_back_when_jquants_unconfigured(self):
        jq = mock.Mock(side_effect=VendorNotConfiguredError("missing key"))
        with self._route(
            "get_verified_market_snapshot",
            {"jquants": jq, "yfinance": _returns("YF FALLBACK")},
        ):
            result = interface.route_to_vendor(
                "get_verified_market_snapshot", "9984.T", "2026-07-15", 30
            )
        self.assertEqual(result, "YF FALLBACK")

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
        self.assertEqual(routes["news_data"], "jp_news,yfinance")
        # Macro stays market-agnostic — must not appear in the per-market block.
        self.assertNotIn("macro_data", routes)

    def test_default_config_registers_shanghai_and_shenzhen(self):
        routes = default_config.DEFAULT_CONFIG["data_vendors_by_market"]
        expected = {
            "core_stock_apis": "akshare,yfinance",
            "technical_indicators": "akshare,yfinance",
            "fundamental_data": "cn_fundamentals,cn_statements,akshare,yfinance",
            "news_data": "cn_news,yfinance",
        }
        self.assertEqual(routes[".SS"], expected)
        self.assertEqual(routes[".SZ"], expected)

    def test_china_fundamental_chain_selects_method_specific_assembler(self):
        cn_fundamentals = mock.Mock(side_effect=_returns("CN FUNDAMENTALS"))
        cn_statements = mock.Mock(side_effect=_returns("CN STATEMENT"))
        with self._route(
            "get_fundamentals",
            {"cn_fundamentals": cn_fundamentals, "yfinance": _returns("YF")},
        ), self._route(
            "get_income_statement",
            {"cn_statements": cn_statements, "yfinance": _returns("YF")},
        ):
            fundamentals = interface.route_to_vendor(
                "get_fundamentals", "600519.SS", "2026-04-01"
            )
            statement = interface.route_to_vendor(
                "get_income_statement", "000001.SZ", "annual", "2026-04-01"
            )

        self.assertEqual(fundamentals, "CN FUNDAMENTALS")
        self.assertEqual(statement, "CN STATEMENT")
        cn_fundamentals.assert_called_once()
        cn_statements.assert_called_once()

    def test_china_news_route_falls_back_only_after_cn_no_data(self):
        cn_vendor = mock.Mock(
            side_effect=NoMarketDataError("600519.SS", detail="empty CN window")
        )
        yfinance = mock.Mock(side_effect=_returns("YF NEWS"))
        with self._route(
            "get_news", {"cn_news": cn_vendor, "yfinance": yfinance}
        ):
            output = interface.route_to_vendor(
                "get_news", "600519.SS", "2026-01-01", "2026-01-10"
            )

        self.assertEqual(output, "YF NEWS")
        cn_vendor.assert_called_once()
        yfinance.assert_called_once()

    def test_cn_market_route_does_not_change_global_news_vendor(self):
        set_config({"data_vendors": {"news_data": "yfinance"}})
        with self._route(
            "get_global_news", {"yfinance": _returns("GLOBAL NEWS")}
        ):
            output = interface.route_to_vendor("get_global_news", "2026-01-10")

        self.assertEqual(output, "GLOBAL NEWS")

    def test_china_fundamental_route_rejects_invalid_date_before_vendor(self):
        cn_fundamentals = mock.Mock(side_effect=_returns("CN FUNDAMENTALS"))
        yfinance = mock.Mock(side_effect=_returns("YF"))
        with self._route(
            "get_fundamentals",
            {"cn_fundamentals": cn_fundamentals, "yfinance": yfinance},
        ), self.assertRaisesRegex(ValueError, "expected YYYY-MM-DD"):
            interface.route_to_vendor("get_fundamentals", "600519.SS", "bad-date")

        cn_fundamentals.assert_not_called()
        yfinance.assert_not_called()

    def test_china_routes_validate_keyword_date_before_vendor(self):
        cn_fundamentals = mock.Mock(side_effect=_returns("CN FUNDAMENTALS"))
        cn_statements = mock.Mock(side_effect=_returns("CN STATEMENT"))
        yfinance = mock.Mock(side_effect=_returns("YF"))
        with self._route(
            "get_fundamentals",
            {"cn_fundamentals": cn_fundamentals, "yfinance": yfinance},
        ), self.assertRaisesRegex(ValueError, "expected YYYY-MM-DD"):
            interface.route_to_vendor(
                "get_fundamentals", "600519.SS", curr_date="bad-date"
            )
        with self._route(
            "get_income_statement",
            {"cn_statements": cn_statements, "yfinance": yfinance},
        ), self.assertRaisesRegex(ValueError, "expected YYYY-MM-DD"):
            interface.route_to_vendor(
                "get_income_statement",
                "000001.SZ",
                freq="annual",
                curr_date="bad-date",
            )

        cn_fundamentals.assert_not_called()
        cn_statements.assert_not_called()
        yfinance.assert_not_called()

    def test_china_statement_fallback_keeps_primary_availability_note(self):
        note = "- AkShare / Sina Income Statement unavailable (changed columns)."
        cn_statements = mock.Mock(
            side_effect=NoMarketDataError(
                "600519.SS",
                "600519.SS",
                "Sina primary unavailable",
                availability_notes=(note,),
            )
        )
        with self._route(
            "get_income_statement",
            {"cn_statements": cn_statements, "yfinance": _returns("YF STATEMENT")},
        ):
            output = interface.route_to_vendor(
                "get_income_statement", "600519.SS", "annual", "2026-04-01"
            )

        self.assertIn("YF STATEMENT", output)
        self.assertIn("### Source availability notes", output)
        self.assertIn(note, output)

    def test_default_market_routes_validate(self):
        interface.validate_market_routing(default_config.DEFAULT_CONFIG)

    def test_market_route_validation_rejects_unknown_vendor(self):
        config = copy.deepcopy(default_config.DEFAULT_CONFIG)
        config["data_vendors_by_market"][".SS"]["core_stock_apis"] = (
            "bogus_vendor,yfinance"
        )
        with self.assertRaisesRegex(ValueError, "Unknown vendor.*bogus_vendor"):
            interface.validate_market_routing(config)

    def test_market_route_validation_rejects_duplicate_vendor(self):
        config = copy.deepcopy(default_config.DEFAULT_CONFIG)
        config["data_vendors_by_market"][".SS"]["core_stock_apis"] = (
            "yfinance,yfinance"
        )
        with self.assertRaisesRegex(ValueError, "duplicate vendor"):
            interface.validate_market_routing(config)

    def test_market_route_validation_rejects_unserved_method(self):
        config = copy.deepcopy(default_config.DEFAULT_CONFIG)
        config["data_vendors_by_market"][".SS"]["news_data"] = "jp_news"
        with self.assertRaisesRegex(ValueError, "cannot serve 'get_insider_transactions'"):
            interface.validate_market_routing(config)

    def test_market_route_validation_respects_tool_override(self):
        config = copy.deepcopy(default_config.DEFAULT_CONFIG)
        config["data_vendors_by_market"][".SS"]["news_data"] = "jp_news"
        config["tool_vendors"].update({
            "get_news": "yfinance",
            "get_insider_transactions": "yfinance",
        })
        interface.validate_market_routing(config)

    def test_market_route_validation_checks_default_chains(self):
        config = copy.deepcopy(default_config.DEFAULT_CONFIG)
        config["data_vendors"]["core_stock_apis"] = "yfinance,yfinance"
        with self.assertRaisesRegex(ValueError, "duplicate vendor"):
            interface.validate_market_routing(config)

    def test_market_route_validation_checks_tool_chains(self):
        config = copy.deepcopy(default_config.DEFAULT_CONFIG)
        config["tool_vendors"]["get_stock_data"] = "bogus_vendor"
        with self.assertRaisesRegex(ValueError, "Unknown vendor.*bogus_vendor"):
            interface.validate_market_routing(config)

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
