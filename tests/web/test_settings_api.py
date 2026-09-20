"""Settings workflows without live provider calls."""

import pytest


@pytest.mark.anyio
async def test_settings_initialize_save_reveal_and_reject_cross_origin(tmp_path):
    import httpx2 as httpx

    from tradingagents.application.settings import AppSettings
    from tradingagents.web import create_app

    settings = AppSettings.from_env(environ={"TRADINGAGENTS_HOME": str(tmp_path)})
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(settings)), base_url="http://testserver"
    ) as web_client:
        initial = (await web_client.get("/api/v1/settings")).json()
        assert initial["initialized"] is False
        response = await web_client.post(
            "/api/v1/settings/import/apply", json={"revision": 0, "use_defaults": True}
        )
        assert response.status_code == 200
        saved = await web_client.patch(
            "/api/v1/settings",
            json={
                "revision": 1,
                "values": {"output_language": "ja"},
                "credentials": {"DEEPSEEK_API_KEY": "private-ui-key"},
            },
        )
        assert saved.status_code == 200
        assert "private-ui-key" not in saved.text
        revealed = await web_client.post(
            "/api/v1/settings/credentials/reveal", json={"name": "DEEPSEEK_API_KEY"}
        )
        assert revealed.json()["value"] == "private-ui-key"
        assert revealed.headers["cache-control"] == "no-store"
        rejected = await web_client.post(
            "/api/v1/settings/credentials/reveal",
            headers={"Origin": "https://other.example"},
            json={"name": "DEEPSEEK_API_KEY"},
        )
        assert rejected.status_code == 403
        conflict = await web_client.patch(
            "/api/v1/settings", json={"revision": 1, "values": {"output_language": "en"}}
        )
        assert conflict.status_code == 409


@pytest.mark.anyio
async def test_web_and_python_inherit_database_defaults_and_queued_snapshot_is_immutable(tmp_path):
    import httpx2 as httpx

    from tradingagents import AnalysisRequest, TradingAgents
    from tradingagents.application.configuration_models import ConfigurationPatch
    from tradingagents.application.settings import AppSettings
    from tradingagents.web import create_app

    settings = AppSettings.from_env(environ={"TRADINGAGENTS_HOME": str(tmp_path)})
    client = TradingAgents(
        settings, eligibility_resolver=lambda ticker: {"symbol": ticker, "quote_type": "EQUITY"}
    )
    store = client.service.configuration
    store.save(
        ConfigurationPatch(
            revision=0,
            values={"profile": "deep", "analysts": ["news"]},
            credentials={"OPENAI_API_KEY": "not-in-history"},
        ),
        initialize=True,
    )
    python_run = client.enqueue(AnalysisRequest(ticker="GOOG", analysis_date="2026-09-10"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(settings, service=client.service)),
        base_url="http://testserver",
    ) as web:
        implicit = await web.post(
            "/api/v1/runs", json={"ticker": "GOOG", "analysis_date": "2026-09-10"}
        )
        explicit = await web.post(
            "/api/v1/runs",
            json={"ticker": "GOOG", "analysis_date": "2026-09-10", "profile": "standard"},
        )
        assert implicit.status_code == explicit.status_code == 202
        assert implicit.json()["request"]["profile"] == python_run.request.profile.value == "deep"
        assert implicit.json()["request"]["analysts"] == ["news"]
        assert explicit.json()["request"]["profile"] == "standard"
        assert "not-in-history" not in implicit.text
        store.save(
            ConfigurationPatch(revision=1, values={"profile": "fast", "analysts": ["market"]})
        )
        assert client.service.repository.get_run(python_run.id).request.profile.value == "deep"
        assert (
            client.enqueue(
                AnalysisRequest(ticker="GOOG", analysis_date="2026-09-10")
            ).request.profile.value
            == "fast"
        )


@pytest.mark.anyio
async def test_connection_api_reveals_only_requested_key_and_preserves_conflicting_edits(tmp_path):
    import httpx2 as httpx

    from tradingagents.application.settings import AppSettings
    from tradingagents.web import create_app

    settings = AppSettings.from_env(environ={"TRADINGAGENTS_HOME": str(tmp_path)})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(settings)), base_url="http://testserver") as client:
        created = await client.patch("/api/v1/settings", json={"revision": 0, "connection_changes": [
            {"action": "create", "id": "connection-a", "name": "A", "preset": "openai_compatible",
             "transport": {"kind": "chat_completions", "base_url": "https://a.example/v1"},
             "credentials": {"api_key": "private-a"}},
            {"action": "create", "id": "connection-b", "name": "B", "preset": "openai_compatible",
             "transport": {"kind": "chat_completions", "base_url": "https://b.example/v1"},
             "credentials": {"api_key": "private-b"}},
        ]})
        assert created.status_code == 200
        assert "private-a" not in created.text and "private-b" not in created.text
        revealed = await client.post("/api/v1/settings/credentials/reveal", json={"connection_id": "connection-b", "name": "api_key"})
        assert revealed.json() == {"value": "private-b"}
        assert revealed.headers["cache-control"] == "no-store"
        conflict = await client.patch("/api/v1/settings", json={"revision": 0, "connection_changes": [{"action": "update", "id": "connection-a", "name": "Changed"}]})
        assert conflict.status_code == 409
        assert (await client.get("/api/v1/settings")).json()["connections"]["connection-a"]["connection"]["name"] == "A"
        denied = await client.post("/api/v1/settings/credentials/reveal", headers={"Origin": "https://other.example"}, json={"connection_id": "connection-a", "name": "api_key"})
        assert denied.status_code == 403
