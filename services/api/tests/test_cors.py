from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def test_health_endpoint_allows_local_nextjs_origin() -> None:
    settings = Settings(
        api_env="test",
        database_url="postgresql+asyncpg://monopoly:monopoly@localhost:5432/monopoly_ai_game",
        cors_origins="http://localhost:3000,http://127.0.0.1:3000",
    )
    client = TestClient(create_app(settings=settings))

    response = client.options(
        "/health",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code in {200, 204}
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
