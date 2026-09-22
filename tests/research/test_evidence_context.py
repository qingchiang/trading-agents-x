from __future__ import annotations

import json
from datetime import date, timedelta

from tradingagents.domain.evidence import EvidenceBundle, EvidenceItem
from tradingagents.domain.evidence_tables import extract_evidence_tables
from tradingagents.research.synthesis.evidence_context import (
    build_analyst_evidence_context,
    build_evidence_catalog,
    prepared_evidence_prompt,
    query_evidence_table_payload,
)


def test_analyst_prompt_supplies_catalog_metadata_only_once() -> None:
    bundle = _bundle(20)
    snapshot = bundle.model_dump_json()
    prepared = build_analyst_evidence_context(
        bundle, evidence_refs=(bundle.items[0].ref,),
    )

    prompt = prepared_evidence_prompt(prepared)

    assert prompt.count('"latest_close"') == 1
    assert "2025-01-02,99,102,98,100,1000000" in prompt
    assert bundle.items[0].ref in prompt
    assert bundle.model_dump_json() == snapshot


def test_prompt_catalog_defaults_preserve_every_item_field() -> None:
    bundle = _bundle(20)
    other = EvidenceItem.create(
        source="second source", evidence_type="observation",
        requested_date=bundle.analysis_date, value=0, unit="USD", fallback=True,
    )
    bundle = EvidenceBundle(
        instrument=bundle.instrument, analysis_date=bundle.analysis_date,
        items=(*bundle.items, other), tables=bundle.tables,
    )
    prepared = build_analyst_evidence_context(
        bundle, evidence_refs=tuple(item.ref for item in bundle.items),
    )

    prompt = prepared_evidence_prompt(prepared)
    catalog = json.loads(prompt.split("\n", 1)[1].split("\n\nEPHEMERAL")[0])

    assert "item_defaults" in catalog
    restored = [{**catalog["item_defaults"], **item} for item in catalog["items"]]
    assert restored == prepared.catalog["items"]
    assert len(json.dumps(catalog)) < len(json.dumps(prepared.catalog))


def test_analyst_prompt_preserves_source_and_table_queries_with_catalog_inheritance() -> None:
    bundle = _bundle(200)
    prepared = build_analyst_evidence_context(
        bundle, evidence_refs=(bundle.items[0].ref,),
    )
    prompt = prepared_evidence_prompt(prepared)
    queries = json.loads(prompt.split("(inherit unchanged metadata from the catalog by ref):\n")[1])
    source = queries[0]

    assert {**prepared.catalog["items"][0], **source} == prepared.query_results[0]
    assert queries[1:] == list(prepared.query_results[1:])
    assert source["content_omitted"] is True
    assert source["content"] is None


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


def test_catalog_excludes_source_bodies_and_table_rows() -> None:
    bundle = _bundle(488)

    catalog = build_evidence_catalog(bundle)

    assert catalog["items"][0]["content_characters"] > 10_000
    assert catalog["items"][0]["analytical_views"]["row_count"] == 488
    assert "content" not in catalog["items"][0]
    assert catalog["tables"][0]["row_count"] == 488
    assert "rows" not in catalog["tables"][0]


def test_deterministic_analyst_context_keeps_raw_rows_out_of_prompt() -> None:
    short_bundle = _bundle(20)
    long_bundle = _bundle(488)

    short = build_analyst_evidence_context(
        short_bundle,
        evidence_refs=(short_bundle.items[0].ref,),
    )
    long = build_analyst_evidence_context(
        long_bundle,
        evidence_refs=(long_bundle.items[0].ref,),
    )

    assert "2025-01-02,99,102,98,100,1000000" in short.query_results[0]["content"]
    assert long.query_results[0]["content"] is None
    assert long.query_results[0]["content_omitted"] is True
    assert long.catalog["tables"][0]["row_count"] == 488
    assert any(
        result.get("operation") == "summary"
        for result in long.query_results
    )
    assert any(
        result.get("operation") == "resample"
        for result in long.query_results
    )
    assert long.inline_characters < len(long_bundle.items[0].content or "")


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
