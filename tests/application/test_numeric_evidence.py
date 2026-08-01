"""Deterministic numeric Evidence catalog coverage."""

from __future__ import annotations

from datetime import date, timedelta

from tradingagents.application.contracts import (
    EvidenceBundle,
    EvidenceItem,
    EvidenceTable,
    EvidenceTableCell,
    EvidenceTableColumn,
    EvidenceTableRow,
    TableDataType,
)
from tradingagents.graph.numeric_evidence import build_numeric_value_catalog


def test_numeric_catalog_indexes_exact_item_scalar_with_locator() -> None:
    item = EvidenceItem(
        ref="ev_0123456789ab",
        source="fixture",
        evidence_type="analyst target",
        requested_date=date(2026, 8, 1),
        effective_date=date(2026, 7, 31),
        value=6129,
        unit="JPY",
    )
    bundle = EvidenceBundle(
        instrument="6501.T",
        analysis_date=date(2026, 8, 1),
        items=(item,),
    )

    catalog = build_numeric_value_catalog(bundle)

    assert len(catalog) == 1
    assert catalog[0].value == 6129
    assert catalog[0].evidence_refs == (item.ref,)
    assert catalog[0].locator.model_dump() == {
        "evidence_ref": item.ref,
        "table_id": None,
        "row_id": None,
        "column": None,
    }


def test_numeric_catalog_limits_large_series_to_latest_and_extrema_rows() -> None:
    ref = "ev_0123456789ab"
    columns = (
        EvidenceTableColumn(key="date", label="Date", data_type=TableDataType.DATE),
        EvidenceTableColumn(key="high", label="High", data_type=TableDataType.NUMBER),
        EvidenceTableColumn(key="low", label="Low", data_type=TableDataType.NUMBER),
        EvidenceTableColumn(key="close", label="Close", data_type=TableDataType.NUMBER),
    )
    start = date(2026, 1, 1)
    rows = tuple(
        EvidenceTableRow(
            id=f"row_{index:03d}",
            cells={
                "date": EvidenceTableCell(raw_value=(start + timedelta(days=index)).isoformat()),
                "high": EvidenceTableCell(raw_value=100 + index),
                "low": EvidenceTableCell(raw_value=80 - index),
                "close": EvidenceTableCell(raw_value=90 + index),
            },
        )
        for index in range(130)
    )
    table = EvidenceTable.create(
        title="Daily OHLCV",
        purpose="Frozen price history",
        columns=columns,
        rows=rows,
        evidence_refs=(ref,),
        source_format="structured",
    )
    bundle = EvidenceBundle(
        instrument="6501.T",
        analysis_date=date(2026, 5, 10),
        items=(
            EvidenceItem(
                ref=ref,
                source="fixture",
                evidence_type="daily prices",
                requested_date=date(2026, 5, 10),
                effective_date=date(2026, 5, 10),
                content="Raw rows are stored in the Evidence Ledger.",
            ),
        ),
        tables=(table,),
    )

    catalog = build_numeric_value_catalog(bundle)

    assert {entry.locator.row_id for entry in catalog} == {"row_129"}
    assert {entry.locator.column for entry in catalog} == {"high", "low", "close"}
    assert all(entry.locator.table_id == table.id for entry in catalog)
    assert all(entry.locator.evidence_ref == ref for entry in catalog)
