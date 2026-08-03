"""Role-specific reasoning effort resolution and capability policy."""

from __future__ import annotations

import pytest

from tradingagents.llm_clients.reasoning_effort import (
    PROVIDER_DEFAULT,
    model_effort_levels,
    resolve_reasoning_effort,
)


def _config(provider="openai", quick=None, deep=None, legacy=None):
    config = {
        "llm_provider": provider,
        "quick_think_llm": "gpt-5.6-luna",
        "deep_think_llm": "gpt-5.6-sol",
        "quick_reasoning_effort": quick,
        "deep_reasoning_effort": deep,
        "openai_reasoning_effort": legacy,
        "google_thinking_level": legacy,
        "anthropic_effort": legacy,
    }
    return config


def test_role_value_wins_over_legacy_and_normalizes():
    result = resolve_reasoning_effort(_config(quick=" XHIGH ", legacy="low"), "quick")
    assert result.source == "quick_reasoning_effort"
    assert result.kwargs == {"reasoning_effort": "xhigh"}


def test_none_falls_back_to_provider_legacy():
    result = resolve_reasoning_effort(_config(legacy=" HIGH "), "deep")
    assert result.source == "openai_reasoning_effort"
    assert result.kwargs == {"reasoning_effort": "high"}


def test_provider_default_blocks_legacy():
    result = resolve_reasoning_effort(
        _config(quick=PROVIDER_DEFAULT, legacy="high"), "quick"
    )
    assert result.source == "quick_reasoning_effort"
    assert result.kwargs == {}
    assert result.display_value == "omitted"


@pytest.mark.parametrize(
    "provider,model,legacy_key,native",
    [
        ("openai", "gpt-5.6-sol", "openai_reasoning_effort", "reasoning_effort"),
        ("openai_compatible", "custom-v1", "openai_reasoning_effort", "reasoning_effort"),
        ("azure", "deployment-v1", "openai_reasoning_effort", "reasoning_effort"),
        ("deepseek", "deepseek-v4-pro", None, "reasoning_effort"),
        ("google", "gemini-3.5-flash", "google_thinking_level", "thinking_level"),
        ("anthropic", "claude-sonnet-5", "anthropic_effort", "effort"),
    ],
)
def test_provider_native_mapping(provider, model, legacy_key, native):
    config = _config(provider, quick="high")
    config["quick_think_llm"] = model
    with pytest.warns(RuntimeWarning) if "custom" in model or "deployment" in model else _no_warn():
        result = resolve_reasoning_effort(config, "quick")
    assert result.native_parameter == native
    assert result.kwargs == {native: "high"}
    if legacy_key is not None:
        assert legacy_key in config


class _no_warn:
    def __enter__(self):
        return None

    def __exit__(self, exc_type, exc, traceback):
        return False


@pytest.mark.parametrize("model", ["gpt-5.6", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"])
def test_gpt_56_has_six_native_levels(model):
    assert model_effort_levels("openai", model) == (
        "none",
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    )


@pytest.mark.parametrize("model", ["gpt-5.5", "gpt-5.4", "gpt-5.4-mini"])
def test_max_is_omitted_for_registered_older_models(model):
    config = _config(quick="max")
    config["quick_think_llm"] = model
    with pytest.warns(RuntimeWarning, match="does not support"):
        result = resolve_reasoning_effort(config, "quick")
    assert result.kwargs == {}


def test_max_is_not_generalized_to_o_series():
    config = _config(quick="max")
    config["quick_think_llm"] = "o3-mini"
    with pytest.warns(RuntimeWarning, match="does not support"):
        result = resolve_reasoning_effort(config, "quick")
    assert result.kwargs == {}


def test_parenthesized_proxy_id_stays_opaque_and_passes_through():
    config = _config(quick="max")
    config["quick_think_llm"] = "gpt-5.6-luna(max)"
    with pytest.warns(RuntimeWarning, match="not in the openai"):
        result = resolve_reasoning_effort(config, "quick")
    assert result.model == "gpt-5.6-luna(max)"
    assert result.kwargs == {"reasoning_effort": "max"}


def test_invalid_provider_value_raises():
    with pytest.raises(ValueError, match="Invalid reasoning effort"):
        resolve_reasoning_effort(_config(quick="ultra"), "quick")


def test_known_unsupported_model_warns_and_omits():
    config = _config(quick="low")
    config["quick_think_llm"] = "gpt-4.1"
    with pytest.warns(RuntimeWarning, match="known not to support"):
        result = resolve_reasoning_effort(config, "quick")
    assert result.kwargs == {}


@pytest.mark.parametrize(
    "provider,model",
    [
        ("openai_compatible", "gpt-4-reasoning-custom"),
        ("azure", "gpt-4-production-deployment"),
    ],
)
def test_openai_compatible_model_ids_are_opaque(provider, model):
    config = _config(provider, quick="high")
    config["quick_think_llm"] = model
    with pytest.warns(RuntimeWarning, match=f"not in the {provider}"):
        result = resolve_reasoning_effort(config, "quick")
    assert result.kwargs == {"reasoning_effort": "high"}


def test_unsupported_provider_warns_and_omits_explicit_value():
    with pytest.warns(RuntimeWarning, match="does not support"):
        result = resolve_reasoning_effort(_config("xai", quick="high"), "quick")
    assert result.kwargs == {}


@pytest.mark.parametrize(
    ("model", "levels"),
    [
        ("deepseek-v4-flash", ("low", "high", "max")),
        ("deepseek-v4-pro", ("high", "max")),
        ("deepseek-reasoner", ("high", "max")),
    ],
)
def test_deepseek_thinking_models_expose_effective_levels(model, levels):
    assert model_effort_levels("deepseek", model) == levels
    config = _config("deepseek", quick="max")
    config["quick_think_llm"] = model
    result = resolve_reasoning_effort(config, "quick")
    assert result.kwargs == {"reasoning_effort": "max"}


def test_deepseek_chat_is_known_non_thinking_alias():
    config = _config("deepseek", quick="high")
    config["quick_think_llm"] = "deepseek-chat"
    with pytest.warns(RuntimeWarning, match="known not to support"):
        result = resolve_reasoning_effort(config, "quick")
    assert result.kwargs == {}


def test_deepseek_flash_accepts_native_low_effort():
    config = _config("deepseek", quick="low")
    config["quick_think_llm"] = "deepseek-v4-flash"
    result = resolve_reasoning_effort(config, "quick")
    assert result.kwargs == {"reasoning_effort": "low"}


def test_deepseek_pro_omits_effectively_duplicated_low_effort():
    config = _config("deepseek", quick="low")
    config["quick_think_llm"] = "deepseek-v4-pro"
    with pytest.warns(RuntimeWarning, match="does not support"):
        result = resolve_reasoning_effort(config, "quick")
    assert result.kwargs == {}


def test_deepseek_does_not_inherit_openai_legacy_effort():
    config = _config("deepseek", legacy="high")
    config["quick_think_llm"] = "deepseek-v4-flash"
    result = resolve_reasoning_effort(config, "quick")
    assert result.source == "provider_default"
    assert result.kwargs == {}


def test_gemini_pro_minimal_compatibility_mapping():
    config = _config("google", quick="minimal")
    config["quick_think_llm"] = "gemini-3.1-pro-preview"
    with pytest.warns(RuntimeWarning, match="using 'low'"):
        result = resolve_reasoning_effort(config, "quick")
    assert result.kwargs == {"thinking_level": "low"}
