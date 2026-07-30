"""Deterministic, locale-aware materialization of research-table values."""

from __future__ import annotations

import ast
import math
from collections.abc import Mapping
from typing import Any

from tradingagents.application.contracts import (
    OutputLanguage,
    ResearchTable,
    ResearchTableCell,
    ResearchTableColumn,
    TableCellKind,
    TableDataType,
    TableDisplaySpec,
    TableNotation,
    report_language_value,
)

_CURRENCY_PREFIXES = {
    "USD": "$",
    "JPY": "¥",
    "CNY": "¥",
    "RMB": "¥",
    "EUR": "€",
    "GBP": "£",
}


def materialize_research_table(
    table: ResearchTable,
    *,
    output_language: OutputLanguage,
) -> ResearchTable:
    """Replace model-authored display strings with canonical values."""

    rows = tuple(
        row.model_copy(
            update={
                "cells": {
                    column.key: _materialize_cell(
                        row.cells[column.key],
                        column=column,
                        output_language=output_language,
                    )
                    for column in table.columns
                }
            }
        )
        for row in table.rows
    )
    return table.model_copy(update={"rows": rows})


def evaluate_formula(
    formula: str,
    inputs: Mapping[str, int | float],
) -> float:
    """Evaluate the limited arithmetic grammar allowed for derived cells."""

    tree = ast.parse(formula, mode="eval")

    def evaluate(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return evaluate(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(
                node.value,
                (int, float),
            ):
                raise ValueError("formula constants must be numeric")
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id not in inputs:
                raise ValueError("formula uses an unknown input")
            return float(inputs[node.id])
        if isinstance(node, ast.UnaryOp) and isinstance(
            node.op,
            (ast.UAdd, ast.USub),
        ):
            value = evaluate(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp):
            left = evaluate(node.left)
            right = evaluate(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
            if isinstance(node.op, ast.Pow):
                if abs(right) > 12:
                    raise ValueError("formula exponent is too large")
                return left**right
        raise ValueError("formula contains an unsupported operation")

    result = evaluate(tree)
    if not math.isfinite(result):
        raise ValueError("formula result must be finite")
    return result


def _materialize_cell(
    cell: ResearchTableCell,
    *,
    column: ResearchTableColumn,
    output_language: OutputLanguage,
) -> ResearchTableCell:
    display_value = format_table_value(
        cell.raw_value,
        column=column,
        output_language=output_language,
        descriptor=cell.kind is TableCellKind.DESCRIPTOR,
    )
    return cell.model_copy(update={"display_value": display_value})


def format_table_value(
    raw_value: Any,
    *,
    column: ResearchTableColumn,
    output_language: OutputLanguage,
    descriptor: bool = False,
) -> str:
    """Format one raw value using validated column-level display intent."""

    if raw_value is None:
        return "—"
    if descriptor or isinstance(raw_value, str) and column.data_type is TableDataType.TEXT:
        return str(raw_value)
    if column.data_type in {TableDataType.DATE, TableDataType.DATETIME}:
        return str(raw_value)
    if isinstance(raw_value, bool):
        return _boolean_text(raw_value, output_language)
    if not isinstance(raw_value, (int, float)) or isinstance(raw_value, bool):
        return str(raw_value)
    numeric = float(raw_value)
    if not math.isfinite(numeric):
        raise ValueError("research table raw values must be finite")

    spec = _effective_spec(column)
    scaled = numeric / spec.scale
    if spec.notation is TableNotation.PERCENT:
        scaled *= 100
    digits = 0 if spec.notation is TableNotation.INTEGER else spec.fraction_digits
    number = _number_text(scaled, digits)
    prefix = (
        _CURRENCY_PREFIXES.get((column.unit or "").upper(), "")
        if spec.notation is TableNotation.CURRENCY
        else ""
    )
    suffix = (
        "%"
        if spec.notation is TableNotation.PERCENT
        else spec.unit_label or ""
    )
    separator = (
        ""
        if (
            not suffix
            or spec.notation is TableNotation.PERCENT
            or _compact_suffix(output_language)
        )
        else " "
    )
    return f"{prefix}{number}{separator}{suffix}"


def _effective_spec(column: ResearchTableColumn) -> TableDisplaySpec:
    spec = column.display
    if spec.notation is not TableNotation.STANDARD:
        return spec
    notation = {
        TableDataType.PERCENTAGE: TableNotation.PERCENT,
        TableDataType.CURRENCY: TableNotation.CURRENCY,
        TableDataType.INTEGER: TableNotation.INTEGER,
        TableDataType.DATE: TableNotation.DATE,
        TableDataType.DATETIME: TableNotation.DATE,
    }.get(column.data_type, TableNotation.STANDARD)
    return spec.model_copy(update={"notation": notation})


def _number_text(value: float, fraction_digits: int) -> str:
    rounded = round(value, fraction_digits)
    if rounded == 0:
        rounded = 0.0
    return f"{rounded:,.{fraction_digits}f}"


def _compact_suffix(output_language: OutputLanguage) -> bool:
    language = report_language_value(output_language).casefold()
    return "zh-cn" in language or language == "ja" or "japanese" in language


def _boolean_text(value: bool, output_language: OutputLanguage) -> str:
    language = report_language_value(output_language).casefold()
    if "zh-cn" in language:
        return "是" if value else "否"
    if language == "ja" or "japanese" in language:
        return "はい" if value else "いいえ"
    return "Yes" if value else "No"
