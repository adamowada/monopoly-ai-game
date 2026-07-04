from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine


API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.core.config import Settings  # noqa: E402
from app.db.metadata import action_idempotency_keys, game_events, metadata, rejected_actions  # noqa: E402
from app.db.persistence import EventPersistence  # noqa: E402
from app.main import create_app  # noqa: E402


async def _count(engine: AsyncEngine, table: sa.Table, game_id: str) -> int:
    async with engine.connect() as connection:
        result = await connection.execute(
            sa.select(sa.func.count()).select_from(table).where(table.c.game_id == UUID(game_id))
        )
        return int(result.scalar_one())


async def _event_sequences(engine: AsyncEngine, game_id: str) -> list[int]:
    async with engine.connect() as connection:
        result = await connection.execute(
            sa.select(game_events.c.sequence)
            .where(game_events.c.game_id == UUID(game_id))
            .order_by(game_events.c.sequence)
        )
        return [int(sequence) for sequence in result.scalars().all()]


async def _create_game(client: httpx.AsyncClient, *, seed: str) -> dict[str, Any]:
    response = await client.post(
        "/games",
        json={
            "seed": seed,
            "players": [
                {"name": "Verifier Ada", "kind": "human"},
                {"name": "Verifier Grace", "kind": "ai"},
            ],
        },
    )
    response.raise_for_status()
    return response.json()


async def _legal_roll_action(
    client: httpx.AsyncClient,
    *,
    game_id: str,
    actor_player_id: str,
) -> dict[str, Any]:
    response = await client.get(
        f"/games/{game_id}/legal-actions",
        params={"actor_player_id": actor_player_id},
    )
    response.raise_for_status()
    return next(action for action in response.json()["legal_actions"] if action["type"] == "ROLL_DICE")


async def verify(database_url: str) -> None:
    settings = Settings(
        api_env="verification",
        database_url=database_url,
        cors_origins="http://localhost:3000",
    )
    app = create_app(settings=settings)
    async with app.state.database_engine.begin() as connection:
        await connection.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        await connection.run_sync(metadata.create_all)

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://verify.local",
        ) as client:
            game = await _create_game(client, seed="stage-4.5-concurrency-verifier")
            game_id = game["id"]
            human_player_id = game["players"][0]["id"]
            ai_player_id = game["players"][1]["id"]
            roll_action = await _legal_roll_action(
                client,
                game_id=game_id,
                actor_player_id=human_player_id,
            )

            accepted = await client.post(
                f"/games/{game_id}/actions",
                headers={"Idempotency-Key": "verify-roll-once"},
                json=roll_action,
            )
            accepted.raise_for_status()
            accepted_body = accepted.json()
            if accepted_body["status"] != "accepted" or not accepted_body["accepted_events"]:
                raise RuntimeError("legal action did not return accepted events")

            repeated = await client.post(
                f"/games/{game_id}/actions",
                headers={"Idempotency-Key": "verify-roll-once"},
                json=roll_action,
            )
            repeated.raise_for_status()
            if repeated.json() != accepted_body:
                raise RuntimeError("idempotent repeat did not return the original outcome")

            event_count_after_repeat = await _count(app.state.database_engine, game_events, game_id)
            if event_count_after_repeat != len(accepted_body["accepted_events"]):
                raise RuntimeError("idempotent repeat duplicated accepted events")

            rejection_count_before_conflict = await _count(
                app.state.database_engine,
                rejected_actions,
                game_id,
            )
            key_count_before_conflict = await _count(
                app.state.database_engine,
                action_idempotency_keys,
                game_id,
            )
            conflicting = await client.post(
                f"/games/{game_id}/actions",
                headers={"Idempotency-Key": "verify-roll-once"},
                json={**roll_action, "payload": {"unexpected": True}},
            )
            if conflicting.status_code != 409:
                raise RuntimeError(f"expected idempotency conflict 409, got {conflicting.status_code}")
            if conflicting.json()["reason_code"] != "idempotency_key_conflict":
                raise RuntimeError("idempotency conflict did not return the structured reason")
            if await _count(app.state.database_engine, game_events, game_id) != event_count_after_repeat:
                raise RuntimeError("idempotency conflict mutated accepted events")
            if (
                await _count(app.state.database_engine, rejected_actions, game_id)
                != rejection_count_before_conflict
            ):
                raise RuntimeError("idempotency conflict created a rejected action")
            if (
                await _count(app.state.database_engine, action_idempotency_keys, game_id)
                != key_count_before_conflict
            ):
                raise RuntimeError("idempotency conflict created a new idempotency row")

            stale_ai = await client.post(
                f"/games/{game_id}/actions",
                headers={"Idempotency-Key": "verify-stale-ai-output"},
                json={
                    "actor_id": ai_player_id,
                    "type": "ROLL_DICE",
                    "payload": {},
                    "expected_state_hash": roll_action["expected_state_hash"],
                    "expected_event_sequence": roll_action["expected_event_sequence"],
                },
            )
            if stale_ai.status_code != 409:
                raise RuntimeError(f"expected stale AI HTTP 409, got {stale_ai.status_code}")
            if stale_ai.json()["reason_code"] != "stale_action":
                raise RuntimeError("stale AI submission did not return stale_action")
            if await _count(app.state.database_engine, game_events, game_id) != event_count_after_repeat:
                raise RuntimeError("stale AI submission appended an accepted event")

            concurrent_game = await _create_game(client, seed="stage-4.5-concurrency-race-verifier")
            concurrent_game_id = concurrent_game["id"]
            concurrent_actor_id = concurrent_game["players"][0]["id"]
            concurrent_action = await _legal_roll_action(
                client,
                game_id=concurrent_game_id,
                actor_player_id=concurrent_actor_id,
            )

            async def submit_concurrent(index: int) -> httpx.Response:
                return await client.post(
                    f"/games/{concurrent_game_id}/actions",
                    headers={"Idempotency-Key": f"verify-concurrent-{index}"},
                    json=concurrent_action,
                )

            responses = await asyncio.gather(*(submit_concurrent(index) for index in range(6)))
            accepted_responses = [response for response in responses if response.status_code == 200]
            stale_responses = [response for response in responses if response.status_code == 409]
            if len(accepted_responses) != 1:
                raise RuntimeError(f"expected one concurrent accepted response, found {len(accepted_responses)}")
            if len(stale_responses) != 5:
                raise RuntimeError(f"expected five concurrent stale responses, found {len(stale_responses)}")
            if {response.json()["reason_code"] for response in stale_responses} != {"stale_action"}:
                raise RuntimeError("concurrent stale responses did not all return stale_action")

            sequences = await _event_sequences(app.state.database_engine, concurrent_game_id)
            if sequences != list(range(1, len(sequences) + 1)):
                raise RuntimeError(f"event sequences are not contiguous: {sequences}")

            session_factory = app.state.database_session_factory
            from_zero = await EventPersistence(session_factory).replay_from_event_zero(concurrent_game_id)
            from_snapshot = await EventPersistence(session_factory).replay_from_latest_snapshot(
                concurrent_game_id
            )
            if from_zero.state_hash() != from_snapshot.state_hash():
                raise RuntimeError("event replay from snapshot does not match replay from zero")
            if from_zero.event_sequence != len(sequences):
                raise RuntimeError("replayed state sequence does not match persisted event count")

            first_game_rejections = await _count(app.state.database_engine, rejected_actions, game_id)
            first_game_keys = await _count(app.state.database_engine, action_idempotency_keys, game_id)
            concurrent_rejections = await _count(
                app.state.database_engine,
                rejected_actions,
                concurrent_game_id,
            )
            concurrent_keys = await _count(
                app.state.database_engine,
                action_idempotency_keys,
                concurrent_game_id,
            )
            if first_game_rejections < 1 or concurrent_rejections < 5:
                raise RuntimeError("verification did not leave expected rejected action rows")
            if first_game_keys < 2 or concurrent_keys < 6:
                raise RuntimeError("verification did not leave expected idempotency rows")

        print(
            "concurrency verification succeeded: "
            f"game_id={game_id} concurrent_game_id={concurrent_game_id} "
            f"accepted_events={event_count_after_repeat + len(sequences)} "
            f"rejected_actions={first_game_rejections + concurrent_rejections} "
            f"idempotency_keys={first_game_keys + concurrent_keys}"
        )
    finally:
        await app.state.database_engine.dispose()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify Phase 4 Stage 4.5 concurrency safety.")
    parser.add_argument(
        "--database-url",
        required=True,
        help="Postgres async SQLAlchemy URL, for example postgresql+asyncpg://user:pass@host/db",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        asyncio.run(verify(args.database_url))
    except Exception as exc:
        print(f"concurrency verification failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
