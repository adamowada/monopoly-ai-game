from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.codex_runtime import verify_codex_runtime
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


def test_codex_runtime_preflight_accepts_fake_executable_and_auth_json(tmp_path: Path) -> None:
    codex_executable = tmp_path / "codex.cmd"
    codex_executable.write_text("@echo off\necho codex-cli 0.133.0\n", encoding="utf-8")
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "auth.json").write_text('{"token":"fake"}\n', encoding="utf-8")
    settings = Settings(
        api_env="local",
        database_url="postgresql+asyncpg://monopoly:monopoly@localhost:5432/monopoly_ai_game",
        codex_ai_executable=str(codex_executable),
        codex_home=str(codex_home),
    )

    runtime = verify_codex_runtime(settings)
    app = create_app(settings=settings)
    with TestClient(app) as client:
        response = client.get("/health")

    assert runtime.executable == str(codex_executable)
    assert runtime.codex_home == codex_home
    assert response.status_code == 200
    assert app.state.codex_ai_executable == str(codex_executable)


def test_codex_runtime_preflight_rejects_missing_executable(tmp_path: Path) -> None:
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "auth.json").write_text('{"token":"fake"}\n', encoding="utf-8")
    settings = Settings(
        api_env="local",
        database_url="postgresql+asyncpg://monopoly:monopoly@localhost:5432/monopoly_ai_game",
        codex_ai_executable=str(tmp_path / "missing-codex"),
        codex_home=str(codex_home),
    )

    with pytest.raises(RuntimeError, match="Codex runtime preflight failed: executable"):
        verify_codex_runtime(settings)


def test_codex_runtime_preflight_rejects_missing_auth_json(tmp_path: Path) -> None:
    codex_executable = tmp_path / "codex.cmd"
    codex_executable.write_text("@echo off\necho codex-cli 0.133.0\n", encoding="utf-8")
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    settings = Settings(
        api_env="local",
        database_url="postgresql+asyncpg://monopoly:monopoly@localhost:5432/monopoly_ai_game",
        codex_ai_executable=str(codex_executable),
        codex_home=str(codex_home),
    )

    with pytest.raises(RuntimeError, match="Codex runtime preflight failed: auth.json"):
        verify_codex_runtime(settings)
