import json
from unittest import mock

import pytest
from langchain_core.messages import ToolMessage

from tradingagents.dataflows import interface
from tradingagents.provenance import (
    ProvenanceRecord,
    append_provenance_appendix,
    attach_provenance,
    extract_provenance,
    provenance_marker,
)


@pytest.mark.unit
def test_marker_round_trip_uses_only_declared_public_fields():
    record = ProvenanceRecord(
        evidence="get_income_statement",
        source="yfinance",
        requested="2026-07-17",
        effective="period ends <= 2026-07-17",
        timing="live non-point-in-time",
        retrieved_at="2026-07-17T01:02:03+00:00",
    )
    text = attach_provenance("BODY", record)

    assert extract_provenance(text) == [record]
    assert "api_key" not in text.casefold()
    assert "authorization" not in text.casefold()


@pytest.mark.unit
def test_extractor_reads_tool_messages_and_ignores_malformed_or_prose_claims():
    record = ProvenanceRecord(evidence="get_news", source="EDINET")
    valid = ToolMessage(content=attach_provenance("body", record), tool_call_id="1")
    malformed = ToolMessage(
        content='<!-- tradingagents-provenance:v1 {"source":"fake"} -->',
        tool_call_id="2",
    )
    prose = ToolMessage(content="Data source: invented vendor", tool_call_id="3")

    assert extract_provenance([valid, malformed, prose]) == [record]


@pytest.mark.unit
def test_appendix_is_deduplicated_escaped_and_marks_missing_expected_tools():
    record = ProvenanceRecord(
        evidence="get_fundamentals",
        source="J-Quants | official",
        requested="2026-07-17",
        effective="disclosures <= 2026-07-17",
        timing="point-in-time",
    )
    first = append_provenance_appendix(
        "REPORT",
        [record, record],
        expected=(("get_income_statement", "income statement"),),
        requested_date="2026-07-17",
    )
    second = append_provenance_appendix(
        first,
        [record],
        expected=(("get_income_statement", "income statement"),),
        requested_date="2026-07-17",
    )

    assert second.count("## Data Provenance") == 1
    assert (
        "REPORT\n\n<!-- tradingagents-data-provenance:start -->\n---\n\n"
        "## Data Quality Warnings"
        in second
    )
    assert second.index("## Data Quality Warnings") < second.index("## Data Provenance")
    assert second.count("\n---\n") == 1
    assert second.count("J-Quants \\| official") == 1
    assert "| income statement | — | 2026-07-17 | — | not requested |" in second
    assert (
        "- **income statement** (source: —): expected evidence was not requested"
        in second
    )


@pytest.mark.unit
def test_appendix_warns_for_degraded_timing_but_not_routine_empty_results():
    records = [
        ProvenanceRecord(
            evidence="fundamentals",
            source="yfinance",
            timing="live non-point-in-time",
        ),
        ProvenanceRecord(
            evidence="macro",
            source="FRED",
            timing="monthly fallback; observation-date filtered",
        ),
        ProvenanceRecord(
            evidence="news",
            source="EDINET",
            effective="2026-07-01 to 2026-07-17",
            timing="available; no relevant items in window",
        ),
        ProvenanceRecord(
            evidence="snapshot",
            source="AkShare / yfinance",
            effective="—",
            timing="no usable data from configured vendors",
        ),
    ]

    report = append_provenance_appendix("REPORT", records)

    assert "## Data Quality Warnings" in report
    assert "- **fundamentals** (source: yfinance): not point-in-time" in report
    assert "- **macro** (source: FRED): fallback source used" in report
    assert (
        "- **snapshot** (source: AkShare / yfinance): "
        "no usable data from configured sources"
    ) in report
    assert "- **news**" not in report


@pytest.mark.unit
def test_appendix_omits_warning_block_when_all_sources_are_date_safe():
    report = append_provenance_appendix(
        "REPORT",
        [
            ProvenanceRecord(
                evidence="snapshot",
                source="AkShare / Eastmoney",
                effective="2026-07-17",
                timing="market-date filtered; future rows excluded",
            )
        ],
    )

    assert "Data Quality Warnings" not in report
    assert "## Data Provenance" in report


@pytest.mark.unit
@pytest.mark.parametrize(
    "timing",
    [
        "available; no relevant items in window",
        "available; no published records",
        "available; no curated line items matched",
        "available; curated line items contained no values",
        "available; no qualifying records",
    ],
)
def test_successful_empty_results_do_not_warn_when_effective_date_is_absent(timing):
    report = append_provenance_appendix(
        "REPORT",
        [
            ProvenanceRecord(
                evidence="evidence",
                source="vendor",
                effective="—",
                timing=timing,
            )
        ],
        enabled=False,
    )

    assert report == "REPORT"


@pytest.mark.unit
def test_disabled_appendix_leaves_safe_report_unchanged():
    record = ProvenanceRecord(
        evidence="get_news",
        source="EDINET",
        effective="2026-07-01 to 2026-07-17",
        timing="publication-date filtered",
    )
    assert append_provenance_appendix("REPORT", [record], enabled=False) == "REPORT"


@pytest.mark.unit
def test_disabled_appendix_keeps_material_warnings_without_provenance_table():
    record = ProvenanceRecord(
        evidence="macro",
        source="FRED",
        effective="2026-06-01",
        timing="monthly fallback; observation-date filtered; non-vintage",
    )

    report = append_provenance_appendix("REPORT", [record], enabled=False)

    assert report.count("## Data Quality Warnings") == 1
    assert "## Data Provenance" not in report
    assert "- **macro** (source: FRED): fallback source used" in report
    assert "- **macro** (source: FRED): non-vintage series" in report
    assert report.count("\n---\n") == 1


@pytest.mark.unit
def test_warnings_dedupe_by_evidence_source_and_issue():
    records = [
        ProvenanceRecord(
            evidence="news",
            source="EDINET",
            effective="2026-07-01 to 2026-07-17",
            timing="unavailable",
        ),
        ProvenanceRecord(
            evidence="news",
            source="EDINET",
            effective="2026-07-01 to 2026-07-17",
            timing="source unavailable for requested window",
        ),
        ProvenanceRecord(
            evidence="news",
            source="TDnet",
            effective="2026-07-01 to 2026-07-17",
            timing="unavailable",
        ),
    ]

    report = append_provenance_appendix("REPORT", records, enabled=False)

    assert report.count("source unavailable for requested date/window") == 2
    assert "- **news** (source: EDINET)" in report
    assert "- **news** (source: TDnet)" in report


@pytest.mark.unit
@pytest.mark.parametrize(
    ("timing", "expected_reason"),
    [
        ("source retrieval failed", "source retrieval failed"),
        ("result set truncated by global cap", "result set truncated"),
        ("stale data", "stale data"),
        ("partial coverage", "partial coverage"),
        ("current-only snapshot; not historical PIT", "not point-in-time"),
        ("non-strict PIT; may include later revisions", "not point-in-time"),
        ("not queried for historical analysis", "source was not queried"),
    ],
)
def test_warning_taxonomy_covers_material_retrieval_and_coverage_issues(
    timing, expected_reason
):
    report = append_provenance_appendix(
        "REPORT",
        [
            ProvenanceRecord(
                evidence="evidence",
                source="vendor",
                effective="2026-07-17",
                timing=timing,
            )
        ],
        enabled=False,
    )

    assert f"- **evidence** (source: vendor): {expected_reason}" in report


@pytest.mark.unit
def test_warning_taxonomy_marks_unknown_source_and_effective_window_separately():
    report = append_provenance_appendix(
        "REPORT",
        [ProvenanceRecord(evidence="evidence", source="unknown")],
        enabled=False,
    )

    assert "- **evidence** (source: unknown): source metadata unknown" in report
    assert "- **evidence** (source: unknown): effective date/window unknown" in report


@pytest.mark.unit
def test_repeated_append_rebuilds_one_section_when_appendix_setting_changes():
    record = ProvenanceRecord(
        evidence="fundamentals",
        source="yfinance",
        effective="current snapshot",
        timing="live non-point-in-time",
    )
    combined_analyst_report = "## Market analyst\nOK\n\n## News analyst\nLIMITED"
    with_table = append_provenance_appendix(combined_analyst_report, [record])
    warnings_only = append_provenance_appendix(with_table, [record], enabled=False)

    assert warnings_only.startswith(combined_analyst_report)
    assert warnings_only.count("tradingagents-data-provenance:start") == 1
    assert warnings_only.count("## Data Quality Warnings") == 1
    assert "## Data Provenance" not in warnings_only
    assert warnings_only.count("\n---\n") == 1


@pytest.mark.unit
def test_append_removes_every_existing_section_from_combined_reports():
    record = ProvenanceRecord(
        evidence="news",
        source="EDINET",
        effective="2026-07-01 to 2026-07-17",
        timing="unavailable",
    )
    market_report = append_provenance_appendix("MARKET", [record])
    news_report = append_provenance_appendix("NEWS", [record])

    rebuilt = append_provenance_appendix(
        f"{market_report}\n\n{news_report}", [record], enabled=False
    )

    assert rebuilt.startswith("MARKET\n\nNEWS")
    assert rebuilt.count("tradingagents-data-provenance:start") == 1
    assert rebuilt.count("## Data Quality Warnings") == 1
    assert "## Data Provenance" not in rebuilt


@pytest.mark.unit
def test_router_marker_names_actual_fallback_vendor_only_when_requested():
    vendors = {
        "primary": mock.Mock(side_effect=interface.VendorNotConfiguredError("no key")),
        "fallback": mock.Mock(return_value="RESULT"),
    }
    with mock.patch.dict(
        interface.VENDOR_METHODS,
        {"get_news": vendors},
    ), mock.patch.object(
        interface,
        "get_vendor",
        return_value="primary,fallback",
    ):
        plain = interface.route_to_vendor(
            "get_news", "NVDA", "2026-07-01", "2026-07-17"
        )
        marked = interface.route_to_vendor(
            "get_news",
            "NVDA",
            "2026-07-01",
            "2026-07-17",
            _provenance=True,
        )

    assert plain == "RESULT"
    record = extract_provenance(marked)[0]
    assert record.source == "fallback"
    assert record.timing.startswith("fallback vendor selected;")


@pytest.mark.unit
def test_router_adds_fallback_status_when_vendor_already_supplies_provenance():
    internal = ProvenanceRecord(
        evidence="get_news",
        source="internal feed",
        requested="2026-07-01 to 2026-07-17",
        effective="2026-07-01 to 2026-07-17",
        timing="publication-date filtered",
    )
    vendors = {
        "primary": mock.Mock(side_effect=interface.VendorNotConfiguredError("no key")),
        "fallback": mock.Mock(
            return_value=attach_provenance(
                "## NEWS\n\n### One article\nbody\n\n"
                "### Source availability notes\n<feed unavailable>",
                internal,
            )
        ),
    }
    with mock.patch.dict(
        interface.VENDOR_METHODS,
        {"get_news": vendors},
    ), mock.patch.object(interface, "get_vendor", return_value="primary,fallback"):
        marked = interface.route_to_vendor(
            "get_news",
            "NVDA",
            "2026-07-01",
            "2026-07-17",
            _provenance=True,
        )

    records = extract_provenance(marked)
    assert internal in records
    assert any(
        record.source == "fallback"
        and record.timing == "fallback vendor selected"
        for record in records
    )


@pytest.mark.unit
def test_stock_route_uses_actual_returned_trading_dates():
    result = (
        "# Stock data for NVDA from 2026-07-15 to 2026-07-19\n\n"
        "Date,Open,Close\n"
        "2026-07-15,170,171\n"
        "2026-07-17,172,173\n"
    )
    with mock.patch.dict(
        interface.VENDOR_METHODS,
        {"get_stock_data": {"yfinance": mock.Mock(return_value=result)}},
    ), mock.patch.object(interface, "get_vendor", return_value="yfinance"):
        marked = interface.route_to_vendor(
            "get_stock_data",
            "NVDA",
            "2026-07-15",
            "2026-07-19",
            _provenance=True,
        )

    record = extract_provenance(marked)[0]
    assert record.requested == "2026-07-15 to 2026-07-19"
    assert record.effective == "2026-07-15 to 2026-07-17"


@pytest.mark.unit
def test_historical_live_only_sentinel_keeps_not_queried_semantics():
    result = (
        "LIVE_DATA_UNAVAILABLE: yfinance .info is a current snapshot and was "
        "not requested for historical analysis date 2020-01-15."
    )
    with mock.patch.dict(
        interface.VENDOR_METHODS,
        {"get_fundamentals": {"yfinance": mock.Mock(return_value=result)}},
    ), mock.patch.object(interface, "get_vendor", return_value="yfinance"):
        marked = interface.route_to_vendor(
            "get_fundamentals",
            "NVDA",
            "2020-01-15",
            _provenance=True,
        )

    record = extract_provenance(marked)[0]
    assert record.effective == "—"
    assert record.timing == "unavailable for historical date; vendor not queried"
    assert record.retrieved_at is None


@pytest.mark.unit
def test_marker_payload_is_valid_json():
    marker = provenance_marker(
        ProvenanceRecord(evidence='news "quoted"', source="共同通信")
    )
    payload = marker.split(" ", 2)[2].rsplit(" -->", 1)[0]
    assert json.loads(payload)["source"] == "共同通信"
