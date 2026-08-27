"""J-Quants vendor: auth/token handling, OHLCV CSV, indicators, and routing.

All network calls are mocked — no credentials or connectivity needed.
"""

import copy
import os
import unittest
from io import StringIO
from unittest import mock

import pandas as pd
import pytest
import requests

import tradingagents.default_config as default_config
from tradingagents.dataflows import interface
from tradingagents.dataflows.config import bind_config
from tradingagents.dataflows.errors import (
    NoMarketDataError,
    VendorNotConfiguredError,
    VendorRateLimitError,
)
from tradingagents.dataflows.jp import jquants_common, jquants_stock
from tradingagents.dataflows.jp.jquants_common import (
    JQuantsNotConfiguredError,
    JQuantsRateLimitError,
    from_jquants_code,
    to_jquants_code,
)
from tradingagents.dataflows.jp.jquants_stock import get_stock


class FakeResp:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        self.text = text

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


def _quote(date, close, *, adjusted=True, **extra):
    """Build a J-Quants v2 /equities/bars/daily record. Raw close is 2x the
    adjusted close so tests can tell which one the parser picked."""
    row = {"Date": date, "Code": "99840"}
    if adjusted:
        row.update(
            {
                "AdjO": close - 1,
                "AdjH": close + 1,
                "AdjL": close - 2,
                "AdjC": close,
                "AdjVo": 1000,
            }
        )
    row.update(
        {
            "O": close * 2 - 1,
            "H": close * 2 + 1,
            "L": close * 2 - 2,
            "C": close * 2,
            "Vo": 2000,
        }
    )
    row.update(extra)
    return row


@pytest.mark.unit
class CodeConversionTests(unittest.TestCase):
    def test_to_jquants_code_strips_tokyo_suffix(self):
        self.assertEqual(to_jquants_code("9984.T"), "9984")
        self.assertEqual(to_jquants_code("9984"), "9984")
        self.assertEqual(to_jquants_code("7203.t"), "7203")

    def test_from_jquants_code_handles_4_and_5_digit(self):
        self.assertEqual(from_jquants_code("9984"), "9984.T")
        self.assertEqual(from_jquants_code("99840"), "9984.T")  # 5-digit internal


@pytest.mark.unit
class StockFetchTests(unittest.TestCase):
    def setUp(self):
        jquants_stock._records_cache.clear()

    def tearDown(self):
        jquants_stock._records_cache.clear()

    def _patch_records(self, records):
        return mock.patch(
            "tradingagents.dataflows.jp.jquants_common.fetch_records",
            return_value=records,
        )

    def test_get_stock_prefers_adjusted_prices(self):
        records = [_quote("2026-06-22", 105.0), _quote("2026-06-23", 108.0)]
        with self._patch_records(records):
            out = get_stock("9984.T", "2026-06-20", "2026-06-23")
        self.assertIn("# Stock data for 9984.T", out)
        body = out.split("\n\n", 1)[1]
        df = pd.read_csv(StringIO(body))
        self.assertEqual(list(df.columns), ["Date", "Open", "High", "Low", "Close", "Volume"])
        # Adjusted close (105/108), not the raw doubled value (210/216).
        self.assertEqual(df["Close"].tolist(), [105.0, 108.0])

    def test_get_stock_falls_back_to_raw_when_adjusted_missing(self):
        records = [_quote("2026-06-23", 50.0, adjusted=False)]  # only raw Close=100
        with self._patch_records(records):
            out = get_stock("9984.T", "2026-06-20", "2026-06-23")
        df = pd.read_csv(StringIO(out.split("\n\n", 1)[1]))
        self.assertEqual(df["Close"].tolist(), [100.0])

    def test_get_stock_requires_adjusted_data_for_bounded_incremental_use(self):
        records = [_quote("2026-06-23", 50.0, adjusted=False)]
        with (
            self._patch_records(records),
            self.assertRaisesRegex(NoMarketDataError, "adjusted close"),
        ):
            get_stock("9984.T", "2026-06-20", "2026-06-23", require_adjusted=True)

    def test_empty_response_raises_no_market_data(self):
        with self._patch_records([]), self.assertRaises(NoMarketDataError):
            get_stock("9984.T", "2026-06-20", "2026-06-23")

    def test_fetch_is_memoized_across_calls(self):
        # The get_indicators tool calls the vendor once per indicator over the
        # same window; only the first should hit the API.
        mock_fetch = mock.Mock(return_value=[_quote("2026-06-23", 100.0)])
        with mock.patch("tradingagents.dataflows.jp.jquants_common.fetch_records", mock_fetch):
            jquants_stock._fetch_ohlcv_frame("9984.T", "2026-06-20", "2026-06-23")
            jquants_stock._fetch_ohlcv_frame("9984.T", "2026-06-20", "2026-06-23")
        mock_fetch.assert_called_once()

    def test_missing_high_is_filled_not_left_nan(self):
        # A row with a present Close but null High should be ffill'd, not NaN.
        r1 = _quote("2026-06-22", 100.0)
        r2 = _quote("2026-06-23", 101.0)
        del r2["AdjH"], r2["H"]  # no high for the second row
        with self._patch_records([r1, r2]):
            out = get_stock("9984.T", "2026-06-20", "2026-06-23")
        df = pd.read_csv(StringIO(out.split("\n\n", 1)[1]))
        self.assertFalse(df["High"].isna().any())
        self.assertEqual(df["High"].tolist(), [101.0, 101.0])  # filled from prior day

    def test_get_indicator_renders_window(self):
        from tradingagents.dataflows.jp.jquants_indicator import get_indicator

        dates = pd.bdate_range(end="2026-06-23", periods=60)
        records = [_quote(d.strftime("%Y-%m-%d"), 100.0 + i) for i, d in enumerate(dates)]
        with self._patch_records(records):
            out = get_indicator("9984.T", "rsi", "2026-06-23", 5)
        self.assertIn("## rsi values from", out)
        self.assertIn("2026-06-23:", out)
        self.assertIn("RSI:", out)  # description appended


@pytest.mark.unit
class AuthTests(unittest.TestCase):
    def test_missing_api_key_raises_not_configured(self):
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            self.assertRaises(JQuantsNotConfiguredError),
        ):
            jquants_common.get_api_key()

    def test_typed_error_hierarchy(self):
        self.assertTrue(issubclass(JQuantsNotConfiguredError, VendorNotConfiguredError))
        self.assertTrue(issubclass(JQuantsRateLimitError, VendorRateLimitError))

    def test_request_sends_x_api_key_header(self):
        captured = {}

        def fake_get(url, params=None, headers=None, timeout=None):
            captured["headers"] = headers
            return FakeResp(200, {"data": []})

        with (
            mock.patch.dict(os.environ, {"JQUANTS_API_KEY": "KEY123"}, clear=True),
            mock.patch.object(jquants_common.requests, "get", side_effect=fake_get),
        ):
            jquants_common._request("/equities/bars/daily", {})
        self.assertEqual(captured["headers"], {"x-api-key": "KEY123"})

    def test_rate_limit_surfaces_typed_error(self):
        with (
            mock.patch.dict(os.environ, {"JQUANTS_API_KEY": "KEY"}, clear=True),
            mock.patch.object(jquants_common.requests, "get", return_value=FakeResp(429)),
            self.assertRaises(JQuantsRateLimitError),
        ):
            jquants_common._request("/equities/bars/daily", {})

    def test_unauthorized_surfaces_not_configured(self):
        with (
            mock.patch.dict(os.environ, {"JQUANTS_API_KEY": "BAD"}, clear=True),
            mock.patch.object(jquants_common.requests, "get", return_value=FakeResp(403)),
            self.assertRaises(JQuantsNotConfiguredError),
        ):
            jquants_common._request("/equities/bars/daily", {})

    def test_unknown_endpoint_403_is_not_mislabelled_as_auth(self):
        # J-Quants returns 403 for an unknown path too; its body says the
        # endpoint doesn't exist. That's a wrong-path programming error, not a
        # key problem — it must NOT surface as JQuantsNotConfiguredError (which
        # the router would degrade as an unconfigured vendor).
        body = (
            '{"message": "The requested endpoint does not exist. Please '
            'check the URL, HTTP method, and API version"}'
        )
        with (
            mock.patch.dict(os.environ, {"JQUANTS_API_KEY": "KEY"}, clear=True),
            mock.patch.object(
                jquants_common.requests, "get", return_value=FakeResp(403, text=body)
            ),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                jquants_common._request("/indices/topix", {})
            self.assertNotIsInstance(ctx.exception, JQuantsNotConfiguredError)
            self.assertIn("does not exist", str(ctx.exception))


@pytest.mark.unit
class RoutingTests(unittest.TestCase):
    def setUp(self):
        bind_config(copy.deepcopy(default_config.DEFAULT_CONFIG), merge=False)

    def tearDown(self):
        bind_config(copy.deepcopy(default_config.DEFAULT_CONFIG), merge=False)

    def test_tokyo_ticker_routes_to_jquants(self):
        bind_config({"data_vendors_by_market": {".T": {"core_stock_apis": "jquants"}}})
        sentinel = mock.Mock(return_value="JQ_DATA")
        yf = mock.Mock(return_value="YF_DATA")
        with mock.patch.dict(
            interface.VENDOR_METHODS,
            {"get_stock_data": {"yfinance": yf, "jquants": sentinel}},
            clear=False,
        ):
            result = interface.route_to_vendor(
                "get_stock_data", "9984.T", "2026-06-20", "2026-06-23"
            )
        self.assertEqual(result, "JQ_DATA")
        yf.assert_not_called()
        sentinel.assert_called_once()

    def test_bounded_adjusted_route_falls_back_when_jquants_lacks_adjc(self):
        bind_config({"data_vendors_by_market": {".T": {"core_stock_apis": "jquants,yfinance"}}})
        records = [_quote("2026-06-23", 50.0, adjusted=False)]
        yf = mock.Mock(return_value="YFINANCE_ADJUSTED")
        with (
            mock.patch(
                "tradingagents.dataflows.jp.jquants_common.fetch_records",
                return_value=records,
            ),
            mock.patch.dict(
                interface.VENDOR_METHODS,
                {"get_stock_data": {"jquants": jquants_stock.get_stock, "yfinance": yf}},
                clear=False,
            ),
        ):
            result = interface.route_to_vendor(
                "get_stock_data",
                "9984.T",
                "2026-06-20",
                "2026-06-23",
                _require_adjusted=True,
            )

        self.assertEqual(result, "YFINANCE_ADJUSTED")
        yf.assert_called_once_with("9984.T", "2026-06-20", "2026-06-23")

    def test_jquants_registered_for_both_methods(self):
        self.assertIn("jquants", interface.VENDOR_METHODS["get_stock_data"])
        self.assertIn("jquants", interface.VENDOR_METHODS["get_indicators"])
        self.assertIn("jquants", interface.VENDOR_LIST)


if __name__ == "__main__":
    unittest.main()
