from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from langchain_core.messages import AIMessage

from tests.factories import analyst_report
from tradingagents.application.contracts import EvidenceBundle, EvidenceItem
from tradingagents.application.evidence import extract_evidence_tables
from tradingagents.graph.deliberation import research_prompt
from tradingagents.graph.evidence_context import (
    build_evidence_catalog,
    prepare_evidence,
    query_evidence_table_payload,
)


def _bundle(rows: int = 20) -> EvidenceBundle:
    start = date(2025, 1, 2)
    lines = ["Date,Open,High,Low,Close,Volume"]
    for index in range(rows):
        current = start + timedelta(days=index)
        value = 100 + index
        lines.append(
            f"{current.isoformat()},{value - 1},{value + 2},{value - 2},{value},{1_000_000 + index}"
        )
    item = EvidenceItem.create(
        source="fixture",
        evidence_type="get_stock_data",
        requested_date=date(2026, 7, 28),
        effective_date=date(2026, 7, 28),
        content="\n".join(lines),
        provenance={
            "dataset_id": "ds_fixture",
            "analytical_views": {"row_count": rows, "latest_close": 100 + rows - 1},
        },
    )
    return EvidenceBundle(
        instrument="4568.T",
        analysis_date=date(2026, 7, 28),
        items=(item,),
        tables=extract_evidence_tables((item,)),
    )


def _state(bundle: EvidenceBundle) -> dict[str, Any]:
    report = analyst_report(evidence_ref=bundle.items[0].ref)
    return {
        "ticker": bundle.instrument,
        "analysis_date": bundle.analysis_date.isoformat(),
        "output_language": "English (en)",
        "analyst_reports": {"market": report.model_dump(mode="json")},
        "evidence_bundle": bundle.model_dump(mode="json"),
        "cases": {},
        "rebuttals": [],
        "risk_reviews": {},
    }


def test_catalog_excludes_source_bodies_and_table_rows() -> None:
    bundle = _bundle(488)

    catalog = build_evidence_catalog(bundle)

    assert catalog["items"][0]["content_characters"] > 10_000
    assert catalog["items"][0]["analytical_views"]["row_count"] == 488
    assert "content" not in catalog["items"][0]
    assert catalog["tables"][0]["row_count"] == 488
    assert "rows" not in catalog["tables"][0]


def test_table_query_supports_paging_filters_summary_extrema_and_resample() -> None:
    bundle = _bundle(200)
    table = bundle.tables[0]

    first_page = query_evidence_table_payload(
        bundle,
        table_id=table.id,
        operation="rows",
        columns=["date", "close"],
    )
    second_page = query_evidence_table_payload(
        bundle,
        table_id=table.id,
        operation="rows",
        columns=["date", "close"],
        cursor=first_page["cursor"],
    )
    summary = query_evidence_table_payload(
        bundle,
        table_id=table.id,
        operation="summary",
        columns=["close"],
    )
    extrema = query_evidence_table_payload(
        bundle,
        table_id=table.id,
        operation="extrema",
        columns=["close"],
    )
    monthly = query_evidence_table_payload(
        bundle,
        table_id=table.id,
        operation="resample",
        columns=["date", "open", "high", "low", "close", "volume"],
        frequency="month",
    )

    assert first_page["returned_rows"] == 120
    assert second_page["returned_rows"] == 80
    assert second_page["cursor"] is None
    assert summary["summary"]["close"]["max"] == 299
    assert extrema["extrema"]["close"]["min"]["row_id"] == "row_0001"
    assert monthly["returned_rows"] > 1


def test_table_query_rejects_future_cutoff() -> None:
    bundle = _bundle()

    result = query_evidence_table_payload(
        bundle,
        table_id=bundle.tables[0].id,
        start_date="2026-07-20",
        end_date="2026-07-29",
    )

    assert result["error"] == "future_data_forbidden"


class _PreparationLLM:
    def __init__(self, table_id: str):
        self.table_id = table_id
        self.calls = 0
        self.configs: list[dict[str, Any] | None] = []

    def bind_tools(self, _tools):
        return self

    def invoke(self, _messages, *, config=None):
        self.calls += 1
        self.configs.append(config)
        if self.calls == 1:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "lookup-1",
                        "name": "query_evidence_table",
                        "args": {
                            "table_id": self.table_id,
                            "operation": "summary",
                            "columns": ["close"],
                        },
                    }
                ],
            )
        return AIMessage(content="Verified the close range from the source table.")


def test_role_preparation_records_actual_read_only_queries() -> None:
    bundle = _bundle()
    llm = _PreparationLLM(bundle.tables[0].id)

    prepared = prepare_evidence(
        llm,
        bundle=bundle,
        role_prompt="Check the price range.",
        node="case.bull",
        invoke_config={"metadata": {"research_node": "case.bull"}},
    )

    assert prepared.memo.startswith("Verified")
    assert prepared.lookups[0].table_id == bundle.tables[0].id
    assert prepared.lookups[0].returned_rows == 0
    assert prepared.query_results[0]["summary"]["close"]["max"] == 119
    assert llm.configs[0]["metadata"]["research_node"] == "case.bull"


def test_post_analyst_prompt_size_does_not_scale_with_raw_rows() -> None:
    short_bundle = _bundle(20)
    long_bundle = _bundle(500)

    short_prompt = research_prompt(
        _state(short_bundle),
        title="Fixture Role",
        objective="Inspect evidence.",
        extra="No additional artifacts.",
    )
    long_prompt = research_prompt(
        _state(long_bundle),
        title="Fixture Role",
        objective="Inspect evidence.",
        extra="No additional artifacts.",
    )

    assert "2025-01-02,99,102,98,100,1000000" not in short_prompt
    assert "2025-01-02,99,102,98,100,1000000" not in long_prompt
    assert abs(len(long_prompt) - len(short_prompt)) < 1_000
