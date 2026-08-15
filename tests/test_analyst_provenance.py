import copy
from datetime import date
from unittest import mock

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableLambda

import tradingagents.default_config as default_config
from tradingagents.agents.analysts.fundamentals_analyst import (
    create_fundamentals_analyst,
)
from tradingagents.agents.analysts.market_analyst import create_market_analyst
from tradingagents.dataflows.config import bind_config
from tradingagents.graph.research_graph import _collect_evidence
from tradingagents.provenance import (
    ProvenanceRecord,
    SourceObservation,
    SourceWatermark,
    attach_provenance,
    attach_source_observations,
    attach_source_watermarks,
)


@pytest.fixture(autouse=True)
def _reset_config():
    bind_config(copy.deepcopy(default_config.DEFAULT_CONFIG), merge=False)
    yield


def _final_llm():
    llm = mock.MagicMock()
    llm.bind_tools.return_value = RunnableLambda(
        lambda _prompt: AIMessage(content="MODEL REPORT")
    )
    return llm


def _state(tool_content: str):
    return {
        "company_of_interest": "6501.T",
        "trade_date": "2026-07-17",
        "asset_type": "stock",
        "messages": [ToolMessage(content=tool_content, tool_call_id="call-1")],
    }


@pytest.mark.unit
def test_market_final_report_keeps_audit_data_out_of_the_narrative():
    content = attach_provenance(
        "SNAPSHOT",
        ProvenanceRecord(
            evidence="get_verified_market_snapshot",
            source="J-Quants",
            requested="2026-07-17",
            effective="2026-07-16",
            timing="market-date filtered",
        ),
    )
    state = _state(content)
    result = create_market_analyst(_final_llm())(state)

    report = result["market_report"]
    assert report == result["messages"][0].content
    assert report == "MODEL REPORT"
    evidence = _collect_evidence(
        [*state["messages"], *result["messages"]],
        report,
        requested_date=date(2026, 7, 17),
        analyst="market",
        prefetched_blocks=result["prefetched_evidence"],
    )
    snapshot = next(
        item
        for item in evidence
        if item.evidence_type == "get_verified_market_snapshot"
    )
    assert snapshot.source == "J-Quants"
    assert snapshot.effective_date == date(2026, 7, 16)


@pytest.mark.unit
def test_fundamentals_keeps_sources_and_missing_tools_as_internal_evidence():
    content = attach_provenance(
        "STATEMENT",
        ProvenanceRecord(
            evidence="get_income_statement",
            source="J-Quants fundamentals",
            requested="2026-07-17",
            effective="disclosures <= 2026-07-17",
            timing="disclosure-date filtered",
        ),
        ProvenanceRecord(
            evidence="get_income_statement",
            source="yfinance curated detail",
            requested="2026-07-17",
            effective="current statement frame",
            timing="live non-point-in-time",
            retrieved_at="2026-07-17T01:02:03+00:00",
        ),
    )
    state = _state(content)
    result = create_fundamentals_analyst(_final_llm())(state)

    report = result["fundamentals_report"]
    assert report == result["messages"][0].content
    assert report == "MODEL REPORT"
    evidence = _collect_evidence(
        [*state["messages"], *result["messages"]],
        report,
        requested_date=date(2026, 7, 17),
        analyst="fundamentals",
        prefetched_blocks=result["prefetched_evidence"],
    )
    statement = next(
        item
        for item in evidence
        if item.evidence_type == "get_income_statement"
    )
    assert {origin.source for origin in statement.origins} == {
        "J-Quants fundamentals",
        "yfinance curated detail",
    }
    missing = {
        item.evidence_type
        for item in evidence
        if item.quality.value == "unavailable"
    }
    assert {"fundamentals overview", "balance sheet", "cash flow statement"} <= (
        missing
    )


@pytest.mark.unit
def test_market_report_omits_appendix_by_default():
    content = attach_provenance(
        "SNAPSHOT",
        ProvenanceRecord(
            evidence="get_verified_market_snapshot",
            source="J-Quants",
            effective="2026-07-17",
            timing="market-date filtered",
        ),
    )
    result = create_market_analyst(_final_llm())(_state(content))

    assert result["market_report"] == "MODEL REPORT"
    assert result["messages"][0].content == "MODEL REPORT"


@pytest.mark.unit
def test_disclosure_source_records_and_watermarks_are_sealed_outside_narrative():
    content = attach_provenance(
        "DISCLOSURE",
        ProvenanceRecord(
            evidence="get_news",
            source="EDINET",
            requested="2026-07-01 to 2026-07-24",
            effective="2026-07-01 to 2026-07-24",
            timing="disclosure-date filtered",
        ),
    )
    content = attach_source_observations(
        content,
        SourceObservation(
            source="EDINET",
            record_id="S100A",
            version_id="edinet:S100B",
            status="corrected",
            published_at="2026-07-23 15:00",
            available_at="2026-07-23T15:00:00+09:00",
            title="訂正有価証券報告書",
            replaces_version_id="edinet:S100A",
        ),
    )
    content = attach_source_watermarks(
        content,
        SourceWatermark(
            source="EDINET",
            scanned_start="2026-07-01",
            scanned_end="2026-07-24",
            status="complete",
            returned_records=1,
            reported_records=1,
        ),
    )

    item = _collect_evidence(
        [ToolMessage(content=content, tool_call_id="call-1")],
        "Report.",
        requested_date=date(2026, 7, 24),
        analyst="news",
    )[0]

    assert item.content == "DISCLOSURE"
    assert item.provenance["source_records"][0]["version_id"] == "edinet:S100B"
    assert item.provenance["source_watermarks"][0]["status"] == "complete"
