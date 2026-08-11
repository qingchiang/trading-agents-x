from __future__ import annotations

import io
import zipfile
from datetime import date

import httpx2 as httpx
import pytest

from tests.factories import (
    analyst_report,
    research_case,
    research_decision,
)
from tradingagents.application.contracts import (
    AnalysisRequest,
    AnalysisResult,
    ArtifactGenerationMethod,
    ArtifactGenerationObservation,
    EvidenceBundle,
    EvidenceItem,
    MarketReferenceLevel,
    ResearchArtifactDraft,
    RunStatus,
)
from tradingagents.application.repository import (
    OutcomeFeedbackRetirementConflictError,
    OutcomeFeedbackRetirementNotFoundError,
    OutcomeReflectionRegenerationConflictError,
    OutcomeReflectionRegenerationNotFoundError,
)
from tradingagents.application.research import (
    IncrementalEscalationReason,
    IncrementalGateResult,
)
from tradingagents.application.service import AnalysisService
from tradingagents.application.settings import AppSettings
from tradingagents.graph.research_graph import GraphExecution
from tradingagents.version import __version__
from tradingagents.web import create_app


def _payload(ticker: str = "NVDA") -> dict:
    return {
        "ticker": ticker,
        "analysis_date": "2026-07-24",
        "profile": "standard",
        "analysts": ["market", "news"],
        "output_language": "en",
    }


class _FullResearchGraph:
    def __init__(self, **_kwargs):
        pass

    def execute(self, context, **_kwargs):
        cutoff = context.request.analysis_date
        close = 95.0 if cutoff == date(2026, 7, 24) else 101.0
        item = EvidenceItem.create(
            source="fixture",
            evidence_type="fixture",
            requested_date=cutoff,
            effective_date=cutoff,
            content="Evidence for the initial chain.",
            provenance={
                "source_records": [
                    {
                        "source": "EDINET",
                        "record_id": "S100ROOT",
                        "version_id": "edinet:S100ROOT",
                        "status": "published",
                        "published_at": "2026-07-23 15:00",
                        "available_at": "2026-07-23T15:00:00+09:00",
                        "title": "有価証券報告書",
                    },
                    {
                        "source": "J-Quants adjusted OHLCV",
                        "record_id": "jquants-market:6501.T",
                        "version_id": f"jquants-market:{close}",
                        "status": "published",
                        "published_at": f"{cutoff.isoformat()} 15:30",
                        "available_at": f"{cutoff.isoformat()}T15:30:00+09:00",
                        "title": f"Adjusted market history through {cutoff.isoformat()}",
                        "record_kind": "market",
                        "adjustment": "J-Quants adjusted OHLCV v2",
                        "observation_value": close,
                        "unit": "JPY",
                        "precision": 2,
                    },
                ],
                "source_watermarks": [
                    {
                        "source": "TDnet",
                        "scanned_start": "2026-06-24",
                        "scanned_end": "2026-07-24",
                        "status": "limited",
                        "limitations": ["rolling archive limited"],
                        "returned_records": 2,
                        "reported_records": 5,
                    },
                    {
                        "source": "J-Quants adjusted OHLCV",
                        "scanned_start": "2025-06-01",
                        "scanned_end": cutoff.isoformat(),
                        "status": "complete",
                        "returned_records": 250,
                    },
                ],
            },
        )
        evidence = EvidenceBundle(
            instrument=context.request.ticker,
            analysis_date=cutoff,
            items=(item,),
        )
        report = analyst_report(evidence_ref=item.ref)
        context.artifact_writer(
            ResearchArtifactDraft(
                node="analyst.market",
                stage="analyst",
                role="market",
                generation_method=ArtifactGenerationMethod.TOOL_CALL,
                content=report,
            )
        )
        return GraphExecution(
            state={},
            evidence=evidence,
            reports={"market": report},
            decision=research_decision(evidence_refs=(item.ref,)).model_copy(
                update={
                    "market_reference_levels": (
                        MarketReferenceLevel(
                            label="Thesis reference",
                            value=100.0,
                            measurement_kind="currency",
                            unit="JPY",
                            as_of_date=cutoff,
                            interpretation="Crossing changes the thesis envelope.",
                            evidence_refs=(item.ref,),
                            date_evidence_refs=(item.ref,),
                            basis="interpreted",
                        ),
                    )
                }
            ),
        )


@pytest.mark.anyio
async def test_run_creation_is_idempotent_and_conflicts_are_explicit(
    web_client: httpx.AsyncClient,
) -> None:
    first = await web_client.post(
        "/api/v1/runs",
        json=_payload(),
        headers={"Idempotency-Key": "browser-submit"},
    )
    repeated = await web_client.post(
        "/api/v1/runs",
        json=_payload(),
        headers={"Idempotency-Key": "browser-submit"},
    )
    conflict = await web_client.post(
        "/api/v1/runs",
        json=_payload("AAPL"),
        headers={"Idempotency-Key": "browser-submit"},
    )

    assert first.status_code == 202
    assert repeated.status_code == 202
    assert repeated.json()["id"] == first.json()["id"]
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"


@pytest.mark.anyio
async def test_initial_research_chain_creation_read_and_export_surfaces(
    web_client: httpx.AsyncClient,
    web_settings,
    web_repository,
) -> None:
    queued = await web_client.post(
        "/api/v1/research-chains",
        json={**_payload("6501.T"), "analysts": ["market"], "output_language": "ja"},
        headers={"Idempotency-Key": "initial-chain"},
    )
    assert queued.status_code == 202
    assert queued.json()["research_chain_requested"] is True

    service = AnalysisService(
        web_settings,
        repository=web_repository,
        llm_factory=lambda *_args, **_kwargs: (object(), object()),
        graph_factory=_FullResearchGraph,
        identity_resolver=lambda ticker, _date: {"company_name": ticker},
        local_name_resolver=lambda _ticker, _date, _config: None,
        market_data_readiness_checker=lambda *_args: None,
        incremental_gate=lambda *_args: IncrementalGateResult(
            escalation_reason=IncrementalEscalationReason.THRESHOLD_CROSSING
        ),
    )
    claimed = web_repository.claim_run(
        queued.json()["id"], "test-worker", web_settings.lease_seconds
    )
    service.execute_claimed(claimed, worker_id="test-worker")

    chains = await web_client.get("/api/v1/research-chains?instrument=6501.T")
    chain = chains.json()[0]
    detail = await web_client.get(f"/api/v1/research-chains/{chain['id']}")
    revision_id = chain["current_revision_id"]
    revision = await web_client.get(f"/api/v1/research-revisions/{revision_id}")
    exported = await web_client.get(f"/api/v1/research-revisions/{revision_id}/export?format=json")

    assert chains.status_code == 200
    assert detail.json()["current_revision"]["current_state"]["language"] == "ja"
    assert detail.json()["current_revision"]["coverage"]["supports_no_material_change"] is False
    assert detail.json()["next_update_policy"] == "full_required"
    assert detail.json()["next_update_reason"] == "required_source_coverage_incomplete"
    assert revision.json()["producing_run_id"] == queued.json()["id"]
    assert revision.json()["evidence_snapshot"]["source_records"][0]["version_id"] == (
        "edinet:S100ROOT"
    )
    assert revision.json()["evidence_snapshot"]["source_watermarks"][0]["status"] == ("limited")
    persisted = web_repository.get_research_revision(revision_id)
    assert persisted.evidence_snapshot.source_records[0].record_id == "S100ROOT"
    assert exported.status_code == 200
    assert exported.json()["revision"]["evidence_snapshot"]["bundle"]["items"]
    assert exported.json()["revision"]["evidence_snapshot"]["source_records"]
    assert exported.json()["linked_reports"]["market"].startswith("# Overview")

    refused_incremental = await web_client.post(
        f"/api/v1/research-chains/{chain['id']}/updates",
        json={
            "baseline_revision_id": revision_id,
            "analysis_date": "2026-07-25",
            "execution_strategy": "incremental",
        },
    )
    assert refused_incremental.status_code == 409
    assert refused_incremental.json()["error"]["code"] == "invalid_research_baseline"
    assert "required_source_coverage_incomplete" in (
        refused_incremental.json()["error"]["message"]
    )

    update = await web_client.post(
        f"/api/v1/research-chains/{chain['id']}/updates",
        json={
            "baseline_revision_id": revision_id,
            "analysis_date": "2026-07-25",
        },
        headers={"Idempotency-Key": "full-update"},
    )
    duplicate = await web_client.post(
        f"/api/v1/research-chains/{chain['id']}/updates",
        json={
            "baseline_revision_id": revision_id,
            "analysis_date": "2026-07-25",
        },
        headers={"Idempotency-Key": "full-update"},
    )
    assert update.status_code == 202
    assert duplicate.json()["id"] == update.json()["id"]
    assert update.json()["research_execution_strategy"] == "full"
    update_claim = web_repository.claim_run(
        update.json()["id"], "test-worker", web_settings.lease_seconds
    )
    service.execute_claimed(update_claim, worker_id="test-worker")
    advanced = (await web_client.get(f"/api/v1/research-chains/{chain['id']}")).json()
    current_id = advanced["current_revision_id"]
    current = (await web_client.get(f"/api/v1/research-revisions/{current_id}")).json()
    run_detail = (await web_client.get(f"/api/v1/runs/{update.json()['id']}")).json()
    signal = current["delta"]["change_signals"][0]
    assert signal["kind"] == "market_boundary_crossing"
    assert signal["previous_value"] == 95.0
    assert signal["current_value"] == 101.0
    assert current["research_update_audit"] is None
    assert run_detail["run"]["research_update_audit"] is None
    updated_export = await web_client.get(
        f"/api/v1/research-revisions/{current_id}/export?format=json"
    )
    assert updated_export.json()["revision"]["delta"]["change_signals"] == [signal]


@pytest.mark.anyio
async def test_run_creation_rejects_crypto_instruments(
    web_client: httpx.AsyncClient,
) -> None:
    response = await web_client.post("/api/v1/runs", json=_payload("BTC-USD"))

    assert response.status_code == 422
    assert "Crypto instruments are not supported" in response.text


@pytest.mark.anyio
@pytest.mark.parametrize("ticker", ["EURUSD", "GC=F", "^GSPC"])
async def test_run_creation_rejects_non_equity_instruments(
    web_client: httpx.AsyncClient,
    ticker: str,
) -> None:
    response = await web_client.post("/api/v1/runs", json=_payload(ticker))

    assert response.status_code == 422
    assert "Only listed equity instruments are supported" in response.text


@pytest.mark.anyio
async def test_run_lifecycle_routes_and_filters(
    web_client: httpx.AsyncClient,
) -> None:
    created = (await web_client.post("/api/v1/runs", json=_payload())).json()
    run_id = created["id"]

    detail = await web_client.get(f"/api/v1/runs/{run_id}")
    pending_evidence = await web_client.get(f"/api/v1/runs/{run_id}/evidence")
    queued = await web_client.get("/api/v1/runs?status=queued")
    cancelled = await web_client.post(f"/api/v1/runs/{run_id}/cancel")
    trashed = await web_client.post(
        "/api/v1/runs/trash",
        json={"run_ids": [run_id]},
    )
    trashed_page = await web_client.get("/api/v1/runs?trash_state=trashed&q=nv")
    trashed_detail = await web_client.get(f"/api/v1/runs/{run_id}")
    trashed_export = await web_client.get(f"/api/v1/runs/{run_id}/export?format=json")
    restored = await web_client.post(
        "/api/v1/runs/restore",
        json={"run_ids": [run_id]},
    )
    templated = await web_client.post(
        "/api/v1/runs",
        json={**_payload("AAPL"), "source_run_id": run_id},
    )

    assert detail.status_code == 200
    assert detail.json()["run"]["id"] == run_id
    assert detail.json()["result"]["status"] == "queued"
    assert detail.json()["result"]["reports"] == {}
    assert detail.json()["result"]["evidence"] is None
    assert detail.json()["attempts"] == [
        {
            "attempt": 1,
            "status": "queued",
            "resume_count": 0,
            "metrics": {
                "llm_calls": 0,
                "tool_calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_hit_input_tokens": 0,
                "cache_miss_input_tokens": 0,
                "reasoning_output_tokens": 0,
                "detailed_usage_calls": 0,
                "cost_usd": None,
                "wall_time_seconds": 0.0,
                "node_metrics": {},
            },
            "started_at": None,
            "finished_at": None,
            "error_code": None,
        }
    ]
    assert [run["id"] for run in queued.json()["items"]] == [run_id]
    assert queued.json()["total"] == 1
    assert cancelled.json()["status"] == "cancelled"
    assert trashed.json()["changed"] == 1
    assert trashed.json()["runs"][0]["trashed_at"] is not None
    assert trashed_page.json()["items"][0]["id"] == run_id
    assert trashed_detail.json()["run"]["trashed_at"] is not None
    assert trashed_export.status_code == 200
    assert restored.json()["changed"] == 1
    assert restored.json()["runs"][0]["trashed_at"] is None
    assert templated.status_code == 202
    assert detail.json()["evidence_status"]["status"] == "pending"
    assert pending_evidence.status_code == 409
    assert pending_evidence.json()["error"]["code"] == "evidence_not_sealed"
    assert templated.json()["source_run_id"] == run_id


@pytest.mark.anyio
async def test_trash_batch_validation_is_atomic_and_idempotent(
    web_client: httpx.AsyncClient,
) -> None:
    terminal = (await web_client.post("/api/v1/runs", json=_payload("NVDA"))).json()
    queued = (await web_client.post("/api/v1/runs", json=_payload("AAPL"))).json()
    await web_client.post(f"/api/v1/runs/{terminal['id']}/cancel")

    invalid = await web_client.post(
        "/api/v1/runs/trash",
        json={"run_ids": [terminal["id"], queued["id"]]},
    )
    active = await web_client.get("/api/v1/runs?trash_state=active")
    trashed = await web_client.post(
        "/api/v1/runs/trash",
        json={"run_ids": [terminal["id"]]},
    )
    repeated = await web_client.post(
        "/api/v1/runs/trash",
        json={"run_ids": [terminal["id"]]},
    )
    duplicate_ids = await web_client.post(
        "/api/v1/runs/trash",
        json={"run_ids": [terminal["id"], terminal["id"]]},
    )

    assert invalid.status_code == 409
    assert {run["id"] for run in active.json()["items"]} == {
        terminal["id"],
        queued["id"],
    }
    assert trashed.json()["changed"] == 1
    assert repeated.json()["changed"] == 0
    assert duplicate_ids.status_code == 422


@pytest.mark.anyio
async def test_research_template_source_must_exist_and_be_terminal(
    web_client: httpx.AsyncClient,
) -> None:
    queued = (await web_client.post("/api/v1/runs", json=_payload("NVDA"))).json()

    active_source = await web_client.post(
        "/api/v1/runs",
        json={**_payload("AAPL"), "source_run_id": queued["id"]},
    )
    missing_source = await web_client.post(
        "/api/v1/runs",
        json={
            **_payload("AAPL"),
            "source_run_id": "00000000-0000-0000-0000-000000000000",
        },
    )

    assert active_source.status_code == 409
    assert active_source.json()["error"]["code"] == "invalid_run_transition"
    assert missing_source.status_code == 404
    assert missing_source.json()["error"]["code"] == "run_not_found"


@pytest.mark.anyio
async def test_sse_replays_committed_events_after_last_event_id(
    web_client: httpx.AsyncClient,
    web_service,
) -> None:
    queued = web_service.enqueue(AnalysisRequest(ticker="NVDA", analysis_date="2026-07-24"))
    web_service.cancel(queued.id)

    response = await web_client.get(
        f"/api/v1/runs/{queued.id}/events",
        headers={"Last-Event-ID": "1"},
    )

    assert response.status_code == 200
    assert "id: 2\n" in response.text
    assert "event: run.cancelled\n" in response.text
    assert "id: 1\n" not in response.text


@pytest.mark.anyio
async def test_sse_rejects_invalid_replay_cursor(
    web_client: httpx.AsyncClient,
    web_service,
) -> None:
    queued = web_service.enqueue(AnalysisRequest(ticker="NVDA", analysis_date="2026-07-24"))

    response = await web_client.get(
        f"/api/v1/runs/{queued.id}/events",
        headers={"Last-Event-ID": "not-an-integer"},
    )

    assert response.status_code == 400


@pytest.mark.anyio
async def test_openapi_contains_versioned_run_center_contract(
    web_client: httpx.AsyncClient,
) -> None:
    schema = (await web_client.get("/openapi.json")).json()
    paths = schema["paths"]

    assert {
        "/api/v1/runs",
        "/api/v1/runs/{run_id}",
        "/api/v1/runs/{run_id}/artifacts",
        "/api/v1/runs/{run_id}/evidence",
        "/api/v1/runs/{run_id}/events",
        "/api/v1/runs/{run_id}/cancel",
        "/api/v1/runs/{run_id}/retry",
        "/api/v1/runs/{run_id}/export",
        "/api/v1/instruments/recent",
        "/api/v1/research-chains",
        "/api/v1/research-chains/{chain_id}",
        "/api/v1/research-chains/{chain_id}/updates",
        "/api/v1/research-revisions/{revision_id}",
        "/api/v1/research-revisions/{revision_id}/export",
        "/api/v1/reviews",
        "/api/v1/reviews/{outcome_id}",
        "/api/v1/outcome-observations/{outcome_id}/reflection-regenerations",
        "/api/v1/capabilities",
        "/api/v1/providers/{provider}/models",
        "/api/v1/health",
    } <= set(paths)
    assert "/api/v1/memory" not in paths
    assert "/api/v1/outcome-observations/{outcome_id}/reflection/regenerations" not in paths
    assert "/api/v1/runs/{run_id}/rerun" not in paths
    assert "provenance" not in schema["components"]["schemas"]["AnalysisRequest"]["properties"]
    assert "provenance" not in schema["components"]["schemas"]["CapabilityDefaults"]["properties"]
    assert schema["components"]["schemas"]["AssetType"]["enum"] == ["stock"]
    audit = schema["components"]["schemas"]["ResearchUpdateAudit"]["properties"]
    assert audit["mode"]["enum"] == ["shadow", "experimental"]
    assert audit["authoritative_strategy"]["enum"] == ["full", "incremental"]
    assert set(audit["comparison"]["enum"]) == {
        "agreement",
        "disagreement",
        "inconclusive",
        "not_applicable",
    }
    assert audit["coverage"]["anyOf"][0]["$ref"].endswith("/ResearchUpdateCoverageAttestation")
    assert audit["semantic_assessment"]["anyOf"][0]["$ref"].endswith(
        "/ResearchUpdateSemanticAssessment"
    )
    relationships = schema["components"]["schemas"]["ResearchUpdateSemanticRelationship"]
    assert set(relationships["properties"]["relationship"]["enum"]) == {
        "support",
        "weakening",
        "contradiction",
        "answering",
        "reopening",
        "irrelevance",
        "uncertainty",
        "potentially_material_novelty",
    }
    revision = schema["components"]["schemas"]["ResearchRevision"]["properties"]
    assert revision["role"]["$ref"].endswith("/ResearchRevisionRole")
    assert revision["change_conclusion"]["anyOf"][0]["$ref"].endswith("/ResearchChangeConclusion")
    question = schema["components"]["schemas"]["ResearchQuestion"]["properties"]
    assert question["successor_question_id"]["anyOf"][0]["pattern"].startswith("^question_")
    assert question["last_disposition"]["anyOf"][0]["$ref"].endswith(
        "/QuestionDispositionKind"
    )
    assert question["disposition_reason"]["anyOf"][0]["maxLength"] == 1000
    question_disposition = schema["components"]["schemas"]["QuestionDispositionKind"]
    assert question_disposition["enum"] == [
        "reaffirmed",
        "answered",
        "reopened",
        "superseded",
        "retired",
    ]
    observation = schema["components"]["schemas"]["OutcomeObservationView"]["properties"]
    assert "effective source cutoff" in observation["observation_start"]["description"]
    assert "strictly after" in observation["observation_end"]["description"]
    feedback = schema["components"]["schemas"]["OutcomeFeedbackView"]["properties"]
    assert "schema and Observation method versions" in feedback[
        "qualification_policy_version"
    ]["description"]
    assert "Observation data, Reflection, and qualification" in feedback["available_at"][
        "description"
    ]
    review = schema["components"]["schemas"]["ResearchReview"]["properties"]
    assert "reflection" not in review
    detail = schema["components"]["schemas"]["ResearchReviewAuditDetail"]["properties"]
    assert {"review", "reflection", "attempts", "aggregate_usage"} <= set(detail)
    delta = schema["components"]["schemas"]["RevisionDelta"]["properties"]
    assert delta["question_disposition"]["anyOf"][0]["$ref"].endswith("/QuestionDispositionAudit")
    chain = schema["components"]["schemas"]["ResearchChain"]["properties"]
    assert chain["next_update_policy"]["enum"] == [
        "incremental_allowed",
        "full_required",
    ]
    assert "server-derived" in chain["next_update_policy"]["description"].lower()
    assert "Full Analysis" in chain["next_update_reason"]["description"]


@pytest.mark.anyio
async def test_recent_instruments_exclude_trashed_runs_and_include_names(
    web_client: httpx.AsyncClient,
    web_repository,
) -> None:
    active = (await web_client.post("/api/v1/runs", json=_payload("NVDA"))).json()
    trashed = (await web_client.post("/api/v1/runs", json=_payload("AAPL"))).json()
    web_repository.set_instrument_name(active["id"], "NVIDIA")
    web_repository.set_instrument_local_name(active["id"], "英伟达")
    web_repository.set_instrument_name(trashed["id"], "Apple")
    await web_client.post(f"/api/v1/runs/{trashed['id']}/cancel")
    await web_client.post(
        "/api/v1/runs/trash",
        json={"run_ids": [trashed["id"]]},
    )

    response = await web_client.get(
        "/api/v1/instruments/recent",
        params={"limit": 1},
    )

    assert response.status_code == 200
    assert response.json()[0]["ticker"] == "NVDA"
    assert response.json()[0]["instrument_name"] == "NVIDIA"
    assert response.json()[0]["instrument_local_name"] == "英伟达"


@pytest.mark.anyio
async def test_removed_provenance_request_is_rejected(
    web_client: httpx.AsyncClient,
) -> None:
    response = await web_client.post(
        "/api/v1/runs",
        json={**_payload(), "provenance": True},
    )
    assert response.status_code == 422


@pytest.mark.anyio
async def test_reviews_api_forwards_audited_search_and_status_group_filters(
    web_client: httpx.AsyncClient,
    web_repository,
    monkeypatch,
) -> None:
    captured = {}

    def review_entries(**filters):
        captured.update(filters)
        return []

    monkeypatch.setattr(web_repository, "review_entries", review_entries)

    response = await web_client.get(
        "/api/v1/reviews",
        params={
            "q": "demand lesson",
            "ticker": "vd",
            "market": "america/new",
            "status_group": "in_progress",
            "limit": 25,
        },
    )

    assert response.status_code == 200
    assert response.json() == []
    assert captured == {
        "q": "demand lesson",
        "ticker": "vd",
        "market": "america/new",
        "status_group": "in_progress",
        "limit": 25,
    }


@pytest.mark.anyio
async def test_legacy_memory_read_api_is_absent(web_client: httpx.AsyncClient) -> None:
    response = await web_client.get("/api/v1/memory")

    assert response.status_code == 404


@pytest.mark.anyio
async def test_review_lifecycle_actions_delegate_without_exposing_errors(
    web_client: httpx.AsyncClient,
    web_repository,
    monkeypatch,
) -> None:
    retried = []
    retired = []
    monkeypatch.setattr(
        web_repository,
        "retry_outcome_reflection",
        lambda outcome_id: retried.append(outcome_id),
    )
    monkeypatch.setattr(
        web_repository,
        "retire_outcome_feedback",
        lambda feedback_id, *, reason, note: retired.append((feedback_id, reason, note))
        or {
            "status": "retired",
            "review_status": "feedback_retired",
            "retirement_reason": reason,
            "retirement_note": note,
            "retired_at": "2026-08-12T00:00:00Z",
        },
    )

    retry_response = await web_client.post(
        "/api/v1/outcome-observations/7/reflection/retry"
    )
    retire_response = await web_client.post(
        "/api/v1/outcome-feedback/11/retire",
        json={"reason": "misleading", "note": "It overstates the result."},
    )

    assert retry_response.json() == {"status": "pending"}
    assert retire_response.json() == {
        "status": "retired",
        "review_status": "feedback_retired",
        "retirement_reason": "misleading",
        "retirement_note": "It overstates the result.",
        "retired_at": "2026-08-12T00:00:00Z",
    }
    assert retried == [7]
    assert retired == [(11, "misleading", "It overstates the result.")]


@pytest.mark.anyio
async def test_feedback_retirement_api_validates_typed_requests_and_transitions(
    web_client: httpx.AsyncClient,
    web_repository,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        web_repository,
        "retire_outcome_feedback",
        lambda _id, *, reason, note: {
            "status": "retired",
            "review_status": "feedback_retired",
            "retirement_reason": reason,
            "retirement_note": note,
            "retired_at": "2026-08-12T00:00:00Z",
        },
    )
    invalid_reason = await web_client.post(
        "/api/v1/outcome-feedback/11/retire",
        json={"reason": "retired_by_user"},
    )
    overlong_note = await web_client.post(
        "/api/v1/outcome-feedback/11/retire",
        json={"reason": "other", "note": "x" * 1001},
    )
    retired = await web_client.post(
        "/api/v1/outcome-feedback/11/retire",
        json={"reason": "other", "note": "  kept for audit  "},
    )

    assert invalid_reason.status_code == 422
    assert overlong_note.status_code == 422
    assert retired.status_code == 200
    assert retired.json() == {
        "status": "retired",
        "review_status": "feedback_retired",
        "retirement_reason": "other",
        "retirement_note": "kept for audit",
        "retired_at": "2026-08-12T00:00:00Z",
    }

    monkeypatch.setattr(
        web_repository,
        "retire_outcome_feedback",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OutcomeFeedbackRetirementNotFoundError("11")
        ),
    )
    missing = await web_client.post(
        "/api/v1/outcome-feedback/11/retire",
        json={"reason": "not_useful"},
    )
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "outcome_feedback_not_found"

    monkeypatch.setattr(
        web_repository,
        "retire_outcome_feedback",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OutcomeFeedbackRetirementConflictError("not eligible")
        ),
    )
    conflict = await web_client.post(
        "/api/v1/outcome-feedback/11/retire",
        json={"reason": "not_useful"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == {
        "code": "outcome_feedback_retirement_conflict",
        "message": "not eligible",
    }


@pytest.mark.anyio
async def test_reflection_regeneration_requires_idempotency_and_returns_cycle(
    web_client: httpx.AsyncClient,
    web_repository,
    monkeypatch,
) -> None:
    calls = []
    monkeypatch.setattr(
        web_repository,
        "enqueue_outcome_reflection_regeneration",
        lambda outcome_id, *, idempotency_key: calls.append((outcome_id, idempotency_key))
        or {
            "cycle": {
                "id": "cycle-1", "outcome_id": outcome_id, "status": "queued",
                "origin": "manual", "trigger": "user_regeneration", "retry_ordinal": 0,
                "queued_at": "2026-08-05T00:00:00Z", "due_at": "2026-08-05T00:00:00Z",
            },
            "review_status": "awaiting_reflection",
            "reflection_status": "pending",
        },
    )
    missing = await web_client.post(
        "/api/v1/outcome-observations/7/reflection-regenerations"
    )
    accepted = await web_client.post(
        "/api/v1/outcome-observations/7/reflection-regenerations",
        headers={"Idempotency-Key": "retry-1"},
    )
    legacy_shape = await web_client.post(
        "/api/v1/outcome-observations/7/reflection/regenerations",
        headers={"Idempotency-Key": "retry-1"},
    )
    assert missing.status_code == 422
    assert accepted.status_code == 202
    assert legacy_shape.status_code == 405
    assert accepted.json()["cycle"]["id"] == "cycle-1"
    assert accepted.json()["review_status"] == "awaiting_reflection"
    assert calls == [(7, "retry-1")]


@pytest.mark.anyio
async def test_reflection_regeneration_returns_typed_not_found_and_active_conflict(
    web_client: httpx.AsyncClient,
    web_repository,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        web_repository,
        "enqueue_outcome_reflection_regeneration",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OutcomeReflectionRegenerationNotFoundError("7")
        ),
    )
    missing = await web_client.post(
        "/api/v1/outcome-observations/7/reflection-regenerations",
        headers={"Idempotency-Key": "retry-1"},
    )
    assert missing.status_code == 404
    monkeypatch.setattr(
        web_repository,
        "enqueue_outcome_reflection_regeneration",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OutcomeReflectionRegenerationConflictError(
                "active", active_cycle_id="cycle-active"
            )
        ),
    )
    conflict = await web_client.post(
        "/api/v1/outcome-observations/7/reflection-regenerations",
        headers={"Idempotency-Key": "retry-2"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["active_cycle_id"] == "cycle-active"


@pytest.mark.anyio
async def test_inconsistent_review_rejects_lifecycle_actions(
    web_client: httpx.AsyncClient,
    web_repository,
    monkeypatch,
) -> None:
    monkeypatch.setattr(web_repository, "retry_outcome_reflection", lambda _id: False)
    monkeypatch.setattr(
        web_repository,
        "retire_outcome_feedback",
        lambda _id, *, reason, note: (_ for _ in ()).throw(
            OutcomeFeedbackRetirementConflictError("Review lifecycle is inconsistent")
        ),
    )

    retry = await web_client.post("/api/v1/outcome-observations/7/reflection/retry")
    retire = await web_client.post(
        "/api/v1/outcome-feedback/11/retire",
        json={"reason": "not_useful"},
    )

    assert retry.status_code == 409
    assert retire.status_code == 409


@pytest.mark.anyio
async def test_run_detail_and_artifact_api_expose_complete_audit_contract(
    web_client: httpx.AsyncClient,
    web_service,
    web_repository,
) -> None:
    queued = web_service.enqueue(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )
    web_repository.claim_run(queued.id, "fixture-worker", 30)
    evidence_item = EvidenceItem.create(
        source="fixture",
        evidence_type="price",
        requested_date=date(2026, 7, 24),
        effective_date=date(2026, 7, 24),
        value=100.0,
        unit="USD",
    )
    evidence = EvidenceBundle(
        instrument="NVDA",
        analysis_date=date(2026, 7, 24),
        items=(evidence_item,),
    )
    reports = {
        name: analyst_report(
            analyst=name,
            executive_summary="Fixture summary.",
            confidence=0.8,
            evidence_ref=evidence_item.ref,
            narrative="Fixture report.",
        )
        for name in ("social", "news", "market", "fundamentals")
    }
    artifacts_created = []
    for name, analyst_output in reports.items():
        stored, _ = web_repository.append_artifact(
            queued.id,
            ResearchArtifactDraft(
                node=f"analyst.{name}",
                stage="analyst",
                role=name,
                generation_method=ArtifactGenerationMethod.TOOL_CALL,
                content=analyst_output,
            ),
        )
        artifacts_created.append(stored)
    artifact = next(item for item in artifacts_created if item.role == "market")
    review, _ = web_repository.append_artifact(
        queued.id,
        ResearchArtifactDraft(
            node="case.bear",
            stage="case",
            role="bear",
            generation_method=ArtifactGenerationMethod.TOOL_CALL,
            content=research_case(
                role="bear",
                evidence_ref=evidence_item.ref,
            ),
        ),
    )
    decision = research_decision(
        confidence=0.6,
        thesis="Fixture thesis.",
        evidence_refs=(evidence_item.ref,),
    )
    web_repository.seal_evidence(queued.id, evidence)
    web_repository.append_artifact(
        queued.id,
        ResearchArtifactDraft(
            node="committee.final",
            stage="decision",
            role="final_committee",
            generation_method=ArtifactGenerationMethod.TOOL_CALL,
            generation_observations=(
                ArtifactGenerationObservation(
                    node="committee.final.serialize.numeric",
                    task_kind="semantic_structured",
                    client_role="deep_reasoning",
                    generation_method=ArtifactGenerationMethod.JSON_MODE,
                ),
            ),
            content=decision,
        ),
    )
    web_repository.append_event(
        queued.id,
        "node.output_retry",
        node="debate.agenda.serialize",
        payload={
            "method": "tool_call_recovered",
            "reason_code": "non_json_response",
        },
    )
    web_repository.append_event(
        queued.id,
        "node.output_recovered",
        node="debate.agenda.serialize",
        payload={"method": "tool_call_recovered"},
    )
    partial_detail = await web_client.get(f"/api/v1/runs/{queued.id}")
    partial_export = await web_client.get(f"/api/v1/runs/{queued.id}/export?format=json")

    assert partial_detail.status_code == 200
    assert partial_export.status_code == 200
    assert partial_detail.json()["run"]["status"] == "running"
    assert partial_detail.json()["result"]["status"] == "running"
    assert partial_detail.json()["result"]["decision"]["thesis"] == ("Fixture thesis.")
    assert partial_detail.json()["result"]["evidence"]["digest"] == (evidence.digest)
    assert list(partial_detail.json()["result"]["reports"]) == [
        "fundamentals",
        "market",
        "news",
        "social",
    ]
    assert partial_detail.json()["result"]["recoveries"][0]["node"] == ("debate.agenda.serialize")
    assert partial_export.json()["result"]["recoveries"][0]["retry_count"] == 1
    assert partial_export.json()["result"]["decision"]["thesis"] == ("Fixture thesis.")
    assert partial_export.json()["evidence"]["digest"] == evidence.digest

    web_repository.complete(
        queued.id,
        AnalysisResult(
            run_id=queued.id,
            status=RunStatus.SUCCEEDED,
            instrument="NVDA",
            reports=reports,
            decision=decision,
            evidence=evidence,
        ),
        evidence=evidence,
        benchmark="SPY",
    )

    detail = await web_client.get(f"/api/v1/runs/{queued.id}")
    artifacts = await web_client.get(f"/api/v1/runs/{queued.id}/artifacts")
    empty_attempt = await web_client.get(f"/api/v1/runs/{queued.id}/artifacts?attempt=2")
    package = await web_client.get(f"/api/v1/runs/{queued.id}/export?format=package")
    evidence_response = await web_client.get(f"/api/v1/runs/{queued.id}/evidence")

    assert detail.status_code == 200
    assert list(detail.json()["result"]["reports"]) == [
        "fundamentals",
        "market",
        "news",
        "social",
    ]
    assert detail.json()["result"]["evidence"]["digest"] == evidence.digest
    assert detail.json()["result"]["evidence"]["items"][0]["ref"] == (evidence_item.ref)
    assert detail.json()["evidence_status"]["status"] == "sealed"
    assert evidence_response.status_code == 200
    assert evidence_response.json()["digest"] == evidence.digest
    assert detail.json()["attempts"][0]["status"] == "succeeded"
    assert detail.json()["attempts"][0]["metrics"] == (detail.json()["run"]["metrics"])
    assert artifacts.status_code == 200
    assert artifact.id in {item["id"] for item in artifacts.json()}
    assert "Fixture report." in artifacts.json()[0]["content"]["markdown"]
    review_payload = next(item for item in artifacts.json() if item["id"] == review.id)
    assert review_payload["generation_method"] == "tool_call"
    decision_payload = next(item for item in artifacts.json() if item["role"] == "final_committee")
    assert decision_payload["generation_observations"] == [
        {
            "node": "committee.final.serialize.numeric",
            "task_kind": "semantic_structured",
            "client_role": "deep_reasoning",
            "generation_method": "json_mode",
        }
    ]
    assert "diagnostics" not in review_payload
    assert "Fixture case statement" in review_payload["content"]["markdown"]
    assert empty_attempt.json() == []
    assert package.status_code == 200
    assert package.headers["content-type"] == "application/zip"
    assert package.headers["content-disposition"].endswith(f'tradingagents-{queued.id}.zip"')
    with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
        assert {
            "report.md",
            "run.json",
            "artifacts.json",
            "evidence.json",
            "manifest.json",
        } <= set(archive.namelist())


@pytest.mark.anyio
async def test_capabilities_expose_effective_non_sensitive_run_defaults(
    web_client: httpx.AsyncClient,
) -> None:
    response = await web_client.get("/api/v1/capabilities")

    assert response.status_code == 200
    payload = response.json()
    assert payload["output_languages"] == ["en", "zh-CN", "ja"]
    assert (
        payload["defaults"]
        | {
            "output_language": "en",
            "quick_reasoning_effort": None,
            "deep_reasoning_effort": None,
        }
        == payload["defaults"]
    )
    assert payload["providers"]["openai"]["label"] == "OpenAI"
    assert payload["providers"]["openai"]["api_key_required"] is True
    assert payload["providers"]["openai"]["configured"] is True
    assert payload["providers"]["openai"]["selectable"] is True
    assert "quick_models" not in payload["providers"]["openai"]
    assert payload["defaults"]["trash_retention_days"] == 30


@pytest.mark.anyio
async def test_capabilities_and_runs_preserve_custom_output_language(
    tmp_path,
) -> None:
    custom_language = "Simplified Chinese (简体中文, zh-CN)"
    settings = AppSettings.from_env(
        environ={
            "TRADINGAGENTS_HOME": str(tmp_path),
            "TRADINGAGENTS_DATABASE_PATH": str(tmp_path / "custom-language.db"),
            "TRADINGAGENTS_OUTPUT_LANGUAGE": custom_language,
        },
        load_env_files=False,
    )
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        capabilities = await client.get("/api/v1/capabilities")
        created = await client.post(
            "/api/v1/runs",
            json={
                "ticker": "NVDA",
                "analysis_date": "2026-07-24",
            },
        )
        detail = await client.get(f"/api/v1/runs/{created.json()['id']}")

    assert capabilities.status_code == 200
    assert capabilities.json()["defaults"]["output_language"] == custom_language
    assert created.status_code == 202
    assert detail.json()["run"]["request"]["output_language"] == custom_language
    assert detail.json()["run"]["config_snapshot"]["output_language"] == custom_language


@pytest.mark.anyio
async def test_model_catalog_falls_back_without_leaking_configuration(
    web_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    response = await web_client.get("/api/v1/providers/openai/models")
    unknown = await web_client.get("/api/v1/providers/not-real/models")

    assert response.status_code == 200
    assert response.json()["source"] == "fallback"
    assert {model["id"] for model in response.json()["models"]} == {
        "gpt-5.4-mini",
        "gpt-5.5",
    }
    assert response.json()["warning"]["code"] == "provider_not_configured"
    assert unknown.status_code == 404


@pytest.mark.anyio
async def test_health_reports_database_and_queue_status(
    web_client: httpx.AsyncClient,
    web_service,
) -> None:
    web_service.enqueue(AnalysisRequest(ticker="NVDA", analysis_date="2026-07-24"))

    response = await web_client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "database": "ok",
        "queue": {
            "queued": 1,
            "running": 0,
            "pending_outcomes": 0,
        },
        "version": __version__,
    }


@pytest.mark.anyio
async def test_frontend_is_served_for_root_and_client_routes(
    web_client: httpx.AsyncClient,
) -> None:
    root = await web_client.get("/")
    client_route = await web_client.get("/runs/example-run")
    missing_api = await web_client.get("/api/v1/not-a-route")

    assert root.status_code == 200
    assert client_route.status_code == 200
    assert '<div id="root"></div>' in root.text
    assert client_route.text == root.text
    assert missing_api.status_code == 404


@pytest.mark.anyio
async def test_validation_error_does_not_echo_request_values(
    web_client: httpx.AsyncClient,
) -> None:
    private_value = "private-value-that-must-not-be-reflected"
    response = await web_client.post(
        "/api/v1/runs",
        json={
            **_payload(),
            "ticker": "",
            "llm_provider": private_value,
            "unexpected_secret": private_value,
        },
    )

    assert response.status_code == 422
    assert private_value not in response.text
    assert response.json()["error"]["code"] == "validation_error"
