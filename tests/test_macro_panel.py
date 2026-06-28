"""Cross-region macro panel: rendering, never-raise degradation, look-ahead.

fred.fetch_series is mocked, so these run without a network connection or key."""
import unittest
from unittest import mock

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from tradingagents.agents.analysts import news_analyst
from tradingagents.agents.analysts.news_analyst import create_news_analyst
from tradingagents.dataflows import estat, fred, macro_panel


def _series(points, series_id="X"):
    return {
        "series_id": series_id, "title": "t", "units": "%", "frequency": "Monthly",
        "seasonal": "", "start_date": "2025-06-20", "points": points,
    }


@pytest.mark.unit
class MacroPanelTests(unittest.TestCase):
    def setUp(self):
        # Make the panel's "is FRED configured?" guard pass so rendering tests run
        # without a real key (CI has none); fetch_series is mocked per test anyway.
        fred._series_cache.clear()
        estat._series_cache.clear()
        self._key_patch = mock.patch.object(fred, "get_api_key", return_value="testkey")
        self._key_patch.start()
        # Stub e-Stat too so the Japan CPI cells never touch the network (a local
        # .env may carry a real ESTAT_APP_ID); per-test patches override as needed.
        self._estat_patch = mock.patch.object(
            estat, "fetch_series", return_value=_series([("2025-06-01", "100.0")])
        )
        self._estat_patch.start()

    def tearDown(self):
        self._estat_patch.stop()
        self._key_patch.stop()
        fred._series_cache.clear()
        estat._series_cache.clear()

    def test_renders_dimensions_rows_and_fx_section(self):
        with mock.patch.object(
            fred, "fetch_series",
            return_value=_series([("2025-06-01", "1.0"), ("2026-06-01", "2.0")]),
        ):
            out = macro_panel.get_global_macro_panel("2026-06-20")
        self.assertIn("Global macro panel (as of 2026-06-20)", out)
        self.assertIn("| Indicator | US | Japan |", out)
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

    def test_cells_dispatch_to_their_declared_source(self):
        # Japan CPI/core are served by e-Stat; everything else (incl. the JP rate
        # mirrors and FX) by FRED. The two sources must not be cross-wired.
        fred_seen, estat_seen = [], []

        def fred_fake(indicator, curr_date, look_back_days=None):
            fred_seen.append(indicator)
            return _series([("2025-01-01", "1.0")])

        def estat_fake(indicator, curr_date, look_back_days=None):
            estat_seen.append(indicator)
            return _series([("2025-01-01", "100.0")])

        with mock.patch.object(fred, "fetch_series", side_effect=fred_fake), \
                mock.patch.object(estat, "fetch_series", side_effect=estat_fake):
            macro_panel.get_global_macro_panel("2026-06-20")
        self.assertEqual(set(estat_seen), {"jp_cpi", "jp_core_cpi"})
        self.assertIn("fed_funds_rate", fred_seen)  # US rate via FRED
        self.assertIn("DEXJPUS", fred_seen)         # FX via FRED
        self.assertNotIn("jp_cpi", fred_seen)       # CPI is never asked of FRED

    def test_none_spec_renders_na_without_fetching(self):
        # A cell with no free source yet (None, e.g. a future China column) must
        # render "n/a" without calling any fetcher.
        with mock.patch.object(fred, "fetch_series", side_effect=AssertionError("fetched")), \
                mock.patch.object(estat, "fetch_series", side_effect=AssertionError("fetched")):
            self.assertEqual(macro_panel._cell(None, "2026-06-20"), "n/a")

    def test_cell_failure_degrades_without_raising(self):
        with mock.patch.object(fred, "fetch_series", side_effect=RuntimeError("boom")):
            out = macro_panel.get_global_macro_panel("2026-06-20")
        self.assertIn("n/a", out)
        self.assertIn("| Indicator | US | Japan |", out)  # still a full table

    def test_missing_key_short_circuits(self):
        # An unconfigured FRED short-circuits to one clear note, not 13 n/a cells.
        with mock.patch.object(
            fred, "get_api_key", side_effect=fred.FredNotConfiguredError("no key")
        ):
            out = macro_panel.get_global_macro_panel("2026-06-20")
        self.assertIn("FRED_API_KEY is not configured", out)
        self.assertNotIn("USD/JPY", out)  # didn't bother building the table

    def test_single_point_shows_value_without_delta(self):
        # One in-window point must not render a fabricated "+0.00" change.
        with mock.patch.object(
            fred, "fetch_series", return_value=_series([("2026-01-01", "5.0")])
        ):
            out = macro_panel.get_global_macro_panel("2026-06-20")
        self.assertIn("5.0 (2026-01-01)", out)
        self.assertNotIn("+0.00", out)

    def test_empty_series_is_na(self):
        with mock.patch.object(fred, "fetch_series", return_value=_series([])):
            out = macro_panel.get_global_macro_panel("2026-06-20")
        self.assertIn("n/a", out)

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
            fred, "fetch_series",
            return_value=_series([("2025-01-01", "1.0"), ("2026-01-01", "x")]),
        ):
            out = macro_panel.get_global_macro_panel("2026-06-20")
        self.assertIn("x (2026-01-01)", out)  # graceful, no crash on bad number


@pytest.mark.unit
class NewsPanelInjectionTests(unittest.TestCase):
    """The news analyst prefetches the panel into its prompt and keeps the
    get_macro_indicators microscope tool bound."""

    def _run(self, panel_text="PANEL_XYZ"):
        captured = {}

        def _bind(tools):
            captured["tools"] = [t.name for t in tools]

            def _fn(prompt_value):
                captured["prompt"] = str(prompt_value)
                return AIMessage(content="REPORT")

            return RunnableLambda(_fn)

        llm = mock.MagicMock()
        llm.bind_tools.side_effect = _bind
        state = {
            "company_of_interest": "NVDA", "trade_date": "2026-01-15",
            "asset_type": "stock", "messages": [],
        }
        with mock.patch.object(news_analyst, "get_global_macro_panel", return_value=panel_text):
            result = create_news_analyst(llm)(state)
        return captured, result

    def test_panel_is_injected_into_prompt(self):
        captured, _ = self._run("PANEL_XYZ")
        self.assertIn("PANEL_XYZ", captured["prompt"])

    def test_macro_indicators_tool_still_bound(self):
        captured, _ = self._run()
        self.assertIn("get_macro_indicators", captured["tools"])


if __name__ == "__main__":
    unittest.main()
