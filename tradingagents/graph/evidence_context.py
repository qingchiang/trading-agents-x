"""Compact evidence catalogs and audited, read-only role worksets."""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field, model_validator

from tradingagents.application.contracts import (
    EvidenceBundle,
    EvidenceItem,
    EvidenceTable,
    EvidenceTableCell,
)
from tradingagents.graph.output_validation import OutputValidationError
from tradingagents.graph.structured_output import (
    StructuredOutputError,
    StructuredOutputRunner,
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


class EvidenceLookupRequest(BaseModel):
    """One batchable read-only lookup selected during evidence preparation."""

    model_config = ConfigDict(extra="forbid")

    tool: Literal["get_evidence_item", "query_evidence_table"]
    evidence_ref: str | None = None
    table_id: str | None = None
    operation: Literal["rows", "resample", "summary", "extrema"] | None = None
    columns: tuple[str, ...] = ()
    start_date: str | None = None
    end_date: str | None = None
    frequency: str | None = None
    row_ids: tuple[str, ...] = ()
    cursor: str | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> EvidenceLookupRequest:
        if self.tool == "get_evidence_item":
            if not self.evidence_ref or self.table_id is not None:
                raise ValueError(
                    "item lookup requires evidence_ref and forbids table_id"
                )
        elif not self.table_id or self.evidence_ref is not None:
            raise ValueError(
                "table lookup requires table_id and forbids evidence_ref"
            )
        return self


class EvidenceWorksetPlan(BaseModel):
    """Small typed plan produced after the thinking model's blueprint."""

    model_config = ConfigDict(extra="forbid")

    memo: str = Field(min_length=1)
    lookups: tuple[EvidenceLookupRequest, ...] = ()


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


def get_evidence_item_payload(
    bundle: EvidenceBundle,
    ref: str,
) -> dict[str, Any]:
    """Return one item, avoiding accidental reinlining of large fact tables."""

    item = next((candidate for candidate in bundle.items if candidate.ref == ref), None)
    if item is None:
        return {"error": "unknown_evidence_ref", "ref": ref}
    source_tables = [table.id for table in bundle.tables if ref in table.evidence_refs]
    include_content = not (
        source_tables and item.content is not None and len(item.content) > _LARGE_TABULAR_CONTENT
    )
    return {
        **_catalog_item(item, source_tables),
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


def prepare_evidence(
    llm: Any,
    *,
    serializer_llm: Any | None = None,
    bundle: EvidenceBundle,
    role_prompt: str,
    node: str,
    invoke_config: dict[str, Any] | None = None,
    memo_instruction: str | None = None,
    event_writer: Callable[[dict[str, Any]], None] | None = None,
) -> PreparedEvidence:
    """Plan once, serialize one batch, then execute immutable local lookups."""

    catalog = build_evidence_catalog(bundle)
    fallback_memo = (
        "Use the complete typed research context and the evidence catalog. "
        "No additional evidence slice was requested."
    )
    try:
        response = llm.invoke(
            [
                SystemMessage(
                    content=(
                        memo_instruction
                        or (
                            "Create a detailed evidence-preparation blueprint "
                            "for the formal research output that follows. "
                            "Identify the exact source passages, values, table "
                            "operations, columns, ranges, comparisons, "
                            "counter-evidence, and uncertainty worth checking. "
                            "Do not write the formal artifact and do not invent "
                            "IDs outside the supplied catalog."
                        )
                    )
                ),
                HumanMessage(
                    content=(
                        f"NODE: {node}\n\nROLE CONTEXT:\n{role_prompt}\n\n"
                        "EVIDENCE CATALOG:\n"
                        + json.dumps(catalog, ensure_ascii=False)
                    )
                ),
            ],
            config=invoke_config,
        )
    except Exception:
        return PreparedEvidence(catalog=catalog, memo=fallback_memo)
    blueprint = _message_text(response).strip() or fallback_memo
    serializer = serializer_llm or llm
    try:
        plan = StructuredOutputRunner(
            llm=serializer,
            schema=EvidenceWorksetPlan,
            validator=lambda candidate: _validate_workset_plan(
                candidate,
                bundle,
            ),
            node=node,
            event_writer=event_writer,
            invoke_config=invoke_config,
            repair_mode="preferred",
        ).invoke(
            (
                "Convert this evidence-preparation blueprint into one batch of "
                "read-only lookups. Preserve the useful research memo. Use "
                "get_evidence_item for exact source text/value and "
                "query_evidence_table for rows, summary, extrema, or resample. "
                "Do not request data merely because it exists.\n\n"
                f"BLUEPRINT:\n{blueprint}\n\n"
                "EVIDENCE CATALOG:\n"
                + json.dumps(catalog, ensure_ascii=False)
            ),
            example={
                "memo": "Verify the latest price range and the relevant passage.",
                "lookups": [
                    {
                        "tool": "get_evidence_item",
                        "evidence_ref": (
                            bundle.items[0].ref if bundle.items else None
                        ),
                    }
                ]
                if bundle.items
                else [],
            },
            allowed_evidence_refs=tuple(item.ref for item in bundle.items),
        ).value
    except StructuredOutputError:
        return PreparedEvidence(catalog=catalog, memo=blueprint)

    lookup_records: list[EvidenceLookup] = []
    query_results: list[dict[str, Any]] = []
    for request in _dedupe_lookup_requests(plan.lookups):
        payload, lookup = _execute_lookup(bundle, request)
        query_results.append(payload)
        lookup_records.append(lookup)
    return PreparedEvidence(
        catalog=catalog,
        memo=plan.memo,
        query_results=tuple(query_results),
        lookups=tuple(lookup_records),
    )


def _message_text(response: Any) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict):
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts)


def _validate_workset_plan(
    plan: EvidenceWorksetPlan,
    bundle: EvidenceBundle,
) -> EvidenceWorksetPlan:
    item_refs = {item.ref for item in bundle.items}
    tables = {table.id: table for table in bundle.tables}
    for request in plan.lookups:
        if request.tool == "get_evidence_item":
            if request.evidence_ref not in item_refs:
                raise OutputValidationError("workset.evidence_ref.unknown")
            continue
        table = tables.get(request.table_id or "")
        if table is None:
            raise OutputValidationError("workset.table.unknown")
        valid_columns = {column.key for column in table.columns}
        if not set(request.columns).issubset(valid_columns):
            raise OutputValidationError("workset.column.unknown")
        valid_rows = {row.id for row in table.rows}
        if not set(request.row_ids).issubset(valid_rows):
            raise OutputValidationError("workset.row.unknown")
        if request.operation == "resample" and request.frequency not in {
            "day",
            "week",
            "month",
            "quarter",
            "year",
        }:
            raise OutputValidationError("workset.frequency.invalid")
        for raw_date in (request.start_date, request.end_date):
            if raw_date is None:
                continue
            try:
                parsed = date.fromisoformat(raw_date)
            except ValueError as exc:
                raise OutputValidationError("workset.date.invalid") from exc
            if parsed > bundle.analysis_date:
                raise OutputValidationError("workset.date.future")
        if request.cursor is not None:
            try:
                if int(request.cursor) < 0:
                    raise ValueError
            except ValueError as exc:
                raise OutputValidationError("workset.cursor.invalid") from exc
    return plan


def _dedupe_lookup_requests(
    requests: tuple[EvidenceLookupRequest, ...],
) -> tuple[EvidenceLookupRequest, ...]:
    deduped: dict[str, EvidenceLookupRequest] = {}
    for request in requests:
        identity = json.dumps(
            request.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        deduped.setdefault(identity, request)
    return tuple(deduped.values())


def _execute_lookup(
    bundle: EvidenceBundle,
    request: EvidenceLookupRequest,
) -> tuple[dict[str, Any], EvidenceLookup]:
    if request.tool == "get_evidence_item":
        payload = get_evidence_item_payload(
            bundle,
            request.evidence_ref or "",
        )
        return (
            payload,
            EvidenceLookup(
                tool=request.tool,
                evidence_ref=request.evidence_ref,
            ),
        )
    payload = query_evidence_table_payload(
        bundle,
        table_id=request.table_id or "",
        operation=request.operation or "rows",
        columns=request.columns,
        start_date=request.start_date,
        end_date=request.end_date,
        frequency=request.frequency,
        row_ids=request.row_ids,
        cursor=request.cursor,
    )
    return (
        payload,
        EvidenceLookup(
            tool=request.tool,
            table_id=request.table_id,
            operation=request.operation or "rows",
            columns=request.columns,
            start_date=request.start_date,
            end_date=request.end_date,
            frequency=request.frequency,
            row_ids=request.row_ids,
            cursor=request.cursor,
            returned_rows=int(payload.get("returned_rows", 0)),
        ),
    )


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
