"""Shared JP HTTP helper: identified UA + one 429/Retry-After backoff fetch."""
import unittest
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from tradingagents.dataflows.errors import VendorTransportError
from tradingagents.dataflows.jp import http_util
from tradingagents.dataflows.rate_limit import stop_on_rate_limit_scope


class _Resp:
    """Minimal context-manager stand-in for urlopen()."""

    def __init__(self, data: bytes):
        self._data = data

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _req():
    return Request("https://example.test/feed")


@pytest.mark.unit
class RetryAfterSecondsTests(unittest.TestCase):
    def test_reads_header_capped_at_30s(self):
        exc = HTTPError("u", 429, "x", {"Retry-After": "120"}, None)
        self.assertEqual(http_util.retry_after_seconds(exc), 30.0)

    def test_missing_header_returns_none(self):
        exc = HTTPError("u", 429, "x", {}, None)
        self.assertIsNone(http_util.retry_after_seconds(exc))

    def test_non_numeric_header_returns_none(self):
        exc = HTTPError("u", 429, "x", {"Retry-After": "soon"}, None)
        self.assertIsNone(http_util.retry_after_seconds(exc))


@pytest.mark.unit
class FetchBytesTests(unittest.TestCase):
    def test_success_returns_body_bytes(self):
        with mock.patch.object(http_util, "urlopen", return_value=_Resp(b"OK")):
            self.assertEqual(http_util.fetch_bytes(_req(), 5, "T"), b"OK")

    def test_429_backs_off_once_then_succeeds(self):
        err = HTTPError("u", 429, "Too Many", {}, None)
        with mock.patch.object(http_util, "urlopen", side_effect=[err, _Resp(b"OK")]) as uo, \
                mock.patch.object(http_util.time, "sleep") as slept:
            out = http_util.fetch_bytes(_req(), 5, "T")
        self.assertEqual(out, b"OK")
        self.assertEqual(uo.call_count, 2)
        slept.assert_called_once()

    def test_persistent_429_gives_up_after_one_retry(self):
        err = HTTPError("u", 429, "Too Many", {}, None)
        with mock.patch.object(http_util, "urlopen", side_effect=[err, err]) as uo, \
                mock.patch.object(http_util.time, "sleep"):
            self.assertIsNone(http_util.fetch_bytes(_req(), 5, "T"))
        self.assertEqual(uo.call_count, 2)  # original + one retry, then degrade

    def test_non_429_http_error_returns_none(self):
        err = HTTPError("u", 404, "Not Found", {}, None)
        with mock.patch.object(http_util, "urlopen", side_effect=err):
            self.assertIsNone(http_util.fetch_bytes(_req(), 5, "T"))

    def test_scoped_non_429_http_error_raises_typed_transport_failure(self):
        err = HTTPError("u", 503, "Unavailable", {}, None)
        with (
            mock.patch.object(http_util, "urlopen", side_effect=err),
            stop_on_rate_limit_scope(True),
            self.assertRaises(VendorTransportError),
        ):
            http_util.fetch_bytes(_req(), 5, "T")

    def test_network_error_returns_none(self):
        with mock.patch.object(http_util, "urlopen", side_effect=OSError("boom")):
            self.assertIsNone(http_util.fetch_bytes(_req(), 5, "T"))


if __name__ == "__main__":
    unittest.main()
