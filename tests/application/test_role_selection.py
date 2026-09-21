"""One nested role contract is shared by requests and saved defaults."""

import pytest
from pydantic import ValidationError

from tradingagents.configuration.models import ConfigurationPatch
from tradingagents.domain.runs import AnalysisRequest
from tradingagents.persistence.configuration import ConfigurationStore
from tradingagents.persistence.migrations import upgrade_database


@pytest.fixture
def roles(app_settings):
    upgrade_database(app_settings)
    store = ConfigurationStore(app_settings)
    store.save(ConfigurationPatch(
        revision=0,
        connection_changes=[
            {'action': 'create', 'id': name, 'preset': 'openai', 'credentials': {'api_key': name + '-secret'}}
            for name in ('quick-connection', 'deep-connection')
        ],
        values={'models': {
            'quick': {'connection_id': 'quick-connection', 'model': 'gpt-5.4-mini', 'reasoning_effort': 'low'},
            'deep': {'connection_id': 'deep-connection', 'model': 'gpt-5.5', 'reasoning_effort': 'high'},
        }},
    ), initialize=True)
    return store


@pytest.mark.parametrize('models', [None, {}, {'deep': None}, {'deep': {'model': None}}])
def test_null_and_omitted_role_fields_inherit_saved_defaults(roles, models):
    request, resolved = roles.resolve_request(AnalysisRequest(ticker='GOOG', analysis_date='2026-09-10', models=models))
    assert request.models.deep.model == 'gpt-5.5'
    assert request.models.deep.connection_id == 'deep-connection'
    assert resolved.deep_binding.reasoning_effort == 'high'
    assert resolved.quick_binding.connection.id == 'quick-connection'


def test_provider_default_overrides_a_saved_effort(roles):
    _, resolved = roles.resolve_request(AnalysisRequest(
        ticker='GOOG', analysis_date='2026-09-10', models={'deep': {'reasoning_effort': 'provider_default'}},
    ))
    assert resolved.deep_binding.reasoning_effort == 'provider_default'


def test_incremental_resolves_only_deep_and_has_no_quick_binding(roles):
    request, resolved = roles.resolve_request(AnalysisRequest(
        ticker='GOOG', analysis_date='2026-09-10', research_kind='incremental', full_baseline_run_id='baseline',
    ))
    assert request.models.quick is None
    assert resolved.quick_binding is None
    assert resolved.deep_binding.connection.id == 'deep-connection'


def test_incremental_rejects_an_explicit_quick_role():
    with pytest.raises(ValidationError, match='quick'):
        AnalysisRequest(ticker='GOOG', analysis_date='2026-09-10', research_kind='incremental',
                        full_baseline_run_id='baseline', models={'quick': {'model': 'unused'}})


@pytest.mark.parametrize('field', ['asset_type', 'llm_provider', 'connection_id', 'quick_model', 'deep_connection_id'])
def test_retired_request_fields_are_rejected(field):
    with pytest.raises(ValidationError, match=field):
        AnalysisRequest(ticker='GOOG', analysis_date='2026-09-10', **{field: 'legacy'})
