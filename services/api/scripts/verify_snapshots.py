from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.db.metadata import games, metadata, players  # noqa: E402
from app.db.persistence import EventPersistence  # noqa: E402
from app.rules.state import PlayerSetup, create_initial_game_state  # noqa: E402


async def run_verification(database_url: str) -> None:
    engine = create_async_engine(database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    game_id = uuid4()
    first_player_id = uuid4()
    second_player_id = uuid4()
    player_ids = (first_player_id, second_player_id)
    player_setups = (
        PlayerSetup(id=str(first_player_id), name="Verifier Ada", kind="human"),
        PlayerSetup(id=str(second_player_id), name="Verifier Grace", kind="human"),
    )
    initial_state = create_initial_game_state(
        seed="stage-4.2-snapshot-verifier",
        players=player_setups,
        game_id=str(game_id),
    )

    try:
        async with engine.begin() as connection:
            await connection.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
            await connection.run_sync(metadata.create_all)

        async with session_factory() as session:
            async with session.begin():
                await session.execute(
                    games.insert().values(
                        id=game_id,
                        status="active",
                        ruleset_version=initial_state.ruleset_version,
                        seed=initial_state.seed,
                        current_phase=initial_state.turn.phase.value,
                        settings={"snapshot_interval": 2, "verifier": "stage-4.2"},
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

        service = EventPersistence(session_factory, snapshot_interval=2)
        await service.append_accepted_event(
            game_id=game_id,
            actor_player_id=player_ids[0],
            event_type="PLAYER_CASH_DELTA",
            payload={"player_id": str(player_ids[0]), "amount": 17},
            expected_sequence=1,
        )
        await service.append_accepted_event(
            game_id=game_id,
            actor_player_id=player_ids[0],
            event_type="PLAYER_POSITION_SET",
            payload={"player_id": str(player_ids[0]), "position": 5},
            expected_sequence=2,
        )
        await service.append_accepted_event(
            game_id=game_id,
            actor_player_id=player_ids[0],
            event_type="PLAYER_CASH_DELTA",
            payload={"player_id": str(player_ids[0]), "amount": -4},
            expected_sequence=3,
        )

        from_zero = await service.replay_from_event_zero(game_id)
        from_snapshot = await service.replay_from_latest_snapshot(game_id)
        if from_zero.state_hash() != from_snapshot.state_hash():
            raise RuntimeError("snapshot replay hash mismatch")

        verification = await service.verify_game_snapshots(game_id)
        if verification.event_count < 1 or verification.snapshot_count < 1:
            raise RuntimeError("verification did not persist at least one event and snapshot")

        print(
            "snapshot verification succeeded: "
            f"game_id={game_id} events={verification.event_count} "
            f"snapshots={verification.snapshot_count} state_hash={verification.replayed_state_hash}"
        )
    finally:
        await engine.dispose()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify event replay and snapshot integrity.")
    parser.add_argument(
        "--database-url",
        required=True,
        help="Postgres async SQLAlchemy URL, for example postgresql+asyncpg://user:pass@host/db",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        asyncio.run(run_verification(args.database_url))
    except Exception as exc:
        print(f"snapshot verification failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
