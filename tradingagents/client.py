"""Public Python API for the independent TradingAgentsX application."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from tradingagents.application.service import AnalysisService
from tradingagents.configuration.settings import AppSettings
from tradingagents.data.interface import resolve_instrument_eligibility
from tradingagents.domain.runs import AnalysisRequest, AnalysisResult, RunEvent, RunView


class TradingAgents:
    def __init__(
        self,
        settings: AppSettings,
        *,
        eligibility_resolver: Callable[..., Any] = resolve_instrument_eligibility,
    ):
        self.settings = settings
        self.service = AnalysisService(
            settings,
            eligibility_resolver=eligibility_resolver,
        )

    @classmethod
    def from_env(
        cls,
        *,
        environ: Mapping[str, str] | None = None,
        load_env_files: bool = True,
        cwd: Path | None = None,
    ) -> TradingAgents:
        return cls(
            AppSettings.from_env(
                environ=environ,
                load_env_files=load_env_files,
                cwd=cwd,
            )
        )

    def run(
        self,
        request: AnalysisRequest,
        *,
        on_event: Callable[[RunEvent], None] | None = None,
    ) -> AnalysisResult:
        return self.service.run(request, on_event=on_event)

    def enqueue(
        self,
        request: AnalysisRequest,
        *,
        idempotency_key: str | None = None,
    ) -> RunView:
        return self.service.enqueue(
            request,
            idempotency_key=idempotency_key,
        )
