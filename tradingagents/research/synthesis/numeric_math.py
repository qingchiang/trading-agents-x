"""Deterministic formula and display-scale rules."""

from __future__ import annotations

import ast
import math
from collections.abc import Mapping
from decimal import Decimal

from tradingagents.domain.common import (
    NumericDisplayScale,
)
from tradingagents.research.synthesis.drafts import (
    CalculationInputDraft,
    DecisionNumericRequirementDraft,
)
from tradingagents.research.synthesis.output_validation import (
    OutputValidationError,
)


def requirement_input_mapping(
    requirement: DecisionNumericRequirementDraft,
) -> dict[str, int | float]:
    return {item.name: item.value for item in requirement.inputs}


def _calculation_date_refs(
    inputs: tuple[CalculationInputDraft, ...],
) -> tuple[str, ...]:
    """Return only Evidence refs that date required formula inputs."""

    return tuple(
        dict.fromkeys(evidence_ref for item in inputs for evidence_ref in item.date_evidence_refs)
    )


def _formula_identity(formula: str) -> str | None:
    try:
        return ast.dump(ast.parse(formula, mode="eval"), include_attributes=False)
    except SyntaxError:
        return None


def _evaluate_formula(
    formula: str,
    inputs: Mapping[str, int | float],
    *,
    issue_prefix: str = "calculation",
) -> float:
    try:
        tree = ast.parse(formula, mode="eval")
    except SyntaxError as exc:
        raise OutputValidationError(f"{issue_prefix}.formula.invalid_syntax") from exc

    referenced_names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    missing_names = referenced_names.difference(inputs)
    if missing_names:
        raise OutputValidationError(f"{issue_prefix}.formula.missing_input")
    unused_names = set(inputs).difference(referenced_names)
    if unused_names:
        raise OutputValidationError(f"{issue_prefix}.formula.unused_input")

    def evaluate(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return evaluate(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(
                node.value,
                (int, float),
            ):
                raise OutputValidationError(f"{issue_prefix}.formula.non_numeric_constant")
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id not in inputs:
                raise OutputValidationError(f"{issue_prefix}.formula.missing_input")
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
                if right == 0:
                    raise OutputValidationError(f"{issue_prefix}.formula.division_by_zero")
                return left / right
            if isinstance(node.op, ast.Pow) and abs(right) <= 12:
                try:
                    return left**right
                except OverflowError as exc:
                    raise OutputValidationError(f"{issue_prefix}.formula.overflow") from exc
        raise OutputValidationError(f"{issue_prefix}.formula.unsupported_operation")

    result = evaluate(tree)
    if not math.isfinite(result):
        raise OutputValidationError(f"{issue_prefix}.formula.non_finite_result")
    return result


_PERCENT_CALCULATION_UNITS = {"%", "PCT", "PERCENT"}


_PERCENTAGE_POINT_CALCULATION_UNITS = {"PP", "PERCENTAGE POINTS"}


_BASIS_POINT_CALCULATION_UNITS = {"BPS", "BASIS POINTS"}


_DIMENSIONLESS_BASE_DISPLAY_UNITS = {
    *_PERCENT_CALCULATION_UNITS,
    *_PERCENTAGE_POINT_CALCULATION_UNITS,
    *_BASIS_POINT_CALCULATION_UNITS,
    "X",
    "倍",
}


_DISPLAY_SCALE_FACTORS = {
    NumericDisplayScale.BASE: Decimal("1"),
    NumericDisplayScale.THOUSAND: Decimal("1000"),
    NumericDisplayScale.TEN_THOUSAND: Decimal("10000"),
    NumericDisplayScale.MILLION: Decimal("1000000"),
    NumericDisplayScale.HUNDRED_MILLION: Decimal("100000000"),
    NumericDisplayScale.BILLION: Decimal("1000000000"),
    NumericDisplayScale.TRILLION: Decimal("1000000000000"),
}


def _is_ratio_scaled_calculation_unit(unit: str) -> bool:
    return unit.strip().upper() in (
        _PERCENT_CALCULATION_UNITS
        | _PERCENTAGE_POINT_CALCULATION_UNITS
        | _BASIS_POINT_CALCULATION_UNITS
    )


def _requires_base_display_scale(unit: str) -> bool:
    return unit.strip().upper() in _DIMENSIONLESS_BASE_DISPLAY_UNITS


def _display_values_approximately_match(
    *,
    stated_value: Decimal,
    comparison_value: Decimal,
    raw_stated_value: Decimal,
    raw_comparison_value: Decimal,
    quantum: Decimal,
) -> bool:
    if (
        raw_stated_value
        and raw_comparison_value
        and (raw_stated_value.is_signed() != raw_comparison_value.is_signed())
    ):
        return False
    rounded_difference = abs(stated_value - comparison_value)
    relative_base = max(abs(raw_stated_value), abs(raw_comparison_value), Decimal("1"))
    relative_difference = abs(raw_stated_value - raw_comparison_value) / relative_base
    return rounded_difference <= quantum and relative_difference <= Decimal("0.01")


def _canonicalize_calculation_result(
    result: float,
    unit: str,
    *,
    issue_prefix: str = "calculation",
) -> float:
    """Convert a safe formula result into the public unit's canonical value."""

    normalized_unit = unit.strip().upper()
    if normalized_unit in (_PERCENT_CALCULATION_UNITS | _PERCENTAGE_POINT_CALCULATION_UNITS):
        canonical = result * 100
    elif normalized_unit in _BASIS_POINT_CALCULATION_UNITS:
        canonical = result * 10_000
    else:
        canonical = result
    if not math.isfinite(canonical):
        raise OutputValidationError(f"{issue_prefix}.formula.non_finite_result")
    return canonical


def _scale_for_display(
    result: int | float,
    scale: NumericDisplayScale,
) -> float:
    value = Decimal(str(result)) / _DISPLAY_SCALE_FACTORS[scale]
    if not value.is_finite():
        raise OutputValidationError("calculation.display_scale.non_finite")
    return float(value)
