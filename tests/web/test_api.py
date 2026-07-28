from __future__ import annotations

from datetime import date

import httpx2 as httpx
import pytest

from tradingagents.application.contracts import (
    AnalysisRequest,
    AnalysisResult,
    AnalystReport,
    EvidenceBundle,
    EvidenceItem,
    PerspectiveReview,
    ResearchArtifactDraft,
    ResearchDecision,
    ResearchRating,
    RunStatus,
)
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
async def test_run_lifecycle_routes_and_filters(
    web_client: httpx.AsyncClient,
) -> None:
    created = (await web_client.post("/api/v1/runs", json=_payload())).json()
    run_id = created["id"]

    detail = await web_client.get(f"/api/v1/runs/{run_id}")
    queued = await web_client.get("/api/v1/runs?status=queued")
    cancelled = await web_client.post(f"/api/v1/runs/{run_id}/cancel")
    rerun = await web_client.post(f"/api/v1/runs/{run_id}/rerun")

    assert detail.status_code == 200
    assert detail.json()["run"]["id"] == run_id
    assert [run["id"] for run in queued.json()] == [run_id]
    assert cancelled.json()["status"] == "cancelled"
    assert rerun.json()["parent_run_id"] == run_id


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
        "/api/v1/runs/{run_id}/events",
        "/api/v1/runs/{run_id}/cancel",
        "/api/v1/runs/{run_id}/retry",
        "/api/v1/runs/{run_id}/rerun",
        "/api/v1/runs/{run_id}/export",
        "/api/v1/memory",
        "/api/v1/capabilities",
        "/api/v1/providers/{provider}/models",
        "/api/v1/health",
    } <= set(paths)
    assert "provenance" not in schema["components"]["schemas"][
        "AnalysisRequest"
    ]["properties"]
    assert "provenance" not in schema["components"]["schemas"][
        "CapabilityDefaults"
    ]["properties"]


@pytest.mark.anyio
async def test_legacy_provenance_request_is_accepted_and_discarded(
    web_client: httpx.AsyncClient,
) -> None:
    response = await web_client.post(
        "/api/v1/runs",
        json={**_payload(), "provenance": True},
    )
    detail = await web_client.get(
        f"/api/v1/runs/{response.json()['id']}"
    )

    assert response.status_code == 202
    assert "provenance" not in detail.json()["run"]["request"]
    assert "provenance" not in detail.json()["run"]["config_snapshot"]


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
    report = AnalystReport(
        analyst="market",
        summary="Fixture summary.",
        confidence=0.8,
        evidence_refs=(evidence_item.ref,),
        narrative="Fixture report.",
    )
    reports = {
        name: report.model_copy(update={"analyst": name})
        for name in ("social", "news", "market", "fundamentals")
    }
    artifact, _ = web_repository.append_artifact(
        queued.id,
        ResearchArtifactDraft(
            node="analyst.market",
            stage="analyst",
            role="market",
            content=report,
        ),
    )
    degraded, _ = web_repository.append_artifact(
        queued.id,
        ResearchArtifactDraft(
            node="perspective.bear",
            stage="perspective",
            role="bear",
            content=PerspectiveReview(
                role="bear",
                thesis='{"summary": "Legacy JSON payload"}',
                evidence_refs=(evidence_item.ref,),
            ),
        ),
    )
    decision = ResearchDecision(
        rating=ResearchRating.HOLD,
        confidence=0.6,
        thesis="Fixture thesis.",
        evidence_refs=(evidence_item.ref,),
        time_horizon="6-12 months",
    )
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
    assert artifacts.status_code == 200
    assert artifacts.json()[0]["id"] == artifact.id
    assert artifacts.json()[0]["content"]["narrative"] == "Fixture report."
    degraded_payload = next(
        item for item in artifacts.json() if item["id"] == degraded.id
    )
    assert degraded_payload["generation_method"] == "legacy_unknown"
    assert degraded_payload["diagnostics"]["legacy_degraded_output"] is True
    assert degraded_payload["diagnostics"]["missing_fields"] == [
        "claim_rebuttals",
        "risks",
    ]
    assert degraded_payload["diagnostics"]["rerun_recommended"] is True
    assert degraded_payload["diagnostics"]["parsed_thesis"] == {
        "summary": "Legacy JSON payload"
    }
    assert empty_attempt.json() == []


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
    assert response.json()["error"]["code"] == "validation_error"
