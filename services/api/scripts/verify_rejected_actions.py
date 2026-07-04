from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.core.config import Settings  # noqa: E402
from app.db.metadata import game_events, games, metadata, players, rejected_actions  # noqa: E402
from app.main import create_app  # noqa: E402
from app.rules.state import PlayerSetup, create_initial_game_state  # noqa: E402


VERIFY_GAME_ID = uuid5(NAMESPACE_URL, "monopoly-ai-game:phase-4.3:rejected-actions")
VERIFY_PLAYER_IDS = (
    uuid5(NAMESPACE_URL, "monopoly-ai-game:phase-4.3:ada"),
    uuid5(NAMESPACE_URL, "monopoly-ai-game:phase-4.3:grace"),
)


async def run_verification(database_url: str) -> None:
    engine = create_async_engine(database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app(
        settings=Settings(
            api_env="verification",
            database_url=database_url,
            cors_origins="http://localhost:3000",
        )
    )

    try:
        async with engine.begin() as connection:
            await connection.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
            await connection.run_sync(metadata.create_all)

        initial_state = create_initial_game_state(
            seed="stage-4.3-rejected-actions-verifier",
            players=(
                PlayerSetup(id=str(VERIFY_PLAYER_IDS[0]), name="Verifier Ada", kind="human"),
                PlayerSetup(id=str(VERIFY_PLAYER_IDS[1]), name="Verifier Grace", kind="human"),
            ),
            game_id=str(VERIFY_GAME_ID),
        )

        async with session_factory() as session:
            async with session.begin():
                await session.execute(games.delete().where(games.c.id == VERIFY_GAME_ID))
                await session.execute(
                    games.insert().values(
                        id=VERIFY_GAME_ID,
                        status="active",
                        ruleset_version=initial_state.ruleset_version,
                        seed=initial_state.seed,
                        current_phase=initial_state.turn.phase.value,
                        settings={"snapshot_interval": 2, "verifier": "stage-4.3"},
                        initial_state=initial_state.model_dump(mode="json"),
                    )
                )
                for seat_order, player_state in enumerate(initial_state.players):
                    await session.execute(
                        players.insert().values(
                            id=UUID(player_state.id),
                            game_id=VERIFY_GAME_ID,
                            seat_order=seat_order,
                            name=player_state.name,
                            controller_type=player_state.kind,
                            state=player_state.model_dump(mode="json"),
                        )
                    )

        invalid_action = {
            "actor_id": str(VERIFY_PLAYER_IDS[0]),
            "type": "BUY_PROPERTY",
            "payload": {"property_id": "property_boardwalk"},
            "expected_state_hash": initial_state.state_hash(),
            "expected_event_sequence": initial_state.event_sequence,
        }
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://verify.local",
        ) as client:
            post_response = await client.post(
                f"/games/{VERIFY_GAME_ID}/actions",
                headers={"Idempotency-Key": "stage-4.3-verify-rejected-action"},
                json=invalid_action,
            )
            if post_response.status_code != 422:
                raise RuntimeError(
                    f"expected rejected action HTTP 422, got {post_response.status_code}: "
                    f"{post_response.text}"
                )
            post_body = post_response.json()
            rejected_action_id = UUID(post_body["rejected_action_id"])

            get_response = await client.get(f"/games/{VERIFY_GAME_ID}/rejected-actions")
            if get_response.status_code != 200:
                raise RuntimeError(f"GET rejected actions failed: {get_response.text}")
            records = get_response.json()["rejected_actions"]
            if len(records) != 1 or UUID(records[0]["id"]) != rejected_action_id:
                raise RuntimeError("GET rejected actions did not return the persisted rejection")

            filtered_response = await client.get(
                f"/games/{VERIFY_GAME_ID}/rejected-actions",
                params={"actor_player_id": str(VERIFY_PLAYER_IDS[0])},
            )
            if filtered_response.status_code != 200:
                raise RuntimeError(f"filtered GET rejected actions failed: {filtered_response.text}")
            filtered_records = filtered_response.json()["rejected_actions"]
            if len(filtered_records) != 1 or UUID(filtered_records[0]["id"]) != rejected_action_id:
                raise RuntimeError("actor filter did not return the expected rejected action")

            empty_filter_response = await client.get(
                f"/games/{VERIFY_GAME_ID}/rejected-actions",
                params={"actor_player_id": str(VERIFY_PLAYER_IDS[1])},
            )
            if empty_filter_response.json()["rejected_actions"] != []:
                raise RuntimeError("actor filter returned a rejection for the wrong player")

        async with session_factory() as session:
            rejected_count = await session.scalar(
                sa.select(sa.func.count())
                .select_from(rejected_actions)
                .where(rejected_actions.c.game_id == VERIFY_GAME_ID)
            )
            event_count = await session.scalar(
                sa.select(sa.func.count()).select_from(game_events).where(game_events.c.game_id == VERIFY_GAME_ID)
            )

        if rejected_count != 1:
            raise RuntimeError(f"expected one rejected_actions row, found {rejected_count}")
        if event_count != 0:
            raise RuntimeError(f"expected zero game_events rows for verifier game, found {event_count}")

        print(
            "rejected action verification succeeded: "
            f"game_id={VERIFY_GAME_ID} rejected_action_id={rejected_action_id} "
            f"rejected_actions={rejected_count} game_events={event_count}"
        )
    finally:
        await app.state.database_engine.dispose()
        await engine.dispose()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify rejected action audit persistence.")
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
        print(f"rejected action verification failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
