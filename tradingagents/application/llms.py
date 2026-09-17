"""Run-scoped LLM construction without graph or persistence side effects."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from tradingagents.llm_clients import create_llm_client
from tradingagents.llm_clients.reasoning_effort import (
    RESOLVED_MARKER,
    resolve_reasoning_effort,
)

from .settings import RunSettings


@dataclass(frozen=True)
class RunLLMs:
    """Reasoning clients plus schema-focused serializers for one run."""

    quick: Any
    deep: Any
    quick_serializer: Any
    deep_serializer: Any


def create_run_llms(
    settings: RunSettings,
    *,
    callbacks: list[Any] | None = None,
    purpose: Literal["full", "incremental"] = "full",
) -> RunLLMs:
    from tradingagents.llm_clients.connections import build_model, serializer_overrides

    if settings.deep_binding is not None and settings.quick_binding is not None:

        def pair(binding):
            options = {
                "temperature": settings.temperature,
                "max_retries": settings.llm_max_retries,
                "callbacks": callbacks,
            }
            model = build_model(binding, **options)
            serial = (
                build_model(binding, serializer=True, **options)
                if serializer_overrides(binding.connection.compatibility, binding.model)
                else model
            )
            return model, serial

        deep, deep_serializer = pair(settings.deep_binding)
        quick, quick_serializer = (
            (deep, deep_serializer) if purpose == "incremental" else pair(settings.quick_binding)
        )
        return RunLLMs(quick, deep, quick_serializer, deep_serializer)

    config = {
        **dict(settings.data_config),
        "llm_provider": settings.llm_provider,
        "quick_think_llm": settings.quick_model,
        "deep_think_llm": settings.deep_model,
        "backend_url": settings.backend_url,
        "quick_reasoning_effort": settings.quick_reasoning_effort,
        "deep_reasoning_effort": settings.deep_reasoning_effort,
        "temperature": settings.temperature,
        "llm_max_retries": settings.llm_max_retries,
    }
    common: dict[str, Any] = {"connection": settings.connection or {}}
    if settings.temperature is not None:
        common["temperature"] = float(settings.temperature)
    if settings.llm_max_retries is not None:
        common["max_retries"] = int(settings.llm_max_retries)
    if callbacks:
        common["callbacks"] = callbacks

    def role_kwargs(role: str) -> dict[str, Any]:
        kwargs = dict(common)
        resolution = resolve_reasoning_effort(config, role)
        kwargs.update(resolution.kwargs)
        if resolution.kwargs:
            kwargs[RESOLVED_MARKER] = True
        return kwargs

    def serializer(model: str, fallback: Any) -> Any:
        overrides = serializer_overrides(settings.llm_provider, model)
        if overrides is None:
            return fallback
        kwargs = {key: value for key, value in common.items() if key != "temperature"}
        kwargs.update(overrides)
        return create_llm_client(
            provider=settings.llm_provider,
            model=model,
            base_url=settings.backend_url,
            **kwargs,
        ).get_llm()

    if purpose == "incremental":
        deep = create_llm_client(
            provider=settings.llm_provider,
            model=settings.deep_model,
            base_url=settings.backend_url,
            **role_kwargs("deep"),
        ).get_llm()
        deep_serializer = serializer(settings.deep_model, deep)
        return RunLLMs(
            quick=deep,
            deep=deep,
            quick_serializer=deep_serializer,
            deep_serializer=deep_serializer,
        )
    quick = create_llm_client(
        provider=settings.llm_provider,
        model=settings.quick_model,
        base_url=settings.backend_url,
        **role_kwargs("quick"),
    ).get_llm()
    deep = create_llm_client(
        provider=settings.llm_provider,
        model=settings.deep_model,
        base_url=settings.backend_url,
        **role_kwargs("deep"),
    ).get_llm()
    return RunLLMs(
        quick=quick,
        deep=deep,
        quick_serializer=serializer(settings.quick_model, quick),
        deep_serializer=serializer(settings.deep_model, deep),
    )
