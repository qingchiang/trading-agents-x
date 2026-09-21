"""Construct clients exclusively from immutable role bindings."""

from dataclasses import dataclass
from typing import Any, Literal

from tradingagents.configuration.settings import RunSettings
from tradingagents.llm.connections import build_model, serializer_overrides


@dataclass(frozen=True)
class RunLLMs:
    quick: Any | None
    deep: Any
    quick_serializer: Any | None
    deep_serializer: Any


def create_run_llms(settings: RunSettings, *, callbacks=None, purpose: Literal['full', 'incremental'] = 'full') -> RunLLMs:
    def pair(binding):
        options = {'temperature': settings.temperature, 'max_retries': settings.llm_max_retries, 'callbacks': callbacks}
        model = build_model(binding, **options)
        serializer = build_model(binding, serializer=True, **options) if serializer_overrides(binding.connection.compatibility, binding.model) else model
        return model, serializer

    deep, deep_serializer = pair(settings.deep_binding)
    if purpose == 'incremental':
        return RunLLMs(None, deep, None, deep_serializer)
    if settings.quick_binding is None:
        raise ValueError('Full Research requires a quick model binding')
    quick, quick_serializer = pair(settings.quick_binding)
    return RunLLMs(quick, deep, quick_serializer, deep_serializer)
