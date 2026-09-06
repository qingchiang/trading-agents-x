from datetime import UTC, date, datetime

import pytest

from tests.factories import research_decision
from tradingagents.application.contracts import (
    AnalysisRequest,
    AnalysisResult,
    EvidenceBundle,
    EvidenceItem,
    RunStatus,
)


def commit_full(
    web_repository, web_settings, analysis_date: date, *, make_primary: bool | None = None
) -> str:
    request = AnalysisRequest(
        ticker="NVDA",
        analysis_date=analysis_date,
        make_primary=make_primary,
    )
    run, _ = web_repository.create_run(
        request,
        web_settings.resolve_run(request).snapshot(),
        research_schema_version="2",
        information_cutoff_at=datetime.combine(analysis_date, datetime.max.time(), UTC),
        method_snapshot={"schema_version": "1"},
        research_kind="full",
    )
    web_repository.claim_run(run.id, "fixture", 30)
    item = EvidenceItem.create(
        source="fixture",
        evidence_type="fixture",
        requested_date=analysis_date,
        effective_date=analysis_date,
        content=run.id,
    )
    evidence = EvidenceBundle(instrument="NVDA", analysis_date=analysis_date, items=(item,))
    web_repository.seal_evidence(run.id, evidence)
    web_repository.complete(
        run.id,
        AnalysisResult(
            run_id=run.id,
            status=RunStatus.SUCCEEDED,
            instrument="NVDA",
            reports={},
            decision=research_decision(evidence_refs=(item.ref,)),
            evidence=evidence,
        ),
        evidence=evidence,
    )
    return run.id


@pytest.mark.anyio
async def test_group_search_keeps_baseline_context_for_pending_incremental(
    web_client, web_repository, web_settings
):
    baseline = commit_full(web_repository, web_settings, date(2026, 7, 20))
    request = AnalysisRequest(
        ticker="NVDA",
        analysis_date="2026-07-24",
        research_kind="incremental",
        full_baseline_run_id=baseline,
    )
    child, _ = web_repository.create_run(
        request,
        web_settings.resolve_run(request).snapshot(),
        research_kind="incremental",
        full_baseline_run_id=baseline,
        incremental_input_fingerprint="fixture-pending",
    )
    response = await web_client.get("/api/v1/run-groups?status=queued&limit=1")
    assert response.status_code == 200
    page = response.json()
    assert page["total"] == 1
    group = page["items"][0]
    assert group["baseline"]["id"] == baseline
    assert group["matched_run_ids"] == [child.id]
    assert [run["id"] for run in group["related_tasks"]] == [child.id]
    assert group["research_runs"][0]["id"] == baseline


@pytest.mark.anyio
async def test_lifecycle_preview_excludes_uncommitted_tasks_and_checks_scope(
    web_client, web_repository, web_settings
):
    baseline = commit_full(web_repository, web_settings, date(2026, 7, 20))
    replacement = commit_full(web_repository, web_settings, date(2026, 7, 21), make_primary=False)
    request = AnalysisRequest(
        ticker="NVDA",
        analysis_date="2026-07-24",
        research_kind="incremental",
        full_baseline_run_id=baseline,
    )
    child, _ = web_repository.create_run(
        request,
        web_settings.resolve_run(request).snapshot(),
        research_kind="incremental",
        full_baseline_run_id=baseline,
        incremental_input_fingerprint="fixture-pending",
    )
    preview = await web_client.post(
        "/api/v1/runs/lifecycle-preview", json={"run_ids": [baseline], "action": "trash"}
    )
    assert preview.status_code == 200
    body = preview.json()
    assert body["affected_run_ids"] == [baseline]
    assert [run["id"] for run in body["affected_runs"]] == [baseline]
    assert body["primary_replacements"][baseline][0]["id"] == replacement
    assert web_repository.get_run(baseline).trashed_at is None
    rejected = await web_client.post(
        "/api/v1/runs/trash",
        json={
            "run_ids": [baseline],
            "expected_affected_run_ids": [],
            "primary_replacements": {baseline: replacement},
        },
    )
    assert rejected.status_code == 409
    assert web_repository.get_run(baseline).trashed_at is None
    moved = await web_client.post(
        "/api/v1/runs/trash",
        json={
            "run_ids": [baseline],
            "expected_affected_run_ids": [baseline],
            "primary_replacements": {baseline: replacement},
        },
    )
    assert moved.status_code == 200
    assert web_repository.get_run(child.id).trashed_at is None


@pytest.mark.anyio
async def test_recent_summary_keeps_primary_judgment_separate_from_newer_cycle(
    web_client, web_repository, web_settings
):
    primary = commit_full(web_repository, web_settings, date(2026, 7, 20))
    latest = commit_full(web_repository, web_settings, date(2026, 7, 24), make_primary=False)
    response = await web_client.get("/api/v1/timelines?sort=recent_activity&limit=6")
    item = response.json()["items"][0]
    assert item["primary_head_run_id"] == primary
    assert item["primary_baseline_date"] == "2026-07-20"
    assert item["primary_thesis"] == research_decision().thesis
    assert item["latest_completed_run_id"] == latest
    assert item["latest_completed_cycle_id"] == latest
    assert item["latest_research_completed_at"] is not None


@pytest.mark.anyio
async def test_preview_scope_changes_when_a_child_commits_and_restore_preserves_independent_trash(
    web_client, web_repository, web_settings
):
    from tests.application.test_cycle_trash_lifecycle import _commit_node

    baseline = _commit_node(web_repository, web_settings, analysis_date=date(2026, 7, 20))
    first = _commit_node(
        web_repository, web_settings, analysis_date=date(2026, 7, 21), baseline_id=baseline.id
    )
    web_repository.trash_runs((first.id,))
    preview = (
        await web_client.post(
            "/api/v1/runs/lifecycle-preview", json={"run_ids": [baseline.id], "action": "trash"}
        )
    ).json()
    assert preview["affected_run_ids"] == [baseline.id]
    second = _commit_node(
        web_repository, web_settings, analysis_date=date(2026, 7, 22), baseline_id=baseline.id
    )
    rejected = await web_client.post(
        "/api/v1/runs/trash",
        json={"run_ids": [baseline.id], "expected_affected_run_ids": preview["affected_run_ids"]},
    )
    assert rejected.status_code == 409
    assert web_repository.get_run(second.id).trashed_at is None
    current = (
        await web_client.post(
            "/api/v1/runs/lifecycle-preview", json={"run_ids": [baseline.id], "action": "trash"}
        )
    ).json()
    assert set(current["affected_run_ids"]) == {baseline.id, second.id}
    assert (
        await web_client.post(
            "/api/v1/runs/trash",
            json={
                "run_ids": [baseline.id],
                "expected_affected_run_ids": current["affected_run_ids"],
            },
        )
    ).status_code == 200
    restoring = (
        await web_client.post(
            "/api/v1/runs/lifecycle-preview", json={"run_ids": [baseline.id], "action": "restore"}
        )
    ).json()
    assert set(restoring["affected_run_ids"]) == {baseline.id, second.id}
    assert (
        await web_client.post(
            "/api/v1/runs/restore",
            json={
                "run_ids": [baseline.id],
                "expected_affected_run_ids": restoring["affected_run_ids"],
            },
        )
    ).status_code == 200
    assert web_repository.get_run(first.id).trashed_at is not None
    assert web_repository.get_run(second.id).trashed_at is None


@pytest.mark.anyio
async def test_groups_page_whole_cycles_and_do_not_count_hidden_trash(
    web_client, web_repository, web_settings
):
    from tests.application.test_cycle_trash_lifecycle import _commit_node

    baseline = _commit_node(web_repository, web_settings, analysis_date=date(2026, 7, 20))
    child = _commit_node(
        web_repository, web_settings, analysis_date=date(2026, 7, 21), baseline_id=baseline.id
    )
    later = _commit_node(
        web_repository, web_settings, analysis_date=date(2026, 7, 22), make_primary=False
    )
    first = (await web_client.get("/api/v1/run-groups?limit=1")).json()
    second = (await web_client.get("/api/v1/run-groups?limit=1&offset=1")).json()
    assert first["total"] == second["total"] == 2
    assert first["items"][0]["id"] == later.id
    assert {run["id"] for run in second["items"][0]["research_runs"]} == {baseline.id, child.id}
    web_repository.trash_runs((later.id,))
    assert (await web_client.get("/api/v1/run-groups?limit=1")).json()["total"] == 1
    trash = (await web_client.get("/api/v1/run-groups?trash_state=trashed")).json()
    assert [item["id"] for item in trash["items"]] == [later.id]


@pytest.mark.anyio
async def test_restore_preview_reports_existing_cycle_cutoff_conflict(
    web_client, web_repository, web_settings
):
    from tests.application.test_cycle_trash_lifecycle import _commit_node

    baseline = _commit_node(web_repository, web_settings, analysis_date=date(2026, 7, 20))
    child = _commit_node(
        web_repository, web_settings, analysis_date=date(2026, 7, 21), baseline_id=baseline.id
    )
    web_repository.trash_runs_detailed((child.id,))
    _commit_node(
        web_repository, web_settings, analysis_date=date(2026, 7, 21), baseline_id=baseline.id
    )
    response = await web_client.post(
        "/api/v1/runs/lifecycle-preview", json={"run_ids": [child.id], "action": "restore"}
    )
    assert response.status_code == 200
    assert any("active slot" in reason for reason in response.json()["blocked_reasons"])
    assert web_repository.get_run(child.id).trashed_at is not None
