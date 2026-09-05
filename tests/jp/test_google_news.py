"""Google News (JP) media-headline vendor. Network mocked."""
import unittest
from datetime import datetime
from unittest import mock

import pytest

from tradingagents.dataflows.jp import google_news as gn


def _rss(*items: str) -> bytes:
    body = "".join(items)
    return f"<rss><channel>{body}</channel></rss>".encode()


def _item(title, source="日本経済新聞", pub="Fri, 10 Jul 2026 06:54:42 GMT", src_url="https://x"):
    return (
        f"<item><title>{title}</title><pubDate>{pub}</pubDate>"
        f'<source url="{src_url}">{source}</source></item>'
    )


def _parsed(title, source="日本経済新聞", y=2026, m=7, d=10):
    return {"title": title, "source": source, "pub_date": datetime(y, m, d, 12, 0)}


@pytest.mark.unit
class FetchItemsTests(unittest.TestCase):
    """Parsing only; the fetch/backoff lives in http_util (see test_http_util)."""

    def test_parses_and_strips_source_suffix(self):
        xml = _rss(_item("大事件が起きた - 日本経済新聞"))
        with mock.patch.object(gn, "fetch_bytes", return_value=xml):
            items = gn._fetch_items("第一三共", timeout=5)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "大事件が起きた")   # " - source" stripped
        self.assertEqual(items[0]["source"], "日本経済新聞")
        # 06:54:42 GMT -> 15:54:42 JST (naive).
        self.assertEqual(items[0]["pub_date"], datetime(2026, 7, 10, 15, 54, 42))

    def test_failed_fetch_degrades_to_empty(self):
        with mock.patch.object(gn, "fetch_bytes", return_value=None):
            self.assertEqual(gn._fetch_items("第一三共", timeout=5), [])

    def test_malformed_xml_degrades_to_empty(self):
        with mock.patch.object(gn, "fetch_bytes", return_value=b"<rss><broken"):
            self.assertEqual(gn._fetch_items("第一三共", timeout=5), [])


@pytest.mark.unit
class GetNewsTests(unittest.TestCase):
    def setUp(self):
        self.cfg = mock.patch.object(gn, "get_config", return_value={"news_article_limit": 10})
        self.cfg.start()
        self.name = mock.patch.object(gn, "get_company_name", return_value="第一三共")
        self.name.start()

    def tearDown(self):
        mock.patch.stopall()

    def _run(self, items, start="2026-07-04", end="2026-07-11"):
        with mock.patch.object(gn, "_fetch_items", return_value=items):
            return gn.get_news("4568.T", start, end)

    def test_renders_headlines_with_source_and_date(self):
        out = self._run([_parsed("第一三共が決算を発表", "日本経済新聞")])
        self.assertIn("## 4568.T News (media, Google News)", out)
        self.assertIn("### [direct] 第一三共が決算を発表", out)
        self.assertIn("candidates=1; relevant=1; kept=1", out)
        self.assertIn("2026-07-10", out)

    def test_window_filter_excludes_out_of_range(self):
        items = [
            _parsed("第一三共の決算は窓内", d=10),
            _parsed("第一三共の古い決算", m=6, d=1),
            _parsed("第一三共の未来の決算", d=20),
        ]
        out = self._run(items)
        self.assertIn("決算は窓内", out)
        self.assertNotIn("古い決算", out)
        self.assertNotIn("未来の決算", out)

    def test_boilerplate_yahoo_quote_pages_dropped(self):
        # Yahoo quote pages carry the "】：" separator; real headlines don't.
        items = [
            _parsed("第一三共(株)【4568】：掲示板"),
            _parsed("第一三共の新薬事業を報道"),
        ]
        out = self._run(items)
        self.assertNotIn("掲示板", out)
        self.assertIn("新薬事業を報道", out)

    def test_real_headline_with_boilerplate_word_kept(self):
        # A genuine headline containing a word like 決算情報 must NOT be dropped —
        # only the Yahoo "】：" template pattern is boilerplate.
        out = self._run([_parsed("第一三共の2026年決算情報を公開")])
        self.assertIn("第一三共の2026年決算情報を公開", out)

    def test_dedupes_repeated_headline(self):
        items = [_parsed("第一三共が投資", d=10), _parsed("第一三共が投資", d=9)]
        out = self._run(items)
        self.assertEqual(out.count("### [direct] 第一三共が投資"), 1)

    def test_reports_separate_source_filter_counts(self):
        items = [
            _parsed("第一三共が決算を発表", d=10),
            _parsed("第一三共が決算を発表", d=9),
            _parsed("第一三共が研究事業へ投資", d=8),
            _parsed("第一三共が投資を発表", "Mshale", d=7),
            _parsed("第一三共の未来の決算", d=20),
        ]
        with mock.patch.object(gn, "get_config", return_value={"news_article_limit": 1}):
            out = self._run(items)

        self.assertIn("upstream_returned=5", out)
        self.assertIn("date_filtered=1", out)
        self.assertIn("relevance_filtered=1", out)
        self.assertIn("duplicates=1", out)
        self.assertIn("source_truncated=1", out)
        self.assertIn("candidates=1", out)
        self.assertIn("dropped=2; omitted_by_limit=1", out)

    def test_sorted_newest_first(self):
        items = [_parsed("第一三共の古い決算", d=6), _parsed("第一三共の新しい決算", d=10)]
        out = self._run(items)
        self.assertLess(out.index("新しい決算"), out.index("古い決算"))

    def test_capped_to_article_limit(self):
        with mock.patch.object(gn, "get_config", return_value={"news_article_limit": 2}):
            out = self._run([_parsed(f"第一三共の投資記事{i}", d=10 - i) for i in range(5)])
        self.assertEqual(out.count("### [direct]"), 2)
        self.assertIn("omitted_by_limit=3", out)

    def test_no_items_returns_no_news_line(self):
        out = self._run([])
        self.assertIn("No Google News found for 4568.T", out)
        self.assertIn("upstream_returned=0", out)
        self.assertIn("candidates=0", out)

    def test_malformed_date_returns_no_news_line(self):
        out = self._run([_parsed("記事")], start="bad", end="date")
        self.assertIn("No Google News found", out)

    def test_hard_filters_blocked_source_ai_template_and_disclosure_mirror(self):
        items = [
            _parsed("第一三共が投資を発表", "Mshale"),
            _parsed("第一三共(株)【4568】：今の株価の理由は？値動きの背景をAIが解説", "Yahoo!ファイナンス"),
            _parsed("[4568]第一三共 適時開示情報", "日経会社情報DIGITAL"),
            _parsed("第一三共が新薬へ投資", "ロイター"),
        ]
        out = self._run(items)
        self.assertNotIn("Mshale", out)
        self.assertNotIn("今の株価の理由", out)
        self.assertNotIn("適時開示情報", out)
        self.assertIn("新薬へ投資", out)
        self.assertIn("dropped=3", out)

    def test_trusted_business_background_without_company_is_context(self):
        out = self._run([_parsed("製薬業界で大型買収が相次ぐ", "Reuters")])
        self.assertIn("### [context] 製薬業界で大型買収が相次ぐ", out)

    def test_unknown_source_requires_entity_and_business_context(self):
        out = self._run([
            _parsed("第一三共のファン交流会", "Unknown Blog"),
            _parsed("第一三共が研究事業へ投資", "Unknown Business Wire"),
        ])
        self.assertNotIn("ファン交流会", out)
        self.assertIn("### [direct] 第一三共が研究事業へ投資", out)

    def test_japanese_group_abbreviation_is_a_company_alias(self):
        with mock.patch.object(
            gn, "get_company_name", return_value="ソフトバンクグループ"
        ), mock.patch.object(
            gn,
            "_fetch_items",
            return_value=[
                _parsed("ソフトバンクGがAI事業へ投資", "Unknown Wire")
            ],
        ):
            out = gn.get_news("9984.T", "2026-07-04", "2026-07-11")
        self.assertIn("### [direct] ソフトバンクGがAI事業へ投資", out)

    def test_query_is_name_plus_code(self):
        # "{name} {code}" softly biases ranking to the financial context.
        with mock.patch.object(gn, "_fetch_items", return_value=[]) as fi:
            gn.get_news("4568.T", "2026-07-04", "2026-07-11")
        self.assertEqual(fi.call_args.args[0], "第一三共 4568")

    def test_falls_back_to_code_when_name_unresolved(self):
        with mock.patch.object(gn, "get_company_name", return_value=None), \
                mock.patch.object(gn, "_fetch_items", return_value=[]) as fi:
            gn.get_news("4568.T", "2026-07-04", "2026-07-11")
        fi.assert_called_once()
        self.assertEqual(fi.call_args.args[0], "4568")  # bare code query


if __name__ == "__main__":
    unittest.main()
