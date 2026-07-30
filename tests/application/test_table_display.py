from __future__ import annotations

import pytest

from tradingagents.application.contracts import (
    ResearchTable,
    ResearchTableCell,
    ResearchTableColumn,
    ResearchTableRow,
    TableCellKind,
    TableDataType,
    TableDisplaySpec,
    TableNotation,
)
from tradingagents.application.table_display import (
    evaluate_formula,
    materialize_research_table,
)

_REF = "ev_0123456789ab"


def _table(
    *,
    column: ResearchTableColumn,
    raw_value,
    model_display: str = "MODEL VALUE MUST NOT SURVIVE",
) -> ResearchTable:
    return ResearchTable(
        id="rt_localized_fixture",
        title="Localized fixture",
        purpose="Verify canonical display materialization.",
        columns=(column,),
        rows=(
            ResearchTableRow(
                id="row_0001",
                cells={
                    column.key: ResearchTableCell(
                        raw_value=raw_value,
                        display_value=model_display,
                        kind=TableCellKind.OBSERVATION,
                    )
                },
            ),
        ),
        evidence_refs=(_REF,),
    )


def test_zh_cn_compact_scale_and_unit_are_canonical() -> None:
    table = _table(
        column=ResearchTableColumn(
            key="revenue",
            label="营业收入",
            data_type=TableDataType.CURRENCY,
            unit="CNY",
            display=TableDisplaySpec(
                notation=TableNotation.CURRENCY,
                scale=100_000_000,
                fraction_digits=1,
                unit_label="亿元",
            ),
        ),
        raw_value=12_345_678_900,
    )

    materialized = materialize_research_table(
        table,
        output_language="zh-CN",
    )

    assert (
        materialized.rows[0].cells["revenue"].display_value
        == "¥123.5亿元"
    )


def test_percentage_raw_value_is_a_decimal_ratio() -> None:
    table = _table(
        column=ResearchTableColumn(
            key="margin",
            label="Margin",
            data_type=TableDataType.PERCENT,
            display=TableDisplaySpec(
                notation=TableNotation.PERCENT,
                fraction_digits=1,
            ),
        ),
        raw_value=0.123,
    )

    materialized = materialize_research_table(
        table,
        output_language="en",
    )

    assert materialized.rows[0].cells["margin"].display_value == "12.3%"


def test_ja_integer_and_missing_values_are_stable() -> None:
    column = ResearchTableColumn(
        key="employees",
        label="従業員数",
        data_type=TableDataType.INTEGER,
        display=TableDisplaySpec(
            notation=TableNotation.INTEGER,
            scale=1,
            fraction_digits=8,
            unit_label="人",
        ),
    )
    populated = materialize_research_table(
        _table(column=column, raw_value=1234567),
        output_language="ja",
    )
    missing = materialize_research_table(
        _table(column=column, raw_value=None),
        output_language="ja",
    )

    assert populated.rows[0].cells["employees"].display_value == "1,234,567人"
    assert missing.rows[0].cells["employees"].display_value == "—"


def test_custom_language_uses_locale_neutral_spacing() -> None:
    table = _table(
        column=ResearchTableColumn(
            key="volume",
            label="Volume",
            data_type=TableDataType.NUMBER,
            display=TableDisplaySpec(
                notation=TableNotation.COMPACT,
                scale=1_000_000,
                fraction_digits=2,
                unit_label="million shares",
            ),
        ),
        raw_value=12_345_678,
    )

    materialized = materialize_research_table(
        table,
        output_language="Use concise bilingual labels",
    )

    assert (
        materialized.rows[0].cells["volume"].display_value
        == "12.35 million shares"
    )


def test_derived_formula_evaluator_rejects_executable_syntax() -> None:
    assert evaluate_formula(
        "(latest / prior - 1) * 100",
        {"latest": 110, "prior": 100},
    ) == pytest.approx(10)
    with pytest.raises(ValueError, match="unsupported operation"):
        evaluate_formula(
            "__import__('os').system('echo unsafe')",
            {},
        )
