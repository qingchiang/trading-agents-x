"""Compact, deterministic scalar index for final-decision numeric audit."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from tradingagents.application.contracts import (
    EvidenceBundle,
    EvidenceTable,
    EvidenceTableRow,
    EvidenceValueLocator,
)


@dataclass(frozen=True)
class NumericValueCatalogEntry:
    """One exact scalar that the model may reference without copying its value."""

    id: str
    label: str
    value: float
    unit: str | None
    evidence_refs: tuple[str, ...]
    locator: EvidenceValueLocator
    observed_date: date | None = None

    def prompt_payload(self) -> dict[str, Any]:
        return {
            "value_ref": self.id,
            "label": self.label,
            "value": self.value,
            "unit": self.unit,
            "evidence_refs": list(self.evidence_refs),
            "observed_date": (
                self.observed_date.isoformat() if self.observed_date else None
            ),
        }


def build_numeric_value_catalog(
    bundle: EvidenceBundle,
    *,
    allowed_evidence_refs: set[str] | None = None,
) -> tuple[NumericValueCatalogEntry, ...]:
    """Index exact item scalars and research-relevant fact-table cells.

    Large raw date-series tables contribute only their latest row and trailing
    252-row high/low extrema. Small fact tables contribute their numeric cells.
    Complete raw rows remain solely in the Evidence Ledger.
    """

    allowed = (
        allowed_evidence_refs
        if allowed_evidence_refs is not None
        else {item.ref for item in bundle.items}
    )
    entries: list[NumericValueCatalogEntry] = []
    for item in bundle.items:
        if item.ref not in allowed or not _is_number(item.value):
            continue
        entries.append(
            _entry(
                label=f"{item.evidence_type} ({item.source})",
                value=float(item.value),
                unit=item.unit,
                evidence_refs=(item.ref,),
                locator=EvidenceValueLocator(evidence_ref=item.ref),
            )
        )

    for table in bundle.tables:
        inherited_refs = tuple(ref for ref in table.evidence_refs if ref in allowed)
        if not inherited_refs:
            continue
        rows = table.rows if len(table.rows) <= 120 else _large_table_rows(table)
        for row in rows:
            row_refs = tuple(ref for ref in row.source_refs if ref in allowed)
            observed_date = _row_date(table, row)
            row_label = _row_label(table, row)
            for column in table.columns:
                cell = row.cells[column.key]
                if not _is_number(cell.raw_value):
                    continue
                refs = tuple(ref for ref in cell.source_refs if ref in allowed)
                refs = refs or row_refs or inherited_refs
                if not refs:
                    continue
                entries.append(
                    _entry(
                        label=f"{table.title} · {row_label} · {column.label}",
                        value=float(cell.raw_value),
                        unit=column.unit,
                        evidence_refs=refs,
                        locator=EvidenceValueLocator(
                            evidence_ref=refs[0],
                            table_id=table.id,
                            row_id=row.id,
                            column=column.key,
                        ),
                        observed_date=observed_date,
                    )
                )

    unique: dict[str, NumericValueCatalogEntry] = {}
    for entry in entries:
        unique.setdefault(entry.id, entry)
    return tuple(unique.values())


def _entry(
    *,
    label: str,
    value: float,
    unit: str | None,
    evidence_refs: tuple[str, ...],
    locator: EvidenceValueLocator,
    observed_date: date | None = None,
) -> NumericValueCatalogEntry:
    identity = {
        "locator": locator.model_dump(mode="json"),
        "value": value,
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:12]
    return NumericValueCatalogEntry(
        id=f"nv_{digest}",
        label=label,
        value=value,
        unit=unit,
        evidence_refs=tuple(dict.fromkeys(evidence_refs)),
        locator=locator,
        observed_date=observed_date,
    )


def _large_table_rows(table: EvidenceTable) -> tuple[EvidenceTableRow, ...]:
    date_key = _date_key(table)
    if date_key is None:
        return ()
    dated = tuple(
        (parsed, row)
        for row in table.rows
        if (parsed := _cell_date(row.cells[date_key].raw_value)) is not None
    )
    if not dated:
        return ()
    ordered = tuple(row for _, row in sorted(dated, key=lambda item: item[0]))
    selected: dict[str, EvidenceTableRow] = {ordered[-1].id: ordered[-1]}
    trailing = ordered[-252:]
    columns = {column.key for column in table.columns}
    for key, pick in (("high", max), ("low", min)):
        if key not in columns:
            continue
        numeric = tuple(
            (float(row.cells[key].raw_value), row)
            for row in trailing
            if _is_number(row.cells[key].raw_value)
        )
        if numeric:
            row = pick(numeric, key=lambda item: item[0])[1]
            selected.setdefault(row.id, row)
    return tuple(selected.values())


def _row_label(table: EvidenceTable, row: EvidenceTableRow) -> str:
    for column in table.columns:
        value = row.cells[column.key].raw_value
        if isinstance(value, str) and value.strip():
            return value.strip()
    return row.id


def _row_date(table: EvidenceTable, row: EvidenceTableRow) -> date | None:
    key = _date_key(table)
    return _cell_date(row.cells[key].raw_value) if key else None


def _date_key(table: EvidenceTable) -> str | None:
    for column in table.columns:
        if column.data_type.value in {"date", "datetime"} or column.key in {
            "date",
            "datetime",
            "as_of_date",
        }:
            return column.key
    return None


def _cell_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)
