"""jp_news assembler: EDINET disclosures + Google-News media, combined."""

import unittest
from datetime import date, datetime, timedelta, timezone
from unittest import mock

import pytest
from langchain_core.messages import ToolMessage

from tradingagents.dataflows.errors import NoMarketDataError, VendorNotConfiguredError
from tradingagents.dataflows.jp import jp_news
from tradingagents.graph.research_graph import (
    _collect_evidence,
    _filter_tool_output_at_information_frontier,
)
from tradingagents.provenance import (
    ProvenanceRecord,
    SourceObservation,
    SourceWatermark,
    attach_provenance,
    attach_source_observations,
    attach_source_watermarks,
    extract_evidence_spans,
    extract_provenance,
    extract_source_observations,
    extract_source_watermarks,
)

_EDINET_DATA = "## 4568.T EDINET disclosures, from a to b:\n\n### 有価証券報告書"
_TDNET_DATA = "## 4568.T timely disclosures (TDnet 適時開示), from a to b:\n\n### 自己株式の取得"
_MEDIA_DATA = "## 4568.T News (media, Google News), from a to b:\n\n### 決算を発表"
_EDINET_EMPTY = "No EDINET disclosures found for 4568.T between a and b"
_TDNET_EMPTY = "No TDnet disclosures found for 4568.T between a and b"
_MEDIA_EMPTY = "No Google News found for 4568.T between a and b"


def _google_payload(
    body: str,
    *,
    retrieved_at: str = "2026-07-17T10:00:00+09:00",
) -> str:
    return attach_provenance(
        body,
        ProvenanceRecord(
            evidence="get_news",
            source="Google News",
            requested="2026-07-03 to 2026-07-17",
            effective="2026-07-03 to 2026-07-17",
            timing="live non-point-in-time; publication-date filtered",
            retrieved_at=retrieved_at,
        ),
    )


def _official_payload(
    body: str,
    source: str,
    scanned_start: str,
    scanned_end: str,
    *,
    information_frontier: str | None = None,
) -> str:
    return attach_source_watermarks(
        body,
        SourceWatermark(
            source=source,
            scanned_start=scanned_start,
            scanned_end=scanned_end,
            status="complete",
            returned_records=body.count("\n### "),
            information_frontier=information_frontier,
        ),
    )


def _spec(value):
    """A string → mock return_value; an exception → mock side_effect (raises)."""
    is_exc = isinstance(value, BaseException) or (
        isinstance(value, type) and issubclass(value, BaseException)
    )
    return {"side_effect": value} if is_exc else {"return_value": value}


def _run(edinet, media, tdnet=_TDNET_EMPTY):
    if (
        isinstance(edinet, str)
        and edinet.startswith("## ")
        and not extract_source_watermarks(edinet)
    ):
        edinet = _official_payload(
            edinet,
            "EDINET",
            "2026-04-19",
            "2026-07-17",
        )
    if (
        isinstance(tdnet, str)
        and tdnet.startswith("## ")
        and not extract_source_watermarks(tdnet)
    ):
        tdnet = _official_payload(
            tdnet,
            "TDnet",
            "2026-06-17",
            "2026-07-17",
        )
    if isinstance(media, str) and not extract_provenance(media):
        media = _google_payload(media)
    with (
        mock.patch.object(jp_news, "_edinet_news", **_spec(edinet)),
        mock.patch.object(jp_news, "_tdnet_news", **_spec(tdnet)),
        mock.patch.object(jp_news, "_google_news", **_spec(media)),
    ):
        return jp_news.get_news("4568.T", "2026-07-03", "2026-07-17")


def _block(source: str, titles: list[str]) -> str:
    items = "\n\n".join(f"### {title}" for title in titles)
    return f"## {source}\n\n{items}"


@pytest.mark.unit
class JpNewsAssemblerTests(unittest.TestCase):
    def test_all_present_are_combined_in_order(self):
        out = _run(_EDINET_DATA, _MEDIA_DATA, tdnet=_TDNET_DATA)
        self.assertIn("有価証券報告書", out)
        self.assertIn("自己株式の取得", out)
        self.assertIn("決算を発表", out)
        # statutory filings, then timely disclosures, then media
        self.assertLess(out.index("EDINET"), out.index("TDnet"))
        self.assertLess(out.index("TDnet"), out.index("media"))

    def test_each_channel_has_independent_temporal_span_and_provenance(self):
        out = _run(_EDINET_DATA, _MEDIA_DATA, tdnet=_TDNET_DATA)

        spans = extract_evidence_spans(out)
        by_source = {
            span.records[0].source: span
            for span in spans
            if span.records
        }

        self.assertEqual(set(by_source), {"EDINET", "TDnet", "Google News"})
        self.assertEqual(by_source["EDINET"].temporal_scope, "point_in_time")
        self.assertEqual(by_source["TDnet"].temporal_scope, "point_in_time")
        google = by_source["Google News"]
        self.assertEqual(google.temporal_scope, "live_only")
        self.assertIsNotNone(google.records[0].retrieved_at)
        self.assertIsNotNone(
            datetime.fromisoformat(google.records[0].retrieved_at).utcoffset()
        )

    def test_assembler_reuses_google_producer_timestamp_without_restamping(self):
        payload = _google_payload(
            _MEDIA_DATA,
            retrieved_at="2026-08-12T23:00:00+09:00",
        )

        first = _run(_EDINET_DATA, payload, tdnet=_TDNET_DATA)
        second = _run(_EDINET_DATA, payload, tdnet=_TDNET_DATA)

        for content in (first, second):
            google = next(
                span
                for span in extract_evidence_spans(content)
                if span.records and span.records[0].source == "Google News"
            )
            self.assertEqual(
                google.records[0].retrieved_at,
                "2026-08-12T23:00:00+09:00",
            )

    def test_each_channel_keeps_only_its_own_source_metadata_in_evidence(self):
        frontier = datetime(
            2026,
            8,
            10,
            23,
            59,
            tzinfo=timezone(timedelta(hours=9)),
        )
        official = {
            "EDINET": (_EDINET_DATA, "edinet:S100"),
            "TDnet": (_TDNET_DATA, "tdnet:1401"),
        }
        rendered = {}
        for source, (body, version_id) in official.items():
            rendered[source] = attach_source_watermarks(
                attach_source_observations(
                    body,
                    SourceObservation(
                        source=source,
                        record_id=version_id,
                        version_id=version_id,
                        status="published",
                        published_at="2026-08-10 15:00",
                        available_at="2026-08-10T15:00:00+09:00",
                        title=source,
                    ),
                ),
                SourceWatermark(
                    source=source,
                    scanned_start="2026-07-12",
                    scanned_end="2026-08-10",
                    status="complete",
                    returned_records=1,
                    information_frontier=frontier.isoformat(),
                ),
            )
        with (
            mock.patch.object(jp_news, "_edinet_news", return_value=rendered["EDINET"]),
            mock.patch.object(jp_news, "_tdnet_news", return_value=rendered["TDnet"]),
            mock.patch.object(
                jp_news,
                "_google_news",
                return_value=_google_payload(
                    _MEDIA_DATA,
                    retrieved_at="2026-08-12T23:00:00+09:00",
                ),
            ),
        ):
            content = jp_news.get_news(
                "4568.T",
                "2026-07-12",
                "2026-08-10",
                information_frontier=frontier.isoformat(),
            )

        evidence = _collect_evidence(
            [
                ToolMessage(
                    content=content,
                    tool_call_id="jp-news-source-metadata",
                    name="get_news",
                )
            ],
            "",
            requested_date=date(2026, 8, 10),
            analyst="news",
        )

        for item in evidence:
            origin_sources = {origin.source for origin in item.origins}
            record_sources = {
                record["source"] for record in item.provenance.get("source_records", [])
            }
            watermark_sources = {
                record["source"]
                for record in item.provenance.get("source_watermarks", [])
            }
            self.assertTrue(record_sources <= origin_sources)
            self.assertTrue(watermark_sources <= origin_sources)

    def test_expired_google_span_does_not_erase_official_siblings(self):
        frontier = datetime(
            2026,
            8,
            1,
            23,
            59,
            tzinfo=timezone(timedelta(hours=9)),
        )
        edinet = attach_source_watermarks(
            attach_source_observations(
                _EDINET_DATA,
                SourceObservation(
                    source="EDINET",
                    record_id="S100",
                    version_id="edinet:S100",
                    status="published",
                    published_at="2026-08-01 15:00",
                    available_at="2026-08-01T15:00:00+09:00",
                    title="有価証券報告書",
                ),
            ),
            SourceWatermark(
                source="EDINET",
                scanned_start="2026-07-01",
                scanned_end="2026-08-01",
                status="complete",
                returned_records=1,
                information_frontier=frontier.isoformat(),
            ),
        )
        tdnet = attach_source_watermarks(
            attach_source_observations(
                _TDNET_DATA,
                SourceObservation(
                    source="TDnet",
                    record_id="1401",
                    version_id="tdnet:1401",
                    status="published",
                    published_at="2026-08-01 16:00",
                    available_at="2026-08-01T16:00:00+09:00",
                    title="自己株式の取得",
                ),
            ),
            SourceWatermark(
                source="TDnet",
                scanned_start="2026-07-02",
                scanned_end="2026-08-01",
                status="complete",
                returned_records=1,
                information_frontier=frontier.isoformat(),
            ),
        )
        with (
            mock.patch.object(jp_news, "_edinet_news", return_value=edinet),
            mock.patch.object(jp_news, "_tdnet_news", return_value=tdnet),
            mock.patch.object(
                jp_news,
                "_google_news",
                return_value=_google_payload(
                    _MEDIA_DATA,
                    retrieved_at="2026-08-14T00:20:00+09:00",
                ),
            ),
        ):
            content = jp_news.get_news(
                "4568.T",
                "2026-07-03",
                "2026-08-01",
                information_frontier=frontier.isoformat(),
            )

        filtered = _filter_tool_output_at_information_frontier(
            {
                "messages": [
                    ToolMessage(
                        content=content,
                        tool_call_id="jp-news-expired-google",
                        name="get_news",
                    )
                ]
            },
            frontier,
            analysis_date=date(2026, 8, 1),
            instrument="4568.T",
            sealed_at=datetime(
                2026,
                8,
                14,
                0,
                21,
                tzinfo=timezone(timedelta(hours=9)),
            ),
        )["messages"][0].content

        self.assertIn("有価証券報告書", filtered)
        self.assertIn("自己株式の取得", filtered)
        self.assertNotIn("決算を発表", filtered)
        google = next(
            item
            for item in extract_source_watermarks(filtered)
            if item.source == "Google News"
        )
        self.assertEqual(google.temporal_scope, "live_only")
        self.assertEqual(google.status, "unavailable")

    def test_empty_google_channel_keeps_advisory_audit_without_absence_body(self):
        out = _run(_EDINET_DATA, _MEDIA_EMPTY, tdnet=_TDNET_DATA)
        google = next(
            span
            for span in extract_evidence_spans(out)
            if span.records and span.records[0].source == "Google News"
        )

        self.assertEqual(google.temporal_scope, "live_only")
        self.assertIsNone(google.content)
        self.assertIn("no relevant items", google.records[0].timing)
        self.assertIsNotNone(google.records[0].retrieved_at)

    def test_empty_official_channel_with_frontier_attestation_remains_visible(self):
        frontier = datetime(
            2026,
            8,
            10,
            23,
            59,
            tzinfo=timezone(timedelta(hours=9)),
        )
        empty_edinet = attach_source_watermarks(
            _EDINET_EMPTY,
            SourceWatermark(
                source="EDINET",
                scanned_start="2026-07-12",
                scanned_end="2026-08-10",
                status="complete",
                returned_records=0,
                information_frontier=frontier.isoformat(),
            ),
        )
        safe_tdnet = _official_payload(
            _TDNET_DATA,
            "TDnet",
            "2026-07-11",
            "2026-08-10",
            information_frontier=frontier.isoformat(),
        )
        with (
            mock.patch.object(jp_news, "_edinet_news", return_value=empty_edinet),
            mock.patch.object(jp_news, "_tdnet_news", return_value=safe_tdnet),
            mock.patch.object(jp_news, "_google_news", return_value=_MEDIA_EMPTY),
        ):
            content = jp_news.get_news(
                "4568.T",
                "2026-07-12",
                "2026-08-10",
                information_frontier=frontier.isoformat(),
            )

        edinet_span = next(
            span
            for span in extract_evidence_spans(content)
            if span.records and span.records[0].source == "EDINET"
        )
        self.assertEqual(
            edinet_span.records[0].effective,
            "2026-07-12 to 2026-08-10",
        )

        filtered = _filter_tool_output_at_information_frontier(
            {
                "messages": [
                    ToolMessage(
                        content=content,
                        tool_call_id="jp-news-empty-official",
                        name="get_news",
                    )
                ]
            },
            frontier,
            analysis_date=date(2026, 8, 10),
            instrument="4568.T",
            sealed_at=datetime(
                2026,
                8,
                12,
                23,
                0,
                tzinfo=timezone(timedelta(hours=9)),
            ),
        )["messages"][0].content

        watermark = next(
            item
            for item in extract_source_watermarks(filtered)
            if item.source == "EDINET"
        )
        self.assertEqual(watermark.status, "complete")
        self.assertEqual(watermark.returned_records, 0)

    def test_official_channel_without_unique_producer_watermark_fails_closed(self):
        frontier = datetime(
            2026,
            8,
            10,
            23,
            59,
            tzinfo=timezone(timedelta(hours=9)),
        )
        ambiguous = attach_source_watermarks(
            attach_source_observations(
                _EDINET_DATA,
                SourceObservation(
                    source="EDINET",
                    record_id="S100",
                    version_id="edinet:S100",
                    status="published",
                    published_at="2026-08-10 15:00",
                    available_at="2026-08-10T15:00:00+09:00",
                    title="有価証券報告書",
                ),
            ),
            SourceWatermark(
                source="EDINET",
                scanned_start="2026-05-13",
                scanned_end="2026-08-10",
                status="complete",
                returned_records=1,
                information_frontier=frontier.isoformat(),
            ),
            SourceWatermark(
                source="EDINET",
                scanned_start="2026-07-12",
                scanned_end="2026-08-10",
                status="complete",
                returned_records=1,
                information_frontier=frontier.isoformat(),
            ),
        )
        safe_tdnet = _official_payload(
            _TDNET_DATA,
            "TDnet",
            "2026-07-11",
            "2026-08-10",
            information_frontier=frontier.isoformat(),
        )

        for producer_payload in (_EDINET_EMPTY, ambiguous):
            with self.subTest(
                watermark_count=len(extract_source_watermarks(producer_payload))
            ):
                with (
                    mock.patch.object(
                        jp_news,
                        "_edinet_news",
                        return_value=producer_payload,
                    ),
                    mock.patch.object(jp_news, "_tdnet_news", return_value=safe_tdnet),
                    mock.patch.object(jp_news, "_google_news", return_value=_MEDIA_EMPTY),
                ):
                    content = jp_news.get_news(
                        "4568.T",
                        "2026-07-12",
                        "2026-08-10",
                        information_frontier=frontier.isoformat(),
                    )

                filtered = _filter_tool_output_at_information_frontier(
                    {
                        "messages": [
                            ToolMessage(
                                content=content,
                                tool_call_id="jp-news-ambiguous-official",
                                name="get_news",
                            )
                        ]
                    },
                    frontier,
                    analysis_date=date(2026, 8, 10),
                    instrument="4568.T",
                    sealed_at=datetime(
                        2026,
                        8,
                        12,
                        23,
                        0,
                        tzinfo=timezone(timedelta(hours=9)),
                    ),
                )["messages"][0].content

                self.assertNotIn("### 有価証券報告書", filtered)
                edinet_watermarks = tuple(
                    item
                    for item in extract_source_watermarks(filtered)
                    if item.source == "EDINET"
                )
                self.assertTrue(edinet_watermarks)
                self.assertTrue(
                    any(item.status == "unavailable" for item in edinet_watermarks)
                )
                edinet_record = next(
                    item
                    for item in extract_provenance(content)
                    if item.source == "EDINET"
                )
                expected_count = 1 if producer_payload == ambiguous else 0
                self.assertIn("unavailable", edinet_record.timing)
                self.assertIn(
                    f"returned_items={expected_count}",
                    edinet_record.timing,
                )
                unavailable = next(
                    item
                    for item in extract_source_watermarks(content)
                    if item.source == "EDINET" and item.status == "unavailable"
                )
                self.assertEqual(unavailable.reported_records, expected_count)

    def test_ambiguous_official_channel_does_not_consume_safe_sibling_budget(self):
        frontier = datetime(
            2026,
            8,
            10,
            23,
            59,
            tzinfo=timezone(timedelta(hours=9)),
        )
        ambiguous_edinet = attach_source_watermarks(
            _EDINET_DATA,
            SourceWatermark(
                source="EDINET",
                scanned_start="2026-05-13",
                scanned_end="2026-08-10",
                status="complete",
                information_frontier=frontier.isoformat(),
            ),
            SourceWatermark(
                source="EDINET",
                scanned_start="2026-07-12",
                scanned_end="2026-08-10",
                status="complete",
                information_frontier=frontier.isoformat(),
            ),
        )
        safe_tdnet = attach_source_watermarks(
            _TDNET_DATA,
            SourceWatermark(
                source="TDnet",
                scanned_start="2026-07-11",
                scanned_end="2026-08-10",
                status="complete",
                information_frontier=frontier.isoformat(),
            ),
        )

        with (
            mock.patch.object(jp_news, "get_config", return_value={"news_article_limit": 1}),
            mock.patch.object(jp_news, "_edinet_news", return_value=ambiguous_edinet),
            mock.patch.object(jp_news, "_tdnet_news", return_value=safe_tdnet),
            mock.patch.object(jp_news, "_google_news", return_value=_MEDIA_EMPTY),
        ):
            content = jp_news.get_news(
                "4568.T",
                "2026-07-12",
                "2026-08-10",
                information_frontier=frontier.isoformat(),
            )

        self.assertNotIn("### 有価証券報告書", content)
        self.assertIn("### 自己株式の取得", content)

    def test_three_sources_share_one_30_item_budget_with_official_priority(self):
        edinet_titles = [f"EDINET item {index}" for index in range(15)]
        tdnet_titles = [f"TDnet item {index}" for index in range(15)]
        media_titles = [f"Media item {index}" for index in range(15)]

        with mock.patch.object(jp_news, "get_config", return_value={"news_article_limit": 30}):
            out = _run(
                _block("EDINET", edinet_titles),
                _block("Google News", media_titles),
                tdnet=_block("TDnet", tdnet_titles),
            )

        self.assertEqual(out.count("\n### "), 30)
        self.assertIn("EDINET item 14", out)
        self.assertIn("TDnet item 14", out)
        self.assertNotIn("Media item 0", out)
        self.assertIn(
            "returned_items=15; duplicate_items=0; kept_items=0; "
            "shared_limit=30; truncated_by_global_cap=15",
            out,
        )

    def test_cross_source_duplicate_keeps_official_item(self):
        edinet = _block("EDINET", ["通期業績予想の修正 (filer: Example Corp)"])
        media = _block(
            "Google News",
            ["[direct] 通期業績予想の修正 (source: Example News)", "独自取材"],
        )

        with mock.patch.object(jp_news, "get_config", return_value={"news_article_limit": 30}):
            out = _run(edinet, media)

        self.assertEqual(out.count("通期業績予想の修正"), 1)
        self.assertIn("filer: Example Corp", out)
        self.assertNotIn("source: Example News", out)
        self.assertIn("独自取材", out)
        self.assertIn(
            "returned_items=2; duplicate_items=1; kept_items=1; shared_limit=30",
            out,
        )
        self.assertNotIn("truncated_by_global_cap", out)

    def test_machine_lineage_survives_deduplication_and_global_cap(self):
        edinet = attach_source_watermarks(
            attach_source_observations(
                _block("EDINET", ["Duplicate"]),
                SourceObservation(
                    source="EDINET",
                    record_id="S100A",
                    version_id="edinet:S100A",
                    status="published",
                    published_at="2026-07-10 15:00",
                    available_at="2026-07-10T15:00:00+09:00",
                    title="Duplicate",
                ),
            ),
            SourceWatermark(
                source="EDINET",
                scanned_start="2026-07-01",
                scanned_end="2026-07-10",
                status="complete",
                returned_records=1,
                reported_records=1,
            ),
        )
        tdnet = attach_source_watermarks(
            attach_source_observations(
                _block("TDnet", ["Duplicate", "Capped"]),
                SourceObservation(
                    source="TDnet",
                    record_id="1401",
                    version_id="tdnet:v1",
                    status="published",
                    published_at="2026-07-10 16:00",
                    available_at="2026-07-10T16:00:00+09:00",
                    title="Duplicate",
                ),
                SourceObservation(
                    source="TDnet",
                    record_id="1402",
                    version_id="tdnet:v2",
                    status="published",
                    published_at="2026-07-10 17:00",
                    available_at="2026-07-10T17:00:00+09:00",
                    title="Capped",
                ),
            ),
            SourceWatermark(
                source="TDnet",
                scanned_start="2026-07-01",
                scanned_end="2026-07-10",
                status="limited",
                limitations=("archive limited",),
                returned_records=2,
                reported_records=2,
            ),
        )

        with mock.patch.object(jp_news, "get_config", return_value={"news_article_limit": 1}):
            out = _run(edinet, _MEDIA_EMPTY, tdnet=tdnet)

        assert {item.version_id for item in extract_source_observations(out)} == {
            "edinet:S100A",
            "tdnet:v1",
            "tdnet:v2",
        }
        assert {item.source for item in extract_source_watermarks(out)} == {
            "EDINET",
            "TDnet",
            "Google News",
        }
        google = next(
            item
            for item in extract_source_watermarks(out)
            if item.source == "Google News"
        )
        assert google.temporal_scope == "live_only"

    def test_configured_limit_is_applied_after_cross_source_merge(self):
        with mock.patch.object(jp_news, "get_config", return_value={"news_article_limit": 2}):
            out = _run(
                _block("EDINET", ["Official one", "Official two"]),
                _block("Google News", ["Media one"]),
                tdnet=_block("TDnet", ["Official three"]),
            )

        self.assertIn("Official one", out)
        self.assertIn("Official two", out)
        self.assertNotIn("Official three", out)
        self.assertNotIn("Media one", out)
        self.assertIn("truncated_by_global_cap=1", out)

    def test_tdnet_only_present(self):
        out = _run(_EDINET_EMPTY, _MEDIA_EMPTY, tdnet=_TDNET_DATA)
        self.assertIn("自己株式の取得", out)
        self.assertNotIn("No TDnet disclosures found", out)

    def test_tdnet_error_does_not_suppress_others(self):
        out = _run(_EDINET_DATA, _MEDIA_DATA, tdnet=RuntimeError("boom"))
        self.assertIn("有価証券報告書", out)
        self.assertIn("決算を発表", out)

    def test_only_edinet_present_drops_empty_media_line(self):
        out = _run(_EDINET_DATA, _MEDIA_EMPTY)
        self.assertIn("有価証券報告書", out)
        self.assertNotIn("No Google News found", out)  # empty block omitted

    def test_only_media_present_drops_empty_edinet_line(self):
        out = _run(_EDINET_EMPTY, _MEDIA_DATA)
        self.assertIn("決算を発表", out)
        self.assertNotIn("No EDINET disclosures found", out)

    def test_edinet_error_does_not_suppress_media(self):
        # EDINET needs a key and can raise; the keyless media feed must survive.
        out = _run(VendorNotConfiguredError("EDINET_API_KEY unset"), _MEDIA_DATA)
        self.assertIn("決算を発表", out)

    def test_media_error_does_not_suppress_edinet(self):
        out = _run(_EDINET_DATA, RuntimeError("boom"))
        self.assertIn("有価証券報告書", out)

    def test_extended_window_is_clamped_only_for_tdnet(self):
        with (
            mock.patch.object(jp_news, "_edinet_news", return_value=_EDINET_DATA) as edinet,
            mock.patch.object(jp_news, "_tdnet_news", return_value=_TDNET_DATA) as tdnet,
            mock.patch.object(jp_news, "_google_news", return_value=_MEDIA_DATA) as media,
        ):
            jp_news.get_news("4568.T", "2026-04-19", "2026-07-17")

        edinet.assert_called_once_with("4568.T", "2026-04-19", "2026-07-17")
        tdnet.assert_called_once_with("4568.T", "2026-06-17", "2026-07-17")
        media.assert_called_once_with("4568.T", "2026-04-19", "2026-07-17")

    def test_recent_window_uses_source_specific_disclosure_overlap(self):
        with (
            mock.patch.object(jp_news, "_edinet_news", return_value=_EDINET_DATA) as edinet,
            mock.patch.object(jp_news, "_tdnet_news", return_value=_TDNET_DATA) as tdnet,
            mock.patch.object(jp_news, "_google_news", return_value=_MEDIA_DATA) as media,
        ):
            jp_news.get_news("4568.T", "2026-07-03", "2026-07-17")

        edinet.assert_called_once_with("4568.T", "2026-04-19", "2026-07-17")
        tdnet.assert_called_once_with("4568.T", "2026-06-17", "2026-07-17")
        media.assert_called_once_with("4568.T", "2026-07-03", "2026-07-17")

    def test_both_empty_raises_no_market_data(self):
        with self.assertRaises(NoMarketDataError) as context:
            _run(_EDINET_EMPTY, _MEDIA_EMPTY)
        watermarks = extract_source_watermarks(
            "\n".join(context.exception.availability_notes)
        )
        assert {item.source for item in watermarks} == {
            "EDINET",
            "TDnet",
            "Google News",
        }

    def test_edinet_error_and_empty_media_raises(self):
        with self.assertRaises(NoMarketDataError) as ctx:
            _run(RuntimeError("boom"), _MEDIA_EMPTY)
        note = ctx.exception.availability_notes[0]
        self.assertIn("<EDINET unavailable: RuntimeError>", note)
        records = extract_provenance(note)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].source, "EDINET")
        self.assertEqual(records[0].timing, "unavailable")


if __name__ == "__main__":
    unittest.main()
