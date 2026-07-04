from __future__ import annotations

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.db.metadata import game_events, game_snapshots, games, metadata, players
from app.db.persistence import (
    EventPersistence,
    SnapshotCorruptionError,
    StaleEventSequenceError,
)
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


async def create_persisted_game(
    session_factory: async_sessionmaker,
    *,
    seed: str = "stage-4.2-test-seed",
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
                    current_phase=initial_state.turn.phase,
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


async def fetch_events(session_factory: async_sessionmaker, game_id: UUID) -> list[sa.Row]:
    async with session_factory() as session:
        result = await session.execute(
            sa.select(game_events)
            .where(game_events.c.game_id == game_id)
            .order_by(game_events.c.sequence)
        )
        return list(result.fetchall())


async def fetch_snapshots(session_factory: async_sessionmaker, game_id: UUID) -> list[sa.Row]:
    async with session_factory() as session:
        result = await session.execute(
            sa.select(game_snapshots)
            .where(game_snapshots.c.game_id == game_id)
            .order_by(game_snapshots.c.event_sequence)
        )
        return list(result.fetchall())


@pytest.mark.asyncio
async def test_successful_append_creates_exactly_one_ordered_event(
    session_factory: async_sessionmaker,
) -> None:
    fixture = await create_persisted_game(session_factory)
    service = EventPersistence(session_factory, snapshot_interval=10)
    try:
        result = await service.append_accepted_event(
            game_id=fixture.game_id,
            actor_player_id=fixture.player_ids[0],
            event_type="PLAYER_CASH_DELTA",
            payload={"player_id": str(fixture.player_ids[0]), "amount": 25},
            expected_sequence=1,
        )

        rows = await fetch_events(session_factory, fixture.game_id)

        assert len(rows) == 1
        assert rows[0].sequence == 1
        assert rows[0].id == result.event_id
        assert rows[0].event_type == "PLAYER_CASH_DELTA"
        assert rows[0].payload == {"player_id": str(fixture.player_ids[0]), "amount": 25}
        assert rows[0].state_hash == result.state_hash
        assert rows[0].actor_player_id == fixture.player_ids[0]
    finally:
        await delete_game(session_factory, fixture.game_id)


@pytest.mark.asyncio
async def test_sequence_allocation_is_monotonic_per_game(
    session_factory: async_sessionmaker,
) -> None:
    first_game = await create_persisted_game(session_factory, seed="first-game")
    second_game = await create_persisted_game(session_factory, seed="second-game")
    service = EventPersistence(session_factory, snapshot_interval=10)
    try:
        await service.append_accepted_event(
            game_id=first_game.game_id,
            event_type="PLAYER_CASH_DELTA",
            payload={"player_id": str(first_game.player_ids[0]), "amount": 5},
        )
        await service.append_accepted_event(
            game_id=second_game.game_id,
            event_type="PLAYER_CASH_DELTA",
            payload={"player_id": str(second_game.player_ids[0]), "amount": 7},
        )
        await service.append_accepted_event(
            game_id=first_game.game_id,
            event_type="PLAYER_CASH_DELTA",
            payload={"player_id": str(first_game.player_ids[0]), "amount": -2},
        )

        first_rows = await fetch_events(session_factory, first_game.game_id)
        second_rows = await fetch_events(session_factory, second_game.game_id)

        assert [row.sequence for row in first_rows] == [1, 2]
        assert [row.sequence for row in second_rows] == [1]
    finally:
        await delete_game(session_factory, first_game.game_id)
        await delete_game(session_factory, second_game.game_id)


@pytest.mark.asyncio
async def test_duplicate_or_stale_sequence_attempts_fail_without_mutation(
    session_factory: async_sessionmaker,
) -> None:
    fixture = await create_persisted_game(session_factory)
    service = EventPersistence(session_factory, snapshot_interval=10)
    try:
        await service.append_accepted_event(
            game_id=fixture.game_id,
            event_type="PLAYER_CASH_DELTA",
            payload={"player_id": str(fixture.player_ids[0]), "amount": 10},
            expected_sequence=1,
        )

        with pytest.raises(StaleEventSequenceError):
            await service.append_accepted_event(
                game_id=fixture.game_id,
                event_type="PLAYER_CASH_DELTA",
                payload={"player_id": str(fixture.player_ids[0]), "amount": 99},
                expected_sequence=1,
            )

        rows = await fetch_events(session_factory, fixture.game_id)
        snapshots = await fetch_snapshots(session_factory, fixture.game_id)

        assert len(rows) == 1
        assert rows[0].payload["amount"] == 10
        assert snapshots == []
    finally:
        await delete_game(session_factory, fixture.game_id)


@pytest.mark.asyncio
async def test_snapshot_creation_occurs_at_configured_interval(
    session_factory: async_sessionmaker,
) -> None:
    fixture = await create_persisted_game(session_factory)
    service = EventPersistence(session_factory, snapshot_interval=2)
    try:
        await service.append_accepted_event(
            game_id=fixture.game_id,
            event_type="PLAYER_CASH_DELTA",
            payload={"player_id": str(fixture.player_ids[0]), "amount": 1},
        )
        second_append = await service.append_accepted_event(
            game_id=fixture.game_id,
            event_type="PLAYER_CASH_DELTA",
            payload={"player_id": str(fixture.player_ids[0]), "amount": 2},
        )

        snapshots = await fetch_snapshots(session_factory, fixture.game_id)

        assert len(snapshots) == 1
        assert snapshots[0].event_sequence == 2
        assert snapshots[0].last_event_id == second_append.event_id
        assert snapshots[0].state_payload["event_sequence"] == 2
        assert snapshots[0].state_hash == second_append.state_hash
    finally:
        await delete_game(session_factory, fixture.game_id)


@pytest.mark.asyncio
async def test_replay_from_zero_and_latest_snapshot_have_identical_hashes(
    session_factory: async_sessionmaker,
) -> None:
    fixture = await create_persisted_game(session_factory)
    service = EventPersistence(session_factory, snapshot_interval=2)
    try:
        for amount in (3, 4, -1):
            await service.append_accepted_event(
                game_id=fixture.game_id,
                event_type="PLAYER_CASH_DELTA",
                payload={"player_id": str(fixture.player_ids[0]), "amount": amount},
            )

        from_zero = await service.replay_from_event_zero(fixture.game_id)
        from_snapshot = await service.replay_from_latest_snapshot(fixture.game_id)

        assert from_snapshot.state_hash() == from_zero.state_hash()
        assert from_snapshot.event_sequence == from_zero.event_sequence == 3
    finally:
        await delete_game(session_factory, fixture.game_id)


@pytest.mark.asyncio
async def test_corrupt_snapshot_payload_or_hash_is_detected(
    session_factory: async_sessionmaker,
) -> None:
    fixture = await create_persisted_game(session_factory)
    service = EventPersistence(session_factory, snapshot_interval=1)
    try:
        await service.append_accepted_event(
            game_id=fixture.game_id,
            event_type="PLAYER_CASH_DELTA",
            payload={"player_id": str(fixture.player_ids[0]), "amount": 11},
        )
        async with session_factory() as session:
            async with session.begin():
                await session.execute(
                    game_snapshots.update()
                    .where(game_snapshots.c.game_id == fixture.game_id)
                    .values(state_hash="corrupt-snapshot-hash")
                )

        with pytest.raises(SnapshotCorruptionError):
            await service.verify_game_snapshots(fixture.game_id)
    finally:
        await delete_game(session_factory, fixture.game_id)
