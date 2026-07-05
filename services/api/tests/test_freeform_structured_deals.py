from __future__ import annotations

import os
from collections.abc import AsyncIterator, Mapping
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.db.metadata import contracts, deals, game_events, games, metadata, negotiation_messages
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
async def client(engine: AsyncEngine) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(
        settings=Settings(
            api_env="test",
            database_url=TEST_DATABASE_URL,
            cors_origins="http://localhost:3000",
        )
    )
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            yield client
    finally:
        await app.state.database_engine.dispose()


async def create_game(client: httpx.AsyncClient) -> dict[str, Any]:
    response = await client.post(
        "/games",
        json={
            "seed": "phase-6-stage-6-3-freeform-structured-deals",
            "players": [
                {"name": "Ada", "kind": "human"},
                {"name": "Grace", "kind": "human"},
                {"name": "Linus", "kind": "human"},
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
            "context": {"topic": "structured stage 6.3 deal"},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def structured_terms(
    player_ids: list[str],
    *,
    amount: int = 100,
) -> dict[str, Any]:
    return {
        "kind": "structured_deal",
        "deal_schema_version": 1,
        "participants": player_ids,
        "terms": [
            {
                "kind": "immediate_cash_transfer",
                "from_player_id": player_ids[0],
                "to_player_id": player_ids[1],
                "amount": amount,
            }
        ],
    }


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


async def get_negotiation(
    client: httpx.AsyncClient,
    game_id: str,
    negotiation_id: str,
) -> dict[str, Any]:
    response = await client.get(f"/games/{game_id}/negotiations/{negotiation_id}")
    assert response.status_code == 200, response.text
    return response.json()


async def create_freeform_message(
    client: httpx.AsyncClient,
    game_id: str,
    negotiation_id: str,
    sender_player_id: str,
    body: str,
    *,
    recipient_player_id: str | None = None,
) -> dict[str, Any]:
    payload = {"sender_player_id": sender_player_id, "body": body}
    if recipient_player_id is not None:
        payload["recipient_player_id"] = recipient_player_id
    response = await client.post(
        f"/games/{game_id}/negotiations/{negotiation_id}/messages",
        json=payload,
    )
    assert response.status_code == 201, response.text
    return response.json()["message"]


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


async def deal_rows(
    session_factory: async_sessionmaker,
    game_id: str | UUID,
) -> list[dict[str, Any]]:
    async with session_factory() as session:
        result = await session.execute(
            sa.select(deals).where(deals.c.game_id == UUID(str(game_id))).order_by(deals.c.version)
        )
        return [dict(row) for row in result.mappings().all()]


@pytest.mark.asyncio
async def test_freeform_message_persists_without_state_mutation(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
) -> None:
    # Players can chat freely without changing game state
    created = await create_game(client)
    game_id = created["id"]
    player_ids = [player["id"] for player in created["players"]]
    try:
        negotiation = await create_negotiation(client, game_id, player_ids)
        before_state = (await client.get(f"/games/{game_id}/state")).json()
        before_events = await table_count(session_factory, game_events, game_id)
        before_contracts = await table_count(session_factory, contracts, game_id)

        created_message = await client.post(
            f"/games/{game_id}/negotiations/{negotiation['id']}/messages",
            json={"sender_player_id": player_ids[0], "body": "Can we talk before locking terms?"},
        )
        listed = await client.get(f"/games/{game_id}/negotiations/{negotiation['id']}/messages")
        after_state = (await client.get(f"/games/{game_id}/state")).json()

        assert created_message.status_code == 201, created_message.text
        message = created_message.json()["message"]
        assert message["message_type"] == "freeform_message"
        assert message["body"] == "Can we talk before locking terms?"
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()["messages"]] == [message["id"]]
        assert after_state["state_hash"] == before_state["state_hash"]
        assert after_state["event_sequence"] == before_state["event_sequence"]
        assert await table_count(session_factory, game_events, game_id) == before_events
        assert await table_count(session_factory, contracts, game_id) == before_contracts

        messages = await audit_messages(session_factory, game_id)
        assert any(item["message_type"] == "freeform_message" for item in messages)
        assert any(item["message_type"] == "NEGOTIATION_MESSAGE_SENT" for item in messages)
    finally:
        await delete_game(session_factory, game_id)


@pytest.mark.asyncio
async def test_addressed_message_visibility_omitted_viewer_lists_only_public_messages(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
) -> None:
    created = await create_game(client)
    game_id = created["id"]
    player_ids = [player["id"] for player in created["players"]]
    try:
        negotiation = await create_negotiation(client, game_id, player_ids)
        public_message = await create_freeform_message(
            client,
            game_id,
            negotiation["id"],
            player_ids[0],
            "Public note for the whole table.",
        )
        await create_freeform_message(
            client,
            game_id,
            negotiation["id"],
            player_ids[0],
            "Private offer for Grace.",
            recipient_player_id=player_ids[1],
        )

        listed = await client.get(f"/games/{game_id}/negotiations/{negotiation['id']}/messages")

        assert listed.status_code == 200, listed.text
        assert [message["id"] for message in listed.json()["messages"]] == [public_message["id"]]
        assert [message["recipient_player_id"] for message in listed.json()["messages"]] == [None]
    finally:
        await delete_game(session_factory, game_id)


@pytest.mark.asyncio
async def test_addressed_message_visibility_viewer_sees_public_sent_and_received_messages(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
) -> None:
    created = await create_game(client)
    game_id = created["id"]
    player_ids = [player["id"] for player in created["players"]]
    try:
        negotiation = await create_negotiation(client, game_id, player_ids)
        public_message = await create_freeform_message(
            client,
            game_id,
            negotiation["id"],
            player_ids[0],
            "Public note for the whole table.",
        )
        sent_by_viewer = await create_freeform_message(
            client,
            game_id,
            negotiation["id"],
            player_ids[0],
            "Private offer from Ada to Grace.",
            recipient_player_id=player_ids[1],
        )
        received_by_viewer = await create_freeform_message(
            client,
            game_id,
            negotiation["id"],
            player_ids[1],
            "Private counter from Grace to Ada.",
            recipient_player_id=player_ids[0],
        )
        await create_freeform_message(
            client,
            game_id,
            negotiation["id"],
            player_ids[1],
            "Private side note from Grace to Linus.",
            recipient_player_id=player_ids[2],
        )

        listed = await client.get(
            f"/games/{game_id}/negotiations/{negotiation['id']}/messages",
            params={"viewer_player_id": player_ids[0]},
        )

        assert listed.status_code == 200, listed.text
        assert [message["id"] for message in listed.json()["messages"]] == [
            public_message["id"],
            sent_by_viewer["id"],
            received_by_viewer["id"],
        ]
    finally:
        await delete_game(session_factory, game_id)


@pytest.mark.asyncio
async def test_addressed_message_visibility_other_participant_cannot_see_messages_between_two_other_players(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
) -> None:
    created = await create_game(client)
    game_id = created["id"]
    player_ids = [player["id"] for player in created["players"]]
    try:
        negotiation = await create_negotiation(client, game_id, player_ids)
        public_message = await create_freeform_message(
            client,
            game_id,
            negotiation["id"],
            player_ids[0],
            "Public note for the whole table.",
        )
        hidden_sent_message = await create_freeform_message(
            client,
            game_id,
            negotiation["id"],
            player_ids[0],
            "Private offer from Ada to Grace.",
            recipient_player_id=player_ids[1],
        )
        hidden_received_message = await create_freeform_message(
            client,
            game_id,
            negotiation["id"],
            player_ids[1],
            "Private counter from Grace to Ada.",
            recipient_player_id=player_ids[0],
        )

        listed = await client.get(
            f"/games/{game_id}/negotiations/{negotiation['id']}/messages",
            params={"viewer_player_id": player_ids[2]},
        )

        visible_ids = [message["id"] for message in listed.json()["messages"]]
        assert listed.status_code == 200, listed.text
        assert visible_ids == [public_message["id"]]
        assert hidden_sent_message["id"] not in visible_ids
        assert hidden_received_message["id"] not in visible_ids
    finally:
        await delete_game(session_factory, game_id)


@pytest.mark.asyncio
async def test_addressed_message_visibility_rejects_non_participant_viewer(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
) -> None:
    created = await create_game(client)
    game_id = created["id"]
    player_ids = [player["id"] for player in created["players"]]
    try:
        negotiation = await create_negotiation(client, game_id, player_ids)
        await create_freeform_message(
            client,
            game_id,
            negotiation["id"],
            player_ids[0],
            "Public note for the whole table.",
        )

        listed = await client.get(
            f"/games/{game_id}/negotiations/{negotiation['id']}/messages",
            params={"viewer_player_id": str(uuid4())},
        )

        assert listed.status_code == 422, listed.text
        assert listed.json()["status"] == "rejected"
        assert listed.json()["reason_code"] == "viewer_not_participant"
        assert listed.json()["validation_errors"] == [
            {
                "code": "viewer_not_participant",
                "message": "viewer_player_id must be a negotiation participant",
                "field": "viewer_player_id",
            }
        ]
    finally:
        await delete_game(session_factory, game_id)


@pytest.mark.asyncio
async def test_structured_deal_stores_schema_version_terms_hash_version_and_participants(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
) -> None:
    created = await create_game(client)
    game_id = created["id"]
    player_ids = [player["id"] for player in created["players"]]
    try:
        negotiation = await create_negotiation(client, game_id, player_ids)

        response = await propose_deal(
            client,
            game_id,
            negotiation["id"],
            player_ids[0],
            terms=structured_terms(player_ids),
        )

        assert response.status_code == 201, response.text
        deal = response.json()
        assert deal["structured_deal"] is True
        assert deal["terms"]["kind"] == "structured_deal"
        assert deal["deal_schema_version"] == 1
        assert deal["terms"]["deal_schema_version"] == 1
        assert deal["terms_hash"] == deal["terms"]["terms_hash"]
        assert len(deal["terms_hash"]) == 64
        assert deal["version"] == 1
        assert deal["deal_version"] == 1
        assert deal["participant_player_ids"] == player_ids
        assert deal["eligible_for_contract"] is False

        loaded = await get_negotiation(client, game_id, negotiation["id"])
        assert loaded["current_deal_id"] == deal["id"]
        assert loaded["current_terms_hash"] == deal["terms_hash"]
        assert loaded["current_deal_version"] == 1
    finally:
        await delete_game(session_factory, game_id)


@pytest.mark.asyncio
async def test_invalid_structured_deal_returns_and_persists_validation_errors(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
) -> None:
    # Deal validation errors
    created = await create_game(client)
    game_id = created["id"]
    player_ids = [player["id"] for player in created["players"]]
    try:
        negotiation = await create_negotiation(client, game_id, player_ids)
        before = await get_negotiation(client, game_id, negotiation["id"])

        response = await propose_deal(
            client,
            game_id,
            negotiation["id"],
            player_ids[0],
            terms={
                "kind": "structured_deal",
                "deal_schema_version": 1,
                "participants": [player_ids[0]],
                "terms": [],
            },
        )

        assert response.status_code == 422, response.text
        body = response.json()
        assert body["status"] == "rejected"
        assert body["reason_code"] == "invalid_structured_deal"
        assert body["validation_errors"]
        assert any(error["field"] in {"participants", "terms"} for error in body["validation_errors"])

        after = await get_negotiation(client, game_id, negotiation["id"])
        assert after["status"] == before["status"]
        assert after["round_number"] == before["round_number"]
        assert after["current_deal_id"] is None
        assert after["current_terms_hash"] is None

        rows = await deal_rows(session_factory, game_id)
        rejected = [row for row in rows if row["status"] == "rejected"]
        assert len(rejected) == 1
        assert rejected[0]["validation_errors"] == body["validation_errors"]
    finally:
        await delete_game(session_factory, game_id)


@pytest.mark.asyncio
async def test_counteroffer_links_parent_increments_version_and_invalidates_acceptances(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
) -> None:
    # changed counteroffer invalidates previous acceptances
    # Exact-term acceptance requirement
    created = await create_game(client)
    game_id = created["id"]
    player_ids = [player["id"] for player in created["players"]]
    try:
        negotiation = await create_negotiation(client, game_id, player_ids)
        first_response = await propose_deal(
            client,
            game_id,
            negotiation["id"],
            player_ids[0],
            terms=structured_terms(player_ids, amount=100),
        )
        assert first_response.status_code == 201, first_response.text
        first = first_response.json()
        for player_id in player_ids[:2]:
            accepted = await accept_deal(client, game_id, first["id"], player_id)
            assert accepted.status_code == 200, accepted.text

        counter_response = await propose_deal(
            client,
            game_id,
            negotiation["id"],
            player_ids[1],
            terms=structured_terms(player_ids, amount=150),
            parent_deal_id=first["id"],
        )

        assert counter_response.status_code == 201, counter_response.text
        counter = counter_response.json()
        assert counter["parent_deal_id"] == first["id"]
        assert counter["version"] == 2
        assert counter["deal_version"] == 2
        assert counter["terms_hash"] != first["terms_hash"]

        loaded = await get_negotiation(client, game_id, negotiation["id"])
        assert loaded["current_deal_id"] == counter["id"]
        assert loaded["current_terms_hash"] == counter["terms_hash"]
        assert loaded["invalidated_acceptances"][first["id"]] == player_ids[:2]

        stale_acceptance = await accept_deal(client, game_id, first["id"], player_ids[2])
        assert stale_acceptance.status_code == 422
        assert stale_acceptance.json()["reason_code"] == "exact_term_acceptance_required"

        messages = await audit_messages(session_factory, game_id)
        assert any(item["message_type"] == "NEGOTIATION_COUNTEROFFER_PROPOSED" for item in messages)
        assert any(item["message_type"] == "NEGOTIATION_ACCEPTANCES_INVALIDATED" for item in messages)
    finally:
        await delete_game(session_factory, game_id)


@pytest.mark.asyncio
async def test_only_current_accepted_structured_deal_is_eligible_for_contract(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
) -> None:
    # Only structured accepted deals can become contracts
    created = await create_game(client)
    game_id = created["id"]
    player_ids = [player["id"] for player in created["players"]]
    try:
        legacy_negotiation = await create_negotiation(client, game_id, player_ids)
        legacy_response = await propose_deal(
            client,
            game_id,
            legacy_negotiation["id"],
            player_ids[0],
            terms={"cash_offer": 10},
        )
        assert legacy_response.status_code == 201, legacy_response.text
        legacy = legacy_response.json()
        for player_id in player_ids:
            accepted = await accept_deal(client, game_id, legacy["id"], player_id)
            assert accepted.status_code == 200, accepted.text
            legacy = accepted.json()
        assert legacy["status"] == "accepted"
        assert legacy["structured_deal"] is False
        assert legacy["eligible_for_contract"] is False

        structured_negotiation = await create_negotiation(client, game_id, player_ids)
        structured_response = await propose_deal(
            client,
            game_id,
            structured_negotiation["id"],
            player_ids[0],
            terms=structured_terms(player_ids, amount=125),
        )
        assert structured_response.status_code == 201, structured_response.text
        structured = structured_response.json()
        for player_id in player_ids:
            accepted = await accept_deal(client, game_id, structured["id"], player_id)
            assert accepted.status_code == 200, accepted.text
            structured = accepted.json()

        assert structured["status"] == "accepted"
        assert structured["structured_deal"] is True
        assert structured["eligible_for_contract"] is True
        assert await table_count(session_factory, contracts, game_id) == 0
    finally:
        await delete_game(session_factory, game_id)
