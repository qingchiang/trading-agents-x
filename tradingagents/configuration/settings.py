"""Immutable application and per-run settings with explicit environment loading."""

from __future__ import annotations

import os
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

from dotenv import dotenv_values, find_dotenv
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from tradingagents.configuration.defaults import build_default_config
from tradingagents.domain.common import (
    OutputLanguage,
    ReportLanguage,
    RunProfile,
    normalize_report_language,
    report_language_prompt_label,
)
from tradingagents.llm.models import ModelBinding, preset_connection

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

    snapshot_version: Literal[1] = 1
    quick_binding: ModelBinding | None = None
    deep_binding: ModelBinding
    research_kind: str = "full"
    profile: RunProfile = RunProfile.STANDARD
    temperature: float | None = None
    llm_max_retries: int | None = Field(default=None, ge=0)
    output_language: OutputLanguage = ReportLanguage.ENGLISH
    data_config: Mapping[str, Any]

    @field_validator("output_language", mode="before")
    @classmethod
    def normalize_output_language(
        cls,
        value: OutputLanguage,
    ) -> OutputLanguage:
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
                "temperature": self.temperature,
                "llm_max_retries": self.llm_max_retries,
                "output_language": report_language_prompt_label(
                    self.output_language
                ),
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
    trash_retention_days: int = Field(default=30, ge=0)
    default_run_settings: RunSettings
    import_environment: dict[str, SecretStr] = Field(default_factory=dict, exclude=True, repr=False)
    import_primary: dict[str, SecretStr] = Field(default_factory=dict, exclude=True, repr=False)
    import_enterprise: dict[str, SecretStr] = Field(default_factory=dict, exclude=True, repr=False)

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
        env = dict(os.environ if environ is None else environ)
        process_environment = dict(env)
        primary_values, enterprise_values = {}, {}
        if load_env_files and environ is None and not env.get("PYTHON_DOTENV_DISABLED"):
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
            primary_values = {k: v for k, v in dotenv_values(primary, interpolate=False).items() if v is not None} if primary else {}
            enterprise_values = {k: v for k, v in dotenv_values(enterprise, interpolate=False).items() if v is not None} if enterprise else {}
            env = {**enterprise_values, **primary_values, **env}
        home = Path(env.get("TRADINGAGENTS_HOME", "~/.tradingagents")).expanduser()
        defaults = build_default_config()
        connection = preset_connection("openai", identity="default")
        run_settings = RunSettings(
            quick_binding=ModelBinding(connection=connection, model="gpt-5.4-mini"),
            deep_binding=ModelBinding(connection=connection, model="gpt-5.5"),
            data_config=defaults,
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
            import_environment={k: SecretStr(v) for k, v in process_environment.items()},
            import_primary={k: SecretStr(v) for k, v in primary_values.items()},
            import_enterprise={k: SecretStr(v) for k, v in enterprise_values.items()},
            default_run_settings=run_settings,
        )

    def prepare_filesystem(self) -> None:
        self.home_dir.mkdir(parents=True, exist_ok=True)
        self.data_cache_dir.mkdir(parents=True, exist_ok=True)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)


def _redact(value: Any, key: str = "") -> Any:
    if not (key == "key_required" and isinstance(value, bool)) and any(
        fragment in key.casefold() for fragment in _SECRET_FRAGMENTS
    ):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(item, key) for item in value]
    return value
