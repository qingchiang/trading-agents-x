"""Compact evidence catalogs and deterministic read-only queries."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from tradingagents.application.contracts import (
    EvidenceBundle,
    EvidenceItem,
    EvidenceTable,
    EvidenceTableCell,
)

_MAX_ROWS = 120
_LARGE_TABULAR_CONTENT = 20_000


@dataclass(frozen=True)
class EvidenceLookup:
    """One non-sensitive record of a read-only evidence query."""

    tool: str
    evidence_ref: str | None = None
    table_id: str | None = None
    operation: str | None = None
    columns: tuple[str, ...] = ()
    start_date: str | None = None
    end_date: str | None = None
    frequency: str | None = None
    row_ids: tuple[str, ...] = ()
    cursor: str | None = None
    returned_rows: int = 0

    def event_payload(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "evidence_ref": self.evidence_ref,
            "table_id": self.table_id,
            "operation": self.operation,
            "columns": list(self.columns),
            "start_date": self.start_date,
            "end_date": self.end_date,
            "frequency": self.frequency,
            "row_ids": list(self.row_ids),
            "cursor": self.cursor,
            "returned_rows": self.returned_rows,
        }


@dataclass(frozen=True)
class PreparedEvidence:
    """Ephemeral evidence memo plus the exact query results that informed it."""

    catalog: dict[str, Any]
    memo: str
    query_results: tuple[dict[str, Any], ...] = ()
    lookups: tuple[EvidenceLookup, ...] = ()

    @property
    def inline_characters(self) -> int:
        return len(
            json.dumps(
                {
                    "catalog": self.catalog,
                    "memo": self.memo,
                    "query_results": self.query_results,
                },
                ensure_ascii=False,
            )
        )


def build_evidence_catalog(bundle: EvidenceBundle) -> dict[str, Any]:
    """Describe sealed evidence without serializing source bodies or table rows."""

    table_refs: dict[str, list[str]] = {}
    for table in bundle.tables:
        for ref in table.evidence_refs:
            table_refs.setdefault(ref, []).append(table.id)
    return {
        "version": bundle.version,
        "digest": bundle.digest,
        "instrument": bundle.instrument,
        "analysis_date": bundle.analysis_date.isoformat(),
        "items": [_catalog_item(item, table_refs.get(item.ref, [])) for item in bundle.items],
        "tables": [_catalog_table(table) for table in bundle.tables],
    }


def build_analyst_evidence_context(
    bundle: EvidenceBundle,
    *,
    evidence_refs: tuple[str, ...],
) -> PreparedEvidence:
    """Build a deterministic analyst context without an LLM planning pass."""

    selected_refs = set(evidence_refs)
    selected_items = tuple(
        item for item in bundle.items if item.ref in selected_refs
    )
    selected_tables = tuple(
        table
        for table in bundle.tables
        if selected_refs.intersection(table.evidence_refs)
    )
    analyst_bundle = EvidenceBundle(
        instrument=bundle.instrument,
        analysis_date=bundle.analysis_date,
        information_frontier=bundle.information_frontier,
        items=selected_items,
        tables=selected_tables,
        sealed_at=bundle.sealed_at,
    )
    query_results: list[dict[str, Any]] = [
        get_evidence_item_payload(analyst_bundle, item.ref)
        for item in selected_items
    ]
    lookups: list[EvidenceLookup] = [
        EvidenceLookup(
            tool="get_evidence_item",
            evidence_ref=item.ref,
        )
        for item in selected_items
    ]
    for table in selected_tables:
        columns = tuple(column.key for column in table.columns)
        summary = query_evidence_table_payload(
            analyst_bundle,
            table_id=table.id,
            operation="summary",
            columns=columns,
        )
        query_results.append(summary)
        lookups.append(
            EvidenceLookup(
                tool="query_evidence_table",
                table_id=table.id,
                operation="summary",
                columns=columns,
                returned_rows=int(summary.get("row_count", 0)),
            )
        )
        resampled = query_evidence_table_payload(
            analyst_bundle,
            table_id=table.id,
            operation="resample",
            columns=columns,
            frequency="month",
        )
        if "error" not in resampled:
            query_results.append(resampled)
            lookups.append(
                EvidenceLookup(
                    tool="query_evidence_table",
                    table_id=table.id,
                    operation="resample",
                    columns=columns,
                    frequency="month",
                    returned_rows=len(resampled.get("rows", [])),
                )
            )
    return PreparedEvidence(
        catalog=build_evidence_catalog(analyst_bundle),
        memo=(
            "This deterministic context contains the collection memo, compact "
            "source passages, analytical table summaries, and monthly "
            "resampling where available. Raw fact-table rows remain in the "
            "sealed Evidence ledger."
        ),
        query_results=tuple(query_results),
        lookups=tuple(lookups),
    )


def get_evidence_item_payload(
    bundle: EvidenceBundle,
    ref: str,
) -> dict[str, Any]:
    """Return one item, avoiding accidental reinlining of large fact tables."""

    item = next((candidate for candidate in bundle.items if candidate.ref == ref), None)
    if item is None:
        return {"error": "unknown_evidence_ref", "ref": ref}
    source_tables = [
        table for table in bundle.tables if ref in table.evidence_refs
    ]
    include_content = not (
        source_tables
        and item.content is not None
        and (
            len(item.content) > _LARGE_TABULAR_CONTENT
            or any(len(table.rows) > _MAX_ROWS for table in source_tables)
        )
    )
    return {
        **_catalog_item(item, [table.id for table in source_tables]),
        "content": item.content if include_content else None,
        "content_omitted": not include_content,
        "instruction": (
            "Use query_evidence_table for the complete tabular payload."
            if not include_content
            else None
        ),
    }


def query_evidence_table_payload(
    bundle: EvidenceBundle,
    *,
    table_id: str,
    operation: Literal["rows", "resample", "summary", "extrema"] = "rows",
    columns: list[str] | tuple[str, ...] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    frequency: str | None = None,
    row_ids: list[str] | tuple[str, ...] | None = None,
    cursor: str | None = None,
) -> dict[str, Any]:
    """Query one immutable EvidenceTable within the sealed PIT boundary."""

    table = next((candidate for candidate in bundle.tables if candidate.id == table_id), None)
    if table is None:
        return {"error": "unknown_table_id", "table_id": table_id}
    selected_columns = tuple(columns or (column.key for column in table.columns))
    valid_columns = {column.key for column in table.columns}
    unknown = sorted(set(selected_columns) - valid_columns)
    if unknown:
        return {
            "error": "unknown_columns",
            "table_id": table_id,
            "columns": unknown,
        }
    rows = _filter_rows(
        table,
        start_date=start_date,
        end_date=end_date,
        row_ids=tuple(row_ids or ()),
        cutoff=bundle.analysis_date,
    )
    if isinstance(rows, dict):
        return rows
    if operation == "rows":
        return _row_page(
            table,
            rows,
            columns=selected_columns,
            cursor=cursor,
        )
    if operation == "summary":
        return _summary(table, rows, selected_columns)
    if operation == "extrema":
        return _extrema(table, rows, selected_columns)
    if operation == "resample":
        return _resample(
            table,
            rows,
            selected_columns,
            frequency=frequency,
        )
    return {"error": "unsupported_operation", "operation": operation}


def prepared_evidence_prompt(prepared: PreparedEvidence) -> str:
    """Render the bounded evidence workset for a formal structured prompt."""

    return (
        "EVIDENCE CATALOG (metadata and analytical views only):\n"
        + json.dumps(prepared.catalog, ensure_ascii=False)
        + "\n\nEPHEMERAL EVIDENCE MEMO:\n"
        + prepared.memo
        + "\n\nACTUAL READ-ONLY QUERY RESULTS:\n"
        + json.dumps(prepared.query_results, ensure_ascii=False)
    )


def _catalog_item(item: EvidenceItem, table_ids: list[str]) -> dict[str, Any]:
    analytical_views = item.provenance.get("analytical_views")
    return {
        "ref": item.ref,
        "source": item.source,
        "evidence_type": item.evidence_type,
        "requested_date": item.requested_date.isoformat(),
        "effective_date": (item.effective_date.isoformat() if item.effective_date else None),
        "available_at": item.available_at.isoformat() if item.available_at else None,
        "quality": item.quality.value,
        "fallback": item.fallback,
        "value": item.value,
        "unit": item.unit,
        "content_available": item.content is not None,
        "content_characters": len(item.content or ""),
        "table_ids": table_ids,
        "dataset_id": item.provenance.get("dataset_id"),
        "analytical_views": analytical_views,
        "origins": [
            {
                "source": origin.source,
                "evidence_type": origin.evidence_type,
                "effective": origin.effective,
                "quality": origin.quality.value,
                "fallback": origin.fallback,
                "temporal_scope": origin.temporal_scope.value,
            }
            for origin in item.origins
        ],
    }


def _catalog_table(table: EvidenceTable) -> dict[str, Any]:
    date_values = _table_date_values(table)
    return {
        "id": table.id,
        "title": table.title,
        "purpose": table.purpose,
        "row_count": len(table.rows),
        "columns": [
            {
                "key": column.key,
                "label": column.label,
                "data_type": column.data_type.value,
                "unit": column.unit,
            }
            for column in table.columns
        ],
        "evidence_refs": list(table.evidence_refs),
        "source_format": table.source_format,
        "coverage_start": min(date_values).isoformat() if date_values else None,
        "coverage_end": max(date_values).isoformat() if date_values else None,
    }


def _filter_rows(
    table: EvidenceTable,
    *,
    start_date: str | None,
    end_date: str | None,
    row_ids: tuple[str, ...],
    cutoff: date,
) -> list[Any] | dict[str, Any]:
    date_key = _date_column_key(table)
    try:
        start = date.fromisoformat(start_date) if start_date else None
        end = date.fromisoformat(end_date) if end_date else cutoff
    except ValueError:
        return {"error": "invalid_date_range", "table_id": table.id}
    if end > cutoff:
        return {
            "error": "future_data_forbidden",
            "table_id": table.id,
            "analysis_cutoff": cutoff.isoformat(),
        }
    valid_row_ids = {row.id for row in table.rows}
    unknown_rows = sorted(set(row_ids) - valid_row_ids)
    if unknown_rows:
        return {
            "error": "unknown_row_ids",
            "table_id": table.id,
            "row_ids": unknown_rows,
        }
    rows = []
    for row in table.rows:
        if row_ids and row.id not in row_ids:
            continue
        if date_key:
            row_date = _cell_date(row.cells[date_key])
            if row_date is not None:
                if row_date > cutoff:
                    continue
                if start is not None and row_date < start:
                    continue
                if end is not None and row_date > end:
                    continue
        rows.append(row)
    return rows


def _row_page(
    table: EvidenceTable,
    rows: list[Any],
    *,
    columns: tuple[str, ...],
    cursor: str | None,
) -> dict[str, Any]:
    try:
        offset = max(0, int(cursor or "0"))
    except ValueError:
        return {"error": "invalid_cursor", "table_id": table.id}
    selected = rows[offset : offset + _MAX_ROWS]
    next_offset = offset + len(selected)
    return {
        "table_id": table.id,
        "operation": "rows",
        "evidence_refs": list(table.evidence_refs),
        "columns": list(columns),
        "rows": [_row_payload(row, columns, default_refs=table.evidence_refs) for row in selected],
        "returned_rows": len(selected),
        "matched_rows": len(rows),
        "cursor": str(next_offset) if next_offset < len(rows) else None,
    }


def _summary(
    table: EvidenceTable,
    rows: list[Any],
    columns: tuple[str, ...],
) -> dict[str, Any]:
    values = {
        column: [
            float(value) for row in rows if (value := _numeric_value(row.cells[column])) is not None
        ]
        for column in columns
    }
    return {
        "table_id": table.id,
        "operation": "summary",
        "evidence_refs": list(table.evidence_refs),
        "summary": {
            column: {
                "count": len(material),
                "min": min(material),
                "max": max(material),
                "mean": sum(material) / len(material),
                "latest": material[-1],
            }
            for column, material in values.items()
            if material
        },
        "returned_rows": 0,
        "matched_rows": len(rows),
    }


def _extrema(
    table: EvidenceTable,
    rows: list[Any],
    columns: tuple[str, ...],
) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for column in columns:
        material = [
            (value, row) for row in rows if (value := _numeric_value(row.cells[column])) is not None
        ]
        if not material:
            continue
        minimum = min(material, key=lambda item: item[0])
        maximum = max(material, key=lambda item: item[0])
        output[column] = {
            "min": _row_payload(
                minimum[1],
                (column,),
                default_refs=table.evidence_refs,
            ),
            "max": _row_payload(
                maximum[1],
                (column,),
                default_refs=table.evidence_refs,
            ),
        }
    return {
        "table_id": table.id,
        "operation": "extrema",
        "evidence_refs": list(table.evidence_refs),
        "extrema": output,
        "returned_rows": len(output) * 2,
        "matched_rows": len(rows),
    }


def _resample(
    table: EvidenceTable,
    rows: list[Any],
    columns: tuple[str, ...],
    *,
    frequency: str | None,
) -> dict[str, Any]:
    normalized = (frequency or "").casefold()
    if normalized not in {"day", "week", "month", "quarter", "year"}:
        return {
            "error": "invalid_frequency",
            "allowed": ["day", "week", "month", "quarter", "year"],
        }
    date_key = _date_column_key(table)
    if date_key is None:
        return {"error": "table_has_no_date_column", "table_id": table.id}
    buckets: dict[str, list[Any]] = {}
    for row in rows:
        row_date = _cell_date(row.cells[date_key])
        if row_date is None:
            continue
        key = _period_key(row_date, normalized)
        buckets.setdefault(key, []).append(row)
    output = []
    for period, bucket in buckets.items():
        cells: dict[str, Any] = {}
        for column in columns:
            if column == date_key:
                cells[column] = period
                continue
            numeric = [
                value for row in bucket if (value := _numeric_value(row.cells[column])) is not None
            ]
            if not numeric:
                cells[column] = bucket[-1].cells[column].raw_value
            elif column.casefold() == "open":
                cells[column] = numeric[0]
            elif column.casefold() == "high":
                cells[column] = max(numeric)
            elif column.casefold() == "low":
                cells[column] = min(numeric)
            elif column.casefold() == "volume":
                cells[column] = sum(numeric)
            else:
                cells[column] = numeric[-1]
        output.append({"period": period, "values": cells})
    return {
        "table_id": table.id,
        "operation": "resample",
        "evidence_refs": list(table.evidence_refs),
        "frequency": normalized,
        "rows": output,
        "returned_rows": len(output),
        "matched_rows": len(rows),
    }


def _row_payload(
    row: Any,
    columns: tuple[str, ...],
    *,
    default_refs: tuple[str, ...],
) -> dict[str, Any]:
    inherited_refs = row.source_refs or default_refs
    return {
        "row_id": row.id,
        "cells": {
            column: {
                "raw_value": row.cells[column].raw_value,
                "evidence_refs": list(row.cells[column].source_refs or inherited_refs),
            }
            for column in columns
        },
    }


def _table_date_values(table: EvidenceTable) -> list[date]:
    date_key = _date_column_key(table)
    if date_key is None:
        return []
    return [value for row in table.rows if (value := _cell_date(row.cells[date_key])) is not None]


def _date_column_key(table: EvidenceTable) -> str | None:
    return next(
        (
            column.key
            for column in table.columns
            if column.data_type.value in {"date", "datetime"}
            or column.key.casefold() in {"date", "datetime", "as_of_date"}
        ),
        None,
    )


def _cell_date(cell: EvidenceTableCell) -> date | None:
    value = cell.raw_value
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _numeric_value(cell: EvidenceTableCell) -> float | None:
    value = cell.raw_value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _period_key(value: date, frequency: str) -> str:
    if frequency == "day":
        return value.isoformat()
    if frequency == "week":
        year, week, _weekday = value.isocalendar()
        return f"{year}-W{week:02d}"
    if frequency == "month":
        return f"{value.year:04d}-{value.month:02d}"
    if frequency == "quarter":
        return f"{value.year:04d}-Q{((value.month - 1) // 3) + 1}"
    return f"{value.year:04d}"
