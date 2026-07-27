"""Tests for the configurable sampling temperature (#178/#168).

Temperature is a cross-provider knob: when set it must reach the underlying
chat client; when unset the provider keeps its own default.
"""

import pytest

from tradingagents.application.llms import create_run_llms
from tradingagents.application.settings import RunSettings
from tradingagents.default_config import build_default_config
from tradingagents.llm_clients.factory import create_llm_client


@pytest.mark.unit
class TestTemperatureForwarding:
    @pytest.mark.parametrize(
        "provider,model",
        [
            # gpt-4.1 is intentionally a non-reasoning model: the GPT-5 family
            # are reasoning models and correctly drop temperature (see
            # test_openai_reasoning_effort), so forwarding is tested on gpt-4.1.
            ("openai", "gpt-4.1"),
            ("anthropic", "claude-sonnet-5"),
            ("google", "gemini-3.5-flash"),
            ("deepseek", "deepseek-chat"),
        ],
    )
    def test_temperature_reaches_client_when_set(self, provider, model):
        llm = create_llm_client(
            provider=provider, model=model, temperature=0.0, api_key="placeholder"
        ).get_llm()
        assert llm.temperature == 0.0

    def test_temperature_omitted_leaves_provider_default(self):
        # Not passing temperature must not force it to a value.
        llm = create_llm_client(
            provider="openai", model="gpt-4.1", api_key="placeholder"
        ).get_llm()
        # langchain's default is unset/None, not 0.0
        assert llm.temperature is None


@pytest.mark.unit
class TestTemperatureEnvOverlay:
    def test_explicit_environment_sets_temperature(self):
        import tradingagents.default_config as dc

        config = dc.build_default_config({"TRADINGAGENTS_TEMPERATURE": "0.2"})

        assert float(config["temperature"]) == 0.2

    def test_module_default_ignores_process_environment(self, monkeypatch):
        import tradingagents.default_config as dc

        monkeypatch.setenv("TRADINGAGENTS_TEMPERATURE", "0.2")

        assert dc.DEFAULT_CONFIG["temperature"] is None


@pytest.mark.unit
class TestProviderKwargsTemperature:
    """Run construction forwards a resolved temperature to both role clients."""

    def _kwargs_for(self, monkeypatch, temperature):
        calls = []

        def factory(**kwargs):
            calls.append(kwargs)

            class Client:
                def get_llm(self):
                    return object()

            return Client()

        monkeypatch.setattr(
            "tradingagents.application.llms.create_llm_client",
            factory,
        )
        settings = RunSettings(
            temperature=temperature,
            data_config=build_default_config({}),
        )
        create_run_llms(settings)
        return calls

    def test_float_string_coerced(self, monkeypatch):
        calls = self._kwargs_for(monkeypatch, "0.3")
        assert [call["temperature"] for call in calls] == [0.3, 0.3]

    def test_float_passthrough(self, monkeypatch):
        calls = self._kwargs_for(monkeypatch, 0.0)
        assert [call["temperature"] for call in calls] == [0.0, 0.0]

    def test_unset_temperature_is_omitted(self, monkeypatch):
        calls = self._kwargs_for(monkeypatch, None)
        assert all("temperature" not in call for call in calls)
