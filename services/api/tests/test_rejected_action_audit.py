from __future__ import annotations

import os
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.db.metadata import game_events, game_snapshots, games, metadata, players, rejected_actions
from app.db.rejected_actions import RejectedActionAudit
from app.main import create_app
from app.rules.state import GameState, PlayerSetup, create_initial_game_state


TEST_DATABASE_URL = os.getenv(
    "MONOPOLY_TEST_DATABASE_URL",
    "postgresql+asyncpg://monopoly:monopoly@127.0.0.1:5432/monopoly_ai_game",
)


@dataclass(frozen=True)
class PersistedGameFixture:
    game_id: UUID
    player_ids: tuple[UUID, UUID]
    initial_state: GameState


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


async def create_persisted_game(
    session_factory: async_sessionmaker,
    *,
    seed: str = "stage-4.3-test-seed",
) -> PersistedGameFixture:
    game_id = uuid4()
    player_ids = (uuid4(), uuid4())
    player_setups = (
        PlayerSetup(id=str(player_ids[0]), name="Ada", kind="human"),
        PlayerSetup(id=str(player_ids[1]), name="Grace", kind="human"),
    )
    initial_state = create_initial_game_state(seed=seed, players=player_setups, game_id=str(game_id))

    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                games.insert().values(
                    id=game_id,
                    status="active",
                    ruleset_version=initial_state.ruleset_version,
                    seed=seed,
                    current_phase=initial_state.turn.phase.value,
                    settings={"snapshot_interval": 2},
                    initial_state=initial_state.model_dump(mode="json"),
                )
            )
            for seat_order, player_state in enumerate(initial_state.players):
                await session.execute(
                    players.insert().values(
                        id=UUID(player_state.id),
                        game_id=game_id,
                        seat_order=seat_order,
                        name=player_state.name,
                        controller_type=player_state.kind,
                        state=player_state.model_dump(mode="json"),
                    )
                )

    return PersistedGameFixture(
        game_id=game_id,
        player_ids=player_ids,
        initial_state=initial_state,
    )


async def delete_game(session_factory: async_sessionmaker, game_id: UUID) -> None:
    async with session_factory() as session:
        async with session.begin():
            await session.execute(games.delete().where(games.c.id == game_id))


async def fetch_rejections(session_factory: async_sessionmaker, game_id: UUID) -> list[sa.Row]:
    async with session_factory() as session:
        result = await session.execute(
            sa.select(rejected_actions)
            .where(rejected_actions.c.game_id == game_id)
            .order_by(rejected_actions.c.created_at.desc(), rejected_actions.c.id.desc())
        )
        return list(result.fetchall())


async def count_events(session_factory: async_sessionmaker, game_id: UUID) -> int:
    async with session_factory() as session:
        result = await session.execute(
            sa.select(sa.func.count()).select_from(game_events).where(game_events.c.game_id == game_id)
        )
        return int(result.scalar_one())


async def count_snapshots(session_factory: async_sessionmaker, game_id: UUID) -> int:
    async with session_factory() as session:
        result = await session.execute(
            sa.select(sa.func.count())
            .select_from(game_snapshots)
            .where(game_snapshots.c.game_id == game_id)
        )
        return int(result.scalar_one())


def action_payload(
    fixture: PersistedGameFixture,
    actor_id: UUID,
    action_type: str,
    payload: Mapping[str, object] | None = None,
) -> dict[str, object]:
    return {
        "actor_id": str(actor_id),
        "type": action_type,
        "payload": {} if payload is None else dict(payload),
        "expected_state_hash": fixture.initial_state.state_hash(),
        "expected_event_sequence": fixture.initial_state.event_sequence,
    }


@pytest.mark.asyncio
async def test_persisting_rejected_action_stores_structured_reason_and_context(
    session_factory: async_sessionmaker,
) -> None:
    fixture = await create_persisted_game(session_factory)
    audit = RejectedActionAudit(session_factory)
    try:
        record = await audit.persist_rejected_action(
            game_id=fixture.game_id,
            actor_player_id=fixture.player_ids[0],
            action_type="BUY_PROPERTY",
            payload={"property_id": "property_boardwalk"},
            reason_code="illegal_action",
            validation_errors=[
                {
                    "code": "illegal_action",
                    "message": "player is not on property_boardwalk",
                    "field": "payload.property_id",
                }
            ],
            legal_action_context={
                "actor_id": str(fixture.player_ids[0]),
                "current_player_id": str(fixture.player_ids[0]),
                "legal_actions": ["ROLL_DICE", "DECLARE_BANKRUPTCY"],
            },
            phase=fixture.initial_state.turn.phase.value,
            state_hash=fixture.initial_state.state_hash(),
        )

        rows = await fetch_rejections(session_factory, fixture.game_id)

        assert len(rows) == 1
        assert rows[0].id == record.id
        assert rows[0].game_id == fixture.game_id
        assert rows[0].actor_player_id == fixture.player_ids[0]
        assert rows[0].action_type == "BUY_PROPERTY"
        assert rows[0].payload == {"property_id": "property_boardwalk"}
        assert rows[0].reason_code == "illegal_action"
        assert rows[0].validation_errors[0]["field"] == "payload.property_id"
        assert rows[0].legal_action_context["legal_actions"] == [
            "ROLL_DICE",
            "DECLARE_BANKRUPTCY",
        ]
        assert rows[0].phase == "START_TURN"
        assert rows[0].state_hash == fixture.initial_state.state_hash()
        assert rows[0].created_at is not None
    finally:
        await delete_game(session_factory, fixture.game_id)


@pytest.mark.asyncio
async def test_illegal_api_action_creates_exactly_one_audit_row(
    api_app: FastAPI,
    session_factory: async_sessionmaker,
) -> None:
    fixture = await create_persisted_game(session_factory)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api_app),
            base_url="http://testserver",
        ) as client:
            response = await client.post(
                f"/games/{fixture.game_id}/actions",
                json=action_payload(
                    fixture,
                    fixture.player_ids[0],
                    "BUY_PROPERTY",
                    {"property_id": "property_boardwalk"},
                ),
            )

        rows = await fetch_rejections(session_factory, fixture.game_id)

        assert response.status_code == 422
        body = response.json()
        assert body["status"] == "rejected"
        assert UUID(body["rejected_action_id"]) == rows[0].id
        assert body["reason_code"] == "illegal_action"
        assert body["validation_errors"][0]["code"] == "illegal_action"
        assert len(rows) == 1
        assert rows[0].actor_player_id == fixture.player_ids[0]
        assert rows[0].reason_code == "illegal_action"
        assert rows[0].legal_action_context["state_hash"] == fixture.initial_state.state_hash()
        assert await count_events(session_factory, fixture.game_id) == 0
        assert await count_snapshots(session_factory, fixture.game_id) == 0
    finally:
        await delete_game(session_factory, fixture.game_id)


@pytest.mark.asyncio
async def test_malformed_api_action_creates_one_audit_without_event_or_snapshot(
    api_app: FastAPI,
    session_factory: async_sessionmaker,
) -> None:
    fixture = await create_persisted_game(session_factory)
    try:
        malformed = action_payload(
            fixture,
            fixture.player_ids[0],
            "BUY_PROPERTY",
            {"property_id": 123},
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api_app),
            base_url="http://testserver",
        ) as client:
            response = await client.post(f"/games/{fixture.game_id}/actions", json=malformed)
            repeat_response = await client.post(f"/games/{fixture.game_id}/actions", json=malformed)

        rows = await fetch_rejections(session_factory, fixture.game_id)

        assert response.status_code == 422
        assert response.json()["reason_code"] == "malformed_action"
        assert repeat_response.status_code == 422
        assert len(rows) == 2
        assert {row.reason_code for row in rows} == {"malformed_action"}
        assert await count_events(session_factory, fixture.game_id) == 0
        assert await count_snapshots(session_factory, fixture.game_id) == 0
    finally:
        await delete_game(session_factory, fixture.game_id)


@pytest.mark.asyncio
async def test_rejection_records_are_queryable_by_game_and_actor(
    api_app: FastAPI,
    session_factory: async_sessionmaker,
) -> None:
    fixture = await create_persisted_game(session_factory)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api_app),
            base_url="http://testserver",
        ) as client:
            await client.post(
                f"/games/{fixture.game_id}/actions",
                json=action_payload(fixture, fixture.player_ids[0], "DANCE"),
            )
            await client.post(
                f"/games/{fixture.game_id}/actions",
                json=action_payload(fixture, fixture.player_ids[1], "ROLL_DICE"),
            )

            all_response = await client.get(f"/games/{fixture.game_id}/rejected-actions")
            actor_response = await client.get(
                f"/games/{fixture.game_id}/rejected-actions",
                params={"actor_player_id": str(fixture.player_ids[1])},
            )

        assert all_response.status_code == 200
        all_records = all_response.json()["rejected_actions"]
        assert [record["reason_code"] for record in all_records] == [
            "mistimed_action",
            "unknown_action",
        ]

        assert actor_response.status_code == 200
        actor_records = actor_response.json()["rejected_actions"]
        assert len(actor_records) == 1
        assert actor_records[0]["actor_player_id"] == str(fixture.player_ids[1])
        assert actor_records[0]["reason_code"] == "mistimed_action"
    finally:
        await delete_game(session_factory, fixture.game_id)


@pytest.mark.asyncio
async def test_legal_action_execution_out_of_scope_creates_no_rejection_or_fake_event(
    api_app: FastAPI,
    session_factory: async_sessionmaker,
) -> None:
    fixture = await create_persisted_game(session_factory)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api_app),
            base_url="http://testserver",
        ) as client:
            response = await client.post(
                f"/games/{fixture.game_id}/actions",
                json=action_payload(fixture, fixture.player_ids[0], "ROLL_DICE"),
            )

        assert response.status_code == 501
        assert response.json()["reason_code"] == "action_execution_not_implemented"
        assert await fetch_rejections(session_factory, fixture.game_id) == []
        assert await count_events(session_factory, fixture.game_id) == 0
        assert await count_snapshots(session_factory, fixture.game_id) == 0
    finally:
        await delete_game(session_factory, fixture.game_id)
