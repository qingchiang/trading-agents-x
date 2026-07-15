"""e-Stat macro vendor: alias resolution, envelope/STATUS handling, parsing,
look-ahead-safe windowing, and process caching.

All API access is mocked, so these run without a network connection or app id.
"""
import unittest
from unittest import mock

import pytest

from tradingagents.dataflows import estat
from tradingagents.dataflows.errors import NoMarketDataError


def _root(values):
    """A fake getStatsData response object (the value _request returns)."""
    return {"STATISTICAL_DATA": {"DATA_INF": {"VALUE": values}}}


def _val(time, value):
    return {"@time": time, "@unit": None, "$": value}


@pytest.mark.unit
class EstatHelpersTests(unittest.TestCase):
    def test_month_code_round_trips_with_date_parse(self):
        code = estat._month_code(2026, 6)
        self.assertEqual(code, "2026000606")
        self.assertEqual(estat._date_from_time_code(code), "2026-06-01")
        self.assertEqual(estat._date_from_time_code("2026000505"), "2026-05-01")
        self.assertEqual(estat._date_from_time_code("2024001212"), "2024-12-01")


@pytest.mark.unit
class EstatRequestTests(unittest.TestCase):
    def _response(self, status, msg=""):
        resp = mock.MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = {
            "GET_STATS_DATA": {"RESULT": {"STATUS": status, "ERROR_MSG": msg}}
        }
        return resp

    def test_auth_failure_status_maps_to_not_configured(self):
        with mock.patch.object(estat, "get_app_id", return_value="k"), \
                mock.patch.object(estat.requests, "get", return_value=self._response(100, "bad")), \
                self.assertRaises(estat.EstatNotConfiguredError):
            estat._request("getStatsData", {})

    def test_other_non_ok_status_raises_value_error(self):
        with mock.patch.object(estat, "get_app_id", return_value="k"), \
                mock.patch.object(estat.requests, "get", return_value=self._response(200, "nope")), \
                self.assertRaises(ValueError):
            estat._request("getStatsData", {})

    def test_ok_no_data_status_is_accepted(self):
        # STATUS 1 = "ended normally but no matching data" — not an error.
        with mock.patch.object(estat, "get_app_id", return_value="k"), \
                mock.patch.object(estat.requests, "get", return_value=self._response(1)):
            root = estat._request("getStatsData", {})
        self.assertIn("RESULT", root)


@pytest.mark.unit
class EstatFetchSeriesTests(unittest.TestCase):
    def setUp(self):
        estat._series_cache.clear()

    def tearDown(self):
        estat._series_cache.clear()

    def test_unknown_alias_raises_value_error(self):
        # An unknown alias is rejected before any API call (no app id needed).
        with self.assertRaises(ValueError):
            estat.fetch_series("not_a_real_alias", "2026-06-20")

    def test_missing_app_id_raises_not_configured(self):
        with mock.patch.dict("os.environ", {}, clear=True), \
                self.assertRaises(estat.EstatNotConfiguredError):
            estat.fetch_series("jp_cpi", "2026-06-20")

    def test_parses_descending_values_into_ascending_points(self):
        # e-Stat returns newest-first; fetch_series must sort ascending.
        values = [_val("2026000505", "113.5"), _val("2026000404", "113.0"),
                  _val("2026000303", "112.7")]
        with mock.patch.object(estat, "_request", return_value=_root(values)):
            data = estat.fetch_series("jp_core_cpi", "2026-06-20")
        self.assertEqual(data["points"],
                         [("2026-03-01", "112.7"), ("2026-04-01", "113.0"),
                          ("2026-05-01", "113.5")])
        self.assertEqual(data["series_id"], "0003427113/0161")
        self.assertEqual(data["units"], "2020=100")
        self.assertEqual(data["frequency"], "Monthly")

    def test_non_numeric_markers_are_skipped(self):
        values = [_val("2026000404", "-"), _val("2026000505", "113.5")]
        with mock.patch.object(estat, "_request", return_value=_root(values)):
            data = estat.fetch_series("jp_cpi", "2026-06-20")
        self.assertEqual(data["points"], [("2026-05-01", "113.5")])

    def test_single_value_object_is_wrapped(self):
        # A one-observation window comes back as a dict, not a list.
        with mock.patch.object(estat, "_request", return_value=_root(_val("2026000505", "113.5"))):
            data = estat.fetch_series("jp_cpi", "2026-06-20")
        self.assertEqual(data["points"], [("2026-05-01", "113.5")])

    def test_lookahead_drops_future_months_and_caps_cdtimeto(self):
        captured = {}

        def _capture(path, params):
            captured.update(params)
            # April is after the March curr_date and must be dropped defensively.
            return _root([_val("2026000202", "112.2"), _val("2026000303", "112.7"),
                          _val("2026000404", "113.0")])

        with mock.patch.object(estat, "_request", side_effect=_capture):
            data = estat.fetch_series("jp_cpi", "2026-03-15")
        self.assertEqual(captured["cdTimeTo"], "2026000303")
        self.assertNotIn(("2026-04-01", "113.0"), data["points"])
        self.assertEqual(data["points"][-1], ("2026-03-01", "112.7"))

    def test_empty_window_returns_none_and_is_not_cached(self):
        calls = []

        def _req(path, params):
            calls.append(path)
            return _root([])

        with mock.patch.object(estat, "_request", side_effect=_req):
            self.assertIsNone(estat.fetch_series("jp_cpi", "2026-06-20"))
            self.assertIsNone(estat.fetch_series("jp_cpi", "2026-06-20"))
        # A miss is not memoized (could be a transient outage), so it is retried.
        self.assertEqual(calls, ["getStatsData", "getStatsData"])

    def test_repeat_fetch_hits_cache(self):
        calls = []

        def _req(path, params):
            calls.append(path)
            return _root([_val("2026000505", "113.5")])

        with mock.patch.object(estat, "_request", side_effect=_req):
            estat.fetch_series("jp_cpi", "2026-06-20")
            estat.fetch_series("jp_cpi", "2026-06-20")
        self.assertEqual(calls, ["getStatsData"])  # second call served from cache


@pytest.mark.unit
class EstatGetMacroDataTests(unittest.TestCase):
    def setUp(self):
        estat._series_cache.clear()

    def tearDown(self):
        estat._series_cache.clear()

    def test_foreign_alias_raises_no_market_data(self):
        # An indicator e-Stat doesn't own must raise so the router chain falls
        # through to the next vendor — without any API call (no app id needed).
        with self.assertRaises(NoMarketDataError):
            estat.get_macro_data("cpi", "2026-06-20")

    def test_owned_alias_renders_markdown(self):
        values = [_val("2025000505", "112.0"), _val("2026000505", "113.5")]
        with mock.patch.object(estat, "_request", return_value=_root(values)):
            out = estat.get_macro_data("jp_cpi", "2026-06-20")
        self.assertIn("## e-Stat: Japan CPI (all items)", out)
        self.assertIn("**Latest:** 113.5 (2026-05-01)", out)

    def test_owned_alias_empty_window_returns_note(self):
        with mock.patch.object(estat, "_request", return_value=_root([])):
            out = estat.get_macro_data("jp_cpi", "2026-06-20")
        self.assertIn("e-Stat: no data", out)


if __name__ == "__main__":
    unittest.main()
