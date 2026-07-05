from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette import status
from starlette.requests import Request

from app.api.games import _missing_idempotency_key_payload, router as games_router
from app.core.config import Settings
from app.core.codex_runtime import codex_runtime_required, verify_codex_runtime
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
        if codex_runtime_required(resolved_settings):
            codex_runtime = verify_codex_runtime(resolved_settings)
            app.state.codex_runtime = codex_runtime
            app.state.codex_ai_executable = codex_runtime.executable
            app.state.codex_home = codex_runtime.codex_home
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
    app.state.codex_ai_executable = resolved_settings.codex_ai_executable
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved_settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(games_router)

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request,
        exc: RequestValidationError,
    ):
        if _is_missing_action_idempotency_key(request, exc):
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content=_missing_idempotency_key_payload(),
            )
        return await request_validation_exception_handler(request, exc)

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


def _is_missing_action_idempotency_key(request: Request, exc: RequestValidationError) -> bool:
    if request.method != "POST":
        return False
    if not request.url.path.startswith("/games/") or not request.url.path.endswith("/actions"):
        return False
    return any(
        tuple(error.get("loc", ())) == ("header", "Idempotency-Key")
        for error in exc.errors()
    )


app = create_app()
