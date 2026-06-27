"""EDINET per-ticker disclosure feed: code matching, windowing, rendering,
auth, and routing. All network calls are mocked — no key or connectivity."""
import copy
import os
import unittest
from unittest import mock

import pytest
import requests

import tradingagents.dataflows.config as config_module
import tradingagents.default_config as default_config
from tradingagents.dataflows import edinet_common, edinet_news, interface
from tradingagents.dataflows.config import set_config
from tradingagents.dataflows.edinet_common import (
    EDINETNotConfiguredError,
    EDINETRateLimitError,
)
from tradingagents.dataflows.errors import (
    VendorNotConfiguredError,
    VendorRateLimitError,
)


class FakeResp:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


def _doc(sec_code="99840", *, desc="有価証券報告書", doc_type="120",
         filer="ソフトバンクグループ株式会社", when="2026-06-22 15:00", doc_id="S100AAAA"):
    return {
        "secCode": sec_code, "docDescription": desc, "docTypeCode": doc_type,
        "filerName": filer, "submitDateTime": when, "docID": doc_id,
    }


def _by_date(mapping):
    """Build a fetch_documents side_effect from a {date: [records]} mapping."""
    return lambda date_str: mapping.get(date_str, [])


@pytest.mark.unit
class NewsRenderTests(unittest.TestCase):
    def setUp(self):
        edinet_common._documents_cache.clear()

    def tearDown(self):
        edinet_common._documents_cache.clear()

    def _patch(self, mapping):
        return mock.patch.object(
            edinet_common, "fetch_documents", side_effect=_by_date(mapping)
        )

    def test_filters_by_securities_code(self):
        mapping = {
            "2026-06-22": [
                _doc("99840", desc="自社株買い", doc_id="MINE"),
                _doc("72030", desc="他社の開示", doc_id="OTHER"),  # Toyota, not ours
            ],
        }
        with self._patch(mapping):
            out = edinet_news.get_news("9984.T", "2026-06-22", "2026-06-22")
        self.assertIn("## 9984.T EDINET disclosures", out)
        self.assertIn("自社株買い", out)
        self.assertNotIn("他社の開示", out)
        self.assertIn("EDINET docID: MINE", out)

    def test_no_disclosures_returns_informative_line(self):
        with self._patch({"2026-06-22": [_doc("72030")]}):  # only another company
            out = edinet_news.get_news("9984.T", "2026-06-22", "2026-06-22")
        self.assertEqual(
            out, "No EDINET disclosures found for 9984.T between 2026-06-22 and 2026-06-22"
        )

    def test_window_iterates_each_date_and_sorts_recent_first(self):
        mapping = {
            "2026-06-20": [_doc(when="2026-06-20 09:00", desc="古い開示")],
            "2026-06-22": [_doc(when="2026-06-22 15:00", desc="新しい開示")],
        }
        with self._patch(mapping):
            out = edinet_news.get_news("9984.T", "2026-06-20", "2026-06-22")
        self.assertLess(out.index("新しい開示"), out.index("古い開示"))  # newest first

    def test_dates_outside_window_are_never_fetched(self):
        # Look-ahead safety: a filing the day after end_date must not appear,
        # because that date is never queried.
        mock_fetch = mock.Mock(side_effect=_by_date({
            "2026-06-22": [_doc(desc="in window")],
            "2026-06-23": [_doc(desc="future leak")],
        }))
        with mock.patch.object(edinet_common, "fetch_documents", mock_fetch):
            out = edinet_news.get_news("9984.T", "2026-06-22", "2026-06-22")
        self.assertIn("in window", out)
        self.assertNotIn("future leak", out)
        self.assertNotIn("2026-06-23", [c.args[0] for c in mock_fetch.call_args_list])

    def test_per_date_fetch_is_memoized(self):
        mock_fetch = mock.Mock(side_effect=_by_date({"2026-06-22": [_doc()]}))
        with mock.patch.object(edinet_common, "fetch_documents", mock_fetch):
            edinet_news.get_news("9984.T", "2026-06-22", "2026-06-22")
            edinet_news.get_news("9984.T", "2026-06-22", "2026-06-22")
        mock_fetch.assert_called_once()  # second call served from cache

    def test_render_caps_at_news_article_limit(self):
        set_config({"news_article_limit": 2})
        try:
            mapping = {
                "2026-06-22": [
                    _doc(when=f"2026-06-22 1{i}:00", desc=f"開示{i}", doc_id=f"D{i}")
                    for i in range(5)
                ]
            }
            with self._patch(mapping):
                out = edinet_news.get_news("9984.T", "2026-06-22", "2026-06-22")
            self.assertEqual(out.count("### "), 2)
        finally:
            config_module._config = copy.deepcopy(default_config.DEFAULT_CONFIG)

    def test_long_window_is_capped(self):
        mock_fetch = mock.Mock(side_effect=_by_date({}))
        with mock.patch.object(edinet_common, "fetch_documents", mock_fetch):
            edinet_news.get_news("9984.T", "2020-01-01", "2026-06-22")
        # Window is clamped to MAX_WINDOW_DAYS+1 dates, not thousands.
        self.assertLessEqual(mock_fetch.call_count, edinet_common.MAX_WINDOW_DAYS + 1)


@pytest.mark.unit
class AuthTests(unittest.TestCase):
    def test_missing_key_raises_not_configured(self):
        with mock.patch.dict(os.environ, {}, clear=True), \
                self.assertRaises(EDINETNotConfiguredError):
            edinet_common.get_api_key()

    def test_typed_error_hierarchy(self):
        self.assertTrue(issubclass(EDINETNotConfiguredError, VendorNotConfiguredError))
        self.assertTrue(issubclass(EDINETRateLimitError, VendorRateLimitError))

    def test_request_sends_subscription_key_header(self):
        captured = {}

        def fake_get(url, params=None, headers=None, timeout=None):
            captured["headers"] = headers
            captured["params"] = params
            return FakeResp(200, {"results": []})

        with mock.patch.dict(os.environ, {"EDINET_API_KEY": "KEY123"}, clear=True), \
                mock.patch.object(edinet_common.requests, "get", side_effect=fake_get):
            edinet_common.fetch_documents("2026-06-22")
        self.assertEqual(captured["headers"], {"Ocp-Apim-Subscription-Key": "KEY123"})
        self.assertEqual(captured["params"], {"date": "2026-06-22", "type": 2})

    def test_rate_limit_surfaces_typed_error(self):
        with mock.patch.dict(os.environ, {"EDINET_API_KEY": "K"}, clear=True), \
                mock.patch.object(edinet_common.requests, "get", return_value=FakeResp(429)), \
                self.assertRaises(EDINETRateLimitError):
            edinet_common.fetch_documents("2026-06-22")

    def test_unauthorized_surfaces_not_configured(self):
        with mock.patch.dict(os.environ, {"EDINET_API_KEY": "BAD"}, clear=True), \
                mock.patch.object(edinet_common.requests, "get", return_value=FakeResp(403)), \
                self.assertRaises(EDINETNotConfiguredError):
            edinet_common.fetch_documents("2026-06-22")


@pytest.mark.unit
class RoutingTests(unittest.TestCase):
    def setUp(self):
        config_module._config = copy.deepcopy(default_config.DEFAULT_CONFIG)

    def tearDown(self):
        config_module._config = copy.deepcopy(default_config.DEFAULT_CONFIG)

    def test_edinet_news_registered_for_get_news(self):
        self.assertIn("edinet_news", interface.VENDOR_METHODS["get_news"])
        self.assertIn("edinet_news", interface.VENDOR_LIST)

    def test_tokyo_ticker_routes_news_to_edinet(self):
        set_config({"data_vendors_by_market": {".T": {"news_data": "edinet_news"}}})
        edn = mock.Mock(return_value="EDINET_NEWS")
        yf = mock.Mock(return_value="YF_NEWS")
        with mock.patch.dict(
            interface.VENDOR_METHODS,
            {"get_news": {"yfinance": yf, "edinet_news": edn}},
            clear=False,
        ):
            result = interface.route_to_vendor("get_news", "9984.T", "2026-06-20", "2026-06-22")
        self.assertEqual(result, "EDINET_NEWS")
        yf.assert_not_called()

    def test_global_news_stays_market_agnostic(self):
        # get_global_news is ticker-less, so a .T news route must not touch it.
        set_config({"data_vendors_by_market": {".T": {"news_data": "edinet_news"}}})
        from tradingagents.dataflows.market_context import infer_market
        self.assertEqual(infer_market("get_global_news", ("2026-06-22",)), "")


if __name__ == "__main__":
    unittest.main()
