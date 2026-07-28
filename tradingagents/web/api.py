"""Versioned HTTP API and persistent SSE replay for the local run center."""

from __future__ import annotations

import asyncio
import json
from importlib import resources
from pathlib import Path
from typing import Annotated, Literal

from fastapi import (
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select

from tradingagents.application.contracts import (
    AnalysisRequest,
    ResearchArtifact,
    RunEvent,
    RunStatus,
    RunView,
    report_language_value,
)
from tradingagents.application.database import OutcomeRecord, RunRecord
from tradingagents.application.repository import (
    IdempotencyConflictError,
    InvalidRunTransitionError,
    RunNotFoundError,
)
from tradingagents.application.service import AnalysisService
from tradingagents.application.settings import AppSettings
from tradingagents.llm_clients.model_discovery import (
    ModelDiscoveryService,
    UnknownProviderError,
)
from tradingagents.version import __version__

from .auth import COOKIE_NAME, SESSION_MAX_AGE, LanSessionManager
from .models import (
    CapabilitiesResponse,
    HealthResponse,
    LoginRequest,
    MemoryEntry,
    ProviderModelCatalog,
    RunDetail,
)

API_PREFIX = "/api/v1"
_TERMINAL = {
    RunStatus.SUCCEEDED,
    RunStatus.FAILED,
    RunStatus.CANCELLED,
}


def create_app(
    settings: AppSettings | None = None,
    *,
    service: AnalysisService | None = None,
    model_discovery: ModelDiscoveryService | None = None,
) -> FastAPI:
    settings = settings or AppSettings.from_env()
    service = service or AnalysisService(settings)
    repository = service.repository
    model_discovery = model_discovery or ModelDiscoveryService(settings)
    auth = LanSessionManager(settings)
    app = FastAPI(
        title="TradingAgentsX API",
        version=__version__,
        description="Local evidence-first investment research run center.",
    )
    app.state.settings = settings
    app.state.service = service
    app.state.model_discovery = model_discovery

    @app.exception_handler(RunNotFoundError)
    async def not_found(_request: Request, exc: RunNotFoundError):
        return _error(404, "run_not_found", str(exc))

    @app.exception_handler(InvalidRunTransitionError)
    async def invalid_transition(
        _request: Request,
        exc: InvalidRunTransitionError,
    ):
        return _error(409, "invalid_run_transition", str(exc))

    @app.exception_handler(IdempotencyConflictError)
    async def idempotency_conflict(
        _request: Request,
        exc: IdempotencyConflictError,
    ):
        return _error(409, "idempotency_conflict", str(exc))

    @app.exception_handler(RequestValidationError)
    async def validation_error(
        _request: Request,
        exc: RequestValidationError,
    ):
        details = [
            {
                "location": [str(part) for part in error.get("loc", ())],
                "message": str(error.get("msg", "Invalid value")),
                "type": str(error.get("type", "validation_error")),
            }
            for error in exc.errors()
        ]
        return _error(
            422,
            "validation_error",
            "Request validation failed",
            details=details,
        )

    @app.middleware("http")
    async def lan_authentication(request: Request, call_next):
        if not auth.enabled:
            return await call_next(request)
        path = request.url.path
        public = path in {
            f"{API_PREFIX}/auth/login",
            f"{API_PREFIX}/health",
        } or not path.startswith(API_PREFIX)
        if public:
            return await call_next(request)
        if not auth.validate(request.cookies.get(COOKIE_NAME)):
            return _error(401, "authentication_required", "LAN session required")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and not auth.same_origin(
            request
        ):
            return _error(403, "origin_mismatch", "Request origin is not allowed")
        return await call_next(request)

    @app.post(f"{API_PREFIX}/auth/login")
    def login(payload: LoginRequest, response: Response):
        if not auth.enabled:
            raise HTTPException(status_code=404)
        if not auth.authenticate_token(payload.token):
            raise HTTPException(status_code=401, detail="invalid token")
        response.set_cookie(
            COOKIE_NAME,
            auth.issue(),
            httponly=True,
            samesite="strict",
            secure=False,
            max_age=SESSION_MAX_AGE,
            path="/",
        )
        return {"authenticated": True}

    @app.post(f"{API_PREFIX}/auth/logout")
    def logout(response: Response):
        response.delete_cookie(COOKIE_NAME, path="/")
        return {"authenticated": False}

    @app.post(f"{API_PREFIX}/runs", response_model=RunView, status_code=202)
    def create_run(
        request: AnalysisRequest,
        idempotency_key: Annotated[
            str | None,
            Header(alias="Idempotency-Key", max_length=200),
        ] = None,
    ):
        return service.enqueue(
            request,
            idempotency_key=idempotency_key,
        )

    @app.get(f"{API_PREFIX}/runs", response_model=list[RunView])
    def list_runs(
        status: RunStatus | None = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        return repository.list_runs(
            status=status,
            limit=limit,
            offset=offset,
        )

    @app.get(f"{API_PREFIX}/runs/{{run_id}}", response_model=RunDetail)
    def get_run(run_id: str):
        view = repository.get_run(run_id)
        result = (
            repository.get_result(run_id)
            if view.status in _TERMINAL
            else None
        )
        return RunDetail(run=view, result=result)

    @app.get(
        f"{API_PREFIX}/runs/{{run_id}}/artifacts",
        response_model=list[ResearchArtifact],
    )
    def list_artifacts(
        run_id: str,
        attempt: Annotated[int | None, Query(ge=1)] = None,
    ):
        return repository.list_artifacts(run_id, attempt=attempt)

    @app.get(
        f"{API_PREFIX}/runs/{{run_id}}/events",
        response_model=RunEvent,
        response_class=StreamingResponse,
    )
    async def stream_events(
        run_id: str,
        request: Request,
        last_event_id: Annotated[
            str | None,
            Header(alias="Last-Event-ID"),
        ] = None,
        after: Annotated[int, Query(ge=0)] = 0,
    ):
        repository.get_run(run_id)
        cursor = after
        if last_event_id:
            try:
                cursor = max(cursor, int(last_event_id))
            except ValueError as exc:
                raise HTTPException(
                    status_code=400,
                    detail="Last-Event-ID must be an integer",
                ) from exc

        async def generate():
            nonlocal cursor
            idle_ticks = 0
            while True:
                if await request.is_disconnected():
                    return
                events = repository.list_events(
                    run_id,
                    after_sequence=cursor,
                    limit=500,
                )
                if events:
                    idle_ticks = 0
                    for event in events:
                        cursor = event.sequence
                        data = event.model_dump_json()
                        yield (
                            f"id: {event.sequence}\n"
                            f"event: {event.event_type}\n"
                            f"data: {data}\n\n"
                        )
                else:
                    idle_ticks += 1
                    view = repository.get_run(run_id)
                    if view.status in _TERMINAL:
                        return
                    if idle_ticks % 30 == 0:
                        yield ": keepalive\n\n"
                    await asyncio.sleep(0.5)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    @app.post(f"{API_PREFIX}/runs/{{run_id}}/cancel", response_model=RunView)
    def cancel_run(run_id: str):
        return service.cancel(run_id)

    @app.post(f"{API_PREFIX}/runs/{{run_id}}/retry", response_model=RunView)
    def retry_run(run_id: str):
        return service.retry(run_id)

    @app.post(f"{API_PREFIX}/runs/{{run_id}}/rerun", response_model=RunView)
    def rerun(run_id: str):
        return service.rerun(run_id)

    @app.get(f"{API_PREFIX}/runs/{{run_id}}/export")
    def export_run(
        run_id: str,
        format: Literal["markdown", "json"] = "markdown",
    ):
        media_type, content = service.export(run_id, format=format)
        extension = "json" if format == "json" else "md"
        return Response(
            content=content,
            media_type=media_type,
            headers={
                "Content-Disposition": (
                    f'attachment; filename="tradingagents-{run_id}.{extension}"'
                )
            },
        )

    @app.get(f"{API_PREFIX}/memory", response_model=list[MemoryEntry])
    def memory(
        ticker: str | None = None,
        market: str | None = None,
        q: Annotated[str | None, Query(max_length=500)] = None,
        status: Literal["pending", "resolved"] | None = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ):
        return repository.memory_entries(
            ticker=ticker,
            market=market,
            q=q,
            status=status,
            limit=limit,
        )

    @app.get(
        f"{API_PREFIX}/capabilities",
        response_model=CapabilitiesResponse,
    )
    def capabilities():
        providers = {}
        for provider, (definition, availability) in model_discovery.providers().items():
            providers[provider] = {
                "label": definition.label,
                "api_key_required": definition.api_key_required,
                "api_key_configured": availability.api_key_configured,
                "configured": availability.configured,
                "selectable": availability.selectable,
                "unavailable_reason": availability.reason,
                "model_discovery_supported": definition.adapter != "custom",
            }
        defaults = settings.default_run_settings
        return CapabilitiesResponse(
            profiles=["fast", "standard", "deep"],
            analysts=["market", "social", "news", "fundamentals"],
            output_languages=["en", "zh-CN", "ja"],
            providers=providers,
            defaults={
                "profile": defaults.profile.value,
                "llm_provider": defaults.llm_provider,
                "quick_model": defaults.quick_model,
                "deep_model": defaults.deep_model,
                "quick_reasoning_effort": defaults.quick_reasoning_effort,
                "deep_reasoning_effort": defaults.deep_reasoning_effort,
                "output_language": report_language_value(
                    defaults.output_language
                ),
                "provenance": defaults.provenance,
                "lan_enabled": settings.lan_enabled,
            },
        )

    @app.get(
        f"{API_PREFIX}/providers/{{provider}}/models",
        response_model=ProviderModelCatalog,
    )
    def provider_models(
        provider: str,
        refresh: bool = False,
    ):
        try:
            return model_discovery.discover(provider, refresh=refresh)
        except UnknownProviderError as exc:
            raise HTTPException(
                status_code=404,
                detail="Unknown model provider",
            ) from exc

    @app.get(f"{API_PREFIX}/health", response_model=HealthResponse)
    def health():
        database_status: Literal["ok", "error"] = "ok"
        queue = {
            "queued": 0,
            "running": 0,
            "pending_outcomes": 0,
        }
        try:
            with repository.sessions() as session:
                counts = dict(
                    session.execute(
                        select(RunRecord.status, func.count())
                        .group_by(RunRecord.status)
                    ).all()
                )
                queue["queued"] = int(counts.get("queued", 0))
                queue["running"] = int(counts.get("running", 0))
                queue["pending_outcomes"] = int(
                    session.scalar(
                        select(func.count())
                        .select_from(OutcomeRecord)
                        .where(OutcomeRecord.status == "pending")
                    )
                    or 0
                )
        except Exception:
            database_status = "error"
        return HealthResponse(
            status="ok" if database_status == "ok" else "degraded",
            database=database_status,
            queue=queue,
            version=__version__,
        )

    _mount_frontend(app)
    return app


def create_default_app() -> FastAPI:
    return create_app()


def _error(
    status: int,
    code: str,
    message: str,
    *,
    details: list[dict[str, object]] | None = None,
):
    payload: dict[str, object] = {
        "error": {"code": code, "message": message},
    }
    if details:
        payload["details"] = details
    return Response(
        status_code=status,
        media_type="application/json",
        content=json.dumps(
            payload,
            ensure_ascii=False,
        ),
    )


def _mount_frontend(app: FastAPI) -> None:
    static_root = resources.files("tradingagents.web").joinpath("static")
    try:
        root = Path(str(static_root))
    except TypeError:
        return
    index = root / "index.html"
    assets = root / "assets"
    if not index.exists():
        return
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/", include_in_schema=False)
    def frontend_index():
        return FileResponse(index)

    @app.get("/{path:path}", include_in_schema=False)
    def frontend_fallback(path: str):
        candidate = (root / path).resolve()
        if candidate.is_relative_to(root.resolve()) and candidate.is_file():
            return FileResponse(candidate)
        if path.startswith("api/"):
            return PlainTextResponse("Not found", status_code=404)
        return FileResponse(index)
