"""Shared exact-content grouping helpers for evidence presentation."""

from __future__ import annotations

import csv
import math
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime

from .contracts import (
    EvidenceItem,
    EvidenceTable,
    EvidenceTableCell,
    EvidenceTableColumn,
    EvidenceTableRow,
    TableDataType,
)

_MARKDOWN_DIVIDER = re.compile(r"^:?-{3,}:?$")
_INTEGER = re.compile(r"^[+-]?\d+$")
_NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
_UNAVAILABLE_VALUES = {"", "-", "—", "n/a", "na", "null", "none"}
_CSV_HEADER_HINTS = {
    "date",
    "datetime",
    "open",
    "high",
    "low",
    "close",
    "adj close",
    "volume",
    "value",
    "field",
    "indicator",
    "reportdate",
    "publishdate",
    "updatedate",
    "visibilitydate",
    "currency",
    "audited",
}
_METADATA_HEADINGS = (
    "actual data source:",
    "data retrieved",
    "effective trading date:",
    "price adjustment:",
    "requested ",
    "total records:",
)


@dataclass(frozen=True)
class EvidenceContentGroup:
    """Evidence items whose complete non-empty bodies are byte-identical."""

    items: tuple[EvidenceItem, ...]

    @property
    def canonical(self) -> EvidenceItem:
        return self.items[0]

    @property
    def refs(self) -> tuple[str, ...]:
        return tuple(item.ref for item in self.items)

    @property
    def content(self) -> str | None:
        return self.canonical.content


def group_evidence_by_content(
    items: Iterable[EvidenceItem],
) -> tuple[EvidenceContentGroup, ...]:
    """Group only exact, non-empty bodies while retaining stable item order."""
    groups: dict[tuple[str, str], list[EvidenceItem]] = {}
    for item in items:
        key = ("content", item.content) if item.content else ("ref", item.ref)
        groups.setdefault(key, []).append(item)
    return tuple(EvidenceContentGroup(items=tuple(group)) for group in groups.values())


def extract_evidence_tables(
    items: Iterable[EvidenceItem],
) -> tuple[EvidenceTable, ...]:
    """Extract complete Markdown and CSV tables from immutable source bodies.

    This is deliberately deterministic: it never asks an LLM to transcribe
    facts, never truncates rows, and cites every cell back to all exact-body
    evidence refs that supplied it.
    """

    tables: dict[str, EvidenceTable] = {}
    for group in group_evidence_by_content(items):
        if not group.content:
            continue
        source = group.canonical
        purpose = (
            f"Deterministically parsed from the complete {source.evidence_type} source payload."
        )
        candidates = [
            *_markdown_table_candidates(group.content),
            *_csv_table_candidates(group.content),
        ]
        for title, headers, raw_rows, source_format in candidates:
            columns = _columns(headers, raw_rows)
            rows = _rows(columns, raw_rows)
            if not rows:
                continue
            table = EvidenceTable.create(
                title=title,
                purpose=purpose,
                columns=columns,
                rows=rows,
                evidence_refs=group.refs,
                source_format=source_format,
            )
            existing = tables.get(table.id)
            tables[table.id] = (
                _merge_evidence_tables(existing, table) if existing is not None else table
            )
    return tuple(tables.values())


def _merge_evidence_tables(
    left: EvidenceTable,
    right: EvidenceTable,
) -> EvidenceTable:
    refs = tuple(dict.fromkeys((*left.evidence_refs, *right.evidence_refs)))
    return EvidenceTable(
        id=left.id,
        title=left.title,
        purpose=left.purpose,
        columns=left.columns,
        rows=left.rows,
        evidence_refs=refs,
        source_format=left.source_format,
    )


def _markdown_table_candidates(
    content: str,
) -> list[tuple[str, list[str], list[list[str]], str]]:
    lines = content.splitlines()
    candidates: list[tuple[str, list[str], list[list[str]], str]] = []
    index = 0
    while index + 2 < len(lines):
        headers = _markdown_cells(lines[index])
        divider = _markdown_cells(lines[index + 1])
        if (
            headers is None
            or divider is None
            or len(headers) < 2
            or len(headers) != len(divider)
            or not all(_MARKDOWN_DIVIDER.fullmatch(cell) for cell in divider)
        ):
            index += 1
            continue
        raw_rows: list[list[str]] = []
        cursor = index + 2
        while cursor < len(lines):
            cells = _markdown_cells(lines[cursor])
            if cells is None or len(cells) != len(headers):
                break
            raw_rows.append(cells)
            cursor += 1
        title = _nearest_heading(
            lines,
            index,
            fallback="Source evidence table",
        )
        normalized_headers = {header.casefold() for header in headers}
        if (
            raw_rows
            and "provenance" not in title.casefold()
            and not {"evidence", "source"}.issubset(normalized_headers)
        ):
            candidates.append((title, headers, raw_rows, "markdown"))
        index = max(cursor, index + 1)
    return candidates


def _csv_table_candidates(
    content: str,
) -> list[tuple[str, list[str], list[list[str]], str]]:
    lines = content.splitlines()
    candidates: list[tuple[str, list[str], list[list[str]], str]] = []
    index = 0
    while index + 1 < len(lines):
        line = lines[index]
        if not line.strip() or line.lstrip().startswith(("#", "|")) or "," not in line:
            index += 1
            continue
        headers = _csv_cells(line)
        if headers is None or len(headers) < 2:
            index += 1
            continue
        raw_rows: list[list[str]] = []
        cursor = index + 1
        while cursor < len(lines):
            candidate = lines[cursor]
            if not candidate.strip() or candidate.lstrip().startswith(("#", "|")):
                break
            cells = _csv_cells(candidate)
            if cells is None or len(cells) != len(headers):
                break
            raw_rows.append(cells)
            cursor += 1
        if raw_rows and _looks_like_csv_header(headers, raw_rows):
            candidates.append(
                (
                    _nearest_heading(
                        lines,
                        index,
                        fallback="Source CSV data",
                    ),
                    headers,
                    raw_rows,
                    "csv",
                )
            )
        index = max(cursor, index + 1)
    return candidates


def _markdown_cells(line: str) -> list[str] | None:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return None
    body = stripped[1:-1]
    cells = re.split(r"(?<!\\)\|", body)
    return [cell.strip().replace(r"\|", "|") for cell in cells]


def _csv_cells(line: str) -> list[str] | None:
    try:
        return [cell.strip() for cell in next(csv.reader([line]))]
    except (csv.Error, StopIteration):
        return None


def _looks_like_csv_header(
    headers: list[str],
    rows: list[list[str]],
) -> bool:
    normalized = {header.strip().casefold() for header in headers}
    if not headers[0].strip() or normalized & _CSV_HEADER_HINTS:
        return True
    if len(rows) < 2:
        return False
    header_is_text = all(
        _parse_number(header) is None and not _is_date(header) for header in headers if header
    )
    data_has_typed_value = any(
        _parse_number(cell) is not None or _is_date(cell) for row in rows for cell in row
    )
    return header_is_text and data_has_typed_value


def _nearest_heading(
    lines: list[str],
    table_index: int,
    *,
    fallback: str,
) -> str:
    for raw in reversed(lines[:table_index]):
        value = raw.strip().lstrip("#").strip()
        if not value:
            continue
        lowered = value.casefold()
        if any(lowered.startswith(prefix) for prefix in _METADATA_HEADINGS):
            continue
        if raw.lstrip().startswith("#"):
            return value
    return fallback


def _columns(
    headers: list[str],
    rows: list[list[str]],
) -> tuple[EvidenceTableColumn, ...]:
    keys: set[str] = set()
    columns = []
    for index, header in enumerate(headers, start=1):
        label = header or f"Column {index}"
        base = re.sub(r"[^a-z0-9]+", "_", label.casefold()).strip("_")
        if not base or not base[0].isalpha():
            base = f"column_{index}"
        key = base
        duplicate = 2
        while key in keys:
            key = f"{base}_{duplicate}"
            duplicate += 1
        keys.add(key)
        values = [row[index - 1] for row in rows]
        data_type, unit = _infer_column_type(label, values)
        columns.append(
            EvidenceTableColumn(
                key=key,
                label=label,
                data_type=data_type,
                unit=unit,
            )
        )
    return tuple(columns)


def _rows(
    columns: tuple[EvidenceTableColumn, ...],
    rows: list[list[str]],
) -> tuple[EvidenceTableRow, ...]:
    output = []
    for row_index, values in enumerate(rows, start=1):
        cells = {}
        for column, displayed in zip(columns, values, strict=True):
            cells[column.key] = EvidenceTableCell(
                raw_value=_typed_value(displayed, column.data_type),
            )
        output.append(
            EvidenceTableRow(
                id=f"row_{row_index:04d}",
                cells=cells,
            )
        )
    return tuple(output)


def _infer_column_type(
    label: str,
    values: list[str],
) -> tuple[TableDataType, str | None]:
    material = [
        value.strip() for value in values if value.strip().casefold() not in _UNAVAILABLE_VALUES
    ]
    lowered_label = label.casefold()
    if not material:
        return TableDataType.TEXT, None
    if all(_is_datetime(value) for value in material):
        return TableDataType.DATETIME, None
    if all(_is_date(value) for value in material):
        return TableDataType.DATE, None
    if all(value.casefold() in {"true", "false", "yes", "no"} for value in material):
        return TableDataType.BOOLEAN, None
    if all(value.rstrip().endswith("%") for value in material):
        return TableDataType.PERCENT, "%"
    currency = _currency_unit(label, material)
    numbers = [_parse_number(value) for value in material]
    if all(number is not None for number in numbers):
        if currency:
            return TableDataType.CURRENCY, currency
        if "%" in label or "percent" in lowered_label or "percentage" in lowered_label:
            return TableDataType.PERCENT, "%"
        if all(isinstance(number, int) and not isinstance(number, bool) for number in numbers):
            return TableDataType.INTEGER, None
        return TableDataType.NUMBER, None
    return TableDataType.TEXT, currency


def _typed_value(value: str, data_type: TableDataType):
    stripped = value.strip()
    if stripped.casefold() in _UNAVAILABLE_VALUES:
        return None
    if data_type is TableDataType.BOOLEAN:
        return stripped.casefold() in {"true", "yes"}
    if data_type in {
        TableDataType.INTEGER,
        TableDataType.NUMBER,
        TableDataType.PERCENT,
        TableDataType.CURRENCY,
    }:
        parsed = _parse_number(stripped)
        return parsed if parsed is not None else stripped
    return stripped


def _parse_number(value: str) -> int | float | None:
    stripped = value.strip()
    negative = stripped.startswith("(") and stripped.endswith(")")
    if negative:
        stripped = stripped[1:-1]
    stripped = stripped.replace(",", "").replace(" ", "")
    stripped = stripped.rstrip("%")
    stripped = re.sub(r"^(?:[$€£¥]|USD|JPY|CNY|RMB|EUR|GBP)", "", stripped)
    stripped = re.sub(r"(?:USD|JPY|CNY|RMB|EUR|GBP)$", "", stripped)
    if _INTEGER.fullmatch(stripped):
        parsed: int | float = int(stripped)
    elif _NUMBER.fullmatch(stripped):
        parsed = float(stripped)
        if not math.isfinite(parsed):
            return None
    else:
        return None
    return -parsed if negative else parsed


def _is_date(value: str) -> bool:
    try:
        date.fromisoformat(value.strip())
    except ValueError:
        return False
    return True


def _is_datetime(value: str) -> bool:
    stripped = value.strip().replace("Z", "+00:00")
    if "T" not in stripped and " " not in stripped:
        return False
    try:
        datetime.fromisoformat(stripped)
    except ValueError:
        return False
    return True


def _currency_unit(label: str, values: list[str]) -> str | None:
    text = " ".join((label, *values)).upper()
    for token in ("USD", "JPY", "CNY", "RMB", "EUR", "GBP"):
        if token in text:
            return "CNY" if token == "RMB" else token
    for symbol, unit in (("$", "USD"), ("¥", "JPY"), ("€", "EUR"), ("£", "GBP")):
        if symbol in text:
            return unit
    return None
