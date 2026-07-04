from __future__ import annotations

import os
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.contracts.trigger_system import (
    bankruptcy_trigger,
    property_transfer_trigger,
    rent_trigger,
    round_trigger,
    time_trigger,
    turn_end_trigger,
    turn_start_trigger,
)
from app.core.config import Settings
from app.db.metadata import contracts, game_events, games, metadata, negotiation_messages, obligations
from app.db.persistence import AcceptedEventTemplate, EventPersistence
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
            "seed": "phase-6-stage-6-5-contract-execution",
            "players": [
                {"name": "Ada", "kind": "human"},
                {"name": "Grace", "kind": "human"},
                {"name": "Linus", "kind": "ai"},
            ],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def delete_game(session_factory: async_sessionmaker, game_id: str | UUID) -> None:
    async with session_factory() as session:
        async with session.begin():
            await session.execute(games.delete().where(games.c.id == UUID(str(game_id))))


async def table_count(session_factory: async_sessionmaker, table: sa.Table, game_id: str | UUID) -> int:
    async with session_factory() as session:
        result = await session.execute(
            sa.select(sa.func.count()).select_from(table).where(table.c.game_id == UUID(str(game_id)))
        )
        return int(result.scalar_one())


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
            "context": {"topic": "stage 6.5 contract execution"},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def propose_deal(
    client: httpx.AsyncClient,
    game_id: str,
    negotiation_id: str,
    proposer_id: str,
    terms: Mapping[str, Any],
) -> dict[str, Any]:
    response = await client.post(
        f"/games/{game_id}/deals",
        json={
            "negotiation_id": negotiation_id,
            "proposed_by_player_id": proposer_id,
            "terms": dict(terms),
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def accept_for_all_players(
    client: httpx.AsyncClient,
    game_id: str,
    deal_id: str,
    player_ids: list[str],
) -> dict[str, Any]:
    deal: dict[str, Any] = {}
    for player_id in player_ids:
        response = await client.post(
            f"/games/{game_id}/deals/{deal_id}/accept",
            json={"player_id": player_id},
        )
        assert response.status_code == 200, response.text
        deal = response.json()
    assert deal["status"] == "accepted"
    return deal


async def accepted_structured_deal(
    client: httpx.AsyncClient,
    game_id: str,
    player_ids: list[str],
    terms: list[dict[str, Any]],
) -> dict[str, Any]:
    negotiation = await create_negotiation(client, game_id, player_ids)
    proposal = await propose_deal(
        client,
        game_id,
        negotiation["id"],
        player_ids[0],
        {
            "kind": "structured_deal",
            "deal_schema_version": 1,
            "participants": player_ids,
            "terms": terms,
        },
    )
    return await accept_for_all_players(client, game_id, proposal["id"], player_ids)


async def audit_rows(session_factory: async_sessionmaker, game_id: str | UUID) -> list[dict[str, Any]]:
    async with session_factory() as session:
        result = await session.execute(
            sa.select(negotiation_messages)
            .where(negotiation_messages.c.game_id == UUID(str(game_id)))
            .order_by(negotiation_messages.c.created_at, negotiation_messages.c.id)
        )
        return [dict(row) for row in result.mappings().all()]


async def event_rows(session_factory: async_sessionmaker, game_id: str | UUID) -> list[dict[str, Any]]:
    async with session_factory() as session:
        result = await session.execute(
            sa.select(game_events)
            .where(game_events.c.game_id == UUID(str(game_id)))
            .order_by(game_events.c.sequence)
        )
        return [dict(row) for row in result.mappings().all()]


@pytest.mark.asyncio
async def test_accepted_structured_deal_creates_one_durable_contract_and_obligations(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
) -> None:
    # Accepted contracts automatically enforce future obligations
    created = await create_game(client)
    game_id = created["id"]
    player_ids = [player["id"] for player in created["players"]]
    try:
        deal = await accepted_structured_deal(
            client,
            game_id,
            player_ids,
            [
                {
                    "kind": "deferred_cash_payment",
                    "from_player_id": player_ids[0],
                    "to_player_id": player_ids[1],
                    "amount": 75,
                    "due_turn": 1,
                }
            ],
        )

        first = await client.post(f"/games/{game_id}/contracts/from-deal", json={"deal_id": deal["id"]})
        second = await client.post(f"/games/{game_id}/contracts/from-deal", json={"deal_id": deal["id"]})

        assert first.status_code == 201, first.text
        assert second.status_code == 200, second.text
        assert first.json()["contract"]["id"] == second.json()["contract"]["id"]
        assert await table_count(session_factory, contracts, game_id) == 1
        assert await table_count(session_factory, obligations, game_id) == 1

        listed_contracts = await client.get(f"/games/{game_id}/contracts")
        listed_obligations = await client.get(f"/games/{game_id}/obligations")
        assert listed_contracts.status_code == 200
        assert listed_obligations.status_code == 200
        assert listed_contracts.json()["contracts"][0]["deal_id"] == deal["id"]
        assert listed_obligations.json()["obligations"][0]["status"] == "pending"

        audit = await audit_rows(session_factory, game_id)
        assert [row["message_type"] for row in audit].count("CONTRACT_CREATED") == 1
        assert [row["message_type"] for row in audit].count("OBLIGATION_SCHEDULED") == 1
    finally:
        await delete_game(session_factory, game_id)


@pytest.mark.asyncio
async def test_contract_creation_rejects_non_accepted_or_non_structured_deals(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
) -> None:
    created = await create_game(client)
    game_id = created["id"]
    player_ids = [player["id"] for player in created["players"]]
    try:
        negotiation = await create_negotiation(client, game_id, player_ids)
        proposed_structured = await propose_deal(
            client,
            game_id,
            negotiation["id"],
            player_ids[0],
            {
                "kind": "structured_deal",
                "deal_schema_version": 1,
                "participants": player_ids,
                "terms": [
                    {
                        "kind": "deferred_cash_payment",
                        "from_player_id": player_ids[0],
                        "to_player_id": player_ids[1],
                        "amount": 25,
                        "due_turn": 1,
                    }
                ],
            },
        )
        proposed_response = await client.post(
            f"/games/{game_id}/contracts/from-deal",
            json={"deal_id": proposed_structured["id"]},
        )
        assert proposed_response.status_code == 422
        assert proposed_response.json()["reason_code"] == "deal_not_contract_eligible"

        legacy_negotiation = await create_negotiation(client, game_id, player_ids)
        legacy_proposal = await propose_deal(
            client,
            game_id,
            legacy_negotiation["id"],
            player_ids[0],
            {"cash_offer": 10},
        )
        legacy_accepted = await accept_for_all_players(client, game_id, legacy_proposal["id"], player_ids)

        legacy_response = await client.post(
            f"/games/{game_id}/contracts/from-deal",
            json={"deal_id": legacy_accepted["id"]},
        )
        assert legacy_response.status_code == 422
        assert legacy_response.json()["reason_code"] == "deal_not_contract_eligible"
        assert await table_count(session_factory, contracts, game_id) == 0
    finally:
        await delete_game(session_factory, game_id)


@pytest.mark.asyncio
async def test_contract_settlement_creates_accepted_game_events_for_cash(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
) -> None:
    # Contract settlement creates accepted game events
    created = await create_game(client)
    game_id = created["id"]
    player_ids = [player["id"] for player in created["players"]]
    try:
        deal = await accepted_structured_deal(
            client,
            game_id,
            player_ids,
            [
                {
                    "kind": "immediate_cash_transfer",
                    "from_player_id": player_ids[0],
                    "to_player_id": player_ids[1],
                    "amount": 90,
                }
            ],
        )
        contract_response = await client.post(
            f"/games/{game_id}/contracts/from-deal",
            json={"deal_id": deal["id"]},
        )
        contract = contract_response.json()["contract"]
        obligation = contract_response.json()["obligations"][0]

        settlement = await client.post(
            f"/games/{game_id}/contracts/{contract['id']}/settle",
            json={"obligation_id": obligation["id"]},
        )

        assert settlement.status_code == 200, settlement.text
        body = settlement.json()
        assert body["settled_obligation_ids"] == [obligation["id"]]
        assert [event["event_type"] for event in body["accepted_events"]] == [
            "PLAYER_CASH_DELTA",
            "PLAYER_CASH_DELTA",
        ]
        state = (await client.get(f"/games/{game_id}/state")).json()["state"]
        cash_by_player = {player["id"]: player["cash"] for player in state["players"]}
        assert cash_by_player[player_ids[0]] == 1410
        assert cash_by_player[player_ids[1]] == 1590

        audit = await audit_rows(session_factory, game_id)
        settlement_audits = [row for row in audit if row["message_type"] == "CONTRACT_SETTLEMENT_EVENT"]
        assert settlement_audits
        assert settlement_audits[-1]["payload"]["contract_id"] == contract["id"]
        assert settlement_audits[-1]["payload"]["obligation_id"] == obligation["id"]
        assert settlement_audits[-1]["payload"]["source_deal_id"] == deal["id"]
        assert any(row["message_type"] == "OBLIGATION_SETTLED" for row in audit)
    finally:
        await delete_game(session_factory, game_id)


@pytest.mark.asyncio
async def test_contract_settlement_creates_property_owner_events_and_replayed_state(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
) -> None:
    created = await create_game(client)
    game_id = created["id"]
    player_ids = [player["id"] for player in created["players"]]
    property_id = "property_mediterranean_avenue"
    try:
        await EventPersistence(session_factory).append_accepted_events(
            game_id=game_id,
            actor_player_id=player_ids[0],
            event_templates=[
                AcceptedEventTemplate(
                    event_type="PROPERTY_OWNER_SET",
                    payload={"property_id": property_id, "owner_id": player_ids[0]},
                )
            ],
        )
        deal = await accepted_structured_deal(
            client,
            game_id,
            player_ids,
            [
                {
                    "kind": "immediate_property_transfer",
                    "from_player_id": player_ids[0],
                    "to_player_id": player_ids[1],
                    "property_id": property_id,
                }
            ],
        )
        contract_response = await client.post(
            f"/games/{game_id}/contracts/from-deal",
            json={"deal_id": deal["id"]},
        )
        contract = contract_response.json()["contract"]
        obligation = contract_response.json()["obligations"][0]

        settlement = await client.post(
            f"/games/{game_id}/contracts/{contract['id']}/settle",
            json={"obligation_id": obligation["id"]},
        )

        assert settlement.status_code == 200, settlement.text
        assert [event["event_type"] for event in settlement.json()["accepted_events"]] == [
            "PROPERTY_OWNER_SET"
        ]
        state = (await client.get(f"/games/{game_id}/state")).json()["state"]
        owner_by_property = {
            ownership["property_id"]: ownership["owner_id"]
            for ownership in state["property_ownership"]
        }
        assert owner_by_property[property_id] == player_ids[1]

        rows = await event_rows(session_factory, game_id)
        assert rows[-1]["event_type"] == "PROPERTY_OWNER_SET"
        assert rows[-1]["payload"] == {"property_id": property_id, "owner_id": player_ids[1]}
        async with session_factory() as session:
            result = await session.execute(sa.select(obligations).where(obligations.c.id == UUID(obligation["id"])))
            obligation_row = dict(result.mappings().one())
        assert obligation_row["status"] == "settled"
        assert obligation_row["settled_event_id"] == rows[-1]["id"]
    finally:
        await delete_game(session_factory, game_id)


@pytest.mark.asyncio
async def test_trigger_enforcement_settles_due_obligations_automatically(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
) -> None:
    # Accepted contracts automatically enforce future obligations
    created = await create_game(client)
    game_id = created["id"]
    player_ids = [player["id"] for player in created["players"]]
    try:
        deal = await accepted_structured_deal(
            client,
            game_id,
            player_ids,
            [
                {
                    "kind": "deferred_cash_payment",
                    "from_player_id": player_ids[0],
                    "to_player_id": player_ids[1],
                    "amount": 45,
                    "due_turn": 1,
                }
            ],
        )
        await client.post(f"/games/{game_id}/contracts/from-deal", json={"deal_id": deal["id"]})

        enforcement = await client.post(
            f"/games/{game_id}/contracts/enforce",
            json={"trigger_context": {"type": "turn_start", "turn": 1}},
        )

        assert enforcement.status_code == 200, enforcement.text
        assert enforcement.json()["settled_obligation_ids"]
        assert [event["event_type"] for event in enforcement.json()["accepted_events"]] == [
            "PLAYER_CASH_DELTA",
            "PLAYER_CASH_DELTA",
        ]
    finally:
        await delete_game(session_factory, game_id)


@pytest.mark.asyncio
async def test_defaults_are_deterministic_and_audited(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
) -> None:
    # Defaults are deterministic
    created = await create_game(client)
    game_id = created["id"]
    player_ids = [player["id"] for player in created["players"]]
    try:
        deal = await accepted_structured_deal(
            client,
            game_id,
            player_ids,
            [
                {
                    "kind": "immediate_cash_transfer",
                    "from_player_id": player_ids[0],
                    "to_player_id": player_ids[1],
                    "amount": 5000,
                }
            ],
        )
        contract_response = await client.post(
            f"/games/{game_id}/contracts/from-deal",
            json={"deal_id": deal["id"]},
        )
        contract = contract_response.json()["contract"]
        obligation = contract_response.json()["obligations"][0]

        first = await client.post(
            f"/games/{game_id}/contracts/{contract['id']}/settle",
            json={"obligation_id": obligation["id"]},
        )
        second = await client.post(
            f"/games/{game_id}/contracts/{contract['id']}/settle",
            json={"obligation_id": obligation["id"]},
        )

        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        assert first.json()["defaulted_obligation_ids"] == [obligation["id"]]
        assert second.json()["defaulted_obligation_ids"] == []
        assert first.json()["accepted_events"] == []

        audit = await audit_rows(session_factory, game_id)
        default_audits = [row for row in audit if row["message_type"] == "CONTRACT_DEFAULTED"]
        assert len(default_audits) == 1
        assert default_audits[0]["payload"]["contract_id"] == contract["id"]
        assert default_audits[0]["payload"]["obligation_id"] == obligation["id"]
        assert default_audits[0]["payload"]["source_deal_id"] == deal["id"]
        assert default_audits[0]["payload"]["reason_code"] == "insufficient_cash"
    finally:
        await delete_game(session_factory, game_id)


def test_trigger_system_recognizes_required_trigger_conditions() -> None:
    # rent trigger
    assert rent_trigger(
        {"trigger": {"type": "rent_collected", "property_id": "property_baltic_avenue"}},
        {"type": "rent_collected", "property_id": "property_baltic_avenue", "amount": 12},
    ).matched

    # turn start
    assert turn_start_trigger({"trigger": {"type": "turn_start", "turn": 3}}, {"type": "turn_start", "turn": 3}).matched

    # turn end
    assert turn_end_trigger({"trigger": {"type": "turn_end", "turn": 3}}, {"type": "turn_end", "turn": 4}).matched

    # property transfer
    assert property_transfer_trigger(
        {"trigger": {"type": "property_transfer", "property_id": "property_baltic_avenue"}},
        {"type": "property_transfer", "property_id": "property_baltic_avenue"},
    ).matched

    # bankruptcy
    assert bankruptcy_trigger(
        {"trigger": {"type": "bankruptcy", "player_id": "11111111-1111-4111-8111-111111111111"}},
        {"type": "bankruptcy", "player_id": "11111111-1111-4111-8111-111111111111"},
    ).matched

    # time/round conditions
    now = datetime.now(UTC).isoformat()
    assert time_trigger({"trigger": {"type": "time", "due_at": now}}, {"type": "time", "at": now}).matched
    assert round_trigger({"trigger": {"type": "round", "round": 2}}, {"type": "round", "round": 3}).matched
