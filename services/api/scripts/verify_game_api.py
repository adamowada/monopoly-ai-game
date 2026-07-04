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


ROOT = Path(__file__).resolve().parents[3]
API_ROOT = ROOT / "services" / "api"

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.core.config import Settings  # noqa: E402
from app.db.metadata import deals, game_events, metadata, negotiations, players, rejected_actions  # noqa: E402
from app.main import create_app  # noqa: E402


async def _count(engine: AsyncEngine, table: sa.Table, game_id: str) -> int:
    async with engine.connect() as connection:
        result = await connection.execute(
            sa.select(sa.func.count()).select_from(table).where(table.c.game_id == UUID(game_id))
        )
        return int(result.scalar_one())


async def verify(database_url: str) -> None:
    settings = Settings(api_env="verification", database_url=database_url)
    app = create_app(settings=settings)
    async with app.state.database_engine.begin() as connection:
        await connection.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        await connection.run_sync(metadata.create_all)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://verifyserver",
    ) as client:
        created_response = await client.post(
            "/games",
            json={
                "seed": "stage-4.4-verifier",
                "players": [
                    {"name": "Verifier Ada", "kind": "human"},
                    {"name": "Verifier Grace", "kind": "ai"},
                ],
            },
        )
        created_response.raise_for_status()
        game = created_response.json()
        game_id = game["id"]
        player_ids = [player["id"] for player in game["players"]]

        metadata_response = await client.get(f"/games/{game_id}")
        state_response = await client.get(f"/games/{game_id}/state")
        metadata_response.raise_for_status()
        state_response.raise_for_status()

        legal_response = await client.get(
            f"/games/{game_id}/legal-actions",
            params={"actor_player_id": player_ids[0]},
        )
        legal_response.raise_for_status()
        legal_actions: list[dict[str, Any]] = legal_response.json()["legal_actions"]
        roll_action = next(action for action in legal_actions if action["type"] == "ROLL_DICE")

        accepted_response = await client.post(f"/games/{game_id}/actions", json=roll_action)
        accepted_response.raise_for_status()
        accepted = accepted_response.json()
        if not accepted["accepted_events"]:
            raise RuntimeError("legal action did not create accepted events")

        invalid_before = await _count(app.state.database_engine, game_events, game_id)
        invalid_response = await client.post(
            f"/games/{game_id}/actions",
            json={
                "actor_id": player_ids[0],
                "type": "BUY_PROPERTY",
                "payload": {"property_id": "property_boardwalk"},
                "expected_state_hash": accepted["state_hash"],
                "expected_event_sequence": accepted["event_sequence"],
            },
        )
        if invalid_response.status_code != 422:
            raise RuntimeError(f"expected invalid action rejection, got {invalid_response.status_code}")
        invalid_after = await _count(app.state.database_engine, game_events, game_id)
        if invalid_after != invalid_before:
            raise RuntimeError("invalid action appended an accepted event")

        events_response = await client.get(f"/games/{game_id}/events")
        rejected_response = await client.get(f"/games/{game_id}/rejected-actions")
        events_response.raise_for_status()
        rejected_response.raise_for_status()
        if not events_response.json()["events"]:
            raise RuntimeError("events list is empty after accepted action")
        if not rejected_response.json()["rejected_actions"]:
            raise RuntimeError("rejected actions list is empty after invalid action")

        negotiation_response = await client.post(
            f"/games/{game_id}/negotiations",
            json={
                "opened_by_player_id": player_ids[0],
                "participant_player_ids": player_ids,
                "context": {"purpose": "verification"},
            },
        )
        negotiation_response.raise_for_status()
        negotiation = negotiation_response.json()

        deal_response = await client.post(
            f"/games/{game_id}/deals",
            json={
                "negotiation_id": negotiation["id"],
                "proposed_by_player_id": player_ids[0],
                "terms": {"cash_offer": 1},
            },
        )
        deal_response.raise_for_status()

        event_count_before_ai = await _count(app.state.database_engine, game_events, game_id)
        rejection_count_before_ai = await _count(app.state.database_engine, rejected_actions, game_id)
        ai_response = await client.post(
            f"/games/{game_id}/ai/step",
            json={"player_id": player_ids[1], "request_context": {"source": "verifier"}},
        )
        if ai_response.status_code != 501:
            raise RuntimeError(f"expected AI runtime 501, got {ai_response.status_code}")
        if ai_response.json()["reason_code"] != "ai_runtime_not_implemented":
            raise RuntimeError("AI response did not expose ai_runtime_not_implemented")
        if await _count(app.state.database_engine, game_events, game_id) != event_count_before_ai:
            raise RuntimeError("AI step created fallback event")
        if await _count(app.state.database_engine, rejected_actions, game_id) != rejection_count_before_ai:
            raise RuntimeError("AI step created fallback rejection")

        async with client.stream("GET", f"/games/{game_id}/events/stream") as stream_response:
            body = (await stream_response.aread()).decode("utf-8")
        if stream_response.status_code != 200 or "event: game_event" not in body:
            raise RuntimeError("SSE endpoint did not stream accepted events")

        for table, minimum in (
            (players, 2),
            (game_events, 1),
            (rejected_actions, 1),
            (negotiations, 1),
            (deals, 1),
        ):
            count = await _count(app.state.database_engine, table, game_id)
            if count < minimum:
                raise RuntimeError(f"expected at least {minimum} {table.name} rows, found {count}")

    await app.state.database_engine.dispose()
    print(f"Game API verification succeeded: game_id={game_id}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Phase 4 Stage 4.4 game API behavior.")
    parser.add_argument("--database-url", required=True)
    args = parser.parse_args()
    asyncio.run(verify(args.database_url))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
