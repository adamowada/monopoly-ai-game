from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.core.config import Settings
from app.core.logging import configure_logging
from app.db.session import create_database_engine, create_session_factory


LOGGER = logging.getLogger(__name__)


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: Literal["api"]
    stage: Literal["phase-1-stage-1.3"]
    environment: str
    database: Literal["configured"]


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or Settings()
    configure_logging(resolved_settings)
    engine = create_database_engine(resolved_settings)
    session_factory = create_session_factory(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = resolved_settings
        app.state.database_engine = engine
        app.state.database_session_factory = session_factory
        LOGGER.info("api startup complete")
        try:
            yield
        finally:
            await engine.dispose()
            LOGGER.info("api shutdown complete")

    app = FastAPI(
        title="Monopoly AI Game API",
        version="0.0.0",
        summary="Local FastAPI foundation for the Monopoly AI research game.",
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    app.state.database_engine = engine
    app.state.database_session_factory = session_factory
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved_settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            service="api",
            stage="phase-1-stage-1.3",
            environment=resolved_settings.api_env,
            database="configured",
        )

    return app


app = create_app()
