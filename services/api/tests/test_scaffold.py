from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def test_health_endpoint_reports_scaffold_status() -> None:
    settings = Settings(
        api_env="test",
        database_url="postgresql+asyncpg://monopoly:monopoly@localhost:5432/monopoly_ai_game",
    )
    client = TestClient(create_app(settings=settings))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "api",
        "stage": "phase-1-stage-1.3",
        "environment": "test",
        "database": "configured",
    }
