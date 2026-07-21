"""Cross-region macro panel: rendering, never-raise degradation, look-ahead.

fred.fetch_series is mocked, so these run without a network connection or key."""

import unittest
from copy import deepcopy
from unittest import mock

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

import tradingagents.dataflows.config as config_module
import tradingagents.default_config as default_config
from tradingagents.agents.analysts import news_analyst
from tradingagents.agents.analysts.news_analyst import create_news_analyst
from tradingagents.dataflows import boj, cn_macro, estat, fred, jp_macro, macro_panel
from tradingagents.dataflows.config import set_config
from tradingagents.provenance import append_provenance_appendix, extract_provenance


def _series(points, series_id="X", timing=None):
    data = {
        "series_id": series_id,
        "title": "t",
        "units": "%",
        "frequency": "Monthly",
        "seasonal": "",
        "start_date": "2025-06-20",
        "points": points,
    }
    if timing is not None:
        data["timing"] = timing
    return data


@pytest.mark.unit
class MacroPanelTests(unittest.TestCase):
    def setUp(self):
        # Make the panel's "is FRED configured?" guard pass so rendering tests run
        # without a real key (CI has none); fetch_series is mocked per test anyway.
        fred._series_cache.clear()
        estat._series_cache.clear()
        boj._series_cache.clear()
        cn_macro._series_cache.clear()
        cn_macro._nbs_index_cache.clear()
        jp_macro._series_cache.clear()
        self._key_patch = mock.patch.object(fred, "get_api_key", return_value="testkey")
        self._key_patch.start()
        # Stub the JP vendors too so their cells never touch the network (a local
        # .env may carry a real ESTAT_APP_ID; BOJ is keyless); per-test patches
        # override as needed.
        self._estat_patch = mock.patch.object(
            estat, "fetch_series", return_value=_series([("2025-06-01", "100.0")])
        )
        self._estat_patch.start()
        self._boj_patch = mock.patch.object(
            boj, "fetch_series", return_value=_series([("2025-06-01", "0.5")])
        )
        self._boj_patch.start()
        self._cn_patch = mock.patch.object(
            cn_macro, "fetch_series", return_value=_series([("2025-06-01", "3.0")])
        )
        self._cn_patch.start()
        self._jp_patch = mock.patch.object(
            jp_macro, "fetch_series", return_value=_series([("2025-06-01", "2.0")])
        )
        self._jp_patch.start()

    def tearDown(self):
        self._jp_patch.stop()
        self._cn_patch.stop()
        self._boj_patch.stop()
        self._estat_patch.stop()
        self._key_patch.stop()
        fred._series_cache.clear()
        estat._series_cache.clear()
        boj._series_cache.clear()
        cn_macro._series_cache.clear()
        cn_macro._nbs_index_cache.clear()
        jp_macro._series_cache.clear()

    def test_renders_dimensions_rows_and_fx_section(self):
        with mock.patch.object(
            fred,
            "fetch_series",
            return_value=_series([("2025-06-01", "1.0"), ("2026-06-01", "2.0")]),
        ):
            out = macro_panel.get_global_macro_panel("2026-06-20")
        self.assertIn("Global macro panel (as of 2026-06-20)", out)
        self.assertIn("| Indicator | US | Japan | China |", out)
        # every dimension heading and indicator label appears
        for dimension, section in macro_panel._REGIONAL_SECTIONS:
            self.assertIn(dimension, out)
            for label, _series_map in section:
                self.assertIn(label, out)
        # cross-border FX/risk section with its global single values
        self.assertIn("Risk / FX", out)
        for label, _sid in macro_panel._GLOBAL_RISK:
            self.assertIn(label, out)
        # cell shows latest value (date) + absolute change + percent change
        self.assertIn("2.0 (2026-06-01, Δ +1.00, +100.0%)", out)

    def test_policy_rate_cells_name_regional_indicator_and_frequency(self):
        with mock.patch.object(
            fred,
            "fetch_series",
            return_value=_series([("2026-06-01", "1.0")]),
        ):
            out = macro_panel.get_global_macro_panel("2026-06-20")

        policy_row = next(
            line for line in out.splitlines() if "Policy / reference rate" in line
        )
        self.assertIn("Fed funds rate [Monthly]", policy_row)
        self.assertIn("BOJ policy rate [Daily]", policy_row)
        self.assertIn("1Y LPR [Monthly]", policy_row)

    def test_fallback_series_provenance_records_source_frequency_date_and_reason(self):
        jp_data = _series([("2026-06-18", "1.9")], timing="observation-date filtered")
        jp_data.update(
            actual_source="FRED",
            frequency="Monthly",
            fallback_reason="MOF primary retrieval unavailable",
        )

        def cn_fetch(indicator, *_args):
            data = _series([("2026-06-19", "7.1")], timing="trade-date filtered")
            if indicator == "cn_10y_yield":
                data.update(
                    actual_source="China Foreign Exchange Trade System",
                    frequency="Latest official curve snapshot",
                    fallback_reason="Eastmoney returned no usable observations",
                )
            elif indicator == "usd_cny":
                data.update(
                    actual_source="Eastmoney",
                    frequency="Daily",
                    fallback_reason="SAFE primary retrieval unavailable",
                )
            elif indicator == "cn_cpi":
                data.update(
                    actual_source="Eastmoney",
                    timing="observation-period filtered; non-vintage",
                    fallback_reason="NBS primary retrieval unavailable",
                )
            return data

        with (
            mock.patch.object(
                fred,
                "fetch_series",
                return_value=_series([("2026-06-01", "1.0")]),
            ),
            mock.patch.object(jp_macro, "fetch_series", return_value=jp_data),
            mock.patch.object(cn_macro, "fetch_series", side_effect=cn_fetch),
        ):
            out = macro_panel.get_global_macro_panel("2026-06-20")

        records = {
            record.evidence: record
            for record in extract_provenance(out)
            if record.evidence.startswith("global macro panel /")
        }
        jp_record = records["global macro panel / jp_10y_yield"]
        self.assertEqual(jp_record.source, "FRED")
        self.assertEqual(jp_record.effective, "2026-06-18")
        self.assertIn("frequency=Monthly", jp_record.timing)
        self.assertIn("MOF primary retrieval unavailable", jp_record.timing)
        cn_record = records["global macro panel / cn_10y_yield"]
        self.assertEqual(cn_record.source, "China Foreign Exchange Trade System")
        self.assertIn("frequency=Latest official curve snapshot", cn_record.timing)
        fx_record = records["global macro panel / usd_cny"]
        self.assertEqual(fx_record.source, "Eastmoney")
        self.assertIn("SAFE primary retrieval unavailable", fx_record.timing)
        cpi_record = records["global macro panel / cn_cpi"]
        self.assertEqual(cpi_record.source, "Eastmoney")
        self.assertEqual(cpi_record.effective, "2026-06-19")
        self.assertIn("non-vintage", cpi_record.timing)
        self.assertIn("NBS primary retrieval unavailable", cpi_record.timing)
        warnings = append_provenance_appendix(
            "REPORT", records.values(), enabled=False
        )
        self.assertIn("fallback source used", warnings)

    def test_primary_fallback_capable_series_do_not_emit_fallback_warning(self):
        jp_data = _series(
            [("2026-06-18", "1.9")], timing="publication-time filtered"
        )
        jp_data.update(actual_source="Japan Ministry of Finance", frequency="Daily")

        def cn_fetch(indicator, *_args):
            data = _series([("2026-06-19", "7.1")], timing="trade-date filtered")
            if indicator == "cn_10y_yield":
                data.update(actual_source="Eastmoney", frequency="Daily")
            elif indicator == "usd_cny":
                data.update(actual_source="SAFE", frequency="Daily")
            elif indicator in {"cn_cpi", "cn_gdp", "cn_pmi"}:
                data.update(
                    actual_source="National Bureau of Statistics of China",
                    timing=(
                        "official release-date filtered; latest-release coverage; "
                        "release date=2026-06-19"
                    ),
                )
            return data

        with (
            mock.patch.object(
                fred,
                "fetch_series",
                return_value=_series([("2026-06-01", "1.0")]),
            ),
            mock.patch.object(jp_macro, "fetch_series", return_value=jp_data),
            mock.patch.object(cn_macro, "fetch_series", side_effect=cn_fetch),
        ):
            out = macro_panel.get_global_macro_panel("2026-06-20")

        records = [
            record
            for record in extract_provenance(out)
            if record.evidence.startswith("global macro panel /")
        ]
        self.assertEqual(
            append_provenance_appendix("REPORT", records, enabled=False),
            "REPORT",
        )

    def test_cells_dispatch_to_their_declared_source(self):
        # Japan CPI/core -> e-Stat; Japan policy rate / Tankan -> BOJ; everything
        # else (US series, the JP 10Y mirror, FX) -> FRED. No cross-wiring.
        fred_seen, estat_seen, boj_seen, cn_seen, jp_seen = [], [], [], [], []

        def _spy(seen, val):
            def fake(indicator, curr_date, look_back_days=None):
                seen.append(indicator)
                return _series([("2025-01-01", val)])

            return fake

        with (
            mock.patch.object(fred, "fetch_series", side_effect=_spy(fred_seen, "1.0")),
            mock.patch.object(estat, "fetch_series", side_effect=_spy(estat_seen, "100.0")),
            mock.patch.object(boj, "fetch_series", side_effect=_spy(boj_seen, "0.5")),
            mock.patch.object(cn_macro, "fetch_series", side_effect=_spy(cn_seen, "3.0")),
            mock.patch.object(jp_macro, "fetch_series", side_effect=_spy(jp_seen, "2.0")),
        ):
            macro_panel.get_global_macro_panel("2026-06-20")
        self.assertEqual(set(estat_seen), {"jp_cpi", "jp_core_cpi"})
        self.assertEqual(set(boj_seen), {"jp_policy_rate", "jp_tankan"})
        self.assertIn("fed_funds_rate", fred_seen)  # US rate via FRED
        self.assertIn("DEXJPUS", fred_seen)  # FX via FRED
        self.assertNotIn("jp_cpi", fred_seen)  # CPI is never asked of FRED
        self.assertNotIn("jp_policy_rate", fred_seen)  # nor the policy rate
        self.assertEqual(jp_seen, ["jp_10y_yield"])
        self.assertEqual(
            set(cn_seen),
            {
                "cn_lpr",
                "cn_10y_yield",
                "cn_cpi",
                "cn_gdp",
                "cn_unemployment",
                "cn_pmi",
                "usd_cny",
            },
        )

    def test_none_spec_renders_na_without_fetching(self):
        # A cell with no free source yet (None, e.g. a future China column) must
        # render "n/a" without calling any fetcher.
        with (
            mock.patch.object(fred, "fetch_series", side_effect=AssertionError("fetched")),
            mock.patch.object(estat, "fetch_series", side_effect=AssertionError("fetched")),
            mock.patch.object(boj, "fetch_series", side_effect=AssertionError("fetched")),
            mock.patch.object(cn_macro, "fetch_series", side_effect=AssertionError("fetched")),
            mock.patch.object(jp_macro, "fetch_series", side_effect=AssertionError("fetched")),
        ):
            self.assertEqual(macro_panel._cell(None, "2026-06-20"), "n/a")

    def test_cell_failure_degrades_without_raising(self):
        with mock.patch.object(fred, "fetch_series", side_effect=RuntimeError("boom")):
            out = macro_panel.get_global_macro_panel("2026-06-20")
        self.assertIn("n/a", out)
        self.assertIn("| Indicator | US | Japan | China |", out)  # still a full table
        records = {record.source: record for record in extract_provenance(out)}
        self.assertEqual(records["FRED"].effective, "—")
        self.assertIn("0/", records["FRED"].timing)
        self.assertIn("retrieval unavailable", records["FRED"].timing)
        self.assertNotIn("unavailable", records["e-Stat"].timing)
        self.assertNotIn("unavailable", records["BOJ"].timing)
        self.assertNotIn("unavailable", records["China macro"].timing)

    def test_missing_fred_key_keeps_keyless_regions_available(self):
        with mock.patch.object(
            fred, "get_api_key", side_effect=fred.FredNotConfiguredError("no key")
        ):
            out = macro_panel.get_global_macro_panel("2026-06-20")
        self.assertIn("| Indicator | US | Japan | China |", out)
        self.assertIn("USD/JPY", out)
        self.assertIn("3.0 (2025-06-01)", out)
        records = {record.source: record for record in extract_provenance(out)}
        self.assertIn("API key is not configured", records["FRED"].timing)
        self.assertNotIn("unavailable", records["China macro"].timing)

    def test_one_china_cell_failure_does_not_hide_other_china_cells(self):
        def fetch(indicator, *_args):
            if indicator == "cn_cpi":
                raise RuntimeError("CPI endpoint unavailable")
            return _series(
                [("2026-06-01", "4.5" if indicator == "cn_gdp" else indicator)],
                timing="observation-period filtered; non-vintage",
            )

        with mock.patch.object(cn_macro, "fetch_series", side_effect=fetch):
            out = macro_panel.get_global_macro_panel("2026-06-20")

        self.assertIn("| CPI / inflation |", out)
        self.assertIn("cn_pmi (2026-06-01)", out)
        extracted = extract_provenance(out)
        records = {record.source: record for record in extracted}
        self.assertIn("6/7 cells available", records["China macro"].timing)
        self.assertIn("partial coverage", records["China macro"].timing)
        self.assertIn("non-vintage", records["China macro"].timing)
        cpi_record = next(
            record
            for record in extracted
            if record.evidence == "global macro panel / cn_cpi"
        )
        self.assertEqual(cpi_record.effective, "—")
        self.assertIn("retrieval unavailable", cpi_record.timing)
        warnings = append_provenance_appendix(
            "REPORT", [cpi_record], enabled=False
        )
        self.assertIn("source unavailable for requested date/window", warnings)

    def test_single_point_shows_value_without_delta(self):
        # One in-window point must not render a fabricated "+0.00" change.
        with mock.patch.object(fred, "fetch_series", return_value=_series([("2026-01-01", "5.0")])):
            out = macro_panel.get_global_macro_panel("2026-06-20")
        self.assertIn("5.0 (2026-01-01)", out)
        self.assertNotIn("+0.00", out)

    def test_empty_series_is_na(self):
        with mock.patch.object(fred, "fetch_series", return_value=_series([])):
            out = macro_panel.get_global_macro_panel("2026-06-20")
        self.assertIn("n/a", out)

    def test_cpi_and_gdp_use_exact_yoy_and_require_comparator(self):
        def fetch(indicator, *_args):
            if indicator == "cpi":
                return _series([("2025-06-01", "100"), ("2026-06-01", "103")])
            if indicator == "real_gdp":
                return _series([("2025-03-01", "100"), ("2026-06-01", "104")])
            return _series([("2026-06-01", "2")])

        with mock.patch.object(fred, "fetch_series", side_effect=fetch):
            out = macro_panel.get_global_macro_panel("2026-06-20")
        self.assertIn("+3.0% YoY (2026-06-01)", out)
        gdp_row = next(line for line in out.splitlines() if "GDP / growth" in line)
        self.assertIn("n/a", gdp_row)
        records = {record.source: record for record in extract_provenance(out)}
        self.assertIn("9/11 cells available", records["FRED"].timing)

    def test_lookahead_curr_date_passed_to_fetch(self):
        captured = {}

        def fake(indicator, curr_date, look_back_days=None):
            captured["curr_date"] = curr_date
            return _series([("2025-01-01", "1.0")])

        with mock.patch.object(fred, "fetch_series", side_effect=fake):
            macro_panel.get_global_macro_panel("2026-06-20")
        self.assertEqual(captured["curr_date"], "2026-06-20")

    def test_non_numeric_latest_renders_without_delta(self):
        with mock.patch.object(
            fred,
            "fetch_series",
            return_value=_series([("2025-01-01", "1.0"), ("2026-01-01", "x")]),
        ):
            out = macro_panel.get_global_macro_panel("2026-06-20")
        self.assertIn("x (2026-01-01)", out)  # graceful, no crash on bad number


@pytest.mark.unit
class NewsPanelInjectionTests(unittest.TestCase):
    """The news analyst prefetches the panel into its prompt and keeps the
    get_macro_indicators microscope tool bound."""

    def setUp(self):
        self._previous_config = deepcopy(config_module._config)
        config_module._config = deepcopy(default_config.DEFAULT_CONFIG)

    def tearDown(self):
        config_module._config = self._previous_config

    def _run(self, panel_text="PANEL_XYZ", ticker="NVDA", market_flows=""):
        captured = {}
        set_config({"provenance_appendix": True})

        def _bind(tools):
            captured["tools"] = [t.name for t in tools]

            def _fn(prompt_value):
                captured["prompt"] = str(prompt_value)
                return AIMessage(content="REPORT")

            return RunnableLambda(_fn)

        llm = mock.MagicMock()
        llm.bind_tools.side_effect = _bind
        state = {
            "company_of_interest": ticker,
            "trade_date": "2026-01-15",
            "asset_type": "stock",
            "messages": [],
        }
        with (
            mock.patch.object(news_analyst, "get_global_macro_panel", return_value=panel_text),
            mock.patch.object(
                news_analyst,
                "get_market_investor_flows",
                return_value=market_flows,
            ) as flows,
        ):
            result = create_news_analyst(llm)(state)
        return captured, result, flows

    def test_panel_is_injected_into_prompt(self):
        captured, result, _ = self._run("PANEL_XYZ")
        self.assertIn("PANEL_XYZ", captured["prompt"])
        self.assertEqual(result["news_report"], result["messages"][0].content)
        self.assertIn("## Data Provenance", result["news_report"])
        self.assertIn("| global macro panel | unknown |", result["news_report"])
        self.assertIn("no auditable source metadata captured", result["news_report"])
        self.assertIn("| routed ticker news | — |", result["news_report"])

    def test_panel_provenance_is_carried_into_report(self):
        panel = macro_panel.get_global_macro_panel("2026-01-15")
        _, result, _ = self._run(panel)
        report = result["news_report"]
        self.assertIn("| global macro panel | FRED |", report)
        self.assertIn("| global macro panel | e-Stat |", report)
        self.assertIn("| global macro panel | BOJ |", report)
        self.assertIn("| global macro panel | China macro |", report)

    def test_macro_indicators_tool_still_bound(self):
        captured, _, _ = self._run()
        self.assertIn("get_macro_indicators", captured["tools"])

    def test_ticker_news_prompt_uses_configured_14_day_window(self):
        captured, _, _ = self._run()
        self.assertIn(
            "window='recent' first; it covers 2026-01-01 through 2026-01-15",
            captured["prompt"],
        )
        self.assertIn("configured lookback offset 14", captured["prompt"])
        self.assertIn(
            "2025-10-18 through 2026-01-15 (90 calendar dates)",
            captured["prompt"],
        )
        self.assertIn("must replace rather than duplicate", captured["prompt"])
        self.assertIn("do not attempt to supply or override any date", captured["prompt"])

    def test_prompt_preserves_configured_recent_window_longer_than_90_dates(self):
        set_config({"ticker_news_lookback_days": 120})
        captured, _, _ = self._run()
        self.assertIn(
            "window='recent' first; it covers 2025-09-17 through 2026-01-15",
            captured["prompt"],
        )
        self.assertIn(
            "Extended covers 2025-09-17 through 2026-01-15 (121 calendar dates)",
            captured["prompt"],
        )

    def test_jp_market_flows_are_injected_as_non_company_context(self):
        captured, _, flows = self._run(
            ticker="9984.T",
            market_flows="MARKET-LEVEL CONTEXT ONLY — NOT 9984.T ORDER FLOW",
        )
        flows.assert_called_once_with("9984.T", "2026-01-15")
        self.assertIn("NOT company order flow", captured["prompt"])
        self.assertIn("NOT 9984.T ORDER FLOW", captured["prompt"])

    def test_empty_market_flows_do_not_claim_effective_data(self):
        _, result, _ = self._run(
            ticker="9984.T",
            market_flows="<no investor-flow data published on or before 2026-01-15>",
        )
        row = next(
            line
            for line in result["news_report"].splitlines()
            if "| regional investor flows |" in line
        )
        self.assertIn("| — | available; no published records |", row)

    def test_unavailable_market_flows_do_not_claim_effective_data(self):
        _, result, _ = self._run(
            ticker="9984.T",
            market_flows="<investor flows unavailable: VendorRateLimitError>",
        )
        row = next(
            line
            for line in result["news_report"].splitlines()
            if "| regional investor flows |" in line
        )
        self.assertIn("| — | unavailable |", row)

    def test_us_prompt_has_no_tse_market_flow_block(self):
        captured, _, flows = self._run(ticker="NVDA", market_flows="")
        flows.assert_not_called()
        self.assertNotIn("target-market capital-flow block", captured["prompt"])


if __name__ == "__main__":
    unittest.main()
