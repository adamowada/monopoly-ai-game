from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


DEFAULT_DATABASE_URL = "postgresql+asyncpg://monopoly:monopoly@localhost:5432/monopoly_ai_game"
DEFAULT_CORS_ORIGINS = "http://localhost:3000,http://127.0.0.1:3000"
VALID_LOG_LEVELS = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"}


def _parse_cors_origins(value: object) -> list[str]:
    if isinstance(value, str):
        raw_origins = value.replace("\n", ",").split(",")
    elif isinstance(value, list):
        raw_origins = [str(origin) for origin in value]
    else:
        raise ValueError("CORS_ORIGINS must be a comma-delimited string or a list")

    origins = [origin.strip().rstrip("/") for origin in raw_origins if origin.strip()]
    if not origins:
        raise ValueError("CORS_ORIGINS must include at least one origin")
    return origins


def _default_codex_home() -> str:
    return str(Path.home() / ".codex")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        case_sensitive=False,
        env_file=".env",
        extra="ignore",
        validate_default=True,
    )

    service_name: str = Field(default="api")
    api_env: str = Field(default="local")
    api_host: str = Field(default="127.0.0.1")
    api_port: int = Field(default=8000)
    log_level: str = Field(default="INFO")
    database_url: str = Field(default=DEFAULT_DATABASE_URL)
    cors_origins: str = Field(default=DEFAULT_CORS_ORIGINS)
    codex_ai_executable: str = Field(default="codex")
    codex_home: str = Field(default_factory=_default_codex_home)

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        try:
            url = make_url(value)
        except ArgumentError as exc:
            raise ValueError("DATABASE_URL must be a valid Postgres URL") from exc

        if url.drivername == "postgresql":
            url = url.set(drivername="postgresql+asyncpg")

        if url.drivername != "postgresql+asyncpg":
            raise ValueError("DATABASE_URL must use postgresql+asyncpg or postgresql")
        if not url.host:
            raise ValueError("DATABASE_URL must include a host")
        if not url.database:
            raise ValueError("DATABASE_URL must include a database name")

        return url.render_as_string(hide_password=False)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def validate_cors_origins(cls, value: Any) -> str:
        return ",".join(_parse_cors_origins(value))

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        level = value.upper()
        if level not in VALID_LOG_LEVELS:
            raise ValueError(f"LOG_LEVEL must be one of {sorted(VALID_LOG_LEVELS)}")
        return level

    @property
    def cors_origin_list(self) -> list[str]:
        return _parse_cors_origins(self.cors_origins)
