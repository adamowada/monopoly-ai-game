from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.config import Settings
from app.db.session import create_database_engine, create_session_factory


@pytest.mark.asyncio
async def test_async_database_engine_and_session_factory_are_configured() -> None:
    settings = Settings(
        api_env="test",
        database_url="postgresql://monopoly:monopoly@localhost:5432/monopoly_ai_game",
    )

    engine = create_database_engine(settings)
    try:
        session_factory = create_session_factory(engine)
        session = session_factory()
        try:
            assert isinstance(engine, AsyncEngine)
            assert engine.url.drivername == "postgresql+asyncpg"
            assert isinstance(session, AsyncSession)
            assert session.sync_session.expire_on_commit is False
        finally:
            await session.close()
    finally:
        await engine.dispose()
