"""Settings routes keep administration responses separate from research data."""

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from tradingagents.application.configuration import (
    ConfigurationConflict,
    ConfigurationError,
    ConfigurationStore,
)
from tradingagents.application.configuration_catalog import configuration_schema
from tradingagents.application.configuration_models import (
    ConfigurationPatch,
    ConfigurationSchema,
    ConfigurationView,
    CredentialRequest,
    CredentialView,
    ImportPreview,
    ImportRequest,
)

from .auth import LanSessionManager


def register_settings_routes(app: FastAPI, store: ConfigurationStore):
    @app.exception_handler(ConfigurationError)
    async def configuration_error(_request, exc):
        return JSONResponse(
            status_code=409 if isinstance(exc, ConfigurationConflict) else 422,
            content={
                "error": {"code": exc.code, "message": str(exc)},
                "details": [
                    {"location": ["values", field], "message": str(exc)} for field in exc.fields
                ],
            },
        )

    @app.middleware("http")
    async def settings_origin(request: Request, call_next):
        if (
            request.url.path.startswith("/api/v1/settings")
            and request.method not in {"GET", "HEAD", "OPTIONS"}
            and not LanSessionManager.same_origin(request)
        ):
            return JSONResponse(
                status_code=403,
                content={
                    "error": {"code": "origin_mismatch", "message": "Request origin is not allowed"}
                },
            )
        response = await call_next(request)
        if request.url.path.startswith("/api/v1/settings"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/v1/settings", response_model=ConfigurationView)
    def read_settings():
        return store.read()

    @app.get("/api/v1/settings/schema", response_model=ConfigurationSchema)
    def schema():
        return configuration_schema()

    @app.patch("/api/v1/settings", response_model=ConfigurationView)
    def save_settings(patch: ConfigurationPatch):
        return store.save(patch)

    @app.post("/api/v1/settings/credentials/reveal", response_model=CredentialView)
    def reveal(payload: CredentialRequest, response: Response):
        response.headers["Cache-Control"] = "no-store"
        return CredentialView(
            value=store.reveal_connection(payload.connection_id, payload.name)
            if payload.connection_id
            else store.reveal(payload.name)
        )

    @app.post("/api/v1/settings/import/preview", response_model=ImportPreview)
    def preview(payload: ImportRequest):
        return store.preview_import(payload)

    @app.post("/api/v1/settings/import/apply", response_model=ConfigurationView)
    def apply(payload: ImportRequest):
        return store.apply_import(payload)
