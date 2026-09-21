"""Frozen 0013-to-current projections, isolated from ordinary reads and execution."""

from copy import deepcopy

from tradingagents.domain.runs import AnalysisRequest

# These are predecessor defaults, not values read from the running installation.
PREDECESSOR_MODELS = {'quick': 'gpt-5.4-mini', 'deep': 'gpt-5.5'}
RETIRED_CONFIGURATION_FIELDS = {
    'llm_provider', 'providers', 'quick_connection_id', 'deep_connection_id',
    'quick_think_llm', 'deep_think_llm', 'quick_reasoning_effort', 'deep_reasoning_effort',
    'openai_reasoning_effort', 'google_thinking_level', 'anthropic_effort',
}


def request_projection(request, config):
    result = {key: value for key, value in request.items() if key in AnalysisRequest.model_fields}
    roles = ('deep',) if request.get('research_kind') == 'incremental' else ('quick', 'deep')
    result['models'] = {}
    for role in roles:
        binding = config.get(f'{role}_binding') or {}
        result['models'][role] = {
            'connection_id': request.get(f'{role}_connection_id') or request.get('connection_id') or (binding.get('connection') or {}).get('id'),
            'model': request.get(f'{role}_model') or binding.get('model') or config.get(f'{role}_model'),
            'reasoning_effort': request.get(f'{role}_reasoning_effort') if request.get(f'{role}_reasoning_effort') is not None else binding.get('reasoning_effort', config.get(f'{role}_reasoning_effort')),
        }
    return result


def config_projection(config, research_kind):
    result = {key: deepcopy(config.get(key)) for key in (
        'profile', 'temperature', 'llm_max_retries', 'output_language', 'data_config',
    )}
    result['research_kind'] = research_kind
    result['quick_binding'] = deepcopy(config.get('quick_binding')) if research_kind == 'full' else None
    result['deep_binding'] = deepcopy(config.get('deep_binding'))
    for role in ('quick_binding', 'deep_binding'):
        if result[role] and result[role].get('connection'):
            result[role]['connection'] = connection_projection(result[role]['connection'])
    # Only fully recorded bindings/settings form an executable projection.
    # Original audit JSON is never passed to the current execution validator.
    complete = bool(result['deep_binding']) and (research_kind == 'incremental' or bool(result['quick_binding']))
    complete = complete and all(config.get(key) is not None for key in ('profile', 'output_language', 'data_config'))
    result['snapshot_version'] = 1 if complete else 0
    if isinstance(result['data_config'], dict):
        for key in RETIRED_CONFIGURATION_FIELDS | {'backend_url'}:
            result['data_config'].pop(key, None)
    return result


def configuration_projection(values, legacy_identities):
    result = {key: value for key, value in values.items() if key not in RETIRED_CONFIGURATION_FIELDS}
    result['models'] = {
        role: {
            'connection_id': values.get(f'{role}_connection_id') or legacy_identities.get(values.get('llm_provider', 'openai')),
            'model': values.get(f'{role}_think_llm', PREDECESSOR_MODELS[role]),
            'reasoning_effort': values.get(f'{role}_reasoning_effort'),
        }
        for role in ('quick', 'deep')
    }
    return result


def submission_projection(identity, legacy_identities):
    if identity is None:
        return None
    if identity.get('version') != 1 or not isinstance(identity.get('request'), dict):
        return None
    request = identity['request']
    payload = {key: value for key, value in request.items() if key in AnalysisRequest.model_fields and value is not None}
    roles = ('deep',) if request.get('research_kind') == 'incremental' else ('quick', 'deep')
    selected = {}
    provider = request.get('llm_provider')
    if provider and provider not in legacy_identities:
        return None
    for role in roles:
        fields = {
            'connection_id': request.get(f'{role}_connection_id') or request.get('connection_id') or legacy_identities.get(provider),
            'model': request.get(f'{role}_model'),
            'reasoning_effort': request.get(f'{role}_reasoning_effort'),
        }
        fields = {key: value for key, value in fields.items() if value is not None}
        if fields:
            selected[role] = fields
    if selected:
        payload['models'] = selected
    return {'version': 2, 'request': payload, 'source_run_id': identity.get('source_run_id')}


def connection_projection(definition):
    """Translate only recorded defaults; preserve the reset template's own policy."""
    result = deepcopy(definition)
    if 'reasoning_defaults' in result:
        defaults = result.pop('reasoning_defaults') or {}
        key = {
            'openai': 'openai_reasoning_effort',
            'openai_compatible': 'openai_reasoning_effort',
            'azure': 'openai_reasoning_effort',
            'google': 'google_thinking_level',
            'anthropic': 'anthropic_effort',
        }.get(result.get('compatibility'))
        result['reasoning_effort'] = defaults.get(key) if key else None
    if 'template' in result:
        result['template'] = connection_projection(result['template'])
    return result
