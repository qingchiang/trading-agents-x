"""Internal LLM drafts and deterministic assembly for analyst reports."""

from __future__ import annotations

import math
import re
from enum import Enum
from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tradingagents.application.contracts import (
    AnalystClaim,
    AnalystReport,
    AnalystSection,
    DerivedValue,
    EvidenceBundle,
    OutputLanguage,
    ResearchTable,
    ResearchTableCell,
    ResearchTableColumn,
    ResearchTableRow,
    ResearchWarning,
    TableCellKind,
    TableDataType,
    TableDisplaySpec,
    TableNotation,
)
from tradingagents.application.table_display import (
    evaluate_formula,
    materialize_research_table,
)
from tradingagents.graph.output_validation import OutputValidationError

DraftScalar: TypeAlias = str | int | float | bool | None


class _DraftModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class TableColumnDataType(str, Enum):
    TEXT = "text"
    NUMBER = "number"
    INTEGER = "integer"
    PERCENT = "percent"
    CURRENCY = "currency"
    DATE = "date"
    BOOLEAN = "boolean"


class TableColumnIntent(_DraftModel):
    """LLM-owned presentation intent without public identifiers."""

    label: str = Field(min_length=1)
    data_type: TableColumnDataType
    compact: bool = False
    scale: float = Field(default=1.0, gt=0)
    fraction_digits: int = Field(default=2, ge=0, le=8)
    unit: str | None = Field(default=None, min_length=1, max_length=32)
    unit_label: str | None = Field(default=None, min_length=1, max_length=40)


class ResearchTablePlan(_DraftModel):
    """One user-facing table proposed while the report core is serialized."""

    title: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    comparison_target: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    evidence_table_ids: tuple[str, ...] = ()
    expected_columns: tuple[str, ...] = Field(min_length=1)

    @field_validator("evidence_refs", "evidence_table_ids", "expected_columns")
    @classmethod
    def deduplicate(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(value))


class AnalystSectionDraft(_DraftModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_.-]*$")
    title: str = Field(min_length=1)
    narrative: str = Field(min_length=1)
    evidence_table_ids: tuple[str, ...] = ()
    research_table_plans: tuple[ResearchTablePlan, ...] = ()

    @field_validator("evidence_table_ids")
    @classmethod
    def deduplicate_table_ids(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        return tuple(dict.fromkeys(value))


class AnalystReportDraft(_DraftModel):
    """Report core generated without complete table rows or public table IDs."""

    analyst: Literal["market", "social", "news", "fundamentals"]
    executive_summary: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    claims: tuple[AnalystClaim, ...] = Field(min_length=1)
    sections: tuple[AnalystSectionDraft, ...] = Field(min_length=1)
    catalysts: tuple[str, ...] = ()
    risks: tuple[str, ...] = Field(min_length=1)
    invalidation_conditions: tuple[str, ...] = Field(min_length=1)
    evidence_refs: tuple[str, ...] = Field(min_length=1)

    @field_validator("evidence_refs")
    @classmethod
    def deduplicate_evidence_refs(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        return tuple(dict.fromkeys(value))


class DerivedValueDraft(_DraftModel):
    """A derivation whose result is calculated only by the application."""

    formula: str = Field(min_length=1)
    inputs: dict[str, int | float] = Field(min_length=1)
    input_evidence_refs: tuple[str, ...] = Field(min_length=1)
    unit: str | None = Field(default=None, min_length=1, max_length=32)

    @field_validator("inputs", mode="before")
    @classmethod
    def reject_non_numeric_inputs(cls, value: Any) -> Any:
        if not isinstance(value, dict) or any(
            isinstance(item, bool) or not isinstance(item, int | float)
            for item in value.values()
        ):
            raise ValueError("derived inputs must be numeric")
        return value

    @field_validator("inputs")
    @classmethod
    def validate_inputs(
        cls,
        value: dict[str, int | float],
    ) -> dict[str, int | float]:
        if any(
            not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", key)
            for key in value
        ):
            raise ValueError("derived input names must be identifiers")
        return value

    @field_validator("input_evidence_refs")
    @classmethod
    def deduplicate_input_refs(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        return tuple(dict.fromkeys(value))


class ResearchTableCellDraft(_DraftModel):
    raw_value: DraftScalar = None
    kind: TableCellKind = TableCellKind.OBSERVATION
    evidence_refs: tuple[str, ...] = ()
    derivation: DerivedValueDraft | None = None

    @field_validator("evidence_refs")
    @classmethod
    def deduplicate_evidence_refs(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        return tuple(dict.fromkeys(value))

    @model_validator(mode="after")
    def validate_derivation_shape(self) -> ResearchTableCellDraft:
        if self.kind is TableCellKind.DERIVED:
            if self.derivation is None:
                raise ValueError("derived cells require a derivation")
            if self.raw_value is not None:
                raise ValueError(
                    "derived raw values are calculated by the application"
                )
        elif self.derivation is not None:
            raise ValueError("only derived cells may contain a derivation")
        return self


class ResearchTableRowDraft(_DraftModel):
    """Ordered cells avoid LLM-authored column IDs and field associations."""

    cells: tuple[ResearchTableCellDraft, ...] = Field(min_length=1)
    evidence_refs: tuple[str, ...] = ()

    @field_validator("evidence_refs")
    @classmethod
    def deduplicate_evidence_refs(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        return tuple(dict.fromkeys(value))


class ResearchTableDraft(_DraftModel):
    """One independently serialized table component."""

    columns: tuple[TableColumnIntent, ...] = Field(min_length=1)
    rows: tuple[ResearchTableRowDraft, ...] = Field(min_length=1)
    evidence_refs: tuple[str, ...] = Field(min_length=1)

    @field_validator("evidence_refs")
    @classmethod
    def deduplicate_evidence_refs(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        return tuple(dict.fromkeys(value))

    @model_validator(mode="after")
    def validate_shape(self) -> ResearchTableDraft:
        expected = len(self.columns)
        if any(len(row.cells) != expected for row in self.rows):
            raise ValueError(
                "every table row must have one ordered cell per column"
            )
        return self


def assemble_analyst_report(
    draft: AnalystReportDraft,
    table_drafts: tuple[ResearchTableDraft, ...],
    *,
    bundle: EvidenceBundle,
    output_language: OutputLanguage,
    warnings: tuple[ResearchWarning, ...] = (),
    confidence_override: float | None = None,
) -> AnalystReport:
    """Materialize public IDs, links, formula results, and display values."""

    plans = tuple(
        (section, plan)
        for section in draft.sections
        for plan in section.research_table_plans
    )
    if len(plans) != len(table_drafts):
        raise OutputValidationError("analyst_table.component_count")
    valid_refs = {item.ref for item in bundle.items}
    evidence_tables = {table.id: table for table in bundle.tables}
    _require_subset(draft.evidence_refs, valid_refs, "analyst.evidence_refs")
    tables_by_section: dict[str, list[ResearchTable]] = {
        section.id: [] for section in draft.sections
    }
    assembled_tables = []
    for table_index, ((section, plan), table_draft) in enumerate(
        zip(plans, table_drafts, strict=True),
        start=1,
    ):
        table = _assemble_research_table(
            analyst=draft.analyst,
            section=section,
            plan=plan,
            draft=table_draft,
            table_index=table_index,
            valid_refs=valid_refs,
            evidence_tables=evidence_tables,
            output_language=output_language,
        )
        tables_by_section[section.id].append(table)
        assembled_tables.append(table)

    sections = []
    for section in draft.sections:
        _require_subset(
            section.evidence_table_ids,
            set(evidence_tables),
            "analyst.section.evidence_table",
        )
        planned_evidence_tables = tuple(
            dict.fromkeys(
                table_id
                for plan in section.research_table_plans
                for table_id in plan.evidence_table_ids
            )
        )
        sections.append(
            AnalystSection(
                id=section.id,
                title=section.title,
                narrative=section.narrative,
                research_table_ids=tuple(
                    table.id for table in tables_by_section[section.id]
                ),
                evidence_table_ids=tuple(
                    dict.fromkeys(
                        (
                            *section.evidence_table_ids,
                            *planned_evidence_tables,
                        )
                    )
                ),
            )
        )

    used_refs = list(draft.evidence_refs)
    for claim in draft.claims:
        _require_subset(
            claim.evidence_refs,
            valid_refs,
            "analyst.claim.evidence_refs",
        )
        used_refs.extend(claim.evidence_refs)
    for table in assembled_tables:
        used_refs.extend(table.evidence_refs)

    return AnalystReport(
        analyst=draft.analyst,
        executive_summary=draft.executive_summary,
        confidence=(
            confidence_override
            if confidence_override is not None
            else draft.confidence
        ),
        claims=draft.claims,
        sections=tuple(sections),
        tables=tuple(assembled_tables),
        catalysts=draft.catalysts,
        risks=draft.risks,
        invalidation_conditions=draft.invalidation_conditions,
        evidence_refs=tuple(dict.fromkeys(used_refs)),
        warnings=warnings,
    )


def _assemble_research_table(
    *,
    analyst: str,
    section: AnalystSectionDraft,
    plan: ResearchTablePlan,
    draft: ResearchTableDraft,
    table_index: int,
    valid_refs: set[str],
    evidence_tables: dict[str, Any],
    output_language: OutputLanguage,
) -> ResearchTable:
    if tuple(column.label for column in draft.columns) != plan.expected_columns:
        raise OutputValidationError("research_table.columns.plan_mismatch")
    _require_subset(
        plan.evidence_refs,
        valid_refs,
        "research_table.plan.evidence_refs",
    )
    _require_subset(
        draft.evidence_refs,
        valid_refs,
        "research_table.evidence_refs",
    )
    _require_subset(
        plan.evidence_table_ids,
        set(evidence_tables),
        "research_table.plan.evidence_table_ids",
    )
    columns = _materialize_columns(draft.columns)
    table_id = f"rt_{analyst}_{section.id}_{table_index}"
    table_refs = tuple(
        dict.fromkeys((*plan.evidence_refs, *draft.evidence_refs))
    )
    rows = tuple(
        _materialize_row(
            table_id=table_id,
            index=index,
            draft=row,
            columns=columns,
            table_refs=table_refs,
            valid_refs=valid_refs,
        )
        for index, row in enumerate(draft.rows, start=1)
    )
    table_refs = tuple(
        dict.fromkeys(
            (
                *table_refs,
                *(
                    ref
                    for row in rows
                    for ref in (
                        *row.evidence_refs,
                        *(
                            ref
                            for cell in row.cells.values()
                            for ref in cell.evidence_refs
                        ),
                    )
                ),
            )
        )
    )
    source = _match_source_table(
        plan=plan,
        columns=columns,
        rows=rows,
        evidence_tables=evidence_tables,
    )
    if source is not None:
        source_table, source_row_ids = source
        table_refs = tuple(
            dict.fromkeys((*table_refs, *source_table.evidence_refs))
        )
        rows = tuple(
            row.model_copy(
                update={
                    "evidence_refs": tuple(
                        dict.fromkeys(
                            (*row.evidence_refs, *source_row.evidence_refs)
                        )
                    )
                }
            )
            for row, source_row in zip(
                rows,
                (
                    next(
                        item
                        for item in source_table.rows
                        if item.id == source_row_id
                    )
                    for source_row_id in source_row_ids
                ),
                strict=True,
            )
        )
    table = ResearchTable(
        id=table_id,
        title=plan.title,
        purpose=plan.purpose,
        columns=columns,
        rows=rows,
        evidence_refs=table_refs,
        source_evidence_table_id=source[0].id if source is not None else None,
        total_source_rows=len(source[0].rows) if source is not None else None,
        source_evidence_row_ids=source[1] if source is not None else (),
    )
    return materialize_research_table(
        table,
        output_language=output_language,
    )


def _materialize_columns(
    intents: tuple[TableColumnIntent, ...],
) -> tuple[ResearchTableColumn, ...]:
    used_keys: set[str] = set()
    columns = []
    for index, intent in enumerate(intents, start=1):
        key = _column_key(intent.label, index)
        if key in used_keys:
            key = f"{key}_{index}"
        used_keys.add(key)
        data_type = TableDataType(intent.data_type.value)
        if data_type is TableDataType.PERCENT:
            notation = TableNotation.PERCENT
        elif data_type is TableDataType.CURRENCY:
            notation = TableNotation.CURRENCY
        elif data_type is TableDataType.DATE:
            notation = TableNotation.DATE
        elif data_type is TableDataType.INTEGER and not intent.compact:
            notation = TableNotation.INTEGER
        elif intent.compact:
            notation = TableNotation.COMPACT
        else:
            notation = TableNotation.STANDARD
        columns.append(
            ResearchTableColumn(
                key=key,
                label=intent.label,
                data_type=data_type,
                unit=intent.unit,
                display=TableDisplaySpec(
                    notation=notation,
                    scale=intent.scale,
                    fraction_digits=intent.fraction_digits,
                    unit_label=intent.unit_label,
                ),
            )
        )
    return tuple(columns)


def _column_key(label: str, index: int) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", label.casefold()).strip("_")
    if not normalized or not normalized[0].isalpha():
        normalized = f"column_{index}"
    return normalized


def _materialize_row(
    *,
    table_id: str,
    index: int,
    draft: ResearchTableRowDraft,
    columns: tuple[ResearchTableColumn, ...],
    table_refs: tuple[str, ...],
    valid_refs: set[str],
) -> ResearchTableRow:
    _require_subset(
        draft.evidence_refs,
        valid_refs,
        "research_table.row.evidence_refs",
    )
    cells = {}
    for column, cell_draft in zip(columns, draft.cells, strict=True):
        _require_subset(
            cell_draft.evidence_refs,
            valid_refs,
            "research_table.cell.evidence_refs",
        )
        raw_value = cell_draft.raw_value
        derived = None
        cell_refs = cell_draft.evidence_refs
        if cell_draft.derivation is not None:
            _require_subset(
                cell_draft.derivation.input_evidence_refs,
                valid_refs,
                "research_table.derived.evidence_refs",
            )
            try:
                result = evaluate_formula(
                    cell_draft.derivation.formula,
                    cell_draft.derivation.inputs,
                )
            except (ValueError, ZeroDivisionError, OverflowError) as exc:
                raise OutputValidationError(
                    "research_table.derived.formula"
                ) from exc
            if not math.isfinite(result):
                raise OutputValidationError(
                    "research_table.derived.result"
                )
            raw_value = result
            cell_refs = tuple(
                dict.fromkeys(
                    (
                        *cell_refs,
                        *cell_draft.derivation.input_evidence_refs,
                    )
                )
            )
            derived = DerivedValue(
                formula=cell_draft.derivation.formula,
                inputs=cell_draft.derivation.inputs,
                input_evidence_refs=(
                    cell_draft.derivation.input_evidence_refs
                ),
                unit=cell_draft.derivation.unit,
                result=result,
            )
        effective_refs = cell_refs or draft.evidence_refs or table_refs
        if (
            cell_draft.kind is not TableCellKind.DESCRIPTOR
            and not effective_refs
        ):
            raise OutputValidationError(
                "research_table.cell.evidence_required"
            )
        cells[column.key] = ResearchTableCell(
            raw_value=raw_value,
            display_value="pending",
            kind=cell_draft.kind,
            evidence_refs=cell_refs,
            derived=derived,
        )
    return ResearchTableRow(
        id=f"{table_id}.row_{index}",
        cells=cells,
        evidence_refs=draft.evidence_refs,
    )


def _match_source_table(
    *,
    plan: ResearchTablePlan,
    columns: tuple[ResearchTableColumn, ...],
    rows: tuple[ResearchTableRow, ...],
    evidence_tables: dict[str, Any],
) -> tuple[Any, tuple[str, ...]] | None:
    matches = []
    for table_id in plan.evidence_table_ids:
        source = evidence_tables[table_id]
        source_columns = {}
        for column in columns:
            candidates = [
                item
                for item in source.columns
                if item.key == column.key
                or item.label.casefold() == column.label.casefold()
            ]
            if len(candidates) != 1:
                break
            source_columns[column.key] = candidates[0].key
        if len(source_columns) != len(columns):
            continue
        row_ids = []
        used = set()
        for row in rows:
            candidates = [
                source_row
                for source_row in source.rows
                if source_row.id not in used
                and all(
                    _raw_values_equal(
                        row.cells[column.key].raw_value,
                        source_row.cells[source_columns[column.key]].raw_value,
                    )
                    for column in columns
                    if row.cells[column.key].kind
                    in {
                        TableCellKind.DESCRIPTOR,
                        TableCellKind.OBSERVATION,
                    }
                )
                and all(
                    row.cells[column.key].kind
                    in {
                        TableCellKind.DESCRIPTOR,
                        TableCellKind.OBSERVATION,
                    }
                    for column in columns
                )
            ]
            if len(candidates) != 1:
                break
            row_ids.append(candidates[0].id)
            used.add(candidates[0].id)
        if len(row_ids) == len(rows):
            matches.append((source, tuple(row_ids)))
    return matches[0] if len(matches) == 1 else None


def _raw_values_equal(left: Any, right: Any) -> bool:
    if (
        isinstance(left, int | float)
        and not isinstance(left, bool)
        and isinstance(right, int | float)
        and not isinstance(right, bool)
    ):
        return math.isclose(
            float(left),
            float(right),
            rel_tol=1e-9,
            abs_tol=1e-9,
        )
    return left == right


def _require_subset(
    values: tuple[str, ...],
    valid: set[str],
    issue: str,
) -> None:
    if not set(values).issubset(valid):
        raise OutputValidationError(issue)
