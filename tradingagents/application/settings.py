"""Immutable application and per-run settings with explicit environment loading."""

from __future__ import annotations

import os
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from types import MappingProxyType
from typing import Any

from dotenv import find_dotenv, load_dotenv
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from tradingagents.default_config import build_default_config

from .contracts import (
    AnalysisRequest,
    ReportLanguage,
    RunProfile,
    normalize_report_language,
)

_SECRET_FRAGMENTS = ("key", "secret", "token", "password", "authorization")


def _env_bool(environ: Mapping[str, str], name: str, default: bool) -> bool:
    raw = environ.get(name)
    if raw is None or raw == "":
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid value for {name}: expected a boolean, got {raw!r}")


def _env_int(environ: Mapping[str, str], name: str, default: int) -> int:
    raw = environ.get(name)
    if raw is None or raw == "":
        return default
    value = int(raw)
    if value < 0:
        raise ValueError(f"Invalid value for {name}: must be >= 0")
    return value


class RunSettings(BaseModel):
    """Resolved immutable settings for one analysis run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    profile: RunProfile = RunProfile.STANDARD
    llm_provider: str = "openai"
    quick_model: str = "gpt-5.4-mini"
    deep_model: str = "gpt-5.5"
    backend_url: str | None = None
    quick_reasoning_effort: str | None = None
    deep_reasoning_effort: str | None = None
    temperature: float | None = None
    llm_max_retries: int | None = Field(default=None, ge=0)
    output_language: ReportLanguage = ReportLanguage.ENGLISH
    provenance: bool = False
    data_config: Mapping[str, Any]

    @field_validator("output_language", mode="before")
    @classmethod
    def normalize_output_language(
        cls,
        value: str | ReportLanguage,
    ) -> ReportLanguage:
        return normalize_report_language(value)

    @field_validator("data_config", mode="before")
    @classmethod
    def copy_data_config(cls, value: Mapping[str, Any]) -> Mapping[str, Any]:
        return MappingProxyType(deepcopy(dict(value)))

    @field_validator("llm_max_retries", mode="before")
    @classmethod
    def validate_retry_budget(cls, value: Any) -> Any:
        if isinstance(value, bool):
            raise ValueError("llm_max_retries must be an integer, not boolean")
        return value

    def dataflow_config(self, app: AppSettings) -> dict[str, Any]:
        """Build the run-scoped data and model configuration."""
        config = deepcopy(dict(self.data_config))
        config.update(
            {
                "data_cache_dir": str(app.data_cache_dir),
                "llm_provider": self.llm_provider,
                "quick_think_llm": self.quick_model,
                "deep_think_llm": self.deep_model,
                "backend_url": self.backend_url,
                "quick_reasoning_effort": self.quick_reasoning_effort,
                "deep_reasoning_effort": self.deep_reasoning_effort,
                "temperature": self.temperature,
                "llm_max_retries": self.llm_max_retries,
                "output_language": self.output_language.prompt_label,
                "provenance_appendix": self.provenance,
            }
        )
        return config

    def snapshot(self) -> dict[str, Any]:
        return _redact(self.model_dump(mode="json"))


class AppSettings(BaseModel):
    """Process-level immutable settings loaded exactly once by an entry point."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    home_dir: Path
    database_path: Path
    data_cache_dir: Path
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    lan_enabled: bool = False
    lan_token: SecretStr | None = Field(default=None, exclude=True)
    session_secret: SecretStr | None = Field(default=None, exclude=True)
    worker_concurrency: int = Field(default=1, ge=1, le=1)
    worker_poll_seconds: float = Field(default=1.0, ge=0.05)
    lease_seconds: int = Field(default=300, ge=30)
    busy_timeout_ms: int = Field(default=5000, ge=100)
    default_run_settings: RunSettings

    @field_validator("database_path", "data_cache_dir", mode="before")
    @classmethod
    def expand_path(cls, value: str | Path) -> Path:
        return Path(value).expanduser().resolve()

    @classmethod
    def from_env(
        cls,
        *,
        environ: Mapping[str, str] | None = None,
        load_env_files: bool = True,
        cwd: Path | None = None,
    ) -> AppSettings:
        """Load dotenv files at an explicit application boundary, never on import."""
        if load_env_files and environ is None:
            if cwd is None:
                primary = find_dotenv(".env", usecwd=True)
                enterprise = find_dotenv(".env.enterprise", usecwd=True)
            else:
                search_dir = cwd.expanduser().resolve()
                primary_path = search_dir / ".env"
                enterprise_path = search_dir / ".env.enterprise"
                primary = str(primary_path) if primary_path.is_file() else ""
                enterprise = (
                    str(enterprise_path) if enterprise_path.is_file() else ""
                )
            if primary:
                load_dotenv(primary, override=False)
            if enterprise:
                load_dotenv(enterprise, override=False)
        env = dict(os.environ if environ is None else environ)
        home = Path(env.get("TRADINGAGENTS_HOME", "~/.tradingagents")).expanduser()
        defaults = build_default_config(env)
        provider = env.get("TRADINGAGENTS_LLM_PROVIDER", defaults["llm_provider"])
        output_language = normalize_report_language(
            env.get(
                "TRADINGAGENTS_OUTPUT_LANGUAGE",
                defaults.get("output_language", "en"),
            )
        )
        provenance = _env_bool(
            env,
            "TRADINGAGENTS_PROVENANCE_APPENDIX",
            bool(defaults.get("provenance_appendix", False)),
        )
        data_config = deepcopy(defaults)
        data_config["output_language"] = output_language.value
        data_config["provenance_appendix"] = provenance
        run_settings = RunSettings(
            llm_provider=provider,
            quick_model=env.get(
                "TRADINGAGENTS_QUICK_THINK_LLM", defaults["quick_think_llm"]
            ),
            deep_model=env.get(
                "TRADINGAGENTS_DEEP_THINK_LLM", defaults["deep_think_llm"]
            ),
            backend_url=env.get(
                "TRADINGAGENTS_LLM_BACKEND_URL", defaults.get("backend_url")
            ),
            quick_reasoning_effort=env.get(
                "TRADINGAGENTS_QUICK_REASONING_EFFORT",
                defaults.get("quick_reasoning_effort"),
            ),
            deep_reasoning_effort=env.get(
                "TRADINGAGENTS_DEEP_REASONING_EFFORT",
                defaults.get("deep_reasoning_effort"),
            ),
            temperature=(
                float(env["TRADINGAGENTS_TEMPERATURE"])
                if env.get("TRADINGAGENTS_TEMPERATURE")
                else defaults.get("temperature")
            ),
            llm_max_retries=(
                int(env["TRADINGAGENTS_LLM_MAX_RETRIES"])
                if env.get("TRADINGAGENTS_LLM_MAX_RETRIES")
                else defaults.get("llm_max_retries")
            ),
            output_language=output_language,
            provenance=provenance,
            data_config=data_config,
        )
        lan_enabled = _env_bool(env, "TRADINGAGENTS_LAN_ENABLED", False)
        token = env.get("TRADINGAGENTS_LAN_TOKEN")
        if lan_enabled and not token:
            raise ValueError(
                "TRADINGAGENTS_LAN_TOKEN is required when LAN mode is enabled"
            )
        return cls(
            home_dir=home.resolve(),
            database_path=Path(
                env.get("TRADINGAGENTS_DATABASE_PATH", home / "tradingagents.db")
            ),
            data_cache_dir=Path(
                env.get("TRADINGAGENTS_CACHE_DIR", home / "cache")
            ),
            host=env.get(
                "TRADINGAGENTS_HOST", "0.0.0.0" if lan_enabled else "127.0.0.1"
            ),
            port=_env_int(env, "TRADINGAGENTS_PORT", 8000),
            lan_enabled=lan_enabled,
            lan_token=SecretStr(token) if token else None,
            session_secret=(
                SecretStr(env["TRADINGAGENTS_SESSION_SECRET"])
                if env.get("TRADINGAGENTS_SESSION_SECRET")
                else None
            ),
            worker_poll_seconds=float(
                env.get("TRADINGAGENTS_WORKER_POLL_SECONDS", "1.0")
            ),
            lease_seconds=_env_int(env, "TRADINGAGENTS_LEASE_SECONDS", 300),
            busy_timeout_ms=_env_int(
                env, "TRADINGAGENTS_SQLITE_BUSY_TIMEOUT_MS", 5000
            ),
            default_run_settings=run_settings,
        )

    def resolve_run(self, request: AnalysisRequest) -> RunSettings:
        base = self.default_run_settings
        return base.model_copy(
            update={
                "profile": request.profile,
                "llm_provider": request.llm_provider or base.llm_provider,
                "quick_model": request.quick_model or base.quick_model,
                "deep_model": request.deep_model or base.deep_model,
                "quick_reasoning_effort": (
                    request.quick_reasoning_effort
                    if request.quick_reasoning_effort is not None
                    else base.quick_reasoning_effort
                ),
                "deep_reasoning_effort": (
                    request.deep_reasoning_effort
                    if request.deep_reasoning_effort is not None
                    else base.deep_reasoning_effort
                ),
                "output_language": request.output_language or base.output_language,
                "provenance": (
                    request.provenance
                    if request.provenance is not None
                    else base.provenance
                ),
            }
        )

    def materialize_request(
        self,
        request: AnalysisRequest,
        *,
        run_settings: RunSettings | None = None,
    ) -> AnalysisRequest:
        """Persist the effective request instead of ambiguous omitted overrides."""
        resolved = run_settings or self.resolve_run(request)
        return request.model_copy(
            update={
                "llm_provider": resolved.llm_provider,
                "quick_model": resolved.quick_model,
                "deep_model": resolved.deep_model,
                "quick_reasoning_effort": resolved.quick_reasoning_effort,
                "deep_reasoning_effort": resolved.deep_reasoning_effort,
                "output_language": resolved.output_language,
                "provenance": resolved.provenance,
            }
        )

    def prepare_filesystem(self) -> None:
        self.home_dir.mkdir(parents=True, exist_ok=True)
        self.data_cache_dir.mkdir(parents=True, exist_ok=True)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)


def _redact(value: Any, key: str = "") -> Any:
    if any(fragment in key.casefold() for fragment in _SECRET_FRAGMENTS):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(item, key) for item in value]
    return value
