from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from collections.abc import Mapping, Sequence
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

from app.ai.orchestrator import CodexExecProcessResult, CodexExecRunner  # noqa: E402
from app.core.config import Settings  # noqa: E402
from app.db.metadata import (  # noqa: E402
    ai_decisions,
    deals,
    game_events,
    metadata,
    negotiations,
    players,
    rejected_actions,
)
from app.main import create_app  # noqa: E402


class VerifyFakeCodexRunner(CodexExecRunner):
    def __init__(self, output: Mapping[str, Any]) -> None:
        self.output = dict(output)
        self.calls: list[dict[str, Any]] = []

    def run(
        self,
        command: Sequence[str],
        *,
        stdin: str,
        timeout_seconds: float,
        output_last_message_path: Path | None,
    ) -> CodexExecProcessResult:
        self.calls.append(
            {
                "command": list(command),
                "stdin": stdin,
                "timeout_seconds": timeout_seconds,
                "output_last_message_path": output_last_message_path,
            }
        )
        if len(self.calls) > 1:
            raise AssertionError("fake Codex runner was called more than once")

        output_text = json.dumps(self.output)
        if output_last_message_path is not None:
            output_last_message_path.parent.mkdir(parents=True, exist_ok=True)
            output_last_message_path.write_text(output_text, encoding="utf-8")

        stdout = "\n".join(
            [
                json.dumps({"type": "session_configured", "model": "codex"}),
                json.dumps(
                    {
                        "type": "item_completed",
                        "item": {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": output_text}],
                        },
                    }
                ),
            ]
        )
        return CodexExecProcessResult(returncode=0, stdout=stdout, stderr="")


async def _count(engine: AsyncEngine, table: sa.Table, game_id: str) -> int:
    async with engine.connect() as connection:
        result = await connection.execute(
            sa.select(sa.func.count()).select_from(table).where(table.c.game_id == UUID(game_id))
        )
        return int(result.scalar_one())


async def _ai_decision(engine: AsyncEngine, ai_decision_id: str) -> Mapping[str, Any]:
    async with engine.connect() as connection:
        result = await connection.execute(
            sa.select(ai_decisions).where(ai_decisions.c.id == UUID(ai_decision_id))
        )
        row = result.mappings().one()
        return dict(row)


def _valid_action_output(
    game_id: str,
    ai_player_id: str,
    state_response: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "decision_type": "action_decision",
        "game_id": game_id,
        "player_id": ai_player_id,
        "self_dialogue": {
            "status": "provided",
            "text": "Stage 7.6 verifier decision for the live AI step endpoint.",
        },
        "memory_updates": [],
        "confidence": 0.91,
        "rationale": "The verifier returns one schema-valid ROLL_DICE decision for the current AI turn.",
        "expected_state_hash": state_response["state_hash"],
        "expected_event_sequence": state_response["event_sequence"],
        "action": {"type": "ROLL_DICE", "payload": {}},
    }


def _install_fake_runner(app: Any, runner: VerifyFakeCodexRunner, root: Path) -> None:
    app.state.codex_ai_runner = runner
    app.state.codex_ai_schema_file = root / "schema.json"
    app.state.codex_ai_sandbox_dir = root / "sandbox"
    app.state.codex_ai_work_dir = root / "work"


async def verify(database_url: str) -> None:
    settings = Settings(api_env="verification", database_url=database_url)
    app = create_app(settings=settings)
    async with app.state.database_engine.begin() as connection:
        await connection.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        await connection.run_sync(metadata.create_all)

    with tempfile.TemporaryDirectory(prefix="monopoly-ai-step-verify-") as temporary_dir:
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

            accepted_response = await client.post(
                f"/games/{game_id}/actions",
                headers={"Idempotency-Key": "stage-4.4-verify-roll"},
                json=roll_action,
            )
            accepted_response.raise_for_status()
            accepted = accepted_response.json()
            if not accepted["accepted_events"]:
                raise RuntimeError("legal action did not create accepted events")

            invalid_before = await _count(app.state.database_engine, game_events, game_id)
            invalid_response = await client.post(
                f"/games/{game_id}/actions",
                headers={"Idempotency-Key": "stage-4.4-verify-invalid"},
                json={
                    "actor_id": player_ids[0],
                    "type": "BUY_PROPERTY",
                    "payload": {"property_id": "property_boardwalk"},
                    "expected_state_hash": accepted["state_hash"],
                    "expected_event_sequence": accepted["event_sequence"],
                },
            )
            if invalid_response.status_code != 422:
                raise RuntimeError(
                    f"expected invalid action rejection, got {invalid_response.status_code}"
                )
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

            ai_created_response = await client.post(
                "/games",
                json={
                    "seed": "stage-7.6-ai-step-verifier",
                    "players": [
                        {"name": "Verifier Grace AI", "kind": "ai"},
                        {"name": "Verifier Ada Human", "kind": "human"},
                    ],
                },
            )
            ai_created_response.raise_for_status()
            ai_game = ai_created_response.json()
            ai_game_id = ai_game["id"]
            ai_player_id = ai_game["players"][0]["id"]
            ai_state_response = await client.get(f"/games/{ai_game_id}/state")
            ai_state_response.raise_for_status()

            runner = VerifyFakeCodexRunner(
                _valid_action_output(ai_game_id, ai_player_id, ai_state_response.json())
            )
            _install_fake_runner(app, runner, Path(temporary_dir))
            event_count_before_ai = await _count(app.state.database_engine, game_events, ai_game_id)
            rejection_count_before_ai = await _count(
                app.state.database_engine,
                rejected_actions,
                ai_game_id,
            )
            ai_response = await client.post(
                f"/games/{ai_game_id}/ai/step",
                json={
                    "player_id": ai_player_id,
                    "decision_type": "action_decision",
                    "mandatory": True,
                    "request_context": {"source": "verifier"},
                },
            )
            if ai_response.status_code != 200:
                raise RuntimeError(f"expected AI step HTTP 200, got {ai_response.status_code}")

            ai_body = ai_response.json()
            if ai_body.get("status") not in {"accepted", "done", "rejected", "blocked"}:
                raise RuntimeError(f"AI step returned non-endpoint status: {ai_body.get('status')}")
            if not ai_body.get("ai_decision_id"):
                raise RuntimeError("AI step response did not include ai_decision_id")
            if ai_body["status"] != "accepted":
                if ai_body["status"] in {"rejected", "blocked"} and not ai_body.get(
                    "rejected_action_id"
                ):
                    raise RuntimeError(
                        "rejected/blocked AI step did not include an audit rejection id"
                    )
                raise RuntimeError(f"deterministic AI step was not accepted: {ai_body}")
            if not ai_body.get("accepted_events"):
                raise RuntimeError("accepted AI step did not create accepted events")
            if ai_body.get("rejected_action_id") is not None:
                raise RuntimeError("accepted AI step unexpectedly created a rejected action")
            if ai_body.get("accepted_event_id") not in {
                event["id"] for event in ai_body["accepted_events"]
            }:
                raise RuntimeError("AI step accepted_event_id was not present in accepted_events")
            if ai_body["accepted_events"][0]["actor_player_id"] != ai_player_id:
                raise RuntimeError("AI step accepted event was not attributed to the AI player")
            if ai_body["accepted_events"][0]["event_type"] != "DICE_ROLLED":
                raise RuntimeError("AI step did not apply the verifier's ROLL_DICE action")
            if len(runner.calls) != 1:
                raise RuntimeError("AI step did not make exactly one fake Codex runner call")
            if (
                await _count(app.state.database_engine, game_events, ai_game_id)
                <= event_count_before_ai
            ):
                raise RuntimeError("accepted AI step did not append an accepted event")
            if (
                await _count(app.state.database_engine, rejected_actions, ai_game_id)
                != rejection_count_before_ai
            ):
                raise RuntimeError("accepted AI step created an unexpected rejected action")

            decision_row = await _ai_decision(app.state.database_engine, ai_body["ai_decision_id"])
            validation_result = decision_row["validation_result"]
            if not isinstance(validation_result, Mapping):
                raise RuntimeError("AI decision validation result was not auditable")
            if validation_result.get("no_substitute_move") is not True:
                raise RuntimeError("AI decision did not preserve the no-substitute-move audit flag")
            if validation_result.get("substitute_move") is not None:
                raise RuntimeError("AI decision audit recorded a substitute move")
            if str(decision_row["accepted_event_id"]) != ai_body["accepted_event_id"]:
                raise RuntimeError("AI decision was not linked to the accepted event")
            if decision_row["rejected_action_id"] is not None:
                raise RuntimeError("accepted AI decision was linked to a rejection")

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
                    raise RuntimeError(
                        f"expected at least {minimum} {table.name} rows, found {count}"
                    )

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
