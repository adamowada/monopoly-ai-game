from __future__ import annotations

from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: Literal["api"] = "api"
    stage: Literal["phase-1-stage-1.1"] = "phase-1-stage-1.1"


def create_app() -> FastAPI:
    app = FastAPI(
        title="Monopoly AI Game API",
        version="0.0.0",
        summary="Local FastAPI scaffold for the Monopoly AI research game.",
    )

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse()

    return app


app = create_app()
