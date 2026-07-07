"""Stage 11.2 evidence:

- AI runtime command uses codex exec --json
- schema validation is enforced
- invalid output is rejected and mandatory failures can lead to AI_BLOCKED
- no fallback or substitute move path is used
- memory and self-dialogue audit records persist across decisions
- AI audit endpoints expose profiles, decisions, self-dialogue, memory, retrievals, and rejected outputs
- decisions are reconstructable from persisted audit rows
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping, Sequence
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.ai.orchestrator import (
    CodexExecProcessResult,
    CodexExecRunner,
    DEFAULT_AI_MODEL,
    LIGHT_REASONING_CONFIG,
    build_codex_exec_command,
)
from app.core.config import Settings
from app.db.metadata import (
    ai_decisions,
    ai_memory_entries,
    ai_profiles,
    ai_self_dialogue,
    games,
    metadata,
    retrieval_records,
)
from app.main import create_app


TEST_DATABASE_URL = "postgresql+asyncpg://monopoly:monopoly@127.0.0.1:5432/monopoly_ai_game"


class Stage112Runner(CodexExecRunner):
    def __init__(
        self,
        queued_outputs: Sequence[Mapping[str, Any] | str] = (),
    ) -> None:
        self.outputs: list[Mapping[str, Any] | str] = list(queued_outputs)
        self.calls: list[dict[str, Any]] = []

    def run(
        self,
        command: Sequence[str],
        *,
        stdin: str,
        timeout_seconds: float,
        output_last_message_path: Path | None,
    ) -> CodexExecProcessResult:
        if not self.outputs:
            raise AssertionError("fake runner called more times than queued outputs")
        output = self.outputs.pop(0)
        output_text = output if isinstance(output, str) else json.dumps(output)
        stdout = "\n".join(
            [
                json.dumps({"type": "session_configured", "model": "codex"}),
                json.dumps(
                    {
                        "type": "item_completed",
                        "item": {
                            "type": "message",
                            "role": "assistant",
                            "content": [
                                {"type": "output_text", "text": output_text},
                            ],
                        },
                    }
                ),
            ]
        )
        self.calls.append({"command": list(command), "stdout": stdout, "stdin": stdin})
        if output_last_message_path is not None:
            output_last_message_path.parent.mkdir(parents=True, exist_ok=True)
            output_last_message_path.write_text(output_text, encoding="utf-8")
        return CodexExecProcessResult(returncode=0, stdout=stdout, stderr="")


def build_action_output(
    game_id: str,
    player_id: str,
    state: Mapping[str, object],
    *,
    suffix: str = "",
) -> dict[str, Any]:
    return {
        "decision_type": "action_decision",
        "game_id": game_id,
        "player_id": player_id,
        "expected_state_hash": state["state_hash"],
        "expected_event_sequence": state["event_sequence"],
        "action": {"type": "ROLL_DICE", "payload": {}},
        "self_dialogue": {
            "status": "provided",
            "text": f"Stage 11.2 action preference {suffix}",
        },
        "memory_updates": [
            {
                "visibility": "private",
                "category": "strategic_belief",
                "importance": 7,
                "content": f"Persistent memory snippet {suffix}",
                "metadata": {"stage": "11.2", "source": "audit test"},
            }
        ],
        "confidence": 0.97,
        "rationale": "Use legal output aligned with legal action context.",
    }


def build_self_dialogue_output(
    game_id: str,
    player_id: str,
    *,
    suffix: str = "",
) -> dict[str, Any]:
    return {
        "decision_type": "self_dialogue",
        "game_id": game_id,
        "player_id": player_id,
        "self_dialogue": {
            "status": "provided",
            "text": f"Stage 11.2 self-dialogue reflection {suffix}",
        },
        "memory_updates": [
            {
                "visibility": "private",
                "category": "player_trust_model",
                "importance": 6,
                "content": f"Reflection summary {suffix}",
                "metadata": {"stage": "11.2", "source": "dialogue"},
            }
        ],
        "confidence": 0.83,
        "rationale": "Persist confidence and memory updates for reconstruction.",
    }


def build_open_negotiation_output(
    game_id: str,
    player_id: str,
    recipient_player_id: str,
) -> dict[str, Any]:
    return {
        "decision_type": "open_negotiation",
        "game_id": game_id,
        "player_id": player_id,
        "self_dialogue": {
            "status": "provided",
            "text": "Stage 11.2 open negotiation preference.",
        },
        "memory_updates": [
            {
                "visibility": "private",
                "category": "deal_history",
                "importance": 5,
                "content": "Explored open negotiation path for reconstruction test.",
                "metadata": {"stage": "11.2", "source": "negotiation"},
            }
        ],
        "confidence": 0.91,
        "rationale": "Open a narrow negotiation with a partner.",
        "negotiation": {
            "participant_player_ids": [player_id, recipient_player_id],
            "context": {
                "topic": "stage-11-2",
            },
        },
    }


def build_malformed_output(game_id: str, player_id: str, decision_type: str = "action_decision") -> dict[str, Any]:
    payload = build_action_output(game_id, player_id, state={"state_hash": "ignored", "event_sequence": 0}, suffix="invalid")
    del payload["self_dialogue"]
    payload["decision_type"] = decision_type
    if decision_type == "self_dialogue":
        del payload["expected_state_hash"]
        del payload["expected_event_sequence"]
        del payload["action"]
    return payload


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


async def create_game(client: httpx.AsyncClient) -> dict[str, Any]:
    response = await client.post(
        "/games",
        json={
            "seed": "phase-11-2-ai-audit-review",
            "players": [
                {"name": "Ada", "kind": "ai"},
                {"name": "Babbage", "kind": "human"},
            ],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def get_state(client: httpx.AsyncClient, game_id: str) -> dict[str, Any]:
    response = await client.get(f"/games/{game_id}/state")
    assert response.status_code == 200, response.text
    return response.json()


async def get_profiles(client: httpx.AsyncClient, game_id: str) -> list[dict[str, Any]]:
    response = await client.get(f"/games/{game_id}/ai/profiles")
    assert response.status_code == 200, response.text
    return response.json()["profiles"]


async def successful_json(response_awaitable: Any) -> dict[str, Any]:
    response = await response_awaitable
    assert response.status_code == 200, response.text
    return response.json()


def install_fake_runner(api_app: FastAPI, runner: Stage112Runner, tmp_path: Path) -> None:
    api_app.state.codex_ai_runner = runner
    api_app.state.codex_ai_schema_file = tmp_path / "schema.json"
    api_app.state.codex_ai_sandbox_dir = tmp_path / "sandbox"
    api_app.state.codex_ai_work_dir = tmp_path / "work"


async def fetch_decision_rows(session_factory: async_sessionmaker, game_id: str) -> list[dict[str, Any]]:
    async with session_factory() as session:
        result = await session.execute(
            sa.select(ai_decisions)
            .where(ai_decisions.c.game_id == UUID(game_id))
            .order_by(ai_decisions.c.created_at, ai_decisions.c.id)
        )
        return [dict(row) for row in result.mappings().all()]


async def fetch_game_status(session_factory: async_sessionmaker, game_id: str) -> str:
    async with session_factory() as session:
        result = await session.execute(sa.select(games.c.status).where(games.c.id == UUID(game_id)))
        return str(result.scalar_one())


async def delete_game(session_factory: async_sessionmaker, game_id: str) -> None:
    async with session_factory() as session:
        async with session.begin():
            await session.execute(ai_self_dialogue.delete().where(ai_self_dialogue.c.game_id == UUID(game_id)))
            await session.execute(ai_memory_entries.delete().where(ai_memory_entries.c.game_id == UUID(game_id)))
            await session.execute(retrieval_records.delete().where(retrieval_records.c.game_id == UUID(game_id)))
            await session.execute(ai_profiles.delete().where(ai_profiles.c.game_id == UUID(game_id)))
            await session.execute(games.delete().where(games.c.id == UUID(game_id)))


def _prompt_context_hash_map(rows: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    return {str(row["id"]): str(row["prompt_context_hash"]) for row in rows}


def _raw_output_map(rows: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    return {str(row["id"]): str(row["raw_output"]) for row in rows}


def _decision_by_id(rows: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    return {str(row["id"]): dict(row) for row in rows}


@pytest.mark.asyncio
async def test_stage_11_2_ai_command_uses_codex_exec_json_with_gpt_5_4_mini_light_reasoning() -> None:
    command = build_codex_exec_command(
        schema_file=Path("tmp-schema.json"),
        sandbox_dir=Path("tmp-sandbox"),
    )
    assert "codex" in command
    assert "exec" in command
    assert "--skip-git-repo-check" in command
    assert "--json" in command
    assert "--output-schema" in command
    assert "--model" in command
    assert command[command.index("--model") + 1] == DEFAULT_AI_MODEL
    assert DEFAULT_AI_MODEL == "gpt-5.4-mini"
    assert "-c" in command
    assert LIGHT_REASONING_CONFIG in command
    assert "model_reasoning_effort" in " ".join(command)
    assert "light" in " ".join(command)


@pytest.mark.asyncio
async def test_stage_11_2_invalid_output_rejected_or_ai_blocked_and_no_fallback_path(
    api_app: FastAPI,
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
    tmp_path: Path,
) -> None:
    created = await create_game(client)
    game_id = created["id"]
    ai_player_id = created["players"][0]["id"]
    state = await get_state(client, game_id)
    runner = Stage112Runner([build_malformed_output(game_id, ai_player_id)])
    install_fake_runner(api_app, runner, tmp_path)

    try:
        response = await client.post(
            f"/games/{game_id}/ai/step",
            json={
                "player_id": ai_player_id,
                "decision_type": "action_decision",
                "mandatory": True,
                "request_context": {
                    "state_snapshot": {"state_hash": state["state_hash"]},
                },
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] in {"rejected", "blocked"}
        assert await fetch_game_status(session_factory, game_id) == "AI_BLOCKED"
        assert len(runner.calls) == 1

        decisions = await fetch_decision_rows(session_factory, game_id)
        assert len(decisions) == 1
        decision = decisions[0]
        assert decision["status"] == "rejected"
        assert decision["validation_result"]["reason_code"] == "malformed_ai_output"
        assert decision["validation_result"]["no_substitute_move"] is True
        assert decision["validation_result"]["substitute_move"] is None

        rejected_outputs = (await successful_json(client.get(f"/games/{game_id}/ai/rejected-outputs")))["rejected_outputs"]
        assert len(rejected_outputs) == 1
        assert rejected_outputs[0]["status"] in {"rejected", "timeout", "process_error"}
        assert rejected_outputs[0]["raw_output"] == runner.calls[0]["stdout"]
    finally:
        await delete_game(session_factory, game_id)


@pytest.mark.asyncio
async def test_stage_11_2_ai_audit_endpoints_expose_memory_self_dialogue_and_reconstructable_decisions(
    api_app: FastAPI,
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
    tmp_path: Path,
) -> None:
    created = await create_game(client)
    game_id = created["id"]
    ai_player_id = created["players"][0]["id"]
    other_player_id = created["players"][1]["id"]
    state = await get_state(client, game_id)
    first_output = build_action_output(game_id, ai_player_id, state, suffix="first")
    second_output = build_open_negotiation_output(game_id, ai_player_id, other_player_id)
    third_output = build_malformed_output(game_id, ai_player_id, decision_type="open_negotiation")
    runner = Stage112Runner([first_output, second_output, third_output])
    install_fake_runner(api_app, runner, tmp_path)

    try:
        first = await client.post(
            f"/games/{game_id}/ai/step",
            json={"player_id": ai_player_id, "decision_type": "action_decision", "mandatory": True},
        )
        second = await client.post(
            f"/games/{game_id}/ai/step",
            json={"player_id": ai_player_id, "decision_type": "open_negotiation", "mandatory": False},
        )
        third = await client.post(
            f"/games/{game_id}/ai/step",
            json={"player_id": ai_player_id, "decision_type": "open_negotiation", "mandatory": False},
        )

        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        assert third.status_code == 200, third.text
        assert first.json()["status"] in {"accepted", "validated"}
        assert second.json()["status"] in {"accepted", "validated", "done"}
        assert third.json()["status"] in {"rejected", "blocked"}
        assert len(runner.calls) == 3

        profiles = await successful_json(client.get(f"/games/{game_id}/ai/profiles"))
        decisions = await successful_json(client.get(f"/games/{game_id}/ai/decisions"))
        memory = await successful_json(client.get(f"/games/{game_id}/ai/memory"))
        self_dialogue = await successful_json(client.get(f"/games/{game_id}/ai/self-dialogue"))
        retrieval = await successful_json(client.get(f"/games/{game_id}/ai/retrieval-records"))
        rejected_outputs = await successful_json(client.get(f"/games/{game_id}/ai/rejected-outputs"))

        assert "profiles" in profiles and profiles["profiles"]
        assert "decisions" in decisions and len(decisions["decisions"]) >= 3
        assert "memory_entries" in memory and len(memory["memory_entries"]) >= 2
        assert "self_dialogue" in self_dialogue and len(self_dialogue["self_dialogue"]) >= 2
        assert "retrieval_records" in retrieval and retrieval["retrieval_records"]
        assert "rejected_outputs" in rejected_outputs and rejected_outputs["rejected_outputs"]

        rows = await fetch_decision_rows(session_factory, game_id)
        by_id = _decision_by_id(rows)
        prompt_hashes = _prompt_context_hash_map(rows)
        raw_outputs = _raw_output_map(rows)

        memory_ids = {entry["memory_entry_id"] for entry in memory["memory_entries"]}
        retrieval_ids = {record["retrieval_record_id"] for record in retrieval["retrieval_records"]}

        for decision in decisions["decisions"]:
            decision_id = decision["ai_decision_id"]
            row = by_id[decision_id]
            assert row["prompt_context_hash"] == prompt_hashes[decision_id]
            assert row["raw_output"] == raw_outputs[decision_id]
            assert isinstance(decision["memory_entry_ids"], list)
            assert isinstance(decision["retrieval_record_ids"], list)
            assert set(decision["memory_entry_ids"]).issubset(memory_ids)
            assert set(decision["retrieval_record_ids"]).issubset(retrieval_ids)
            assert decision["prompt_context"]["context_pack_schema_version"] == "ai-context-pack-v1"

        linked_self_dialogue_ids = {entry["ai_decision_id"] for entry in self_dialogue["self_dialogue"]}
        linked_memory_source = {entry["source_decision_id"] for entry in memory["memory_entries"] if entry["source_decision_id"] is not None}

        for ai_decision_id in linked_memory_source:
            assert any(decision["ai_decision_id"] == ai_decision_id for decision in decisions["decisions"])

        assert linked_self_dialogue_ids
        assert linked_memory_source
        assert any("invalid" in (entry["content"] or "") for entry in memory["memory_entries"]) is False

        assert all(
            entry["player_id"] == ai_player_id for entry in self_dialogue["self_dialogue"]
        )
        assert await fetch_game_status(session_factory, game_id) in {"active", "AI_BLOCKED"}
        assert (
            str(ai_player_id)
            == str(self_dialogue["self_dialogue"][0]["player_id"])
        )
    finally:
        await delete_game(session_factory, game_id)
