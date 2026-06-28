"""BOJ macro vendor: alias resolution, STATUS handling, frequency-aware parsing,
publication-lag look-ahead, and process caching.

All API access is mocked, so these run without a network connection (the BOJ API
is keyless, so there is no credential to stub).
"""
import unittest
from unittest import mock

import pytest

from tradingagents.dataflows import boj
from tradingagents.dataflows.errors import NoMarketDataError


def _body(survey_dates, values, unit="percent per annum", freq="DAILY"):
    """A fake getDataCode response with one series' parallel date/value arrays."""
    return {
        "STATUS": 200,
        "RESULTSET": [{
            "SERIES_CODE": "X", "NAME_OF_TIME_SERIES": "n",
            "UNIT": unit, "FREQUENCY": freq,
            "VALUES": {"SURVEY_DATES": survey_dates, "VALUES": values},
        }],
    }


@pytest.mark.unit
class BojHelpersTests(unittest.TestCase):
    def test_parse_point_daily(self):
        self.assertEqual(boj._parse_point("D", 20260619), ("2026-06-19", "2026-06-19"))

    def test_parse_point_quarterly_publishes_after_quarter_end(self):
        # Q1 (Jan-Mar) -> available Apr 1; Q4 (Oct-Dec) -> available Jan 1 next year.
        self.assertEqual(boj._parse_point("Q", 202601), ("2026 Q1", "2026-04-01"))
        self.assertEqual(boj._parse_point("Q", 202604), ("2026 Q4", "2027-01-01"))

    def test_request_dates_per_frequency(self):
        from datetime import datetime
        start, end = datetime(2025, 6, 20), datetime(2026, 6, 20)
        self.assertEqual(boj._request_dates("D", start, end), ("202506", "202606"))
        self.assertEqual(boj._request_dates("Q", start, end), ("202502", "202602"))


@pytest.mark.unit
class BojRequestTests(unittest.TestCase):
    def _response(self, status, msg=""):
        resp = mock.MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = {"STATUS": status, "MESSAGE": msg}
        return resp

    def test_non_200_status_raises_value_error(self):
        with mock.patch.object(boj.requests, "get", return_value=self._response(400, "bad")), \
                self.assertRaises(ValueError):
            boj._request("getDataCode", {})

    def test_200_status_returns_body(self):
        with mock.patch.object(boj.requests, "get", return_value=self._response(200)):
            self.assertEqual(boj._request("getDataCode", {})["STATUS"], 200)


@pytest.mark.unit
class BojFetchSeriesTests(unittest.TestCase):
    def setUp(self):
        boj._series_cache.clear()

    def tearDown(self):
        boj._series_cache.clear()

    def test_unknown_alias_raises_value_error(self):
        with self.assertRaises(ValueError):
            boj.fetch_series("not_a_real_alias", "2026-06-20")

    def test_daily_skips_nulls_and_future_dates(self):
        # 06-20 is null (non-business day); 06-21 is after curr_date -> both dropped.
        body = _body([20260618, 20260619, 20260620, 20260621], [0.7, 0.72, None, 0.9])
        with mock.patch.object(boj, "_request", return_value=body):
            data = boj.fetch_series("jp_policy_rate", "2026-06-20")
        self.assertEqual(data["points"], [("2026-06-18", "0.7"), ("2026-06-19", "0.72")])
        self.assertEqual(data["units"], "percent per annum")
        self.assertEqual(data["series_id"], "STRDCLUCON")

    def test_quarterly_lookahead_drops_unpublished_quarter(self):
        # As of 2026-06-20, 2026 Q2 (published ~Jul 1) is not yet available.
        body = _body([202504, 202601, 202602], [15, 17, 99], unit="% points", freq="QUARTERLY")
        with mock.patch.object(boj, "_request", return_value=body):
            data = boj.fetch_series("jp_tankan", "2026-06-20")
        self.assertEqual(data["points"], [("2025 Q4", "15"), ("2026 Q1", "17")])

    def test_empty_resultset_returns_none_and_is_not_cached(self):
        calls = []

        def _req(path, params):
            calls.append(path)
            return {"STATUS": 200, "RESULTSET": []}

        with mock.patch.object(boj, "_request", side_effect=_req):
            self.assertIsNone(boj.fetch_series("jp_policy_rate", "2026-06-20"))
            self.assertIsNone(boj.fetch_series("jp_policy_rate", "2026-06-20"))
        self.assertEqual(calls, ["getDataCode", "getDataCode"])  # miss not memoized

    def test_all_filtered_points_returns_none_and_is_not_cached(self):
        # Distinct from the empty-RESULTSET path: data rows are present but yield
        # no usable points (all null / all after curr_date), so fetch_series must
        # still return None *and* not memoize it (the second miss path).
        calls = []

        def _req(path, params):
            calls.append(path)
            # One null observation + one not-yet-published (future) quarter.
            return _body([202601, 202602], [None, 99], unit="% points", freq="QUARTERLY")

        with mock.patch.object(boj, "_request", side_effect=_req):
            # 2026 Q1 value is null; 2026 Q2 (avail 2026-07-01) is after curr_date.
            self.assertIsNone(boj.fetch_series("jp_tankan", "2026-05-01"))
            self.assertIsNone(boj.fetch_series("jp_tankan", "2026-05-01"))
        self.assertEqual(calls, ["getDataCode", "getDataCode"])  # miss not memoized

    def test_repeat_fetch_hits_cache(self):
        calls = []

        def _req(path, params):
            calls.append(path)
            return _body([20260619], [0.72])

        with mock.patch.object(boj, "_request", side_effect=_req):
            boj.fetch_series("jp_policy_rate", "2026-06-20")
            boj.fetch_series("jp_policy_rate", "2026-06-20")
        self.assertEqual(calls, ["getDataCode"])  # second served from cache


@pytest.mark.unit
class BojGetMacroDataTests(unittest.TestCase):
    def setUp(self):
        boj._series_cache.clear()

    def tearDown(self):
        boj._series_cache.clear()

    def test_foreign_alias_raises_no_market_data(self):
        # An indicator BOJ doesn't own must raise so the router chain falls through.
        with self.assertRaises(NoMarketDataError):
            boj.get_macro_data("cpi", "2026-06-20")

    def test_owned_alias_renders_markdown(self):
        body = _body([20260618, 20260619], [0.97, 0.977])
        with mock.patch.object(boj, "_request", return_value=body):
            out = boj.get_macro_data("jp_policy_rate", "2026-06-20")
        self.assertIn("## BOJ: Japan policy rate (overnight call, avg)", out)
        self.assertIn("**Latest:** 0.977 (2026-06-19)", out)

    def test_owned_alias_empty_window_returns_note(self):
        with mock.patch.object(boj, "_request", return_value={"STATUS": 200, "RESULTSET": []}):
            out = boj.get_macro_data("jp_tankan", "2026-06-20")
        self.assertIn("BOJ: no data", out)


if __name__ == "__main__":
    unittest.main()
