"""Run-scoped LLM construction without graph or persistence side effects."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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

    def __iter__(self):
        """Keep two-value unpacking for outcome reflection and callers."""

        yield self.quick
        yield self.deep


def create_run_llms(
    settings: RunSettings,
    *,
    callbacks: list[Any] | None = None,
) -> RunLLMs:
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
    common: dict[str, Any] = {}
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

    def serializer(model: str, fallback: Any) -> Any:
        if (
            settings.llm_provider != "deepseek"
            or model not in {"deepseek-v4-flash", "deepseek-v4-pro"}
        ):
            return fallback
        kwargs = {
            key: value
            for key, value in common.items()
            if key != "temperature"
        }
        kwargs["temperature"] = 0.0
        kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        return create_llm_client(
            provider=settings.llm_provider,
            model=model,
            base_url=settings.backend_url,
            **kwargs,
        ).get_llm()

    return RunLLMs(
        quick=quick,
        deep=deep,
        quick_serializer=serializer(settings.quick_model, quick),
        deep_serializer=serializer(settings.deep_model, deep),
    )
