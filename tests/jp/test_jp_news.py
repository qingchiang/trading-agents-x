"""jp_news assembler: EDINET disclosures + Google-News media, combined."""

import unittest
from datetime import date
from unittest import mock
from urllib.error import HTTPError

import pytest

from tests.data_policy import data_policy, request_context
from tests.source_results import news_fixture
from tradingagents.data.jp import http_util, jp_news, tdnet_news
from tradingagents.data.rate_limit import stop_on_rate_limit_scope
from tradingagents.domain.vendor_errors import (
    NoMarketDataError,
    VendorNotConfiguredError,
    VendorRateLimitError,
)

_EDINET_DATA = news_fixture("## 4568.T EDINET disclosures, from a to b:\n\n### 有価証券報告書")
_TDNET_DATA = news_fixture(
    "## 4568.T timely disclosures (TDnet 適時開示), from a to b:\n\n### 自己株式の取得"
)
_MEDIA_DATA = news_fixture("## 4568.T News (media, Google News), from a to b:\n\n### 決算を発表")
_EDINET_EMPTY = news_fixture("No EDINET disclosures found for 4568.T between a and b")
_TDNET_EMPTY = news_fixture("No TDnet disclosures found for 4568.T between a and b")
_MEDIA_EMPTY = news_fixture("No Google News found for 4568.T between a and b")


def _spec(value):
    """A string → mock return_value; an exception → mock side_effect (raises)."""
    is_exc = isinstance(value, BaseException) or (
        isinstance(value, type) and issubclass(value, BaseException)
    )
    return {"side_effect": value} if is_exc else {"return_value": value}


def _run(edinet, media, tdnet=_TDNET_EMPTY):
    with (
        mock.patch.object(jp_news, "_edinet_news", **_spec(edinet)),
        mock.patch.object(jp_news, "_tdnet_news", **_spec(tdnet)),
        mock.patch.object(jp_news, "_google_news", **_spec(media)),
    ):
        return jp_news.get_news("4568.T", "a", "b", data_context=request_context())


def _block(source: str, titles: list[str]) -> str:
    items = "\n\n".join(f"### {title}" for title in titles)
    return news_fixture(f"## {source}\n\n{items}")


@pytest.mark.unit
class JpNewsAssemblerTests(unittest.TestCase):
    def test_all_present_are_combined_in_order(self):
        out = _run(_EDINET_DATA, _MEDIA_DATA, tdnet=_TDNET_DATA)
        self.assertIn("有価証券報告書", out.content)
        self.assertIn("自己株式の取得", out.content)
        self.assertIn("決算を発表", out.content)
        # statutory filings, then timely disclosures, then media
        self.assertLess(out.content.index("EDINET"), out.content.index("TDnet"))
        self.assertLess(out.content.index("TDnet"), out.content.index("media"))

    def test_three_sources_share_one_30_item_budget_with_official_priority(self):
        edinet_titles = [f"EDINET item {index}" for index in range(15)]
        tdnet_titles = [f"TDnet item {index}" for index in range(15)]
        media_titles = [f"Media item {index}" for index in range(15)]

        with data_policy({"news_article_limit": 30}, merge=True):
            out = _run(
                _block("EDINET", edinet_titles),
                _block("Google News", media_titles),
                tdnet=_block("TDnet", tdnet_titles),
            )

        self.assertEqual(out.content.count("\n### "), 30)
        self.assertIn("EDINET item 14", out.content)
        self.assertIn("TDnet item 14", out.content)
        self.assertNotIn("Media item 0", out.content)
        self.assertIn(
            "returned_items=15; duplicate_items=0; kept_items=0; "
            "shared_limit=30; truncated_by_global_cap=15",
            "; ".join(record.timing for record in out.provenance),
        )

    def test_cross_source_duplicate_keeps_official_item(self):
        edinet = _block("EDINET", ["通期業績予想の修正 (filer: Example Corp)"])
        media = _block(
            "Google News",
            ["[direct] 通期業績予想の修正 (source: Example News)", "独自取材"],
        )

        with data_policy({"news_article_limit": 30}, merge=True):
            out = _run(edinet, media)

        self.assertEqual(
            sum(
                line.startswith("### ") and "通期業績予想の修正" in line
                for line in out.content.splitlines()
            ),
            1,
        )
        self.assertIn("filer: Example Corp", out.content)
        self.assertNotIn("source: Example News", out.content)
        self.assertIn("独自取材", out.content)
        self.assertIn(
            "returned_items=2; duplicate_items=1; kept_items=1; shared_limit=30",
            "; ".join(record.timing for record in out.provenance),
        )
        self.assertNotIn("truncated_by_global_cap", out.content)

    def test_configured_limit_is_applied_after_cross_source_merge(self):
        with data_policy({"news_article_limit": 2}, merge=True):
            out = _run(
                _block("EDINET", ["Official one", "Official two"]),
                _block("Google News", ["Media one"]),
                tdnet=_block("TDnet", ["Official three"]),
            )

        self.assertIn("Official one", out.content)
        self.assertIn("Official two", out.content)
        self.assertNotIn("Official three", out.content)
        self.assertNotIn("Media one", out.content)
        self.assertIn(
            "truncated_by_global_cap=1", "; ".join(record.timing for record in out.provenance)
        )

    def test_tdnet_only_present(self):
        out = _run(_EDINET_EMPTY, _MEDIA_EMPTY, tdnet=_TDNET_DATA)
        self.assertIn("自己株式の取得", out.content)
        self.assertNotIn("No TDnet disclosures found", out.content)

    def test_tdnet_error_does_not_suppress_others(self):
        out = _run(_EDINET_DATA, _MEDIA_DATA, tdnet=RuntimeError("boom"))
        self.assertIn("有価証券報告書", out.content)
        self.assertIn("決算を発表", out.content)

    def test_only_edinet_present_drops_empty_media_line(self):
        out = _run(_EDINET_DATA, _MEDIA_EMPTY)
        self.assertIn("有価証券報告書", out.content)
        self.assertNotIn("No Google News found", out.content)  # empty block omitted

    def test_only_media_present_drops_empty_edinet_line(self):
        out = _run(_EDINET_EMPTY, _MEDIA_DATA)
        self.assertIn("決算を発表", out.content)
        self.assertNotIn("No EDINET disclosures found", out.content)

    def test_edinet_error_does_not_suppress_media(self):
        # EDINET needs a key and can raise; the keyless media feed must survive.
        out = _run(VendorNotConfiguredError("EDINET_API_KEY unset"), _MEDIA_DATA)
        self.assertIn("決算を発表", out.content)

    def test_media_error_does_not_suppress_edinet(self):
        out = _run(_EDINET_DATA, RuntimeError("boom"))
        self.assertIn("有価証券報告書", out.content)

    def test_extended_window_is_clamped_only_for_tdnet(self):
        with (
            mock.patch.object(tdnet_news, "tokyo_today", return_value=date(2026, 7, 17)),
            mock.patch.object(jp_news, "_edinet_news", return_value=_EDINET_DATA) as edinet,
            mock.patch.object(jp_news, "_tdnet_news", return_value=_TDNET_DATA) as tdnet,
            mock.patch.object(jp_news, "_google_news", return_value=_MEDIA_DATA) as media,
        ):
            jp_news.get_news("4568.T", "2026-04-19", "2026-07-17", data_context=request_context())

        edinet.assert_called_once_with(
            "4568.T", "2026-04-19", "2026-07-17", data_context=request_context()
        )
        tdnet.assert_called_once_with(
            "4568.T", "2026-06-17", "2026-07-17", data_context=request_context()
        )
        media.assert_called_once_with(
            "4568.T", "2026-04-19", "2026-07-17", data_context=request_context()
        )

    def test_edinet_capped_window_is_recorded_as_limited_provenance(self):
        with (
            mock.patch.object(jp_news, "_edinet_news", return_value=_EDINET_DATA),
            mock.patch.object(jp_news, "_tdnet_news", return_value=_TDNET_EMPTY),
            mock.patch.object(jp_news, "_google_news", return_value=_MEDIA_EMPTY),
        ):
            out = jp_news.get_news(
                "4568.T", "2020-01-01", "2026-07-17", data_context=request_context()
            )

        record = next(record for record in list(out.provenance) if record.source == "EDINET")
        self.assertEqual(record.effective, "2026-04-19 to 2026-07-17")
        self.assertIn("source_window_limited", record.timing)

    def test_tdnet_retained_archive_overlap_is_recorded_as_limited_provenance(self):
        with (
            mock.patch.object(tdnet_news, "tokyo_today", return_value=date(2026, 7, 12)),
            mock.patch.object(jp_news, "_edinet_news", return_value=_EDINET_EMPTY),
            mock.patch.object(jp_news, "_tdnet_news", return_value=_TDNET_DATA),
            mock.patch.object(jp_news, "_google_news", return_value=_MEDIA_EMPTY),
        ):
            out = jp_news.get_news(
                "4568.T", "2026-06-01", "2026-07-05", data_context=request_context()
            )

        record = next(record for record in list(out.provenance) if record.source == "TDnet")
        self.assertEqual(record.effective, "2026-06-12 to 2026-07-05")
        self.assertIn("source_window_limited", record.timing)

    def test_tdnet_expired_archive_is_recorded_as_not_queried(self):
        with (
            mock.patch.object(tdnet_news, "tokyo_today", return_value=date(2026, 7, 12)),
            mock.patch.object(jp_news, "_edinet_news", return_value=_EDINET_DATA),
            mock.patch.object(
                jp_news,
                "_tdnet_news",
                return_value=news_fixture(
                    "<TDnet unavailable: requested window is outside the rolling archive>"
                ),
            ),
            mock.patch.object(jp_news, "_google_news", return_value=_MEDIA_EMPTY),
        ):
            out = jp_news.get_news(
                "4568.T", "2026-05-01", "2026-06-01", data_context=request_context()
            )

        record = next(record for record in list(out.provenance) if record.source == "TDnet")
        self.assertEqual(record.effective, "outside rolling TDnet archive; no query")
        self.assertIn("source_window_limited", record.timing)

    def test_both_empty_raises_no_market_data(self):
        with self.assertRaises(NoMarketDataError):
            _run(_EDINET_EMPTY, _MEDIA_EMPTY)

    def test_edinet_error_and_empty_media_raises(self):
        with self.assertRaises(NoMarketDataError) as ctx:
            _run(RuntimeError("boom"), _MEDIA_EMPTY)
        note = ctx.exception.availability_notes[0]
        self.assertIn("<EDINET unavailable: RuntimeError>", note.content)
        records = list(note.provenance)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].source, "EDINET")
        self.assertEqual(records[0].timing, "unavailable")

    def test_unscoped_rate_limit_still_degrades_to_other_feeds(self):
        out = _run(VendorRateLimitError("slow down"), _MEDIA_DATA)

        self.assertIn("決算を発表", out.content)
        self.assertIn("<EDINET unavailable: VendorRateLimitError>", out.content)

    def test_scoped_rate_limit_stops_before_later_subfeeds(self):
        edinet = mock.Mock(side_effect=VendorRateLimitError("slow down"))
        tdnet = mock.Mock(return_value=_TDNET_DATA)
        media = mock.Mock(return_value=_MEDIA_DATA)
        with (
            mock.patch.object(jp_news, "_edinet_news", edinet),
            mock.patch.object(jp_news, "_tdnet_news", tdnet),
            mock.patch.object(jp_news, "_google_news", media),
            stop_on_rate_limit_scope(True),
            self.assertRaises(VendorRateLimitError),
        ):
            jp_news.get_news("4568.T", "2026-06-20", "2026-06-22", data_context=request_context())

        edinet.assert_called_once()
        tdnet.assert_not_called()
        media.assert_not_called()

    def test_scoped_tdnet_http_429_raises_without_retry_or_later_feed(self):
        rate_limited = HTTPError("https://example.test", 429, "Too Many", {}, None)
        media = mock.Mock(return_value=_MEDIA_DATA)
        with (
            mock.patch.object(tdnet_news, "tokyo_today", return_value=date(2026, 8, 25)),
            mock.patch.object(jp_news, "_edinet_news", return_value=_EDINET_EMPTY),
            mock.patch.object(jp_news, "_google_news", media),
            mock.patch.object(http_util, "urlopen", side_effect=rate_limited) as urlopen,
            mock.patch.object(http_util.time, "sleep") as sleep,
            stop_on_rate_limit_scope(True),
            self.assertRaises(VendorRateLimitError),
        ):
            jp_news.get_news("7203.T", "2026-08-20", "2026-08-25", data_context=request_context())

        self.assertEqual(urlopen.call_count, 1)
        sleep.assert_not_called()
        media.assert_not_called()

    def test_scoped_tdnet_transport_failure_becomes_note_and_keeps_later_feed(self):
        media = mock.Mock(return_value=_MEDIA_EMPTY)
        with (
            mock.patch.object(tdnet_news, "tokyo_today", return_value=date(2026, 8, 25)),
            mock.patch.object(jp_news, "_edinet_news", return_value=_EDINET_EMPTY),
            mock.patch.object(jp_news, "_google_news", media),
            mock.patch.object(http_util, "urlopen", side_effect=OSError("fixture transport")),
            stop_on_rate_limit_scope(True),
            self.assertRaises(NoMarketDataError) as ctx,
        ):
            jp_news.get_news("7203.T", "2026-08-20", "2026-08-25", data_context=request_context())

        self.assertIn(
            "<TDnet unavailable: VendorTransportError>", ctx.exception.availability_notes[0].content
        )
        media.assert_called_once()


if __name__ == "__main__":
    unittest.main()
