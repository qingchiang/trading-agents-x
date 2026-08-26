from __future__ import annotations

from datetime import UTC, date, datetime

import httpx2 as httpx
import pytest
from sqlalchemy import func, select

from tests.application.test_cycle_trash_lifecycle import _commit_node
from tests.factories import research_decision
from tradingagents.application.contracts import (
    AnalysisRequest,
    AnalysisResult,
    EvidenceBundle,
    EvidenceItem,
    RunStatus,
)
from tradingagents.application.database import RunEventRecord, RunRecord


def _selection(node_id: str, lifecycle_state: str = "active") -> dict[str, str]:
    return {"node_id": node_id, "lifecycle_state": lifecycle_state}


def _commit_full(repository, settings, ticker: str, analysis_date: date) -> str:
    request = AnalysisRequest(ticker=ticker, analysis_date=analysis_date)
    run, _ = repository.create_run(
        request,
        settings.resolve_run(request).snapshot(),
        research_schema_version="1",
        information_cutoff_at=datetime.combine(analysis_date, datetime.max.time(), UTC),
        method_snapshot={"schema_version": "1", "llm_provider": "fixture"},
        research_kind="full",
    )
    repository.claim_run(run.id, "fixture", 30)
    item = EvidenceItem.create(
        source="fixture",
        evidence_type="fixture",
        requested_date=analysis_date,
        effective_date=analysis_date,
        content=run.id,
    )
    evidence = EvidenceBundle(instrument=ticker, analysis_date=analysis_date, items=(item,))
    repository.seal_evidence(run.id, evidence)
    repository.complete(
        run.id,
        AnalysisResult(
            run_id=run.id,
            status=RunStatus.SUCCEEDED,
            instrument=ticker,
            reports={},
            decision=research_decision(evidence_refs=(item.ref,)),
            evidence=evidence,
        ),
        evidence=evidence,
    )
    return run.id


@pytest.mark.anyio
async def test_comparison_api_supports_every_pair_shape_and_explicit_trash(
    web_client: httpx.AsyncClient,
    web_repository,
    web_settings,
) -> None:
    first_full = _commit_node(web_repository, web_settings, analysis_date=date(2026, 7, 20))
    first_incremental = _commit_node(
        web_repository,
        web_settings,
        analysis_date=date(2026, 7, 22),
        baseline_id=first_full.id,
    )
    sibling = _commit_node(
        web_repository,
        web_settings,
        analysis_date=date(2026, 7, 23),
        baseline_id=first_full.id,
    )
    second_full = _commit_node(
        web_repository,
        web_settings,
        analysis_date=date(2026, 7, 21),
        make_primary=False,
    )
    cross_incremental = _commit_node(
        web_repository,
        web_settings,
        analysis_date=date(2026, 7, 24),
        baseline_id=second_full.id,
    )
    web_repository.trash_runs((sibling.id,))
    with web_repository.sessions() as session:
        before = (
            session.scalar(select(func.count()).select_from(RunRecord)),
            session.scalar(select(func.count()).select_from(RunEventRecord)),
            tuple(session.execute(select(RunRecord.id, RunRecord.updated_at).order_by(RunRecord.id))),
        )
    pairs = (
        (first_full.id, second_full.id, "full", "full", True),
        (first_full.id, first_incremental.id, "full", "incremental", False),
        (first_incremental.id, sibling.id, "incremental", "incremental", False),
        (first_incremental.id, cross_incremental.id, "incremental", "incremental", True),
    )

    for left, right, left_kind, right_kind, cross_cycle in pairs:
        response = await web_client.post(
            "/api/v1/timelines/NVDA/compare",
            json={
                "nodes": [
                    _selection(left),
                    _selection(right, "trashed" if right == sibling.id else "active"),
                ]
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert [side["node_id"] for side in payload["sides"]] == [left, right]
        assert [side["research_kind"] for side in payload["sides"]] == [
            left_kind,
            right_kind,
        ]
        assert payload["cross_cycle"] is cross_cycle
    with web_repository.sessions() as session:
        after = (
            session.scalar(select(func.count()).select_from(RunRecord)),
            session.scalar(select(func.count()).select_from(RunEventRecord)),
            tuple(session.execute(select(RunRecord.id, RunRecord.updated_at).order_by(RunRecord.id))),
        )
    assert after == before


@pytest.mark.anyio
async def test_comparison_api_rejects_every_invalid_selection_path(
    web_client: httpx.AsyncClient,
    web_repository,
    web_settings,
) -> None:
    first = _commit_node(web_repository, web_settings, analysis_date=date(2026, 7, 20))
    trashed = _commit_node(
        web_repository,
        web_settings,
        analysis_date=date(2026, 7, 21),
        baseline_id=first.id,
    )
    purged = _commit_node(
        web_repository,
        web_settings,
        analysis_date=date(2026, 7, 22),
        make_primary=False,
    )
    failed = _commit_node(
        web_repository,
        web_settings,
        analysis_date=date(2026, 7, 23),
        make_primary=False,
    )
    cancelled = _commit_node(
        web_repository,
        web_settings,
        analysis_date=date(2026, 7, 24),
        make_primary=False,
    )
    foreign = _commit_full(web_repository, web_settings, "AAPL", date(2026, 7, 20))
    legacy_request = AnalysisRequest(ticker="NVDA", analysis_date=date(2026, 7, 19))
    legacy, _ = web_repository.create_run(
        legacy_request,
        web_settings.resolve_run(legacy_request).snapshot(),
    )
    web_repository.trash_runs((trashed.id,))
    web_repository.trash_runs((purged.id,))
    with web_repository.sessions.begin() as session:
        session.delete(session.get(RunRecord, purged.id))
        session.get(RunRecord, failed.id).status = RunStatus.FAILED.value
        session.get(RunRecord, cancelled.id).status = RunStatus.CANCELLED.value

    invalid_pairs = (
        (legacy.id, "retained Research Node"),
        (failed.id, "Failed or cancelled"),
        (cancelled.id, "Failed or cancelled"),
        (foreign, "Instrument Key"),
        ("missing-node", "retained Research Node"),
        (purged.id, "retained Research Node"),
        (trashed.id, "Trash participation"),
    )
    for rejected_id, message in invalid_pairs:
        response = await web_client.post(
            "/api/v1/timelines/NVDA/compare",
            json={"nodes": [_selection(first.id), _selection(rejected_id)]},
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_research_node_comparison"
        assert message in response.json()["error"]["message"]

    duplicate = await web_client.post(
        "/api/v1/timelines/NVDA/compare",
        json={"nodes": [_selection(first.id), _selection(first.id)]},
    )
    too_few = await web_client.post(
        "/api/v1/timelines/NVDA/compare",
        json={"nodes": [_selection(first.id)]},
    )
    too_many = await web_client.post(
        "/api/v1/timelines/NVDA/compare",
        json={
            "nodes": [
                _selection(first.id),
                _selection(trashed.id, "trashed"),
                _selection(foreign),
            ]
        },
    )
    assert duplicate.status_code == 422
    assert duplicate.json()["error"]["code"] == "invalid_research_node_comparison"
    assert too_few.status_code == too_many.status_code == 422
    assert too_few.json()["error"]["code"] == "validation_error"
    assert too_many.json()["error"]["code"] == "validation_error"
