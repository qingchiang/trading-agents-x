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
    ResearchArtifactDraft,
    RunStatus,
)
from tradingagents.application.database import RunRecord
from tradingagents.application.service import AnalysisService
from tradingagents.application.settings import AppSettings
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
@pytest.mark.parametrize(
    ("result", "status", "code"),
    [
        (
            {"symbol": "SPY", "quote_type": "ETF"},
            422,
            "unsupported_instrument",
        ),
        (
            {"symbol": "NVDA", "quote_type": 17},
            503,
            "instrument_eligibility_unavailable",
        ),
    ],
)
async def test_run_creation_distinguishes_typed_admission_failures(
    web_settings,
    web_repository,
    result,
    status,
    code,
) -> None:
    service = AnalysisService(
        web_settings,
        repository=web_repository,
        eligibility_resolver=lambda _ticker: result,
    )
    transport = httpx.ASGITransport(app=create_app(web_settings, service=service))
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/runs",
            json=_payload("SPY" if status == 422 else "NVDA"),
        )

    assert response.status_code == status
    payload = response.json()
    assert payload["error"]["code"] == code
    assert payload["error"]["message"]
    assert web_repository.list_runs().total == 0


@pytest.mark.anyio
async def test_legacy_crypto_retry_returns_stable_unsupported_response(
    web_client: httpx.AsyncClient,
    web_repository,
    web_service,
) -> None:
    queued = web_service.enqueue(
        AnalysisRequest(ticker="NVDA", analysis_date="2026-07-24")
    )
    web_repository.claim_run(queued.id, "legacy-fixture", 30)
    web_repository.fail(queued.id, RuntimeError("fixture failure"))
    with web_repository.sessions.begin() as session:
        record = session.get(RunRecord, queued.id)
        record.request_json = {
            **record.request_json,
            "ticker": "BTC-USD",
            "asset_type": "crypto",
        }

    response = await web_client.post(f"/api/v1/runs/{queued.id}/retry")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unsupported_instrument"
    unchanged = web_repository.get_run(queued.id)
    assert unchanged.status is RunStatus.FAILED
    assert unchanged.attempt == 1


@pytest.mark.anyio
async def test_legacy_crypto_source_returns_stable_unsupported_response(
    web_client: httpx.AsyncClient,
    web_repository,
    web_service,
) -> None:
    source = web_service.enqueue(
        AnalysisRequest(ticker="NVDA", analysis_date="2026-07-24")
    )
    web_repository.claim_run(source.id, "legacy-fixture", 30)
    web_repository.fail(source.id, RuntimeError("fixture failure"))
    with web_repository.sessions.begin() as session:
        record = session.get(RunRecord, source.id)
        record.request_json = {
            **record.request_json,
            "ticker": "BTC-USD",
            "asset_type": "crypto",
        }

    response = await web_client.post(
        "/api/v1/runs",
        json={**_payload("AAPL"), "source_run_id": source.id},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unsupported_instrument"
    assert web_repository.list_runs().total == 1


@pytest.mark.anyio
async def test_run_lifecycle_routes_and_filters(
    web_client: httpx.AsyncClient,
) -> None:
    created = (await web_client.post("/api/v1/runs", json=_payload())).json()
    run_id = created["id"]

    detail = await web_client.get(f"/api/v1/runs/{run_id}")
    pending_evidence = await web_client.get(
        f"/api/v1/runs/{run_id}/evidence"
    )
    queued = await web_client.get("/api/v1/runs?status=queued")
    cancelled = await web_client.post(f"/api/v1/runs/{run_id}/cancel")
    trashed = await web_client.post(
        "/api/v1/runs/trash",
        json={"run_ids": [run_id]},
    )
    trashed_page = await web_client.get(
        "/api/v1/runs?trash_state=trashed&q=nv"
    )
    trashed_detail = await web_client.get(f"/api/v1/runs/{run_id}")
    trashed_export = await web_client.get(
        f"/api/v1/runs/{run_id}/export?format=json"
    )
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
    terminal = (
        await web_client.post("/api/v1/runs", json=_payload("NVDA"))
    ).json()
    queued = (
        await web_client.post("/api/v1/runs", json=_payload("AAPL"))
    ).json()
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
    queued = (
        await web_client.post("/api/v1/runs", json=_payload("NVDA"))
    ).json()

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
    queued = web_service.enqueue(
        AnalysisRequest(ticker="NVDA", analysis_date="2026-07-24")
    )
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
    queued = web_service.enqueue(
        AnalysisRequest(ticker="NVDA", analysis_date="2026-07-24")
    )

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
        "/api/v1/memory",
        "/api/v1/capabilities",
        "/api/v1/providers/{provider}/models",
        "/api/v1/health",
    } <= set(paths)
    assert "/api/v1/runs/{run_id}/rerun" not in paths
    assert "provenance" not in schema["components"]["schemas"][
        "AnalysisRequest"
    ]["properties"]
    assert "provenance" not in schema["components"]["schemas"][
        "CapabilityDefaults"
    ]["properties"]
    create_run_422 = paths["/api/v1/runs"]["post"]["responses"]["422"]
    response_schema = create_run_422["content"]["application/json"]["schema"]
    assert {
        member["$ref"]
        for member in response_schema["anyOf"]
    } == {
        "#/components/schemas/InstrumentAdmissionErrorResponse",
        "#/components/schemas/RequestValidationErrorResponse",
    }
    assert {
        "unsupported_instrument",
        "validation_error",
    } <= set(create_run_422["content"]["application/json"]["examples"])
    assert schema["components"]["schemas"]["RequestValidationErrorCode"][
        "enum"
    ] == ["validation_error"]


@pytest.mark.anyio
async def test_recent_instruments_exclude_trashed_runs_and_include_names(
    web_client: httpx.AsyncClient,
    web_repository,
) -> None:
    active = (
        await web_client.post("/api/v1/runs", json=_payload("NVDA"))
    ).json()
    trashed = (
        await web_client.post("/api/v1/runs", json=_payload("AAPL"))
    ).json()
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
async def test_memory_api_forwards_audited_search_filters(
    web_client: httpx.AsyncClient,
    web_repository,
    monkeypatch,
) -> None:
    captured = {}

    def memory_entries(**filters):
        captured.update(filters)
        return []

    monkeypatch.setattr(web_repository, "memory_entries", memory_entries)

    response = await web_client.get(
        "/api/v1/memory",
        params={
            "q": "demand lesson",
            "ticker": "vd",
            "market": "america/new",
            "status": "resolved",
            "limit": 25,
        },
    )

    assert response.status_code == 200
    assert response.json() == []
    assert captured == {
        "q": "demand lesson",
        "ticker": "vd",
        "market": "america/new",
        "status": "resolved",
        "limit": 25,
    }


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
    partial_export = await web_client.get(
        f"/api/v1/runs/{queued.id}/export?format=json"
    )

    assert partial_detail.status_code == 200
    assert partial_export.status_code == 200
    assert partial_detail.json()["run"]["status"] == "running"
    assert partial_detail.json()["result"]["status"] == "running"
    assert partial_detail.json()["result"]["decision"]["thesis"] == (
        "Fixture thesis."
    )
    assert partial_detail.json()["result"]["evidence"]["digest"] == (
        evidence.digest
    )
    assert list(partial_detail.json()["result"]["reports"]) == [
        "fundamentals",
        "market",
        "news",
        "social",
    ]
    assert partial_detail.json()["result"]["recoveries"][0]["node"] == (
        "debate.agenda.serialize"
    )
    assert partial_export.json()["result"]["recoveries"][0]["retry_count"] == 1
    assert partial_export.json()["result"]["decision"]["thesis"] == (
        "Fixture thesis."
    )
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
    artifacts = await web_client.get(
        f"/api/v1/runs/{queued.id}/artifacts"
    )
    empty_attempt = await web_client.get(
        f"/api/v1/runs/{queued.id}/artifacts?attempt=2"
    )
    package = await web_client.get(
        f"/api/v1/runs/{queued.id}/export?format=package"
    )
    evidence_response = await web_client.get(
        f"/api/v1/runs/{queued.id}/evidence"
    )

    assert detail.status_code == 200
    assert list(detail.json()["result"]["reports"]) == [
        "fundamentals",
        "market",
        "news",
        "social",
    ]
    assert detail.json()["result"]["evidence"]["digest"] == evidence.digest
    assert detail.json()["result"]["evidence"]["items"][0]["ref"] == (
        evidence_item.ref
    )
    assert detail.json()["evidence_status"]["status"] == "sealed"
    assert evidence_response.status_code == 200
    assert evidence_response.json()["digest"] == evidence.digest
    assert detail.json()["attempts"][0]["status"] == "succeeded"
    assert detail.json()["attempts"][0]["metrics"] == (
        detail.json()["run"]["metrics"]
    )
    assert artifacts.status_code == 200
    assert artifact.id in {item["id"] for item in artifacts.json()}
    assert "Fixture report." in artifacts.json()[0]["content"]["markdown"]
    review_payload = next(
        item for item in artifacts.json() if item["id"] == review.id
    )
    assert review_payload["generation_method"] == "tool_call"
    decision_payload = next(
        item for item in artifacts.json() if item["role"] == "final_committee"
    )
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
    assert package.headers["content-disposition"].endswith(
        f'tradingagents-{queued.id}.zip"'
    )
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
    assert payload["defaults"] | {
        "output_language": "en",
        "quick_reasoning_effort": None,
        "deep_reasoning_effort": None,
    } == payload["defaults"]
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
    service = AnalysisService(
        settings,
        eligibility_resolver=lambda ticker: {
            "symbol": ticker,
            "quote_type": "EQUITY",
        },
    )
    transport = httpx.ASGITransport(app=create_app(settings, service=service))
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
        detail = await client.get(
            f"/api/v1/runs/{created.json()['id']}"
        )

    assert capabilities.status_code == 200
    assert capabilities.json()["defaults"]["output_language"] == custom_language
    assert created.status_code == 202
    assert (
        detail.json()["run"]["request"]["output_language"]
        == custom_language
    )
    assert (
        detail.json()["run"]["config_snapshot"]["output_language"]
        == custom_language
    )


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
    web_service.enqueue(
        AnalysisRequest(ticker="NVDA", analysis_date="2026-07-24")
    )

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
    payload = response.json()
    assert payload["error"]["code"] == "validation_error"
    assert payload["details"]
    assert {"location", "message", "type"} <= set(payload["details"][0])
