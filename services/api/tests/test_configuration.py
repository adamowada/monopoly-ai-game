from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.main import create_app


def test_settings_normalizes_local_postgres_urls_to_asyncpg() -> None:
    settings = Settings(
        api_env="test",
        database_url="postgresql://monopoly:monopoly@localhost:5432/monopoly_ai_game",
    )

    assert (
        settings.database_url
        == "postgresql+asyncpg://monopoly:monopoly@localhost:5432/monopoly_ai_game"
    )


def test_settings_reject_invalid_database_url() -> None:
    with pytest.raises(ValidationError):
        Settings(api_env="test", database_url="not-a-database-url")


def test_create_app_fails_loudly_on_invalid_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "not-a-database-url")

    with pytest.raises(ValidationError):
        create_app()


def test_cors_origins_are_parsed_from_comma_delimited_settings() -> None:
    settings = Settings(
        api_env="test",
        database_url="postgresql+asyncpg://monopoly:monopoly@localhost:5432/monopoly_ai_game",
        cors_origins="http://localhost:3000, http://127.0.0.1:3000",
    )

    assert settings.cors_origin_list == [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]
