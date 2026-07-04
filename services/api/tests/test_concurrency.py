from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.db.metadata import action_idempotency_keys, game_events, games, metadata, rejected_actions
from app.db.persistence import EventPersistence
from app.main import create_app


TEST_DATABASE_URL = os.getenv(
    "MONOPOLY_TEST_DATABASE_URL",
    "postgresql+asyncpg://monopoly:monopoly@127.0.0.1:5432/monopoly_ai_game",
)


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    async with engine.begin() as connection:
        await connection.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        await connection.run_sync(metadata.create_all)

    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(bind=engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def api_app() -> AsyncIterator[FastAPI]:
    app = create_app(
        settings=Settings(
            api_env="test",
            database_url=TEST_DATABASE_URL,
            cors_origins="http://localhost:3000",
        )
    )
    try:
        yield app
    finally:
        await app.state.database_engine.dispose()


@pytest_asyncio.fixture
async def client(api_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api_app),
        base_url="http://testserver",
    ) as client:
        yield client


async def create_game(client: httpx.AsyncClient, *, seed: str = "stage-4.5-test") -> dict[str, Any]:
    response = await client.post(
        "/games",
        json={
            "seed": seed,
            "players": [
                {"name": "Ada", "kind": "human"},
                {"name": "Grace", "kind": "ai"},
            ],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def delete_game(session_factory: async_sessionmaker, game_id: str | UUID) -> None:
    async with session_factory() as session:
        async with session.begin():
            await session.execute(games.delete().where(games.c.id == UUID(str(game_id))))


async def table_count(
    session_factory: async_sessionmaker,
    table: sa.Table,
    game_id: str | UUID,
) -> int:
    async with session_factory() as session:
        result = await session.execute(
            sa.select(sa.func.count()).select_from(table).where(table.c.game_id == UUID(str(game_id)))
        )
        return int(result.scalar_one())


async def fetch_rows(
    session_factory: async_sessionmaker,
    table: sa.Table,
    game_id: str | UUID,
    *,
    order_by: sa.Column[Any] | None = None,
) -> list[dict[str, Any]]:
    statement = sa.select(table).where(table.c.game_id == UUID(str(game_id)))
    if order_by is not None:
        statement = statement.order_by(order_by)
    async with session_factory() as session:
        result = await session.execute(statement)
        return [dict(row) for row in result.mappings().all()]


async def legal_roll_action(client: httpx.AsyncClient, game_id: str, actor_id: str) -> dict[str, Any]:
    response = await client.get(
        f"/games/{game_id}/legal-actions",
        params={"actor_player_id": actor_id},
    )
    assert response.status_code == 200, response.text
    return next(action for action in response.json()["legal_actions"] if action["type"] == "ROLL_DICE")


@pytest.mark.asyncio
async def test_missing_idempotency_key_returns_400_without_accepted_event(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
) -> None:
    created = await create_game(client, seed="stage-4.5-missing-idempotency")
    game_id = created["id"]
    actor_id = created["players"][0]["id"]
    try:
        roll_action = await legal_roll_action(client, game_id, actor_id)

        response = await client.post(f"/games/{game_id}/actions", json=roll_action)

        assert response.status_code == 400
        assert response.json()["status"] == "rejected"
        assert response.json()["reason_code"] == "missing_idempotency_key"
        assert await table_count(session_factory, game_events, game_id) == 0
    finally:
        await delete_game(session_factory, game_id)


@pytest.mark.asyncio
async def test_duplicate_same_key_same_body_replays_outcome_without_duplicate_event(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
) -> None:
    created = await create_game(client, seed="stage-4.5-idempotent-replay")
    game_id = created["id"]
    actor_id = created["players"][0]["id"]
    key = "roll-once"
    try:
        roll_action = await legal_roll_action(client, game_id, actor_id)

        first = await client.post(
            f"/games/{game_id}/actions",
            headers={"Idempotency-Key": key},
            json=roll_action,
        )
        second = await client.post(
            f"/games/{game_id}/actions",
            headers={"Idempotency-Key": key},
            json=roll_action,
        )

        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        assert second.json() == first.json()
        assert await table_count(session_factory, game_events, game_id) == len(
            first.json()["accepted_events"]
        )
        assert await table_count(session_factory, rejected_actions, game_id) == 0

        idempotency_rows = await fetch_rows(session_factory, action_idempotency_keys, game_id)
        assert len(idempotency_rows) == 1
        assert idempotency_rows[0]["idempotency_key"] == key
        assert idempotency_rows[0]["status"] == "accepted"
        assert idempotency_rows[0]["created_event_sequence_start"] == 1
        assert idempotency_rows[0]["created_event_sequence_end"] == len(
            first.json()["accepted_events"]
        )
        assert idempotency_rows[0]["rejected_action_id"] is None
    finally:
        await delete_game(session_factory, game_id)


@pytest.mark.asyncio
async def test_same_key_different_body_returns_409_without_mutation(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
) -> None:
    created = await create_game(client, seed="stage-4.5-idempotency-conflict")
    game_id = created["id"]
    actor_id = created["players"][0]["id"]
    key = "conflicting-click"
    try:
        roll_action = await legal_roll_action(client, game_id, actor_id)
        first = await client.post(
            f"/games/{game_id}/actions",
            headers={"Idempotency-Key": key},
            json=roll_action,
        )
        assert first.status_code == 200, first.text
        before_events = await table_count(session_factory, game_events, game_id)
        before_rejections = await table_count(session_factory, rejected_actions, game_id)
        before_keys = await table_count(session_factory, action_idempotency_keys, game_id)

        conflicting_body = {**roll_action, "payload": {"unexpected": True}}
        conflict = await client.post(
            f"/games/{game_id}/actions",
            headers={"Idempotency-Key": key},
            json=conflicting_body,
        )

        assert conflict.status_code == 409
        assert conflict.json()["status"] == "rejected"
        assert conflict.json()["reason_code"] == "idempotency_key_conflict"
        assert await table_count(session_factory, game_events, game_id) == before_events
        assert await table_count(session_factory, rejected_actions, game_id) == before_rejections
        assert await table_count(session_factory, action_idempotency_keys, game_id) == before_keys
    finally:
        await delete_game(session_factory, game_id)


@pytest.mark.asyncio
async def test_stale_state_submission_from_ai_player_is_audited_and_idempotent(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
) -> None:
    created = await create_game(client, seed="stage-4.5-stale-ai")
    game_id = created["id"]
    ai_player_id = created["players"][1]["id"]
    stale_action = {
        "actor_id": ai_player_id,
        "type": "ROLL_DICE",
        "payload": {},
        "expected_state_hash": "stale-ai-output",
        "expected_event_sequence": 0,
    }
    try:
        first = await client.post(
            f"/games/{game_id}/actions",
            headers={"Idempotency-Key": "stale-ai-decision"},
            json=stale_action,
        )
        second = await client.post(
            f"/games/{game_id}/actions",
            headers={"Idempotency-Key": "stale-ai-decision"},
            json=stale_action,
        )

        assert first.status_code == 409
        assert second.status_code == 409
        assert second.json() == first.json()
        assert first.json()["reason_code"] == "stale_action"
        assert await table_count(session_factory, game_events, game_id) == 0

        rejection_rows = await fetch_rows(session_factory, rejected_actions, game_id)
        assert len(rejection_rows) == 1
        assert rejection_rows[0]["actor_player_id"] == UUID(ai_player_id)
        assert rejection_rows[0]["reason_code"] == "stale_action"

        idempotency_rows = await fetch_rows(session_factory, action_idempotency_keys, game_id)
        assert len(idempotency_rows) == 1
        assert idempotency_rows[0]["status"] == "rejected"
        assert idempotency_rows[0]["rejected_action_id"] == rejection_rows[0]["id"]
    finally:
        await delete_game(session_factory, game_id)


@pytest.mark.asyncio
async def test_concurrent_submissions_keep_event_sequence_contiguous_and_replayable(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
) -> None:
    created = await create_game(client, seed="stage-4.5-concurrent")
    game_id = created["id"]
    actor_id = created["players"][0]["id"]
    try:
        roll_action = await legal_roll_action(client, game_id, actor_id)

        async def submit(index: int) -> httpx.Response:
            return await client.post(
                f"/games/{game_id}/actions",
                headers={"Idempotency-Key": f"concurrent-{index}"},
                json=roll_action,
            )

        responses = await asyncio.gather(*(submit(index) for index in range(6)))
        accepted = [response for response in responses if response.status_code == 200]
        rejected = [response for response in responses if response.status_code == 409]

        assert len(accepted) == 1
        assert len(rejected) == 5
        assert {response.json()["reason_code"] for response in rejected} == {"stale_action"}

        event_rows = await fetch_rows(
            session_factory,
            game_events,
            game_id,
            order_by=game_events.c.sequence,
        )
        assert [row["sequence"] for row in event_rows] == list(range(1, len(event_rows) + 1))
        assert len(event_rows) == len(accepted[0].json()["accepted_events"])
        assert await table_count(session_factory, rejected_actions, game_id) == len(rejected)
        assert await table_count(session_factory, action_idempotency_keys, game_id) == len(responses)

        replayed = await EventPersistence(session_factory).replay_from_event_zero(game_id)
        latest = await EventPersistence(session_factory).replay_from_latest_snapshot(game_id)
        assert latest.state_hash() == replayed.state_hash()
        assert latest.event_sequence == len(event_rows)
    finally:
        await delete_game(session_factory, game_id)


@pytest.mark.asyncio
async def test_idempotency_table_enforces_unique_game_key_lookup(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
) -> None:
    created = await create_game(client, seed="stage-4.5-idempotency-table")
    game_id = created["id"]
    actor_id = created["players"][0]["id"]
    key = "unique-game-key"
    try:
        roll_action = await legal_roll_action(client, game_id, actor_id)
        response = await client.post(
            f"/games/{game_id}/actions",
            headers={"Idempotency-Key": key},
            json=roll_action,
        )
        assert response.status_code == 200, response.text

        rows = await fetch_rows(session_factory, action_idempotency_keys, game_id)
        assert len(rows) == 1
        row = rows[0]
        assert row["game_id"] == UUID(game_id)
        assert row["actor_player_id"] == UUID(actor_id)
        assert row["idempotency_key"] == key
        assert isinstance(row["request_hash"], str)
        assert row["response_payload"] == response.json()

        with pytest.raises(IntegrityError):
            async with session_factory() as session:
                async with session.begin():
                    await session.execute(
                        action_idempotency_keys.insert().values(
                            game_id=UUID(game_id),
                            actor_player_id=UUID(actor_id),
                            idempotency_key=key,
                            request_hash="different-hash",
                            status="accepted",
                            response_payload={"status": "accepted"},
                        )
                    )
    finally:
        await delete_game(session_factory, game_id)
