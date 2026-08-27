"""TDnet timely-disclosure (適時開示) vendor, via the keyless code/date search."""
import unittest
from datetime import date, datetime
from unittest import mock
from urllib.error import HTTPError
from urllib.parse import parse_qs

import pytest

from tradingagents.dataflows.jp import http_util, tdnet_news as td


def _row(code="72030", title="2026年3月期決算短信", pdf="/inbs/140120260710590974.pdf",
         when="2026/07/10 16:00", cls="odd"):
    return (
        f'<tr class="{cls}">'
        f'<td class="time" nowrap>{when}</td>'
        f'<td class="code" nowrap>{code}</td>'
        f'<td class="companyname">トヨタ自</td>'
        f'<td class="title" align=left><a target="_blank" href="{pdf}">{title}</a></td>'
        f'<td class="xbrl"><br></td>'
        f'<td class="exchange">東</td>'
        f'<td class="update"><br></td>'
        f"</tr>"
    )


def _page(*rows: str, count: int | None = None) -> str:
    # Real TDnet renders the count as ``<span id="result">N件</span>`` (件 inside
    # the span, right after the digits) — keep this in lockstep with the markup.
    n = len(rows) if count is None else count
    return (
        f'<html><body><h4><span id="result">{n}件</span>の結果</h4>'
        f'<table id="maintable">' + "".join(rows) + "</table></body></html>"
    )


@pytest.mark.unit
class SearchTests(unittest.TestCase):
    """`_search` builds the POST and decodes; the fetch/backoff lives in http_util."""

    def test_posts_code_and_date_range_then_decodes(self):
        captured = {}

        def fake_fetch(req, timeout, label):
            captured["url"] = req.full_url
            captured["body"] = parse_qs(req.data.decode())
            return _page(_row()).encode()

        with mock.patch.object(td, "fetch_bytes", side_effect=fake_fetch):
            html = td._search("7203", "20260612", "20260712", timeout=5)
        self.assertIn("maintable", html)  # bytes decoded to str
        self.assertEqual(captured["url"], td._SEARCH_URL)
        self.assertEqual(captured["body"]["q"], ["7203"])
        self.assertEqual(captured["body"]["t0"], ["20260612"])
        self.assertEqual(captured["body"]["t1"], ["20260712"])
        self.assertEqual(captured["body"]["m"], ["0"])

    def test_failed_fetch_returns_none(self):
        with mock.patch.object(td, "fetch_bytes", return_value=None):
            self.assertIsNone(td._search("7203", "20260612", "20260712", timeout=5))


@pytest.mark.unit
class ParseRowsTests(unittest.TestCase):
    def test_extracts_fields_and_absolute_pdf(self):
        rows = td._parse_rows(_page(_row(when="2026/07/10 16:00")))
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["code"], "72030")
        self.assertEqual(r["title"], "2026年3月期決算短信")
        self.assertEqual(r["at"], datetime(2026, 7, 10, 16, 0))
        self.assertEqual(
            r["pdf"], "https://www.release.tdnet.info/inbs/140120260710590974.pdf"
        )

    def test_decodes_html_entities_in_title(self):
        rows = td._parse_rows(_page(_row(title="M&amp;A・株式交換に関するお知らせ")))
        self.assertEqual(rows[0]["title"], "M&A・株式交換に関するお知らせ")

    def test_skips_unparseable_timestamp(self):
        # An undated row can't be proven in-window, so it's dropped.
        self.assertEqual(td._parse_rows(_page(_row(when="不明"))), [])

    def test_tolerates_seconds_in_timestamp(self):
        rows = td._parse_rows(_page(_row(when="2026/07/10 16:00:30")))
        self.assertEqual(rows[0]["at"], datetime(2026, 7, 10, 16, 0, 30))

    def test_parses_row_with_extra_tr_attributes(self):
        # A benign markup tweak (extra attribute on <tr>) must not drop every row.
        row = _row().replace('<tr class="odd">', '<tr class="odd" data-id="9" id="r1">')
        self.assertEqual(len(td._parse_rows(_page(row))), 1)

    def test_absolute_pdf_href_passes_through(self):
        rows = td._parse_rows(_page(_row(pdf="https://cdn.example.jp/doc.pdf")))
        self.assertEqual(rows[0]["pdf"], "https://cdn.example.jp/doc.pdf")

    def test_skips_row_without_anchor(self):
        broken = (
            '<tr class="odd"><td class="time">2026/07/10 16:00</td>'
            '<td class="code">72030</td>'
            '<td class="title">no link here</td></tr>'
        )
        self.assertEqual(td._parse_rows(_page(broken)), [])

    def test_parses_odd_and_even_rows(self):
        rows = td._parse_rows(_page(_row(code="72030", cls="odd"),
                                    _row(code="67580", cls="even")))
        self.assertEqual([r["code"] for r in rows], ["72030", "67580"])


@pytest.mark.unit
class GetNewsTests(unittest.TestCase):
    def setUp(self):
        mock.patch.object(td, "get_config", return_value={"news_article_limit": 10}).start()
        mock.patch.object(td, "tokyo_today", return_value=date(2026, 7, 12)).start()

    def tearDown(self):
        mock.patch.stopall()

    def _run(self, html, ticker="7203.T", start="2026-06-12", end="2026-07-12"):
        with mock.patch.object(td, "_search", return_value=html):
            return td.get_news(ticker, start, end)

    def test_filters_by_securities_code(self):
        html = _page(_row(code="72030", title="対象"), _row(code="99840", title="他社"))
        out = self._run(html)
        self.assertIn("対象", out)
        self.assertNotIn("他社", out)

    def test_window_filter_enforces_look_ahead(self):
        # The search tolerates loose ranges, so out-of-window rows must be dropped
        # client-side: only the row inside [start, end] survives.
        html = _page(
            _row(title="前", when="2026/06/10 10:00"),   # before start
            _row(title="窓内", when="2026/06/20 10:00"),  # inside
            _row(title="未来", when="2026/07/20 10:00"),  # after end
        )
        out = self._run(html, start="2026-06-15", end="2026-07-01")
        self.assertIn("窓内", out)
        self.assertNotIn("前", out)
        self.assertNotIn("未来", out)

    def test_renders_block_header_and_pdf(self):
        html = _page(_row(title="決算短信", pdf="/inbs/1.pdf", when="2026/07/10 15:00"))
        out = self._run(html)
        self.assertIn("## 7203.T timely disclosures (TDnet 適時開示)", out)
        self.assertIn("### 決算短信", out)
        self.assertIn("Disclosed: 2026-07-10 15:00 JST", out)
        self.assertIn("https://www.release.tdnet.info/inbs/1.pdf", out)

    def test_no_match_returns_no_disclosures_line(self):
        out = self._run(_page(_row(code="99840", title="他社")))
        self.assertIn("No TDnet disclosures found for 7203.T", out)

    def test_search_failure_returns_no_disclosures_line(self):
        out = self._run(None)  # network degraded to None
        self.assertIn("No TDnet disclosures found for 7203.T", out)

    def test_unscoped_429_retries_once_then_degrades(self):
        rate_limited = HTTPError("https://example.test", 429, "Too Many", {}, None)
        with (
            mock.patch.object(http_util, "urlopen", side_effect=[rate_limited, rate_limited]) as urlopen,
            mock.patch.object(http_util.time, "sleep") as sleep,
        ):
            out = td.get_news("7203.T", "2026-06-12", "2026-07-12")

        self.assertIn("No TDnet disclosures found for 7203.T", out)
        self.assertEqual(urlopen.call_count, 2)
        sleep.assert_called_once()

    def test_newest_first(self):
        html = _page(_row(title="古い", when="2026/06/13 09:00"),
                     _row(title="新しい", when="2026/07/10 15:00"))
        out = self._run(html)
        self.assertLess(out.index("新しい"), out.index("古い"))

    def test_capped_to_article_limit(self):
        rows = [_row(title=f"開示{i}", when=f"2026/07/1{i} 10:00") for i in range(5)]
        with mock.patch.object(td, "get_config", return_value={"news_article_limit": 2}):
            out = self._run(_page(*rows))
        self.assertEqual(out.count("### 開示"), 2)

    def test_malformed_input_dates_returns_no_disclosures_line(self):
        out = self._run(_page(_row()), start="bad", end="date")
        self.assertIn("No TDnet disclosures found", out)

    def test_historical_window_outside_free_archive_is_unavailable(self):
        with mock.patch.object(td, "_search") as search:
            out = td.get_news("7203.T", "2026-05-01", "2026-05-31")
        search.assert_not_called()
        self.assertIn("<TDnet unavailable:", out)

    def test_requested_window_is_clamped_to_31_calendar_dates(self):
        with mock.patch.object(td, "_search", return_value=_page()) as search:
            td.get_news("7203.T", "2026-01-01", "2026-07-12")
        search.assert_called_once_with("7203", "20260612", "20260712", 10.0)

    def test_warns_when_result_count_exceeds_parsed_rows(self):
        # TDnet reports more matches than the page yielded (possible pagination):
        # the count regex must match the real ``>N件</span>`` markup to catch it.
        html = _page(_row(), count=5)  # header says 5, only 1 row present
        with self.assertLogs(td.logger, level="WARNING") as logs:
            self._run(html)
        self.assertTrue(any("possible pagination" in m for m in logs.output))

    def test_no_truncation_warning_when_counts_agree(self):
        html = _page(_row(), _row(code="99840"))  # count == 2 == rows parsed
        with mock.patch.object(td.logger, "warning") as warn:
            self._run(html)
        warn.assert_not_called()


@pytest.mark.unit
class RegistrationTests(unittest.TestCase):
    def test_tdnet_news_registered_for_get_news(self):
        from tradingagents.dataflows import interface
        self.assertIn("tdnet_news", interface.VENDOR_METHODS["get_news"])
        self.assertIn("tdnet_news", interface.VENDOR_LIST)


if __name__ == "__main__":
    unittest.main()
