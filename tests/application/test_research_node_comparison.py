from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from tests.application.test_cycle_trash_lifecycle import _commit_node, _warning_products
from tests.application.test_service import _equity_resolver, _Graph
from tradingagents.application.contracts import (
    AnalysisRequest,
    ResearchNodeComparisonSelection,
    RunStatus,
)
from tradingagents.application.database import (
    DecisionRecord,
    ResearchNodeRecord,
    RunRecord,
)
from tradingagents.application.errors import InvalidResearchNodeComparisonError
from tradingagents.application.service import AnalysisService


def test_service_compares_two_full_nodes_without_writes_or_semantic_calls(
    app_settings,
    repository,
) -> None:
    semantic_calls = 0

    def forbidden_llm_factory(*_args, **_kwargs):
        nonlocal semantic_calls
        semantic_calls += 1
        raise AssertionError("comparison must not create semantic clients")

    writer = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: (object(), object()),
        graph_factory=_Graph,
        identity_resolver=lambda ticker, _date: {"company_name": ticker},
        eligibility_resolver=_equity_resolver,
    )
    first = writer.run(AnalysisRequest(ticker="NVDA", analysis_date=date(2026, 7, 23)))
    second = writer.run(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date=date(2026, 7, 24),
            make_primary=False,
        )
    )
    reader = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=forbidden_llm_factory,
        eligibility_resolver=_equity_resolver,
    )
    def database_snapshot() -> dict[str, tuple[tuple[object, ...], ...]]:
        with repository.engine.connect() as connection:
            table_names = tuple(
                row[0]
                for row in connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
                    "ORDER BY name"
                )
            )
            return {
                table_name: tuple(
                    connection.exec_driver_sql(f'SELECT * FROM "{table_name}"')
                )
                for table_name in table_names
            }

    before = database_snapshot()

    comparison = reader.compare_research_nodes(
        "NVDA",
        (
            ResearchNodeComparisonSelection(node_id=second.run_id),
            ResearchNodeComparisonSelection(node_id=first.run_id),
        ),
    )

    after = database_snapshot()
    assert [side.node_id for side in comparison.sides] == [second.run_id, first.run_id]
    assert [side.research_kind for side in comparison.sides] == ["full", "full"]
    assert comparison.instrument == "NVDA"
    assert comparison.cross_cycle is True
    assert comparison.method_changed is False
    assert comparison.warnings == ()
    assert comparison.decision_sections[0].key == "rating"
    assert [value.state for value in comparison.decision_sections[0].values] == [
        "recorded",
        "recorded",
    ]
    assert semantic_calls == 0
    assert after == before


def test_comparison_supports_incremental_siblings_cross_cycle_and_explicit_trash(
    app_settings,
    repository,
) -> None:
    first_full = _commit_node(
        repository, app_settings, analysis_date=date(2026, 7, 20)
    )
    first_incremental = _commit_node(
        repository,
        app_settings,
        analysis_date=date(2026, 7, 23),
        baseline_id=first_full.id,
    )
    sibling = _commit_node(
        repository,
        app_settings,
        analysis_date=date(2026, 7, 24),
        baseline_id=first_full.id,
    )
    second_full = _commit_node(
        repository,
        app_settings,
        analysis_date=date(2026, 7, 21),
        make_primary=False,
    )
    cross_cycle_incremental = _commit_node(
        repository,
        app_settings,
        analysis_date=date(2026, 7, 25),
        baseline_id=second_full.id,
    )
    repository.trash_runs((sibling.id,))
    service = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("comparison must not create semantic clients")
        ),
        eligibility_resolver=_equity_resolver,
    )

    full_incremental = service.compare_research_nodes(
        "NVDA",
        (
            ResearchNodeComparisonSelection(node_id=first_full.id),
            ResearchNodeComparisonSelection(node_id=first_incremental.id),
        ),
    )
    siblings = service.compare_research_nodes(
        "NVDA",
        (
            ResearchNodeComparisonSelection(node_id=first_incremental.id),
            ResearchNodeComparisonSelection(
                node_id=sibling.id,
                lifecycle_state="trashed",
            ),
        ),
    )
    cross_cycle = service.compare_research_nodes(
        "NVDA",
        (
            ResearchNodeComparisonSelection(node_id=first_incremental.id),
            ResearchNodeComparisonSelection(node_id=cross_cycle_incremental.id),
        ),
    )

    assert [side.research_kind for side in full_incremental.sides] == [
        "full",
        "incremental",
    ]
    assert full_incremental.cross_cycle is False
    assert [side.research_kind for side in siblings.sides] == [
        "incremental",
        "incremental",
    ]
    assert siblings.cross_cycle is False
    assert siblings.sides[1].lifecycle_state == "trashed"
    assert cross_cycle.cross_cycle is True


def test_comparison_distinguishes_schema_absence_null_empty_and_semantic_values(
    app_settings,
    repository,
) -> None:
    old = _commit_node(repository, app_settings, analysis_date=date(2026, 7, 20))
    current = _commit_node(
        repository,
        app_settings,
        analysis_date=date(2026, 7, 21),
        make_primary=False,
    )
    with repository.sessions.begin() as session:
        old_run = session.get(RunRecord, old.id)
        old_run.research_schema_version = "0"
        old_decision = session.scalar(
            select(DecisionRecord).where(DecisionRecord.run_id == old.id)
        )
        historical = dict(old_decision.decision_json)
        historical.pop("unresolved_questions")
        historical["valuation_assessment"] = None
        historical["catalysts"] = []
        historical["time_horizon"] = "unavailable / not applicable / unchanged / unsupported"
        old_decision.decision_json = historical
    service = AnalysisService(
        app_settings,
        repository=repository,
        eligibility_resolver=_equity_resolver,
    )

    comparison = service.compare_research_nodes(
        "NVDA",
        (
            ResearchNodeComparisonSelection(node_id=old.id),
            ResearchNodeComparisonSelection(node_id=current.id),
        ),
    )
    sections = {section.key: section for section in comparison.decision_sections}

    assert sections["unresolved_questions"].values[0].state == (
        "not_recorded_under_this_schema"
    )
    assert sections["valuation_assessment"].values[0].state == "null"
    assert sections["catalysts"].values[0].state == "empty"
    assert sections["time_horizon"].values[0].state == "recorded"


def test_comparison_warns_on_method_change_and_preserves_each_performance_record(
    app_settings,
    repository,
) -> None:
    baseline = _commit_node(repository, app_settings, analysis_date=date(2026, 7, 20))
    first = _commit_node(
        repository,
        app_settings,
        analysis_date=date(2026, 7, 23),
        baseline_id=baseline.id,
    )
    second = _commit_node(
        repository,
        app_settings,
        analysis_date=date(2026, 7, 24),
        baseline_id=baseline.id,
    )

    def calculation(
        *,
        provider: str,
        basis: str,
        start: float,
        end: float,
    ) -> dict[str, object]:
        return {
            "provider": provider,
            "fallback": False,
            "adjustment_basis": basis,
            "retrieved_at": "2026-07-25T00:00:00Z",
            "baseline_information_cutoff_at": "2026-07-20T23:59:59Z",
            "target_information_cutoff_at": "2026-07-24T23:59:59Z",
            "start_session": "2026-07-20",
            "end_session": "2026-07-24",
            "start_value": start,
            "end_value": end,
            "unrounded_return": (end / start) - 1,
        }

    def products(
        *,
        provider: str,
        basis: str,
        start: float,
        end: float,
        benchmark_end: float,
    ):
        value = _warning_products()
        stock_calculation = calculation(
            provider=provider,
            basis=basis,
            start=start,
            end=end,
        )
        benchmark_calculation = calculation(
            provider=f"{provider}.benchmark",
            basis=basis,
            start=start,
            end=benchmark_end,
        )
        value["performance"] = {
            "stock": {
                "status": "calculated",
                "calculation": stock_calculation,
            },
            "benchmarks": [
                {
                    "name": "S&P 500",
                    "component": {
                        "status": "calculated",
                        "calculation": benchmark_calculation,
                    },
                    "reported_difference": (
                        stock_calculation["unrounded_return"]
                        - benchmark_calculation["unrounded_return"]
                    ),
                }
            ],
        }
        return value

    with repository.sessions.begin() as session:
        session.get(RunRecord, second.id).method_snapshot_json = {
            "schema_version": "1",
            "llm_provider": "different-fixture",
        }
        session.get(ResearchNodeRecord, first.id).incremental_products_json = products(
            provider="fixture.first",
            basis="split_adjusted",
            start=100,
            end=110,
            benchmark_end=105,
        )
        session.get(ResearchNodeRecord, second.id).incremental_products_json = products(
            provider="fixture.second",
            basis="dividend_adjusted",
            start=200,
            end=190,
            benchmark_end=196,
        )
    comparison = AnalysisService(
        app_settings,
        repository=repository,
        eligibility_resolver=_equity_resolver,
    ).compare_research_nodes(
        "NVDA",
        (
            ResearchNodeComparisonSelection(node_id=first.id),
            ResearchNodeComparisonSelection(node_id=second.id),
        ),
    )

    assert comparison.method_changed is True
    assert [warning.code for warning in comparison.warnings] == ["method_changed"]
    assert "not automatically attributable" in comparison.warnings[0].message
    assert comparison.sides[0].performance.stock.calculation.provider == "fixture.first"
    assert comparison.sides[0].performance.stock.calculation.adjustment_basis == "split_adjusted"
    assert comparison.sides[0].performance.stock.calculation.unrounded_return == pytest.approx(0.1)
    assert comparison.sides[0].performance.benchmarks[0].reported_difference == pytest.approx(
        0.05
    )
    assert comparison.sides[1].performance.stock.calculation.provider == "fixture.second"
    assert comparison.sides[1].performance.stock.calculation.adjustment_basis == (
        "dividend_adjusted"
    )
    assert comparison.sides[1].performance.stock.calculation.unrounded_return == pytest.approx(-0.05)
    assert comparison.sides[1].performance.benchmarks[0].reported_difference == pytest.approx(
        -0.03
    )
    assert not hasattr(comparison, "performance_difference")
    assert not hasattr(comparison, "ranking")


@pytest.mark.parametrize("status", [RunStatus.FAILED, RunStatus.CANCELLED])
def test_comparison_rejects_failed_and_cancelled_run_backed_nodes(
    app_settings,
    repository,
    status,
) -> None:
    first = _commit_node(repository, app_settings, analysis_date=date(2026, 7, 20))
    second = _commit_node(
        repository,
        app_settings,
        analysis_date=date(2026, 7, 21),
        make_primary=False,
    )
    with repository.sessions.begin() as session:
        session.get(RunRecord, second.id).status = status.value

    with pytest.raises(InvalidResearchNodeComparisonError, match="Failed or cancelled"):
        repository.compare_research_nodes(
            "NVDA",
            (
                ResearchNodeComparisonSelection(node_id=first.id),
                ResearchNodeComparisonSelection(node_id=second.id),
            ),
        )


def test_comparison_rejects_invalid_identity_count_legacy_missing_purged_and_implicit_trash(
    app_settings,
    repository,
) -> None:
    first = _commit_node(repository, app_settings, analysis_date=date(2026, 7, 20))
    trashed = _commit_node(
        repository,
        app_settings,
        analysis_date=date(2026, 7, 21),
        baseline_id=first.id,
    )
    other = _commit_node(
        repository,
        app_settings,
        analysis_date=date(2026, 7, 22),
        make_primary=False,
    )
    foreign = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: (object(), object()),
        graph_factory=_Graph,
        identity_resolver=lambda ticker, _date: {"company_name": ticker},
        eligibility_resolver=_equity_resolver,
    ).run(AnalysisRequest(ticker="AAPL", analysis_date=date(2026, 7, 20)))
    legacy_request = AnalysisRequest(ticker="NVDA", analysis_date=date(2026, 7, 19))
    legacy, _ = repository.create_run(
        legacy_request,
        app_settings.resolve_run(legacy_request).snapshot(),
    )
    repository.trash_runs((trashed.id,))
    repository.trash_runs((other.id,))
    repository.purge_runs_detailed((other.id,))

    def compare(*selections):
        return repository.compare_research_nodes("NVDA", selections)

    with pytest.raises(InvalidResearchNodeComparisonError, match="exactly two"):
        compare(ResearchNodeComparisonSelection(node_id=first.id))
    with pytest.raises(InvalidResearchNodeComparisonError, match="distinct"):
        compare(
            ResearchNodeComparisonSelection(node_id=first.id),
            ResearchNodeComparisonSelection(node_id=first.id),
        )
    for rejected_id in (legacy.id, "missing-node", other.id):
        with pytest.raises(InvalidResearchNodeComparisonError, match="retained Research Node"):
            compare(
                ResearchNodeComparisonSelection(node_id=first.id),
                ResearchNodeComparisonSelection(node_id=rejected_id),
            )
    with pytest.raises(InvalidResearchNodeComparisonError, match="Instrument Key"):
        compare(
            ResearchNodeComparisonSelection(node_id=first.id),
            ResearchNodeComparisonSelection(node_id=foreign.run_id),
        )
    with pytest.raises(InvalidResearchNodeComparisonError, match="Trash participation"):
        compare(
            ResearchNodeComparisonSelection(node_id=first.id),
            ResearchNodeComparisonSelection(node_id=trashed.id),
        )
