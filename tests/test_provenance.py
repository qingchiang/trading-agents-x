from unittest import mock

import pytest
from langchain_core.messages import ToolMessage

from tests.data_policy import request_context
from tests.source_results import source_message
from tradingagents.data import interface
from tradingagents.data.evidence_workset import tool_message_records
from tradingagents.data.result_metadata import describe_source
from tradingagents.domain.data import ProvenanceRecord
from tradingagents.domain.data_quality import provenance_quality_issues
from tradingagents.domain.data_result import DataResult


@pytest.mark.unit
def test_extractor_reads_tool_messages_and_ignores_malformed_or_prose_claims():
    record = ProvenanceRecord(evidence="get_news", source="EDINET")
    valid = source_message(DataResult("body").with_provenance(record), tool_call_id="1")
    malformed = ToolMessage(
        content='<!-- tradingagents-provenance:v1 {"source":"fake"} -->',
        tool_call_id="2",
    )
    prose = ToolMessage(content="Data source: invented vendor", tool_call_id="3")

    assert tool_message_records([valid, malformed, prose]) == [record]


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
def test_warning_taxonomy_covers_material_retrieval_and_coverage_issues(timing, expected_reason):
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
def test_router_always_retains_actual_fallback_vendor():
    vendors = {
        "primary": mock.Mock(side_effect=interface.VendorNotConfiguredError("no key")),
        "fallback": mock.Mock(return_value=DataResult("RESULT")),
    }
    with (
        mock.patch.dict(interface.VENDOR_METHODS, {"get_news": vendors}),
        mock.patch.object(interface, "get_vendor", return_value="primary,fallback"),
    ):
        result = interface.route_to_vendor(
            "get_news", "NVDA", "2026-07-01", "2026-07-17", data_context=request_context()
        )
    assert result.content == "RESULT"
    assert result.provenance[0].source == "fallback"
    assert "fallback vendor selected" in result.provenance[0].timing


@pytest.mark.unit
@pytest.mark.parametrize("scoped", [False, True])
def test_router_adds_fallback_status_when_vendor_already_supplies_provenance(scoped):
    internal = ProvenanceRecord(
        evidence="get_news",
        source="internal feed",
        requested="2026-07-01 to 2026-07-17",
        effective="2026-07-01 to 2026-07-17",
        timing="publication-date filtered",
    )
    result = DataResult("article").with_provenance(internal)
    if scoped:
        result = result.with_scope("point_in_time")
    vendors = {
        "primary": mock.Mock(side_effect=interface.VendorNotConfiguredError("no key")),
        "fallback": mock.Mock(return_value=result),
    }
    with (
        mock.patch.dict(
            interface.VENDOR_METHODS,
            {"get_news": vendors},
        ),
        mock.patch.object(interface, "get_vendor", return_value="primary,fallback"),
    ):
        marked = interface.route_to_vendor(
            "get_news",
            "NVDA",
            "2026-07-01",
            "2026-07-17",
            data_context=request_context(),
        )

    records = list(marked.provenance)
    assert len(records) == 1
    assert records[0].source == internal.source
    assert records[0].timing == "fallback vendor selected; publication-date filtered"
    if scoped:
        assert marked.spans[0].records == tuple(records)
        assert marked.spans[0].temporal_scope == "point_in_time"


@pytest.mark.unit
def test_stock_source_uses_actual_returned_trading_dates():
    result = (
        "# Stock data for NVDA from 2026-07-15 to 2026-07-19\n\n"
        "Date,Open,Close\n"
        "2026-07-15,170,171\n"
        "2026-07-17,172,173\n"
    )
    record = describe_source(
        "get_stock_data",
        "yfinance",
        ("NVDA", "2026-07-15", "2026-07-19"),
        request_context().config,
        result,
    )

    assert record.requested == "2026-07-15 to 2026-07-19"
    assert record.effective == "2026-07-15 to 2026-07-17"


@pytest.mark.unit
def test_indicator_source_uses_latest_valid_indicator_observation() -> None:
    result = (
        "## atr values from 2026-07-27 to 2026-08-01:\n\n"
        "Latest valid indicator observation: 2026-07-31\n\n"
        "2026-08-01: N/A: Not a trading day (weekend or holiday)\n"
        "2026-07-31: 160.18145\n"
    )
    record = describe_source(
        "get_indicators",
        "yfinance",
        ("6501.T", "atr", "2026-08-01", 5),
        request_context().config,
        result,
    )

    assert record.requested == "2026-08-01"
    assert record.effective == "2026-07-31"


@pytest.mark.unit
def test_snapshot_source_uses_latest_verified_trading_row() -> None:
    result = (
        "## Verified market data snapshot for 6501.T\n\n"
        "- Requested analysis date: 2026-08-01\n"
        "- Latest trading row used: 2026-07-31\n"
    )
    record = describe_source(
        "get_verified_market_snapshot",
        "fixture",
        ("6501.T", "2026-08-01"),
        request_context().config,
        result,
    )

    assert record.effective == "2026-07-31"


@pytest.mark.unit
def test_historical_live_only_sentinel_keeps_not_queried_semantics():
    result = (
        "LIVE_DATA_UNAVAILABLE: yfinance .info is a current snapshot and was "
        "not requested for historical analysis date 2020-01-15."
    )
    record = describe_source(
        "get_fundamentals", "yfinance", ("NVDA", "2020-01-15"), request_context().config, result
    )

    assert record.effective == "—"
    assert record.timing == (
        "live-only; unavailable for historical or future date; vendor not queried"
    )
    assert record.retrieved_at is None
