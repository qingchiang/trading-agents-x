"""Explicit environment import; legacy aliases never participate in execution."""

import json
from dataclasses import dataclass
from hashlib import sha256
from io import StringIO
from typing import Literal
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid5

from dotenv import dotenv_values
from pydantic import SecretStr, ValidationError, field_validator

from tradingagents.configuration.models import (
    ConfigurationModel,
    ConfigurationPatch,
    ConfigurationValues,
    ImportIssue,
)
from tradingagents.configuration.resolution import credential_owners
from tradingagents.llm.models import ConnectionChange, preset_connection
from tradingagents.llm.provider_registry import PROVIDER_REGISTRY


class ImportedTransportOptions(ConfigurationModel):
    base_url: str | None = None
    deployment: str | None = None
    api_version: str | None = None
    region: str = "us-west-2"
    auth_mode: Literal["bearer", "system", "static"] = "system"
    aws_profile: str | None = None

    @field_validator("base_url")
    @classmethod
    def endpoint(cls, value):
        if value is not None:
            parsed = urlsplit(value)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("Use an HTTP(S) service address without embedded credentials")
        return value.rstrip("/") if value else None


ENV_FIELDS = {
    'TRADINGAGENTS_LLM_PROVIDER': 'provider',
    'TRADINGAGENTS_QUICK_THINK_LLM': 'models.quick.model',
    'TRADINGAGENTS_DEEP_THINK_LLM': 'models.deep.model',
    'TRADINGAGENTS_QUICK_REASONING_EFFORT': 'models.quick.reasoning_effort',
    'TRADINGAGENTS_DEEP_REASONING_EFFORT': 'models.deep.reasoning_effort',
    'TRADINGAGENTS_OUTPUT_LANGUAGE': 'output_language',
    'TRADINGAGENTS_TEMPERATURE': 'temperature',
    'TRADINGAGENTS_LLM_MAX_RETRIES': 'llm_max_retries',
    'TRADINGAGENTS_TICKER_NEWS_LOOKBACK_DAYS': 'ticker_news_lookback_days',
    'TRADINGAGENTS_SOCIAL_LOOKBACK_DAYS': 'social_lookback_days',
    'TRADINGAGENTS_TRASH_RETENTION_DAYS': 'trash_retention_days',
    'TRADINGAGENTS_OPENAI_REASONING_EFFORT': 'openai_reasoning_effort',
    'TRADINGAGENTS_GOOGLE_THINKING_LEVEL': 'google_thinking_level',
    'TRADINGAGENTS_ANTHROPIC_EFFORT': 'anthropic_effort',
}
BOOTSTRAP_ENV = {
    'TRADINGAGENTS_' + field for field in (
        'HOME', 'DATABASE_PATH', 'CACHE_DIR', 'HOST', 'PORT', 'WORKER_POLL_SECONDS',
        'LEASE_SECONDS', 'SQLITE_BUSY_TIMEOUT_MS', 'PUBLISH_HOST', 'WEB_PORT',
        'ENV_FILE', 'LAN_ENABLED', 'LAN_TOKEN', 'SESSION_SECRET',
    )
}


@dataclass
class ImportedConfiguration:
    patch: ConfigurationPatch
    issues: list[ImportIssue]
    fingerprint: str
    targets: dict[str, str]
    credentials: dict[str, bool]


def parse_import(settings, request, existing):
    environment, uploaded = {}, set()
    for content, original in ((request.enterprise, settings.import_enterprise), (request.primary, settings.import_primary)):
        parsed = dotenv_values(stream=StringIO(content.get_secret_value()), interpolate=False) if content is not None else {
            key: value.get_secret_value() for key, value in original.items()
        }
        environment.update({key: value for key, value in parsed.items() if value is not None})
        uploaded.update(parsed)
    environment.update({key: value.get_secret_value() for key, value in settings.import_environment.items()})
    environment = {key: value for key, value in environment.items() if key not in request.exclude and value != ''}
    fingerprint = sha256(json.dumps(environment, sort_keys=True).encode()).hexdigest()
    provider = environment.get('TRADINGAGENTS_LLM_PROVIDER', 'openai')
    issues, values, source_secrets, targets, selected, overrides, auth, native = [], {}, {}, {}, {}, {}, {}, {}
    connection_fields = {
        'OLLAMA_BASE_URL': ('ollama', 'base_url'),
        'AZURE_OPENAI_ENDPOINT': ('azure', 'base_url'),
        'AZURE_OPENAI_DEPLOYMENT_NAME': ('azure', 'deployment'),
        'OPENAI_API_VERSION': ('azure', 'api_version'),
        'AWS_DEFAULT_REGION': ('bedrock', 'region'), 'AWS_REGION': ('bedrock', 'region'),
        'AWS_PROFILE': ('bedrock', 'aws_profile'),
        'TRADINGAGENTS_LLM_BACKEND_URL': (provider, 'base_url'),
    }
    aliases = legacy_credential_fields()
    for name, value in environment.items():
        if name in aliases:
            owner, field = aliases[name]
            auth.setdefault(owner, {})[field] = SecretStr(value)
        elif name in credential_owners():
            source_secrets[name] = SecretStr(value)
        elif name in connection_fields:
            owner, field = connection_fields[name]
            overrides.setdefault(owner, {})[field] = value
        elif name in ENV_FIELDS:
            field = ENV_FIELDS[name]
            if field == 'provider':
                if value not in PROVIDER_REGISTRY:
                    issues.append(ImportIssue(name=name, message='Unknown provider preset'))
            elif field.startswith('models.'):
                _, role, key = field.split('.')
                selected.setdefault(role, {})[key] = value
            elif field in {'openai_reasoning_effort', 'google_thinking_level', 'anthropic_effort'}:
                owner = {'openai_reasoning_effort': 'openai', 'google_thinking_level': 'google', 'anthropic_effort': 'anthropic'}[field]
                native[owner] = value
            else:
                try:
                    values[field] = getattr(ConfigurationValues.model_validate({field: value}), field)
                except (ValidationError, ValueError):
                    issues.append(ImportIssue(name=name, message='Invalid value or outside the supported range'))
        elif (name in uploaded or name.startswith('TRADINGAGENTS_')) and name not in BOOTSTRAP_ENV:
            issues.append(ImportIssue(name=name, message='Unsupported import field'))
    owners = set(overrides) | set(auth) | set(native)
    change_roles = bool(selected) or 'TRADINGAGENTS_LLM_PROVIDER' in environment or 'TRADINGAGENTS_LLM_BACKEND_URL' in environment
    if change_roles and provider in PROVIDER_REGISTRY:
        owners.add(provider)
    changes, identities = [], {}
    for owner in sorted(owners):
        matches = [connection for connection in existing.values() if connection.preset == owner]
        if len(matches) > 1:
            issues.append(ImportIssue(name=owner, message='Multiple connections match; configure these fields in Settings'))
            continue
        identity = matches[0].id if matches else ('default' if owner == 'openai' else 'import-' + uuid5(NAMESPACE_URL, 'tradingagents:env-import:' + owner).hex)
        identities[owner] = identity
        if matches and matches[0].deleted:
            issues.append(ImportIssue(name=owner, message='Original connection was deleted; configure a new connection manually'))
            continue
        fields = overrides.get(owner, {})
        if 'bearer_token' in auth.get(owner, {}):
            fields['auth_mode'] = 'bearer'
        elif {'access_key_id', 'secret_access_key'} & auth.get(owner, {}).keys():
            fields['auth_mode'] = 'static'
        try:
            ImportedTransportOptions.model_validate(fields)
            base = matches[0] if matches else preset_connection(owner, identity=identity)
            transport = {**base.transport.model_dump(), **fields}
            changes.append(ConnectionChange(
                action='update' if matches else 'create', id=identity, preset=owner,
                transport=transport, credentials=auth.get(owner, {}),
                reasoning_effort=native.get(owner, base.reasoning_effort),
            ))
        except (ValidationError, ValueError):
            issues.extend(ImportIssue(name=name, message='Invalid provider connection') for name, (p, _) in connection_fields.items() if p == owner and name in environment)
        for name, (p, field) in aliases.items():
            if p == owner and name in environment:
                targets[name] = f'{identity}.{field}'
    if change_roles and provider in identities:
        defaults = ConfigurationValues().models
        values['models'] = {
            role: {**getattr(defaults, role).model_dump(), **selected.get(role, {}), 'connection_id': identities[provider]}
            for role in ('quick', 'deep')
        }
    return ImportedConfiguration(
        ConfigurationPatch(revision=request.revision, values=values, credentials=source_secrets, connection_changes=changes),
        issues, fingerprint, targets, {name: True for name in environment if name in aliases or name in source_secrets},
    )


def legacy_credential_fields():
    from tradingagents.llm.api_key_env import PROVIDER_API_KEY_ENV

    fields = {
        name: (provider, "api_key") for provider, name in PROVIDER_API_KEY_ENV.items() if name
    }
    fields.update(
        {
            "AWS_ACCESS_KEY_ID": ("bedrock", "access_key_id"),
            "AWS_SECRET_ACCESS_KEY": ("bedrock", "secret_access_key"),
            "AWS_SESSION_TOKEN": ("bedrock", "session_token"),
            "AWS_BEARER_TOKEN_BEDROCK": ("bedrock", "bearer_token"),
        }
    )
    return fields
