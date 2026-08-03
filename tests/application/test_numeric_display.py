from __future__ import annotations

import json
from pathlib import Path

import pytest

from tradingagents.application.numeric_display import format_decision_number

_CASES = json.loads(
    (
        Path(__file__).parents[2]
        / "frontend"
        / "src"
        / "test-fixtures"
        / "numeric-display.json"
    ).read_text(encoding="utf-8")
)


@pytest.mark.parametrize("case", _CASES)
def test_decision_number_format_matches_shared_web_cases(
    case: dict[str, object],
) -> None:
    assert format_decision_number(
        float(case["value"]),
        str(case["unit"]),
        output_language=str(case["language"]),
    ) == str(case["expected"])
