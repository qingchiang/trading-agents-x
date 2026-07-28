from __future__ import annotations

import httpx2 as httpx
import pytest

from tradingagents.application.contracts import AnalysisRequest
from tradingagents.version import __version__


def _payload(ticker: str = "NVDA") -> dict:
    return {
        "ticker": ticker,
        "analysis_date": "2026-07-24",
        "profile": "standard",
        "analysts": ["market", "news"],
        "output_language": "en",
        "provenance": True,
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
        "/api/v1/runs/{run_id}/events",
        "/api/v1/runs/{run_id}/cancel",
        "/api/v1/runs/{run_id}/retry",
        "/api/v1/runs/{run_id}/rerun",
        "/api/v1/runs/{run_id}/export",
        "/api/v1/memory",
        "/api/v1/capabilities",
        "/api/v1/health",
    } <= set(paths)


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
        "provenance": False,
        "quick_reasoning_effort": None,
        "deep_reasoning_effort": None,
    } == payload["defaults"]


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
