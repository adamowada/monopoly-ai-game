from __future__ import annotations

import json
import os
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
    CodexExecAIDecisionRequest,
    CodexExecProcessResult,
    CodexExecRunner,
    CodexExecTimeoutError,
    request_codex_ai_decision,
)
from app.core.config import Settings
from app.db.metadata import (
    ai_decisions,
    ai_memory_entries,
    game_events,
    games,
    metadata,
)
from app.main import create_app


TEST_DATABASE_URL = os.getenv(
    "MONOPOLY_TEST_DATABASE_URL",
    "postgresql+asyncpg://monopoly:monopoly@127.0.0.1:5432/monopoly_ai_game",
)


class AuditFakeCodexRunner(CodexExecRunner):
    def __init__(
        self,
        outputs: Sequence[Mapping[str, Any] | str] = (),
        *,
        timeout: bool = False,
        returncode: int = 0,
        stderr: str = "",
    ) -> None:
        self.outputs = list(outputs)
        self.timeout = timeout
        self.returncode = returncode
        self.stderr = stderr
        self.calls: list[dict[str, Any]] = []

    def run(
        self,
        command: Sequence[str],
        *,
        stdin: str,
        timeout_seconds: float,
        output_last_message_path: Path | None,
    ) -> CodexExecProcessResult:
        if self.timeout:
            self.calls.append(
                {
                    "command": list(command),
                    "stdin": stdin,
                    "stdout": "",
                    "timeout_seconds": timeout_seconds,
                    "output_last_message_path": output_last_message_path,
                }
            )
            raise CodexExecTimeoutError(timeout_seconds)
        if not self.outputs:
            raise AssertionError("fake Codex runner received more calls than queued outputs")

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
                            "content": [{"type": "output_text", "text": output_text}],
                        },
                    }
                ),
            ]
        )
        if output_last_message_path is not None:
            output_last_message_path.parent.mkdir(parents=True, exist_ok=True)
            output_last_message_path.write_text(output_text, encoding="utf-8")

        self.calls.append(
            {
                "command": list(command),
                "stdin": stdin,
                "stdout": stdout,
                "timeout_seconds": timeout_seconds,
                "output_last_message_path": output_last_message_path,
            }
        )
        return CodexExecProcessResult(returncode=self.returncode, stdout=stdout, stderr=self.stderr)


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


@pytest.mark.asyncio
async def test_stage_8_4_decision_audit_validated_ai_action_stores_full_jsonl_and_exposes_links(
    api_app: FastAPI,
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
    tmp_path: Path,
) -> None:
    created = await create_game(client)
    game_id = created["id"]
    ai_player_id = created["players"][0]["id"]
    state = await get_state(client, game_id)
    profile = (await get_profiles(client, game_id))[0]
    memory_id = await insert_memory_snippet(
        session_factory,
        game_id=game_id,
        player_id=ai_player_id,
        ai_profile_id=profile["ai_profile_id"],
    )
    output = valid_action_output(game_id, ai_player_id, state)
    runner = AuditFakeCodexRunner([output])
    install_fake_runner(api_app, runner, tmp_path)

    try:
        response = await client.post(
            f"/games/{game_id}/ai/step",
            json={"player_id": ai_player_id, "decision_type": "action_decision", "mandatory": True},
        )
        body = response.json()
        assert response.status_code == 200, response.text
        assert body["status"] == "accepted"

        decision_row = await fetch_single_decision_row(session_factory, game_id)
        decisions = (await successful_json(client.get(f"/games/{game_id}/ai/decisions")))["decisions"]
        retrieval = (await successful_json(client.get(f"/games/{game_id}/ai/retrieval-records")))[
            "retrieval_records"
        ]
        decision = decisions[0]

        assert decision_row["raw_output"] == runner.calls[0]["stdout"]
        assert decision_row["raw_output"] != json.dumps(output)
        assert "session_configured" in decision_row["raw_output"]
        assert decision_row["parsed_output"]["action"] == output["action"]
        assert decision_row["parsed_output"]["self_dialogue"]["text"] == output["self_dialogue"]["text"]
        assert decision_row["validation_result"]["final_assistant_output"] == json.dumps(output)
        assert decision_row["validation_result"]["raw_output_format"] == "codex_exec_jsonl"
        assert decision_row["accepted_event_id"] == UUID(body["accepted_event_id"])
        assert decision_row["rejected_action_id"] is None

        assert decision["ai_decision_id"] == str(decision_row["id"])
        assert decision["status"] == "accepted"
        assert decision["accepted_event_id"] == body["accepted_event_id"]
        assert decision["rejected_action_id"] is None
        assert decision["prompt_context_hash"] == decision_row["prompt_context_hash"]
        assert decision["prompt_context"]["context_pack_schema_version"] == "ai-context-pack-v1"
        assert decision["raw_output"] == runner.calls[0]["stdout"]
        assert decision["parsed_output"]["action"] == output["action"]
        assert decision["parsed_output"]["self_dialogue"]["text"] == output["self_dialogue"]["text"]
        assert decision["validation_errors"] == []
        assert decision["legal_actions"][0]["type"] == "ROLL_DICE"
        assert str(memory_id) in decision["memory_entry_ids"]
        assert decision["retrieval_record_ids"]

        linked_retrieval_ids = {record["retrieval_record_id"] for record in retrieval}
        assert set(decision["retrieval_record_ids"]) == linked_retrieval_ids
        assert {record["source_type"] for record in retrieval} >= {"memory", "rule"}
        memory_records = [record for record in retrieval if record["source_type"] == "memory"]
        assert memory_records[0]["memory_entry_id"] == str(memory_id)
        assert memory_records[0]["ai_decision_id"] == decision["ai_decision_id"]
    finally:
        await delete_game(session_factory, game_id)


@pytest.mark.asyncio
async def test_stage_8_4_decision_audit_malformed_output_reaches_rejected_outputs_without_state_mutation(
    api_app: FastAPI,
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
    tmp_path: Path,
) -> None:
    created = await create_game(client)
    game_id = created["id"]
    ai_player_id = created["players"][0]["id"]
    before_state = await get_state(client, game_id)
    malformed = valid_action_output(game_id, ai_player_id, before_state)
    del malformed["self_dialogue"]
    runner = AuditFakeCodexRunner([malformed])
    install_fake_runner(api_app, runner, tmp_path)

    try:
        response = await client.post(
            f"/games/{game_id}/ai/step",
            json={"player_id": ai_player_id, "decision_type": "action_decision", "mandatory": True},
        )
        after_state = await get_state(client, game_id)
        rejected_outputs = (await successful_json(client.get(f"/games/{game_id}/ai/rejected-outputs")))[
            "rejected_outputs"
        ]
        decisions = (await successful_json(client.get(f"/games/{game_id}/ai/decisions")))["decisions"]

        assert response.status_code == 200, response.text
        assert response.json()["status"] == "blocked"
        assert after_state["state_hash"] == before_state["state_hash"]
        assert after_state["event_sequence"] == before_state["event_sequence"]
        assert await table_count(session_factory, game_events, game_id) == 0

        decision = decisions[0]
        rejected = rejected_outputs[0]
        assert decision["status"] == "rejected"
        assert decision["accepted_event_id"] is None
        assert decision["rejected_action_id"] == response.json()["rejected_action_id"]
        assert decision["raw_output"] == runner.calls[0]["stdout"]
        assert decision["parsed_output"]["action"]["type"] == "ROLL_DICE"
        assert decision["validation_errors"][0]["code"] == "malformed_ai_output"

        assert rejected["ai_decision_id"] == decision["ai_decision_id"]
        assert rejected["source_ai_decision_id"] == decision["ai_decision_id"]
        assert rejected["rejected_action_id"] == decision["rejected_action_id"]
        assert rejected["raw_output"] == runner.calls[0]["stdout"]
        assert rejected["parsed_output"]["action"]["type"] == "ROLL_DICE"
        assert rejected["validation_errors"][0]["code"] == "malformed_ai_output"
        assert rejected["state_hash"] == before_state["state_hash"]
        assert rejected["player_id"] == ai_player_id
    finally:
        await delete_game(session_factory, game_id)


@pytest.mark.asyncio
async def test_stage_8_4_decision_audit_timeout_and_process_error_records_are_listed(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
    tmp_path: Path,
) -> None:
    created = await create_game(client)
    game_id = created["id"]
    ai_player_id = created["players"][0]["id"]
    state = await get_state(client, game_id)
    profile = (await get_profiles(client, game_id))[0]
    request = CodexExecAIDecisionRequest(
        game_id=game_id,
        player_id=ai_player_id,
        ai_profile_id=profile["ai_profile_id"],
        decision_type="action_decision",
        phase=state["state"]["turn"]["phase"],
        state_hash=state["state_hash"],
        prompt_context={
            "legal_actions": [{"type": "ROLL_DICE", "payload": {}}],
            "memory": {"snippets": []},
            "rules": {"snippets": [{"id": "timeout-rule", "source": "test", "text": "No fallback."}]},
        },
        timeout_seconds=3,
    )

    try:
        timeout_result = await request_codex_ai_decision(
            session_factory,
            request,
            runner=AuditFakeCodexRunner(timeout=True),
            schema_file=tmp_path / "timeout-schema.json",
            sandbox_dir=tmp_path / "timeout-sandbox",
            work_dir=tmp_path / "timeout-work",
        )
        process_stdout = json.dumps({"type": "error", "message": "boom"}) + "\n"
        process_result = await request_codex_ai_decision(
            session_factory,
            request,
            runner=AuditFakeCodexRunner(
                [valid_action_output(game_id, ai_player_id, state)],
                returncode=2,
                stderr="boom",
            ),
            schema_file=tmp_path / "process-schema.json",
            sandbox_dir=tmp_path / "process-sandbox",
            work_dir=tmp_path / "process-work",
        )
        await force_process_error_stdout(session_factory, process_result.ai_decision_id, process_stdout)

        decisions = (await successful_json(client.get(f"/games/{game_id}/ai/decisions")))["decisions"]
        rejected_outputs = (await successful_json(client.get(f"/games/{game_id}/ai/rejected-outputs")))[
            "rejected_outputs"
        ]

        statuses = {decision["status"] for decision in decisions}
        assert statuses == {"timeout", "process_error"}
        assert {output["status"] for output in rejected_outputs} == {"timeout", "process_error"}
        timeout_output = next(output for output in rejected_outputs if output["status"] == "timeout")
        process_output = next(output for output in rejected_outputs if output["status"] == "process_error")
        assert timeout_output["ai_decision_id"] == str(timeout_result.ai_decision_id)
        assert timeout_output["raw_output"] == ""
        assert timeout_output["validation_errors"][0]["code"] == "codex_exec_timeout"
        assert process_output["ai_decision_id"] == str(process_result.ai_decision_id)
        assert process_output["raw_output"] == process_stdout
        assert process_output["validation_errors"][0]["code"] == "codex_exec_process_error"
    finally:
        await delete_game(session_factory, game_id)


async def create_game(client: httpx.AsyncClient) -> dict[str, Any]:
    response = await client.post(
        "/games",
        json={
            "seed": "stage-8-4-decision-audit",
            "players": [
                {"name": "Grace", "kind": "ai"},
                {"name": "Ada", "kind": "human"},
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
    profiles = response.json()["profiles"]
    assert profiles
    return profiles


async def successful_json(response_awaitable: Any) -> dict[str, Any]:
    response = await response_awaitable
    assert response.status_code == 200, response.text
    return response.json()


def install_fake_runner(api_app: FastAPI, runner: AuditFakeCodexRunner, tmp_path: Path) -> None:
    api_app.state.codex_ai_runner = runner
    api_app.state.codex_ai_schema_file = tmp_path / "schema.json"
    api_app.state.codex_ai_sandbox_dir = tmp_path / "sandbox"
    api_app.state.codex_ai_work_dir = tmp_path / "work"


def valid_action_output(game_id: str, ai_player_id: str, state_response: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "decision_type": "action_decision",
        "game_id": game_id,
        "player_id": ai_player_id,
        "expected_state_hash": state_response["state_hash"],
        "expected_event_sequence": state_response["event_sequence"],
        "action": {"type": "ROLL_DICE", "payload": {}},
        "self_dialogue": {
            "status": "provided",
            "text": "Stage 8.4 audit test chooses the legal roll.",
        },
        "memory_updates": [],
        "confidence": 0.91,
        "rationale": "The legal action snapshot contains ROLL_DICE.",
    }


async def insert_memory_snippet(
    session_factory: async_sessionmaker,
    *,
    game_id: str,
    player_id: str,
    ai_profile_id: str,
) -> UUID:
    async with session_factory() as session:
        async with session.begin():
            result = await session.execute(
                ai_memory_entries.insert()
                .values(
                    game_id=UUID(game_id),
                    player_id=UUID(player_id),
                    ai_profile_id=UUID(ai_profile_id),
                    category="strategic_belief",
                    visibility="private",
                    content="Stage 8.4 memory snippet actually passed to Codex.",
                    importance=8,
                    metadata_blob={"stage": "8.4", "source": "test"},
                )
                .returning(ai_memory_entries.c.id)
            )
            return result.scalar_one()


async def fetch_single_decision_row(
    session_factory: async_sessionmaker,
    game_id: str,
) -> dict[str, Any]:
    async with session_factory() as session:
        result = await session.execute(
            sa.select(ai_decisions)
            .where(ai_decisions.c.game_id == UUID(game_id))
            .order_by(ai_decisions.c.created_at.desc(), ai_decisions.c.id.desc())
        )
        rows = [dict(row) for row in result.mappings().all()]
    assert len(rows) == 1
    return rows[0]


async def table_count(session_factory: async_sessionmaker, table: sa.Table, game_id: str) -> int:
    async with session_factory() as session:
        result = await session.execute(
            sa.select(sa.func.count()).select_from(table).where(table.c.game_id == UUID(game_id))
        )
        return int(result.scalar_one())


async def force_process_error_stdout(
    session_factory: async_sessionmaker,
    ai_decision_id: UUID,
    raw_output: str,
) -> None:
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                ai_decisions.update()
                .where(ai_decisions.c.id == ai_decision_id)
                .values(raw_output=raw_output)
            )


async def delete_game(session_factory: async_sessionmaker, game_id: str) -> None:
    async with session_factory() as session:
        async with session.begin():
            await session.execute(games.delete().where(games.c.id == UUID(game_id)))
