"""Selected model bindings preserve native reasoning capability policy."""

from contextlib import nullcontext

import pytest

from tradingagents.llm.reasoning_effort import (
    PROVIDER_DEFAULT,
    model_effort_levels,
    resolve_reasoning_effort,
)


@pytest.mark.parametrize(
    'selected,default,source,expected',
    [
        (' XHIGH ', 'low', 'role', {'reasoning_effort': 'xhigh'}),
        (None, ' HIGH ', 'connection', {'reasoning_effort': 'high'}),
        (PROVIDER_DEFAULT, 'high', 'role', {}),
        (None, None, 'provider_default', {}),
    ],
)
def test_binding_precedence_and_normalization(selected, default, source, expected):
    result = resolve_reasoning_effort(
        'openai', 'gpt-5.6-sol', selected, connection_default=default,
    )
    assert result.source == source
    assert result.kwargs == expected
    if not expected:
        assert result.display_value == 'omitted'


@pytest.mark.parametrize(
    'provider,model,native,unknown',
    [
        ('openai', 'gpt-5.6-sol', 'reasoning_effort', False),
        ('openai_compatible', 'custom-v1', 'reasoning_effort', True),
        ('azure', 'deployment-v1', 'reasoning_effort', True),
        ('deepseek', 'deepseek-v4-pro', 'reasoning_effort', False),
        ('google', 'gemini-3.5-flash', 'thinking_level', False),
        ('anthropic', 'claude-sonnet-5', 'effort', False),
    ],
)
def test_provider_native_mapping(provider, model, native, unknown):
    with pytest.warns(RuntimeWarning) if unknown else nullcontext():
        result = resolve_reasoning_effort(provider, model, 'high')
    assert result.native_parameter == native
    assert result.kwargs == {native: 'high'}


@pytest.mark.parametrize('model', ['gpt-5.6', 'gpt-5.6-sol', 'gpt-5.6-terra', 'gpt-5.6-luna'])
def test_gpt_56_has_six_native_levels(model):
    assert model_effort_levels('openai', model) == (
        'none', 'low', 'medium', 'high', 'xhigh', 'max',
    )


@pytest.mark.parametrize(
    'provider,model,selected,warning',
    [
        ('openai', 'gpt-5.5', 'max', 'does not support'),
        ('openai', 'gpt-5.4', 'max', 'does not support'),
        ('openai', 'gpt-5.4-mini', 'max', 'does not support'),
        ('openai', 'o3-mini', 'max', 'does not support'),
        ('openai', 'gpt-4.1', 'low', 'known not to support'),
        ('xai', 'grok-custom', 'high', 'does not support'),
        ('deepseek', 'deepseek-chat', 'high', 'known not to support'),
        ('deepseek', 'deepseek-v4-pro', 'low', 'does not support'),
    ],
)
def test_unsupported_parameter_is_omitted(provider, model, selected, warning):
    with pytest.warns(RuntimeWarning, match=warning):
        result = resolve_reasoning_effort(provider, model, selected)
    assert result.kwargs == {}


@pytest.mark.parametrize(
    'provider,model,effort',
    [
        ('openai', 'gpt-5.6-luna(max)', 'max'),
        ('openai_compatible', 'gpt-4-reasoning-custom', 'high'),
        ('azure', 'gpt-4-production-deployment', 'high'),
    ],
)
def test_custom_model_identity_is_opaque(provider, model, effort):
    with pytest.warns(RuntimeWarning, match=f'not in the {provider}'):
        result = resolve_reasoning_effort(provider, model, effort)
    assert result.model == model
    assert result.kwargs == {'reasoning_effort': effort}


def test_invalid_provider_value_raises():
    with pytest.raises(ValueError, match='Invalid reasoning effort'):
        resolve_reasoning_effort('openai', 'gpt-5.6-sol', 'ultra')


@pytest.mark.parametrize(
    'model,levels',
    [
        ('deepseek-v4-flash', ('low', 'high', 'max')),
        ('deepseek-v4-pro', ('high', 'max')),
        ('deepseek-reasoner', ('high', 'max')),
    ],
)
def test_deepseek_thinking_models_expose_effective_levels(model, levels):
    assert model_effort_levels('deepseek', model) == levels
    result = resolve_reasoning_effort('deepseek', model, 'max')
    assert result.kwargs == {'reasoning_effort': 'max'}


def test_deepseek_flash_accepts_native_low_effort():
    result = resolve_reasoning_effort('deepseek', 'deepseek-v4-flash', 'low')
    assert result.kwargs == {'reasoning_effort': 'low'}


def test_gemini_pro_minimal_mapping_preserves_request_policy():
    with pytest.warns(RuntimeWarning, match="using 'low'"):
        result = resolve_reasoning_effort('google', 'gemini-3.1-pro-preview', 'minimal')
    assert result.kwargs == {'thinking_level': 'low'}
