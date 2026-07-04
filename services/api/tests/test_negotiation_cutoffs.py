from __future__ import annotations

import os
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.db.metadata import contracts, deals, game_events, games, metadata, negotiation_messages, negotiations
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


async def create_game(
    client: httpx.AsyncClient,
    *,
    cutoffs: Mapping[str, Any],
) -> dict[str, Any]:
    response = await client.post(
        "/games",
        json={
            "seed": "phase-6-stage-6-2-negotiation-cutoffs",
            "players": [
                {"name": "Ada", "kind": "human"},
                {"name": "Grace", "kind": "human"},
                {"name": "Linus", "kind": "ai"},
            ],
            "settings": {"negotiation_cutoffs": dict(cutoffs)},
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
    *,
    expires_at: datetime | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "opened_by_player_id": player_ids[0],
        "participant_player_ids": player_ids,
        "context": {"topic": "cutoff test"},
    }
    if expires_at is not None:
        payload["expires_at"] = expires_at.isoformat()
    response = await client.post(f"/games/{game_id}/negotiations", json=payload)
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
) -> httpx.Response:
    return await client.post(
        f"/games/{game_id}/deals",
        json={
            "negotiation_id": negotiation_id,
            "proposed_by_player_id": proposer_id,
            "parent_deal_id": parent_deal_id,
            "terms": dict(terms),
        },
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


async def set_negotiation_expires_at(
    session_factory: async_sessionmaker,
    negotiation_id: str | UUID,
    expires_at: datetime,
) -> None:
    async with session_factory() as session:
        async with session.begin():
            result = await session.execute(
                sa.select(negotiations.c.context).where(
                    negotiations.c.id == UUID(str(negotiation_id))
                )
            )
            context = dict(result.scalar_one())
            context["expires_at"] = expires_at.isoformat()
            await session.execute(
                negotiations.update()
                .where(negotiations.c.id == UUID(str(negotiation_id)))
                .values(context=context)
            )


async def deal_status(
    session_factory: async_sessionmaker,
    deal_id: str | UUID,
) -> str:
    async with session_factory() as session:
        result = await session.execute(sa.select(deals.c.status).where(deals.c.id == UUID(str(deal_id))))
        return str(result.scalar_one())


async def assert_cutoff_expiration(
    *,
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
    game_id: str,
    negotiation_id: str,
    response: httpx.Response,
    cutoff_reason: str,
) -> dict[str, Any]:
    assert response.status_code == 422, response.text
    rejection = response.json()
    assert rejection["status"] == "rejected"
    assert rejection["reason_code"] == cutoff_reason
    assert rejection["validation_errors"][0]["code"] == cutoff_reason

    negotiation = await get_negotiation(client, game_id, negotiation_id)
    assert negotiation["status"] == "expired"
    assert negotiation["expired_by_cutoff"] is True
    assert negotiation["cutoff_reason"] == cutoff_reason

    cutoff_audits = [
        message
        for message in await audit_messages(session_factory, game_id)
        if message["message_type"] == "NEGOTIATION_EXPIRED_BY_CUTOFF"
    ]
    assert cutoff_audits
    assert cutoff_audits[-1]["payload"]["cutoff_reason"] == cutoff_reason
    assert cutoff_audits[-1]["payload"]["no_substitute_action"] is True
    return negotiation


@pytest.mark.asyncio
async def test_max_rounds_per_negotiation_window_expires_and_rejects_counteroffer(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
) -> None:
    # max rounds per negotiation window
    created = await create_game(
        client,
        cutoffs={
            "max_rounds": 1,
            "max_proposals_per_player": 10,
            "max_active_seconds": 3600,
            "max_ai_decision_attempts": 10,
            "max_pending_offers_per_player": 10,
            "negotiation_intensity": "focused",
        },
    )
    game_id = created["id"]
    player_ids = [player["id"] for player in created["players"]]
    try:
        negotiation = await create_negotiation(client, game_id, player_ids)
        first = await propose_deal(
            client,
            game_id,
            negotiation["id"],
            player_ids[0],
            terms={"cash_offer": 10},
        )
        assert first.status_code == 201, first.text
        before_deals = await table_count(session_factory, deals, game_id)

        counter = await propose_deal(
            client,
            game_id,
            negotiation["id"],
            player_ids[1],
            parent_deal_id=first.json()["id"],
            terms={"cash_offer": 15},
        )

        expired = await assert_cutoff_expiration(
            client=client,
            session_factory=session_factory,
            game_id=game_id,
            negotiation_id=negotiation["id"],
            response=counter,
            cutoff_reason="negotiation_cutoff_max_rounds",
        )
        assert expired["round_number"] == 1
        assert await table_count(session_factory, deals, game_id) == before_deals
    finally:
        await delete_game(session_factory, game_id)


@pytest.mark.asyncio
async def test_max_proposals_per_player_per_window_expires_and_rejects_extra_proposal(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
) -> None:
    # max proposals per player per window
    created = await create_game(
        client,
        cutoffs={
            "max_rounds": 10,
            "max_proposals_per_player": 1,
            "max_active_seconds": 3600,
            "max_ai_decision_attempts": 10,
            "max_pending_offers_per_player": 10,
            "negotiation_intensity": "standard",
        },
    )
    game_id = created["id"]
    player_ids = [player["id"] for player in created["players"]]
    try:
        negotiation = await create_negotiation(client, game_id, player_ids)
        first = await propose_deal(
            client,
            game_id,
            negotiation["id"],
            player_ids[0],
            terms={"cash_offer": 10},
        )
        assert first.status_code == 201, first.text
        second = await propose_deal(
            client,
            game_id,
            negotiation["id"],
            player_ids[1],
            parent_deal_id=first.json()["id"],
            terms={"cash_offer": 15},
        )
        assert second.status_code == 201, second.text
        before_deals = await table_count(session_factory, deals, game_id)

        third = await propose_deal(
            client,
            game_id,
            negotiation["id"],
            player_ids[0],
            parent_deal_id=second.json()["id"],
            terms={"cash_offer": 20},
        )

        expired = await assert_cutoff_expiration(
            client=client,
            session_factory=session_factory,
            game_id=game_id,
            negotiation_id=negotiation["id"],
            response=third,
            cutoff_reason="negotiation_cutoff_max_proposals_per_player",
        )
        assert expired["proposal_counts_by_player_id"][player_ids[0]] == 1
        assert await table_count(session_factory, deals, game_id) == before_deals
    finally:
        await delete_game(session_factory, game_id)


@pytest.mark.asyncio
async def test_max_active_wall_clock_duration_long_negotiations_close_as_expired(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
) -> None:
    # max active wall-clock duration
    # Long negotiations close as expired
    created = await create_game(
        client,
        cutoffs={
            "max_rounds": 10,
            "max_proposals_per_player": 10,
            "max_active_seconds": 3600,
            "max_ai_decision_attempts": 10,
            "max_pending_offers_per_player": 10,
            "negotiation_intensity": "slow",
        },
    )
    game_id = created["id"]
    player_ids = [player["id"] for player in created["players"]]
    try:
        negotiation = await create_negotiation(
            client,
            game_id,
            player_ids,
            expires_at=datetime.now(UTC) - timedelta(seconds=5),
        )
        response = await propose_deal(
            client,
            game_id,
            negotiation["id"],
            player_ids[0],
            terms={"cash_offer": 10},
        )

        expired = await assert_cutoff_expiration(
            client=client,
            session_factory=session_factory,
            game_id=game_id,
            negotiation_id=negotiation["id"],
            response=response,
            cutoff_reason="negotiation_cutoff_max_active_seconds",
        )
        assert expired["proposal_counts_by_player_id"] == {}
        assert await table_count(session_factory, deals, game_id) == 0
    finally:
        await delete_game(session_factory, game_id)


@pytest.mark.asyncio
async def test_max_ai_decision_attempts_per_negotiation_message_expires_without_runtime(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
) -> None:
    # max AI decision attempts per negotiation message
    created = await create_game(
        client,
        cutoffs={
            "max_rounds": 10,
            "max_proposals_per_player": 10,
            "max_active_seconds": 3600,
            "max_ai_decision_attempts": 1,
            "max_pending_offers_per_player": 10,
            "negotiation_intensity": "ai-limited",
        },
    )
    game_id = created["id"]
    player_ids = [player["id"] for player in created["players"]]
    try:
        negotiation = await create_negotiation(client, game_id, player_ids)
        first = await propose_deal(
            client,
            game_id,
            negotiation["id"],
            player_ids[0],
            terms={"cash_offer": 10},
        )
        assert first.status_code == 201, first.text
        message_id = str((await audit_messages(session_factory, game_id))[0]["id"])

        first_attempt = await client.post(
            f"/games/{game_id}/negotiations/{negotiation['id']}/messages/{message_id}/ai-decision-attempts",
            json={"player_id": player_ids[2]},
        )
        assert first_attempt.status_code == 200, first_attempt.text
        assert first_attempt.json()["ai_decision_attempts_by_message_id"][message_id] == 1

        second_attempt = await client.post(
            f"/games/{game_id}/negotiations/{negotiation['id']}/messages/{message_id}/ai-decision-attempts",
            json={"player_id": player_ids[2]},
        )

        expired = await assert_cutoff_expiration(
            client=client,
            session_factory=session_factory,
            game_id=game_id,
            negotiation_id=negotiation["id"],
            response=second_attempt,
            cutoff_reason="negotiation_cutoff_max_ai_decision_attempts",
        )
        assert expired["ai_decision_attempts_by_message_id"][message_id] == 2
    finally:
        await delete_game(session_factory, game_id)


@pytest.mark.asyncio
async def test_max_pending_offers_per_player_expires_and_rejects_extra_pending_offer(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
) -> None:
    # max pending offers per player
    created = await create_game(
        client,
        cutoffs={
            "max_rounds": 10,
            "max_proposals_per_player": 10,
            "max_active_seconds": 3600,
            "max_ai_decision_attempts": 10,
            "max_pending_offers_per_player": 1,
            "negotiation_intensity": "strict-pending",
        },
    )
    game_id = created["id"]
    player_ids = [player["id"] for player in created["players"]]
    try:
        negotiation = await create_negotiation(client, game_id, player_ids)
        first = await propose_deal(
            client,
            game_id,
            negotiation["id"],
            player_ids[0],
            terms={"cash_offer": 10},
        )
        assert first.status_code == 201, first.text
        second = await propose_deal(
            client,
            game_id,
            negotiation["id"],
            player_ids[1],
            parent_deal_id=first.json()["id"],
            terms={"cash_offer": 15},
        )
        assert second.status_code == 201, second.text
        before_deals = await table_count(session_factory, deals, game_id)

        third = await propose_deal(
            client,
            game_id,
            negotiation["id"],
            player_ids[0],
            parent_deal_id=second.json()["id"],
            terms={"cash_offer": 20},
        )

        expired = await assert_cutoff_expiration(
            client=client,
            session_factory=session_factory,
            game_id=game_id,
            negotiation_id=negotiation["id"],
            response=third,
            cutoff_reason="negotiation_cutoff_max_pending_offers_per_player",
        )
        assert expired["pending_offer_counts_by_player_id"][player_ids[0]] == 1
        assert await table_count(session_factory, deals, game_id) == before_deals
    finally:
        await delete_game(session_factory, game_id)


@pytest.mark.asyncio
async def test_active_duration_cutoff_rejects_accept_and_execute_without_applying_mutation(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
) -> None:
    created = await create_game(
        client,
        cutoffs={
            "max_rounds": 10,
            "max_proposals_per_player": 10,
            "max_active_seconds": 3600,
            "max_ai_decision_attempts": 10,
            "max_pending_offers_per_player": 10,
            "negotiation_intensity": "accept-execute-cutoff",
        },
    )
    game_id = created["id"]
    player_ids = [player["id"] for player in created["players"]]
    try:
        accept_negotiation = await create_negotiation(client, game_id, player_ids)
        accept_deal = await propose_deal(
            client,
            game_id,
            accept_negotiation["id"],
            player_ids[0],
            terms={"cash_offer": 10},
        )
        assert accept_deal.status_code == 201, accept_deal.text
        await set_negotiation_expires_at(
            session_factory,
            accept_negotiation["id"],
            datetime.now(UTC) - timedelta(seconds=5),
        )

        accept_response = await client.post(
            f"/games/{game_id}/deals/{accept_deal.json()['id']}/accept",
            json={"player_id": player_ids[1]},
        )
        accept_expired = await assert_cutoff_expiration(
            client=client,
            session_factory=session_factory,
            game_id=game_id,
            negotiation_id=accept_negotiation["id"],
            response=accept_response,
            cutoff_reason="negotiation_cutoff_max_active_seconds",
        )
        assert accept_expired["acceptances"][accept_deal.json()["id"]] == []
        assert await deal_status(session_factory, accept_deal.json()["id"]) == "expired"

        execute_negotiation = await create_negotiation(client, game_id, player_ids)
        execute_deal = await propose_deal(
            client,
            game_id,
            execute_negotiation["id"],
            player_ids[0],
            terms={"cash_offer": 30},
        )
        assert execute_deal.status_code == 201, execute_deal.text
        for player_id in player_ids:
            response = await client.post(
                f"/games/{game_id}/deals/{execute_deal.json()['id']}/accept",
                json={"player_id": player_id},
            )
            assert response.status_code == 200, response.text
        await set_negotiation_expires_at(
            session_factory,
            execute_negotiation["id"],
            datetime.now(UTC) - timedelta(seconds=5),
        )

        execute_response = await client.post(
            f"/games/{game_id}/negotiations/{execute_negotiation['id']}/execute"
        )
        execute_expired = await assert_cutoff_expiration(
            client=client,
            session_factory=session_factory,
            game_id=game_id,
            negotiation_id=execute_negotiation["id"],
            response=execute_response,
            cutoff_reason="negotiation_cutoff_max_active_seconds",
        )
        assert execute_expired["status"] == "expired"
        assert await deal_status(session_factory, execute_deal.json()["id"]) == "accepted"
        assert await table_count(session_factory, game_events, game_id) == 0
        assert await table_count(session_factory, contracts, game_id) == 0
    finally:
        await delete_game(session_factory, game_id)


@pytest.mark.asyncio
async def test_expiration_never_causes_substitute_action_and_legal_next_action_after_expiration(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
) -> None:
    # Expiration never causes a substitute action
    # legal next action after expiration
    created = await create_game(
        client,
        cutoffs={
            "max_rounds": 1,
            "max_proposals_per_player": 10,
            "max_active_seconds": 3600,
            "max_ai_decision_attempts": 10,
            "max_pending_offers_per_player": 10,
            "negotiation_intensity": "no-fallback",
        },
    )
    game_id = created["id"]
    player_ids = [player["id"] for player in created["players"]]
    try:
        before_game = (await client.get(f"/games/{game_id}")).json()
        negotiation = await create_negotiation(client, game_id, player_ids)
        first = await propose_deal(
            client,
            game_id,
            negotiation["id"],
            player_ids[0],
            terms={"cash_offer": 10},
        )
        assert first.status_code == 201, first.text

        response = await propose_deal(
            client,
            game_id,
            negotiation["id"],
            player_ids[1],
            parent_deal_id=first.json()["id"],
            terms={"cash_offer": 15},
        )
        await assert_cutoff_expiration(
            client=client,
            session_factory=session_factory,
            game_id=game_id,
            negotiation_id=negotiation["id"],
            response=response,
            cutoff_reason="negotiation_cutoff_max_rounds",
        )

        after_game = (await client.get(f"/games/{game_id}")).json()
        legal_actions = await client.get(
            f"/games/{game_id}/legal-actions",
            params={"actor_player_id": player_ids[0]},
        )
        events_response = await client.get(f"/games/{game_id}/events")

        assert legal_actions.status_code == 200, legal_actions.text
        assert legal_actions.json()["legal_actions"]
        assert events_response.status_code == 200
        assert events_response.json()["events"] == []
        assert await table_count(session_factory, game_events, game_id) == 0
        assert await table_count(session_factory, contracts, game_id) == 0
        assert [player["state"] for player in after_game["players"]] == [
            player["state"] for player in before_game["players"]
        ]
    finally:
        await delete_game(session_factory, game_id)
