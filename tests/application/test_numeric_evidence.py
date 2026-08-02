"""Deterministic numeric Evidence catalog coverage."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from tradingagents.application.contracts import (
    EvidenceBundle,
    EvidenceItem,
    EvidenceTable,
    EvidenceTableCell,
    EvidenceTableColumn,
    EvidenceTableRow,
    MeasurementKind,
    TableDataType,
)
from tradingagents.application.evidence import extract_evidence_tables
from tradingagents.graph.numeric_evidence import (
    build_numeric_value_catalog,
    compact_numeric_value_catalog,
)


def test_numeric_catalog_indexes_exact_item_scalar_with_locator() -> None:
    item = EvidenceItem(
        ref="ev_0123456789ab",
        source="fixture",
        evidence_type="analyst target",
        requested_date=date(2026, 8, 1),
        effective_date=date(2026, 7, 31),
        value=6129,
        measurement_kind=MeasurementKind.CURRENCY,
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
    assert catalog[0].measurement_kind is MeasurementKind.CURRENCY
    assert catalog[0].unit == "JPY"
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


def test_numeric_catalog_uses_row_measurement_metadata_for_mixed_value_column() -> None:
    item = EvidenceItem(
        ref="ev_0123456789ab",
        source="fixture",
        evidence_type="verified market snapshot",
        requested_date=date(2026, 8, 1),
        effective_date=date(2026, 7, 31),
        content=(
            "## Verified technical indicators (latest row)\n\n"
            "| Indicator | Value | Measurement | Unit |\n"
            "|---|---:|---|---|\n"
            "| rsi | 67.24 | index | — |\n"
            "| close_50_sma | 4854.86 | currency | JPY |"
        ),
    )
    table = extract_evidence_tables((item,))[0]
    bundle = EvidenceBundle(
        instrument="6501.T",
        analysis_date=date(2026, 8, 1),
        items=(item,),
        tables=(table,),
    )

    catalog = build_numeric_value_catalog(bundle)
    by_label = {entry.label: entry for entry in catalog}
    rsi = next(entry for label, entry in by_label.items() if "rsi" in label)
    sma = next(entry for label, entry in by_label.items() if "close_50_sma" in label)

    assert rsi.measurement_kind is MeasurementKind.INDEX
    assert rsi.unit is None
    assert sma.measurement_kind is MeasurementKind.CURRENCY
    assert sma.unit == "JPY"


def test_numeric_catalog_inherits_column_measurement_for_unannotated_cells() -> None:
    regression = json.loads(
        (Path(__file__).parent / "fixtures" / "3778_numeric_normalization.json").read_text(
            encoding="utf-8"
        )
    )["measurement_inheritance"]
    ref = "ev_0123456789ab"
    table = EvidenceTable.create(
        title="Daily OHLCV",
        purpose="Frozen price history",
        columns=(
            EvidenceTableColumn(
                key="date",
                label="Date",
                data_type=TableDataType.DATE,
            ),
            EvidenceTableColumn(
                key="high",
                label="High",
                data_type=TableDataType.NUMBER,
                measurement_kind=MeasurementKind(
                    regression["column"]["measurement_kind"]
                ),
                unit=regression["column"]["unit"],
            ),
        ),
        rows=(
            EvidenceTableRow(
                id="row_0001",
                cells={
                    "date": EvidenceTableCell(raw_value="2026-07-31"),
                    "high": EvidenceTableCell(
                        raw_value=4025,
                        measurement_kind=regression["unannotated_cell"][
                            "measurement_kind"
                        ],
                        unit=regression["unannotated_cell"]["unit"],
                    ),
                },
            ),
        ),
        evidence_refs=(ref,),
        source_format="structured",
    )
    bundle = EvidenceBundle(
        instrument="3778.T",
        analysis_date=date(2026, 8, 1),
        items=(
            EvidenceItem(
                ref=ref,
                source="fixture",
                evidence_type="daily prices",
                requested_date=date(2026, 8, 1),
                effective_date=date(2026, 7, 31),
            ),
        ),
        tables=(table,),
    )

    entry = build_numeric_value_catalog(bundle)[0]

    assert entry.measurement_kind.value == regression["expected"]["measurement_kind"]
    assert entry.unit == regression["expected"]["unit"]


def test_table_parser_keeps_metadata_carriers_unmeasured() -> None:
    item = EvidenceItem(
        ref="ev_0123456789ab",
        source="fixture",
        evidence_type="verified market snapshot",
        requested_date=date(2026, 8, 1),
        effective_date=date(2026, 7, 31),
        content=(
            "| Indicator | Value | Measurement | Unit |\n"
            "|---|---:|---|---|\n"
            "| close | 3075 | currency | JPY |"
        ),
    )

    table = extract_evidence_tables((item,))[0]
    columns = {column.key: column for column in table.columns}

    assert columns["measurement"].data_type is TableDataType.TEXT
    assert columns["measurement"].unit is None
    assert columns["unit"].data_type is TableDataType.TEXT
    assert columns["unit"].measurement_kind is MeasurementKind.UNKNOWN
    assert columns["unit"].unit is None


def test_compact_numeric_catalog_shares_measurements_and_omits_unknowns() -> None:
    items = (
        EvidenceItem(
            ref="ev_0123456789ab",
            source="fixture",
            evidence_type="close",
            requested_date=date(2026, 8, 1),
            effective_date=date(2026, 7, 31),
            value=100,
            measurement_kind=MeasurementKind.CURRENCY,
            unit="JPY",
        ),
        EvidenceItem(
            ref="ev_abcdef012345",
            source="fixture",
            evidence_type="moving average",
            requested_date=date(2026, 8, 1),
            effective_date=date(2026, 7, 31),
            value=95,
            measurement_kind=MeasurementKind.CURRENCY,
            unit="JPY",
        ),
        EvidenceItem(
            ref="ev_9876543210ab",
            source="fixture",
            evidence_type="untyped scalar",
            requested_date=date(2026, 8, 1),
            effective_date=date(2026, 7, 31),
            value=7,
        ),
    )
    bundle = EvidenceBundle(
        instrument="6501.T",
        analysis_date=date(2026, 8, 1),
        items=items,
    )

    payload = compact_numeric_value_catalog(build_numeric_value_catalog(bundle))

    assert payload["measurements"] == {
        "m01": {"measurement_kind": "currency", "unit": "JPY"}
    }
    assert [item.get("measurement_id") for item in payload["values"]] == [
        "m01",
        "m01",
        None,
    ]
    assert all("measurement_kind" not in item for item in payload["values"])
    assert all("unit" not in item for item in payload["values"])
