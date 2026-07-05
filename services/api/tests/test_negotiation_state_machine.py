from __future__ import annotations

import os
from collections.abc import AsyncIterator, Mapping
from typing import Any
from uuid import UUID

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.db.metadata import deals, games, metadata, negotiation_messages, negotiations
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


async def create_game(client: httpx.AsyncClient, *, player_count: int = 3) -> dict[str, Any]:
    player_names = ["Ada", "Grace", "Linus", "Barbara", "Donald"]
    response = await client.post(
        "/games",
        json={
            "seed": "phase-6-stage-6-1-negotiations",
            "players": [
                {"name": player_names[index], "kind": "human"}
                for index in range(player_count)
            ],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def delete_game(session_factory: async_sessionmaker, game_id: str | UUID) -> None:
    async with session_factory() as session:
        async with session.begin():
            await session.execute(games.delete().where(games.c.id == UUID(str(game_id))))


async def create_negotiation(
    client: httpx.AsyncClient,
    game_id: str,
    player_ids: list[str],
) -> dict[str, Any]:
    response = await client.post(
        f"/games/{game_id}/negotiations",
        json={
            "opened_by_player_id": player_ids[0],
            "participant_player_ids": player_ids,
            "context": {"topic": "railroad trade"},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def propose_deal(
    client: httpx.AsyncClient,
    game_id: str,
    negotiation_id: str,
    proposer_id: str,
    *,
    terms: Mapping[str, Any],
    parent_deal_id: str | None = None,
) -> dict[str, Any]:
    response = await client.post(
        f"/games/{game_id}/deals",
        json={
            "negotiation_id": negotiation_id,
            "proposed_by_player_id": proposer_id,
            "parent_deal_id": parent_deal_id,
            "terms": dict(terms),
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def accept_deal(
    client: httpx.AsyncClient,
    game_id: str,
    deal_id: str,
    player_id: str,
) -> httpx.Response:
    return await client.post(
        f"/games/{game_id}/deals/{deal_id}/accept",
        json={"player_id": player_id},
    )


async def reject_deal(
    client: httpx.AsyncClient,
    game_id: str,
    deal_id: str,
    player_id: str,
) -> httpx.Response:
    return await client.post(
        f"/games/{game_id}/deals/{deal_id}/reject",
        json={"player_id": player_id},
    )


async def get_negotiation(
    client: httpx.AsyncClient,
    game_id: str,
    negotiation_id: str,
) -> dict[str, Any]:
    response = await client.get(f"/games/{game_id}/negotiations/{negotiation_id}")
    assert response.status_code == 200, response.text
    return response.json()


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


async def audit_messages(
    session_factory: async_sessionmaker,
    game_id: str | UUID,
) -> list[dict[str, Any]]:
    async with session_factory() as session:
        result = await session.execute(
            sa.select(negotiation_messages)
            .where(negotiation_messages.c.game_id == UUID(str(game_id)))
            .order_by(negotiation_messages.c.created_at, negotiation_messages.c.message_type)
        )
        return [dict(row) for row in result.mappings().all()]


@pytest.mark.asyncio
async def test_new_negotiation_starts_opened_with_participants_and_round_number(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
) -> None:
    created = await create_game(client)
    game_id = created["id"]
    player_ids = [player["id"] for player in created["players"]]
    try:
        negotiation = await create_negotiation(client, game_id, player_ids)
        listing = await client.get(f"/games/{game_id}/negotiations")
        loaded = await get_negotiation(client, game_id, negotiation["id"])

        assert listing.status_code == 200
        assert listing.json()["negotiations"][0]["id"] == negotiation["id"]
        assert loaded["status"] == "opened"
        assert loaded["participant_player_ids"] == player_ids
        assert loaded["round_number"] == 0
        assert loaded["pending_deal_id"] is None
        assert loaded["current_deal_id"] is None
        assert loaded["acceptances"] == {}
        assert loaded["expires_at"] is None
    finally:
        await delete_game(session_factory, game_id)


@pytest.mark.asyncio
async def test_lifecycle_transitions_are_deterministic_and_audited_all_required_parties_accept(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
) -> None:
    # Lifecycle transitions are deterministic and audited; all required parties accept.
    created = await create_game(client)
    game_id = created["id"]
    player_ids = [player["id"] for player in created["players"]]
    try:
        negotiation = await create_negotiation(client, game_id, player_ids)
        first_deal = await propose_deal(
            client,
            game_id,
            negotiation["id"],
            player_ids[0],
            terms={"cash_offer": 10},
        )
        active = await get_negotiation(client, game_id, negotiation["id"])

        assert first_deal["status"] == "proposed"
        assert active["status"] == "active"
        assert active["round_number"] == 1
        assert active["current_deal_id"] == first_deal["id"]
        assert active["pending_deal_id"] == first_deal["id"]

        for player_id in player_ids[:2]:
            response = await accept_deal(client, game_id, first_deal["id"], player_id)
            assert response.status_code == 200, response.text

        counter_deal = await propose_deal(
            client,
            game_id,
            negotiation["id"],
            player_ids[1],
            terms={"cash_offer": 25},
            parent_deal_id=first_deal["id"],
        )
        countered = await get_negotiation(client, game_id, negotiation["id"])

        assert counter_deal["parent_deal_id"] == first_deal["id"]
        assert countered["status"] == "countered"
        assert countered["round_number"] == 2
        assert countered["current_deal_id"] == counter_deal["id"]
        assert set(countered["acceptances"][first_deal["id"]]) == set(player_ids[:2])
        assert countered["acceptances"].get(counter_deal["id"], []) == []

        early_execute = await client.post(
            f"/games/{game_id}/negotiations/{negotiation['id']}/execute"
        )
        assert early_execute.status_code == 422
        assert early_execute.json()["status"] == "rejected"

        for player_id in player_ids[:2]:
            response = await accept_deal(client, game_id, counter_deal["id"], player_id)
            assert response.status_code == 200, response.text
        still_countered = await get_negotiation(client, game_id, negotiation["id"])
        assert still_countered["status"] == "countered"

        final_acceptance = await accept_deal(client, game_id, counter_deal["id"], player_ids[2])
        assert final_acceptance.status_code == 200, final_acceptance.text
        assert final_acceptance.json()["status"] == "accepted"

        accepted = await get_negotiation(client, game_id, negotiation["id"])
        assert accepted["status"] == "accepted"
        assert set(accepted["acceptances"][counter_deal["id"]]) == set(player_ids)
        assert [
            entry["to_status"] for entry in accepted["status_history"]
        ] == ["opened", "active", "countered", "accepted"]

        execute = await client.post(f"/games/{game_id}/negotiations/{negotiation['id']}/execute")
        assert execute.status_code == 200, execute.text
        assert execute.json()["status"] == "executed"
        assert execute.json()["status_history"][-1]["to_status"] == "executed"

        messages = await audit_messages(session_factory, game_id)
        status_transitions = [
            message["payload"]
            for message in messages
            if message["message_type"] == "NEGOTIATION_STATUS_CHANGED"
        ]
        accepted_messages = [
            message for message in messages if message["message_type"] == "NEGOTIATION_DEAL_ACCEPTED"
        ]
        assert [
            (message["from_status"], message["to_status"], message.get("deal_id"))
            for message in status_transitions
        ] == [
            ("opened", "active", first_deal["id"]),
            ("active", "countered", counter_deal["id"]),
            ("countered", "accepted", counter_deal["id"]),
            ("accepted", "executed", counter_deal["id"]),
        ]
        assert len(accepted_messages) == 5
    finally:
        await delete_game(session_factory, game_id)


@pytest.mark.asyncio
async def test_expired_negotiations_do_nothing_and_cannot_accept_reject_or_execute(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
) -> None:
    # Expired negotiations do nothing after terminal expiration.
    created = await create_game(client)
    game_id = created["id"]
    player_ids = [player["id"] for player in created["players"]]
    try:
        negotiation = await create_negotiation(client, game_id, player_ids)
        deal = await propose_deal(
            client,
            game_id,
            negotiation["id"],
            player_ids[0],
            terms={"cash_offer": 10},
        )

        expire = await client.post(f"/games/{game_id}/negotiations/{negotiation['id']}/expire")
        assert expire.status_code == 200, expire.text
        assert expire.json()["status"] == "expired"
        before = await get_negotiation(client, game_id, negotiation["id"])
        message_count = await table_count(session_factory, negotiation_messages, game_id)

        accept = await accept_deal(client, game_id, deal["id"], player_ids[1])
        reject = await reject_deal(client, game_id, deal["id"], player_ids[1])
        execute = await client.post(f"/games/{game_id}/negotiations/{negotiation['id']}/execute")
        after = await get_negotiation(client, game_id, negotiation["id"])

        assert accept.status_code == 422
        assert reject.status_code == 422
        assert execute.status_code == 422
        assert accept.json()["reason_code"] == "negotiation_expired"
        assert reject.json()["reason_code"] == "negotiation_expired"
        assert execute.json()["reason_code"] == "negotiation_expired"
        assert after == before
        assert await table_count(session_factory, negotiation_messages, game_id) == message_count
    finally:
        await delete_game(session_factory, game_id)


@pytest.mark.asyncio
async def test_rejected_deals_and_negotiations_cannot_execute_and_are_audited(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
) -> None:
    created = await create_game(client)
    game_id = created["id"]
    player_ids = [player["id"] for player in created["players"]]
    try:
        negotiation = await create_negotiation(client, game_id, player_ids)
        deal = await propose_deal(
            client,
            game_id,
            negotiation["id"],
            player_ids[0],
            terms={"cash_offer": 10},
        )

        reject = await reject_deal(client, game_id, deal["id"], player_ids[1])
        assert reject.status_code == 200, reject.text
        assert reject.json()["status"] == "rejected"
        rejected = await get_negotiation(client, game_id, negotiation["id"])
        assert rejected["status"] == "rejected"

        execute = await client.post(f"/games/{game_id}/negotiations/{negotiation['id']}/execute")
        accept_after_reject = await accept_deal(client, game_id, deal["id"], player_ids[2])
        assert execute.status_code == 422
        assert accept_after_reject.status_code == 422
        assert execute.json()["reason_code"] == "negotiation_rejected"
        assert accept_after_reject.json()["reason_code"] == "negotiation_rejected"
        assert await get_negotiation(client, game_id, negotiation["id"]) == rejected

        messages = await audit_messages(session_factory, game_id)
        assert any(message["message_type"] == "NEGOTIATION_DEAL_REJECTED" for message in messages)
        assert any(
            message["message_type"] == "NEGOTIATION_STATUS_CHANGED"
            and message["payload"]["to_status"] == "rejected"
            for message in messages
        )
        assert await table_count(session_factory, deals, game_id) == 1
        assert await table_count(session_factory, negotiations, game_id) == 1
    finally:
        await delete_game(session_factory, game_id)


def test_openapi_exposes_negotiation_lifecycle_paths(api_app: FastAPI) -> None:
    paths = api_app.openapi()["paths"]
    expected = {
        ("get", "/games/{game_id}/negotiations"),
        ("get", "/games/{game_id}/negotiations/{negotiation_id}"),
        ("post", "/games/{game_id}/negotiations"),
        ("post", "/games/{game_id}/deals"),
        ("post", "/games/{game_id}/deals/{deal_id}/accept"),
        ("post", "/games/{game_id}/deals/{deal_id}/reject"),
        ("post", "/games/{game_id}/negotiations/{negotiation_id}/expire"),
        ("post", "/games/{game_id}/negotiations/{negotiation_id}/execute"),
    }

    for method, path in expected:
        assert path in paths
        assert method in paths[path]
