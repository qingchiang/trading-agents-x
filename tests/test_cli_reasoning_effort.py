"""CLI collection for independent quick/deep reasoning effort."""

from unittest import mock

from cli import utils


def _config(**overrides):
    config = {
        "llm_provider": "openai",
        "quick_think_llm": "gpt-5.6-luna",
        "deep_think_llm": "gpt-5.6-sol",
        "quick_reasoning_effort": None,
        "deep_reasoning_effort": None,
        "openai_reasoning_effort": None,
        "google_thinking_level": None,
        "anthropic_effort": None,
    }
    config.update(overrides)
    return config


def _selection(value):
    prompt = mock.Mock()
    prompt.ask.return_value = value
    return prompt


def test_shared_mode_uses_model_level_intersection(monkeypatch):
    monkeypatch.delenv("TRADINGAGENTS_QUICK_REASONING_EFFORT", raising=False)
    monkeypatch.delenv("TRADINGAGENTS_DEEP_REASONING_EFFORT", raising=False)
    with mock.patch.object(utils.questionary, "select", return_value=_selection("shared")), \
         mock.patch.object(utils, "_ask_role_reasoning_level", return_value="max") as ask:
        result = utils.configure_role_reasoning_efforts(
            "openai", "gpt-5.6-luna", "gpt-5.6-sol", _config()
        )
    assert result == {"quick": "max", "deep": "max"}
    assert ask.call_args.args[-1] == ("none", "low", "medium", "high", "xhigh", "max")


def test_single_role_env_only_prompts_unresolved_role(monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_QUICK_REASONING_EFFORT", "low")
    monkeypatch.delenv("TRADINGAGENTS_DEEP_REASONING_EFFORT", raising=False)
    with mock.patch.object(utils, "_ask_role_reasoning_level", return_value="high") as ask:
        result = utils.configure_role_reasoning_efforts(
            "openai",
            "gpt-5.6-luna",
            "gpt-5.6-sol",
            _config(quick_reasoning_effort="low"),
        )
    assert result == {"quick": "low", "deep": "high"}
    assert ask.call_args.args[1] == "deep"


def test_legacy_env_keeps_step_noninteractive(monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_OPENAI_REASONING_EFFORT", "medium")
    with mock.patch.object(utils.questionary, "select") as prompt:
        result = utils.configure_role_reasoning_efforts(
            "openai", "gpt-5.6-luna", "gpt-5.6-sol", _config(openai_reasoning_effort="medium")
        )
    prompt.assert_not_called()
    assert result == {"quick": None, "deep": None}


def test_provider_default_mode_sets_both_sentinels(monkeypatch):
    monkeypatch.delenv("TRADINGAGENTS_OPENAI_REASONING_EFFORT", raising=False)
    monkeypatch.delenv("TRADINGAGENTS_QUICK_REASONING_EFFORT", raising=False)
    monkeypatch.delenv("TRADINGAGENTS_DEEP_REASONING_EFFORT", raising=False)
    with mock.patch.object(
        utils.questionary, "select", return_value=_selection("default")
    ):
        result = utils.configure_role_reasoning_efforts(
            "openai", "gpt-5.6-luna", "gpt-5.6-sol", _config()
        )
    assert result == {"quick": "provider_default", "deep": "provider_default"}


def test_deepseek_separate_mode_only_offers_native_levels(monkeypatch):
    monkeypatch.delenv("TRADINGAGENTS_QUICK_REASONING_EFFORT", raising=False)
    monkeypatch.delenv("TRADINGAGENTS_DEEP_REASONING_EFFORT", raising=False)
    with mock.patch.object(
        utils.questionary, "select", return_value=_selection("separate")
    ), mock.patch.object(
        utils, "_ask_role_reasoning_level", side_effect=["high", "max"]
    ) as ask:
        result = utils.configure_role_reasoning_efforts(
            "deepseek",
            "deepseek-v4-flash",
            "deepseek-v4-pro",
            _config(
                llm_provider="deepseek",
                quick_think_llm="deepseek-v4-flash",
                deep_think_llm="deepseek-v4-pro",
            ),
        )
    assert result == {"quick": "high", "deep": "max"}
    assert ask.call_args_list[0].args[-1] == ("high", "max")
    assert ask.call_args_list[1].args[-1] == ("high", "max")


def test_summary_does_not_include_endpoint_or_key(capsys):
    config = _config(
        openai_reasoning_effort="low",
        backend_url="https://private.example/v1",
        api_key="secret-value",
    )
    utils.configure_role_reasoning_efforts(
        "openai", "gpt-5.6-luna", "gpt-5.6-sol", config, non_interactive=True
    )
    output = capsys.readouterr().out
    assert "source=openai_reasoning_effort" in output
    assert "reasoning_effort=low" in output
    assert "private.example" not in output
    assert "secret-value" not in output
