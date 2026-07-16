"""Resolve role-specific reasoning effort to provider-native parameters.

This module deliberately keeps capability policy separate from the CLI model
catalog.  The catalog is a presentation surface; this table controls whether a
wire parameter is safe to send.
"""

from __future__ import annotations

import re
import warnings
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

PROVIDER_DEFAULT = "provider_default"
RESOLVED_MARKER = "_reasoning_effort_resolved"

_NATIVE_PARAMETERS = {
    "openai": "reasoning_effort",
    "openai_compatible": "reasoning_effort",
    "azure": "reasoning_effort",
    "deepseek": "reasoning_effort",
    "google": "thinking_level",
    "anthropic": "effort",
}

_LEGACY_KEYS = {
    "openai": "openai_reasoning_effort",
    "openai_compatible": "openai_reasoning_effort",
    "azure": "openai_reasoning_effort",
    "google": "google_thinking_level",
    "anthropic": "anthropic_effort",
}

_PROVIDER_LEVELS = {
    "openai": ("none", "minimal", "low", "medium", "high", "xhigh", "max"),
    "openai_compatible": (
        "none",
        "minimal",
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    ),
    "azure": ("none", "minimal", "low", "medium", "high", "xhigh", "max"),
    "deepseek": ("high", "max"),
    "google": ("minimal", "low", "medium", "high"),
    "anthropic": ("low", "medium", "high", "xhigh", "max"),
}

_OPENAI_MODEL_LEVELS = {
    "gpt-5.6": ("none", "low", "medium", "high", "xhigh", "max"),
    "gpt-5.6-sol": ("none", "low", "medium", "high", "xhigh", "max"),
    "gpt-5.6-terra": ("none", "low", "medium", "high", "xhigh", "max"),
    "gpt-5.6-luna": ("none", "low", "medium", "high", "xhigh", "max"),
    "gpt-5.5": ("none", "low", "medium", "high", "xhigh"),
    "gpt-5.5-pro": ("medium", "high", "xhigh"),
    "gpt-5.4": ("none", "low", "medium", "high", "xhigh"),
    "gpt-5.4-mini": ("none", "low", "medium", "high", "xhigh"),
    "gpt-5.4-nano": ("none", "low", "medium", "high", "xhigh"),
    "gpt-5.2": ("none", "low", "medium", "high", "xhigh"),
}

_DEEPSEEK_MODEL_LEVELS = {
    "deepseek-v4-flash": ("high", "max"),
    "deepseek-v4-pro": ("high", "max"),
    "deepseek-reasoner": ("high", "max"),
}

_GOOGLE_MODEL_LEVELS = {
    "gemini-3.5-flash": ("minimal", "low", "medium", "high"),
    "gemini-3.1-flash-lite": ("minimal", "low", "medium", "high"),
    "gemini-3.1-pro-preview": ("low", "medium", "high"),
}

_ANTHROPIC_EXACT = {"claude-mythos-preview", "claude-mythos-5"}
_ANTHROPIC_MODEL = re.compile(r"^claude-(opus|sonnet|fable)-(\d+)(?:-(\d+))?$")
_ANTHROPIC_MIN_VERSION = {"opus": (4, 5), "sonnet": (4, 6), "fable": (5, 0)}
_ANTHROPIC_XHIGH_MIN_VERSION = {
    "opus": (4, 7),
    "sonnet": (5, 0),
    "fable": (5, 0),
}


@dataclass(frozen=True)
class ReasoningEffortResolution:
    """A resolved role value and the provider-native wire representation."""

    role: str
    provider: str
    model: str
    source: str
    requested: str | None
    value: str | None
    native_parameter: str | None
    omitted_reason: str | None = None

    @property
    def kwargs(self) -> dict[str, str]:
        if self.native_parameter is None or self.value is None:
            return {}
        return {self.native_parameter: self.value}

    @property
    def display_value(self) -> str:
        return self.value if self.value is not None else "omitted"


def _normalize(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    return normalized or None


def legacy_config_key(provider: str) -> str | None:
    """Return the legacy shared config key for a provider."""
    return _LEGACY_KEYS.get(provider.strip().lower())


def provider_effort_levels(provider: str) -> tuple[str, ...]:
    """Return the provider-native value domain, excluding the omit sentinel."""
    return _PROVIDER_LEVELS.get(provider.strip().lower(), ())


def _anthropic_levels(model: str) -> tuple[bool | None, tuple[str, ...]]:
    if model in _ANTHROPIC_EXACT:
        return True, ("low", "medium", "high", "max")
    if "haiku" in model:
        return False, ()
    match = _ANTHROPIC_MODEL.fullmatch(model)
    if not match:
        if model.startswith("claude-"):
            return None, _PROVIDER_LEVELS["anthropic"]
        return None, _PROVIDER_LEVELS["anthropic"]
    family = match.group(1)
    version = (int(match.group(2)), int(match.group(3) or 0))
    if version < _ANTHROPIC_MIN_VERSION[family]:
        return False, ()
    levels = ["low", "medium", "high"]
    if version >= _ANTHROPIC_XHIGH_MIN_VERSION[family]:
        levels.append("xhigh")
    levels.append("max")
    return True, tuple(levels)


def model_effort_levels(provider: str, model: str) -> tuple[str, ...]:
    """Return levels supported by a known model, or the provider domain if custom.

    An empty tuple means the model is known not to support the parameter.
    Unknown/custom IDs intentionally return the provider domain because the
    runtime policy is warning plus pass-through.
    """
    provider = provider.strip().lower()
    model = model.strip().lower()
    if provider in {"openai", "openai_compatible", "azure"}:
        if model in _OPENAI_MODEL_LEVELS:
            return _OPENAI_MODEL_LEVELS[model]
        if re.match(r"^o[1-9](?:-|$)", model):
            return ("low", "medium", "high")
        if model.startswith(("gpt-4", "gpt-3")):
            return ()
        return _PROVIDER_LEVELS.get(provider, ())
    if provider == "deepseek":
        if model in _DEEPSEEK_MODEL_LEVELS:
            return _DEEPSEEK_MODEL_LEVELS[model]
        if model == "deepseek-chat":
            return ()
        return _PROVIDER_LEVELS[provider]
    if provider == "google":
        if model in _GOOGLE_MODEL_LEVELS:
            return _GOOGLE_MODEL_LEVELS[model]
        if model.startswith("gemini-") and not model.startswith("gemini-3"):
            return ()
        return _PROVIDER_LEVELS[provider]
    if provider == "anthropic":
        return _anthropic_levels(model)[1]
    return ()


def _model_status(provider: str, model: str) -> bool | None:
    """Return True for known support, False for known unsupported, else None."""
    if provider in {"openai", "openai_compatible", "azure"}:
        if model in _OPENAI_MODEL_LEVELS or re.match(r"^o[1-9](?:-|$)", model):
            return True
        if model.startswith(("gpt-4", "gpt-3")):
            return False
        return None
    if provider == "deepseek":
        if model in _DEEPSEEK_MODEL_LEVELS:
            return True
        if model == "deepseek-chat":
            return False
        return None
    if provider == "google":
        if model in _GOOGLE_MODEL_LEVELS:
            return True
        if model.startswith("gemini-") and not model.startswith("gemini-3"):
            return False
        return None
    if provider == "anthropic":
        return _anthropic_levels(model)[0]
    return None


def resolve_native_reasoning_value(
    provider: str,
    model: str,
    value: Any,
    *,
    warn: bool = True,
) -> str | None:
    """Validate and normalize one provider-native value for one model."""
    provider = provider.strip().lower()
    model = model.strip().lower()
    value = _normalize(value)
    if value is None or value == PROVIDER_DEFAULT:
        return None

    allowed_provider_values = _PROVIDER_LEVELS.get(provider)
    if allowed_provider_values is None:
        if warn:
            warnings.warn(
                f"Provider {provider!r} does not support an explicit reasoning effort; "
                "the value will be omitted.",
                RuntimeWarning,
                stacklevel=2,
            )
        return None
    if value not in allowed_provider_values:
        allowed = ", ".join(allowed_provider_values)
        raise ValueError(
            f"Invalid reasoning effort {value!r} for provider {provider!r}; "
            f"expected one of: {allowed}, {PROVIDER_DEFAULT}"
        )

    status = _model_status(provider, model)
    if status is False:
        if warn:
            warnings.warn(
                f"Model {model!r} is known not to support "
                f"{_NATIVE_PARAMETERS[provider]}; the value will be omitted.",
                RuntimeWarning,
                stacklevel=2,
            )
        return None

    # Compatibility with the existing Gemini Pro behavior: Pro has no
    # ``minimal`` level, so preserve the established nearest-level mapping.
    if provider == "google" and model == "gemini-3.1-pro-preview" and value == "minimal":
        if warn:
            warnings.warn(
                "Gemini 3.1 Pro does not support thinking_level='minimal'; "
                "using 'low' for compatibility.",
                RuntimeWarning,
                stacklevel=2,
            )
        return "low"

    model_levels = model_effort_levels(provider, model)
    if status is True and value not in model_levels:
        if warn:
            warnings.warn(
                f"Model {model!r} does not support {_NATIVE_PARAMETERS[provider]}="
                f"{value!r}; the value will be omitted.",
                RuntimeWarning,
                stacklevel=2,
            )
        return None
    if status is None and warn:
        warnings.warn(
            f"Model {model!r} is not in the {provider} reasoning capability table; "
            f"passing {_NATIVE_PARAMETERS[provider]}={value!r} through unchanged.",
            RuntimeWarning,
            stacklevel=2,
        )
    return value


def resolve_reasoning_effort(
    config: Mapping[str, Any],
    role: str,
    model: str | None = None,
    *,
    warn: bool = True,
) -> ReasoningEffortResolution:
    """Resolve a quick/deep config value using role, legacy, default precedence."""
    role = role.strip().lower()
    if role not in {"quick", "deep"}:
        raise ValueError(f"role must be 'quick' or 'deep', got {role!r}")
    provider = str(config.get("llm_provider", "")).strip().lower()
    model = str(model or config.get(f"{role}_think_llm", "")).strip()
    role_key = f"{role}_reasoning_effort"
    requested = _normalize(config.get(role_key))
    source = role_key

    if requested is None:
        legacy_key = legacy_config_key(provider)
        requested = _normalize(config.get(legacy_key)) if legacy_key else None
        source = legacy_key or "provider_default"
    if requested is None:
        source = "provider_default"
        requested = PROVIDER_DEFAULT

    native_parameter = _NATIVE_PARAMETERS.get(provider)
    if requested == PROVIDER_DEFAULT:
        return ReasoningEffortResolution(
            role, provider, model, source, requested, None, native_parameter, "provider default"
        )

    value = resolve_native_reasoning_value(provider, model, requested, warn=warn)
    omitted_reason = None if value is not None else "unsupported"
    return ReasoningEffortResolution(
        role,
        provider,
        model,
        source,
        requested,
        value,
        native_parameter,
        omitted_reason,
    )
