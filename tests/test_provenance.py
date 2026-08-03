import json
from unittest import mock

import pytest
from langchain_core.messages import ToolMessage

from tradingagents.dataflows import interface
from tradingagents.provenance import (
    ProvenanceRecord,
    attach_provenance,
    extract_provenance,
    provenance_marker,
    provenance_quality_issues,
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
def test_quality_issues_cover_degraded_timing_but_not_routine_empty_results():
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

    issues = provenance_quality_issues(records)
    observed = {(issue.evidence, issue.source, issue.reason) for issue in issues}

    assert ("fundamentals", "yfinance", "not point-in-time") in observed
    assert ("macro", "FRED", "fallback source used") in observed
    assert (
        "snapshot",
        "AkShare / yfinance",
        "no usable data from configured sources",
    ) in observed
    assert not any(issue.evidence == "news" for issue in issues)


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
    issues = provenance_quality_issues(
        [
            ProvenanceRecord(
                evidence="evidence",
                source="vendor",
                effective="2026-07-17",
                timing=timing,
            )
        ]
    )

    assert expected_reason in {issue.reason for issue in issues}


@pytest.mark.unit
def test_warning_taxonomy_marks_unknown_source_and_effective_window_separately():
    reasons = {
        issue.reason
        for issue in provenance_quality_issues(
            [ProvenanceRecord(evidence="evidence", source="unknown")]
        )
    }

    assert reasons == {
        "effective date/window unknown",
        "source metadata unknown",
    }


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
def test_indicator_route_uses_latest_valid_indicator_observation() -> None:
    result = (
        "## atr values from 2026-07-27 to 2026-08-01:\n\n"
        "Latest valid indicator observation: 2026-07-31\n\n"
        "2026-08-01: N/A: Not a trading day (weekend or holiday)\n"
        "2026-07-31: 160.18145\n"
    )
    with mock.patch.dict(
        interface.VENDOR_METHODS,
        {"get_indicators": {"yfinance": mock.Mock(return_value=result)}},
    ), mock.patch.object(interface, "get_vendor", return_value="yfinance"):
        marked = interface.route_to_vendor(
            "get_indicators",
            "6501.T",
            "atr",
            "2026-08-01",
            5,
            _provenance=True,
        )

    record = extract_provenance(marked)[0]
    assert record.requested == "2026-08-01"
    assert record.effective == "2026-07-31"


@pytest.mark.unit
def test_snapshot_route_uses_latest_verified_trading_row() -> None:
    result = (
        "## Verified market data snapshot for 6501.T\n\n"
        "- Requested analysis date: 2026-08-01\n"
        "- Latest trading row used: 2026-07-31\n"
    )
    with mock.patch.dict(
        interface.VENDOR_METHODS,
        {"get_verified_market_snapshot": {"fixture": mock.Mock(return_value=result)}},
    ), mock.patch.object(interface, "get_vendor", return_value="fixture"):
        marked = interface.route_to_vendor(
            "get_verified_market_snapshot",
            "6501.T",
            "2026-08-01",
            _provenance=True,
        )

    assert extract_provenance(marked)[0].effective == "2026-07-31"


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
    assert record.timing == (
        "live-only; unavailable for historical or future date; vendor not queried"
    )
    assert record.retrieved_at is None


@pytest.mark.unit
def test_marker_payload_is_valid_json():
    marker = provenance_marker(
        ProvenanceRecord(evidence='news "quoted"', source="共同通信")
    )
    payload = marker.split(" ", 2)[2].rsplit(" -->", 1)[0]
    assert json.loads(payload)["source"] == "共同通信"
