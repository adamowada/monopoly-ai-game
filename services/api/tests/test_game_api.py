from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.db.metadata import (
    deals,
    game_events,
    games,
    metadata,
    negotiations,
    players,
    rejected_actions,
)
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


async def create_game(client: httpx.AsyncClient) -> dict[str, Any]:
    response = await client.post(
        "/games",
        json={
            "seed": "stage-4.4-game-api-test",
            "players": [
                {"name": "Ada", "kind": "human"},
                {"name": "Grace", "kind": "ai"},
            ],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
async def test_create_and_load_game_metadata_and_state(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
) -> None:
    created = await create_game(client)
    game_id = created["id"]
    try:
        assert created["status"] == "active"
        assert created["seed"] == "stage-4.4-game-api-test"
        assert created["current_phase"] == "START_TURN"
        assert [player["seat_order"] for player in created["players"]] == [0, 1]
        assert [player["controller_type"] for player in created["players"]] == ["human", "ai"]

        metadata_response = await client.get(f"/games/{game_id}")
        state_response = await client.get(f"/games/{game_id}/state")

        assert metadata_response.status_code == 200
        assert metadata_response.json()["id"] == game_id
        assert len(metadata_response.json()["players"]) == 2

        assert state_response.status_code == 200
        state_body = state_response.json()
        assert state_body["game_id"] == game_id
        assert state_body["event_sequence"] == 0
        assert state_body["state"]["turn"]["phase"] == "START_TURN"
        assert state_body["state_hash"]

        async with session_factory() as session:
            persisted_players = await session.execute(
                sa.select(players).where(players.c.game_id == UUID(str(game_id)))
            )
            assert len(persisted_players.mappings().all()) == 2
    finally:
        await delete_game(session_factory, str(game_id))


@pytest.mark.asyncio
async def test_create_game_rejects_malformed_player_setup(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/games",
        json={"seed": "bad", "players": [{"name": "Solo", "kind": "human"}]},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_missing_game_endpoints_return_404(client: httpx.AsyncClient) -> None:
    game_id = "00000000-0000-0000-0000-000000000404"

    for path in (
        f"/games/{game_id}",
        f"/games/{game_id}/state",
        f"/games/{game_id}/events",
        f"/games/{game_id}/rejected-actions",
        f"/games/{game_id}/events/stream",
    ):
        response = await client.get(path)
        assert response.status_code == 404


@pytest.mark.asyncio
async def test_legal_actions_require_actor_and_return_current_actions(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
) -> None:
    created = await create_game(client)
    game_id = created["id"]
    actor_id = created["players"][0]["id"]
    try:
        missing_actor = await client.get(f"/games/{game_id}/legal-actions")
        response = await client.get(
            f"/games/{game_id}/legal-actions",
            params={"actor_player_id": actor_id},
        )

        assert missing_actor.status_code == 422
        assert response.status_code == 200
        body = response.json()
        assert body["actor_player_id"] == actor_id
        assert body["state_hash"]
        assert body["event_sequence"] == 0
        assert {action["type"] for action in body["legal_actions"]} >= {
            "ROLL_DICE",
            "DECLARE_BANKRUPTCY",
        }
    finally:
        await delete_game(session_factory, str(game_id))


@pytest.mark.asyncio
async def test_legal_action_commits_real_ordered_events_without_rejection(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
) -> None:
    created = await create_game(client)
    game_id = created["id"]
    actor_id = created["players"][0]["id"]
    try:
        legal_response = await client.get(
            f"/games/{game_id}/legal-actions",
            params={"actor_player_id": actor_id},
        )
        roll_action = next(
            action
            for action in legal_response.json()["legal_actions"]
            if action["type"] == "ROLL_DICE"
        )

        action_response = await client.post(f"/games/{game_id}/actions", json=roll_action)
        events_response = await client.get(f"/games/{game_id}/events")

        assert action_response.status_code == 200
        body = action_response.json()
        assert body["status"] == "accepted"
        assert body["accepted_events"][0]["event_type"] == "DICE_ROLLED"
        assert [event["sequence"] for event in body["accepted_events"]] == list(
            range(1, len(body["accepted_events"]) + 1)
        )
        assert body["state"]["event_sequence"] == len(body["accepted_events"])
        assert body["state_hash"] == body["state"]["state_hash"]
        assert events_response.status_code == 200
        assert events_response.json()["events"] == body["accepted_events"]
        assert await table_count(session_factory, game_events, str(game_id)) == len(
            body["accepted_events"]
        )
        assert await table_count(session_factory, rejected_actions, str(game_id)) == 0
    finally:
        await delete_game(session_factory, str(game_id))


@pytest.mark.asyncio
async def test_illegal_action_creates_rejection_without_appending_event(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
) -> None:
    created = await create_game(client)
    game_id = created["id"]
    actor_id = created["players"][0]["id"]
    try:
        state = (await client.get(f"/games/{game_id}/state")).json()
        before_events = await table_count(session_factory, game_events, str(game_id))
        response = await client.post(
            f"/games/{game_id}/actions",
            json={
                "actor_id": actor_id,
                "type": "BUY_PROPERTY",
                "payload": {"property_id": "property_boardwalk"},
                "expected_state_hash": state["state_hash"],
                "expected_event_sequence": state["event_sequence"],
            },
        )

        assert response.status_code == 422
        assert response.json()["status"] == "rejected"
        assert response.json()["reason_code"] == "illegal_action"
        assert await table_count(session_factory, game_events, str(game_id)) == before_events
        assert await table_count(session_factory, rejected_actions, str(game_id)) == 1

        rejected_response = await client.get(f"/games/{game_id}/rejected-actions")
        assert rejected_response.status_code == 200
        assert len(rejected_response.json()["rejected_actions"]) == 1
    finally:
        await delete_game(session_factory, str(game_id))


@pytest.mark.asyncio
async def test_malformed_and_stale_actions_are_audited_once(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
) -> None:
    created = await create_game(client)
    game_id = created["id"]
    actor_id = created["players"][0]["id"]
    try:
        malformed = await client.post(f"/games/{game_id}/actions", json=["ROLL_DICE"])
        stale = await client.post(
            f"/games/{game_id}/actions",
            json={
                "actor_id": actor_id,
                "type": "ROLL_DICE",
                "payload": {},
                "expected_state_hash": "not-current",
                "expected_event_sequence": 0,
            },
        )

        assert malformed.status_code == 422
        assert malformed.json()["reason_code"] == "malformed_action"
        assert stale.status_code == 409
        assert stale.json()["reason_code"] == "stale_action"
        assert await table_count(session_factory, rejected_actions, str(game_id)) == 2
        assert await table_count(session_factory, game_events, str(game_id)) == 0
    finally:
        await delete_game(session_factory, str(game_id))


@pytest.mark.asyncio
async def test_negotiations_and_deals_create_minimal_durable_records(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
) -> None:
    created = await create_game(client)
    game_id = created["id"]
    player_ids = [player["id"] for player in created["players"]]
    try:
        bad_negotiation = await client.post(
            f"/games/{game_id}/negotiations",
            json={
                "opened_by_player_id": player_ids[0],
                "participant_player_ids": [player_ids[0]],
                "context": {},
            },
        )
        negotiation_response = await client.post(
            f"/games/{game_id}/negotiations",
            json={
                "opened_by_player_id": player_ids[0],
                "participant_player_ids": player_ids,
                "context": {"topic": "trade"},
            },
        )

        assert bad_negotiation.status_code == 422
        assert negotiation_response.status_code == 201
        negotiation = negotiation_response.json()
        assert negotiation["status"] == "opened"
        assert negotiation["participant_player_ids"] == player_ids
        assert negotiation["context"] == {"topic": "trade"}

        bad_deal = await client.post(
            f"/games/{game_id}/deals",
            json={
                "negotiation_id": "00000000-0000-0000-0000-000000000000",
                "proposed_by_player_id": player_ids[0],
                "terms": {"cash": 10},
            },
        )
        deal_response = await client.post(
            f"/games/{game_id}/deals",
            json={
                "negotiation_id": negotiation["id"],
                "proposed_by_player_id": player_ids[0],
                "terms": {"cash_offer": 10},
            },
        )

        assert bad_deal.status_code == 422
        assert deal_response.status_code == 201
        deal = deal_response.json()
        assert deal["status"] == "proposed"
        assert deal["negotiation_id"] == negotiation["id"]
        assert deal["terms"] == {"cash_offer": 10}
        assert await table_count(session_factory, negotiations, str(game_id)) == 1
        assert await table_count(session_factory, deals, str(game_id)) == 1
    finally:
        await delete_game(session_factory, str(game_id))


@pytest.mark.asyncio
async def test_ai_step_is_explicit_not_implemented_without_fallback_mutation(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
) -> None:
    created = await create_game(client)
    game_id = created["id"]
    ai_player_id = created["players"][1]["id"]
    try:
        malformed = await client.post(f"/games/{game_id}/ai/step", json={})
        response = await client.post(
            f"/games/{game_id}/ai/step",
            json={"player_id": ai_player_id, "request_context": {"mode": "manual"}},
        )

        assert malformed.status_code == 422
        assert response.status_code == 501
        assert response.json()["status"] == "not_implemented"
        assert response.json()["reason_code"] == "ai_runtime_not_implemented"
        assert await table_count(session_factory, game_events, str(game_id)) == 0
        assert await table_count(session_factory, rejected_actions, str(game_id)) == 0
    finally:
        await delete_game(session_factory, str(game_id))


@pytest.mark.asyncio
async def test_sse_stream_returns_existing_accepted_events(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
) -> None:
    created = await create_game(client)
    game_id = created["id"]
    actor_id = created["players"][0]["id"]
    try:
        legal_actions = await client.get(
            f"/games/{game_id}/legal-actions",
            params={"actor_player_id": actor_id},
        )
        await client.post(
            f"/games/{game_id}/actions",
            json=next(
                action
                for action in legal_actions.json()["legal_actions"]
                if action["type"] == "ROLL_DICE"
            ),
        )

        async with client.stream("GET", f"/games/{game_id}/events/stream") as response:
            body = await response.aread()

        text = body.decode("utf-8")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert "event: game_event" in text
        assert "DICE_ROLLED" in text
    finally:
        await delete_game(session_factory, str(game_id))


def test_openapi_includes_stage_4_4_endpoints(api_app: FastAPI) -> None:
    paths = api_app.openapi()["paths"]
    expected = {
        ("post", "/games"),
        ("get", "/games/{game_id}"),
        ("get", "/games/{game_id}/state"),
        ("get", "/games/{game_id}/legal-actions"),
        ("post", "/games/{game_id}/actions"),
        ("get", "/games/{game_id}/events"),
        ("get", "/games/{game_id}/rejected-actions"),
        ("post", "/games/{game_id}/negotiations"),
        ("post", "/games/{game_id}/deals"),
        ("post", "/games/{game_id}/ai/step"),
        ("get", "/games/{game_id}/events/stream"),
    }

    for method, path in expected:
        assert path in paths
        assert method in paths[path]
