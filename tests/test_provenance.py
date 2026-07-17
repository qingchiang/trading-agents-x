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

    assert second.count("## Data provenance") == 1
    assert second.count("J-Quants \\| official") == 1
    assert "| income statement | — | 2026-07-17 | — | not requested |" in second


@pytest.mark.unit
def test_disabled_appendix_leaves_report_unchanged():
    record = ProvenanceRecord(evidence="get_news", source="EDINET")
    assert append_provenance_appendix("REPORT", [record], enabled=False) == "REPORT"


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
    assert extract_provenance(marked)[0].source == "fallback"


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
