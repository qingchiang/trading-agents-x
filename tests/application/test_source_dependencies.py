from __future__ import annotations

import pytest

from tradingagents.application.source_dependencies import (
    is_internal_source_reference,
    partition_source_dependencies,
)


@pytest.mark.parametrize(
    "value",
    (
        "ev_0123456789ab",
        "et_0123456789ab",
        "memory:run-1",
        "claim_0123456789abcdef",
        "question_0123456789abcdef",
        "calc_margin",
        "req_margin",
        "nv_0123456789ab",
        "group_valuation",
        "debate.issue_valuation",
        "fundamentals.claim_1",
        "market.section_2",
    ),
)
def test_internal_research_references_are_not_source_names(value: str):
    assert is_internal_source_reference(value)


def test_source_dependency_partition_preserves_normalized_external_names():
    external, internal = partition_source_dependencies(
        (" EDINET ", "ev_0123456789ab", "EDINET", "Google News")
    )

    assert external == ("EDINET", "Google News")
    assert internal == ("ev_0123456789ab",)


@pytest.mark.parametrize(
    "value",
    ("EDINET", "TDnet", "J-Quants fundamentals", "Google News", "group insights"),
)
def test_external_source_names_are_not_misclassified(value: str):
    assert not is_internal_source_reference(value)
