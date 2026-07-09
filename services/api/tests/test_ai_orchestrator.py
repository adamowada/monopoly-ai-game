"""Stage 7.3 evidence:

- fake subprocess harness
- Python subprocess wrapper
- Prompt construction
- stdin/stdout handling
- JSONL event parsing
- Timeout handling
- Process error handling
- Storage of raw AI output and parsed output
- Invalid process output is rejected without mutating game state
- Timeouts produce audit records and do not create fallback moves
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.ai.enforcement import AIOutputEnforcementRequest, enforce_ai_output
from app.ai.orchestrator import (
    CodexExecAIDecisionRequest,
    CodexExecProcessResult,
    CodexExecRunner,
    CodexExecTimeoutError,
    CodexSubprocessRunner,
    DEFAULT_AI_MODEL,
    DEFAULT_AI_SCHEMA_FILE,
    LIGHT_REASONING_CONFIG,
    build_codex_exec_command,
    build_prompt,
    parse_codex_jsonl_events,
    request_codex_ai_decision,
    write_ai_output_schema_file,
)
from app.db.metadata import (
    ai_decisions,
    ai_memory_entries,
    ai_profiles,
    ai_self_dialogue,
    game_events,
    games,
    metadata,
    players,
)
from app.rules.state import GameState, PlayerSetup, create_initial_game_state


TEST_DATABASE_URL = os.getenv(
    "MONOPOLY_TEST_DATABASE_URL",
    "postgresql+asyncpg://monopoly:monopoly@127.0.0.1:5432/monopoly_ai_game",
)

GAME_ID = UUID("00000000-0000-0000-0000-000000007301")
PLAYER_ID = UUID("00000000-0000-0000-0000-000000007302")
OTHER_PLAYER_ID = UUID("00000000-0000-0000-0000-000000007303")


@dataclass(frozen=True)
class OrchestratorFixture:
    game_id: UUID
    player_id: UUID
    ai_profile_id: UUID
    state: GameState


class FakeCodexRunner(CodexExecRunner):
    def __init__(
        self,
        *,
        final_output: dict[str, Any] | str | None = None,
        stdout: str | None = None,
        stderr: str = "",
        returncode: int = 0,
        timeout: bool = False,
    ) -> None:
        self.final_output = final_output
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.timeout = timeout
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
        if self.timeout:
            raise CodexExecTimeoutError(timeout_seconds)

        stdout = self.stdout
        if stdout is None:
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
                                    {
                                        "type": "output_text",
                                        "text": json.dumps(self.final_output),
                                    }
                                ],
                            },
                        }
                    ),
                ]
            )

        if output_last_message_path is not None and self.final_output is not None:
            output_last_message_path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(self.final_output, str):
                output_last_message_path.write_text(self.final_output, encoding="utf-8")
            else:
                output_last_message_path.write_text(
                    json.dumps(self.final_output),
                    encoding="utf-8",
                )

        return CodexExecProcessResult(
            returncode=self.returncode,
            stdout=stdout,
            stderr=self.stderr,
        )


class LaunchFailingCodexRunner(CodexExecRunner):
    def __init__(self, error: OSError) -> None:
        self.error = error
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
        raise self.error


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


async def create_ai_game(session_factory: async_sessionmaker) -> OrchestratorFixture:
    state = create_initial_game_state(
        seed="phase-7-stage-7.3-orchestrator",
        game_id=str(GAME_ID),
        players=(
            PlayerSetup(id=str(PLAYER_ID), name="Grace", kind="ai"),
            PlayerSetup(id=str(OTHER_PLAYER_ID), name="Ada", kind="human"),
        ),
    )
    ai_profile_id = UUID("00000000-0000-0000-0000-000000007304")

    async with session_factory() as session:
        async with session.begin():
            await session.execute(games.delete().where(games.c.id == GAME_ID))
            await session.execute(
                games.insert().values(
                    id=GAME_ID,
                    status="active",
                    ruleset_version=state.ruleset_version,
                    seed="phase-7-stage-7.3-orchestrator",
                    current_phase=state.turn.phase.value,
                    settings={},
                    initial_state=state.model_dump(mode="json"),
                )
            )
            for seat_order, player_state in enumerate(state.players):
                await session.execute(
                    players.insert().values(
                        id=UUID(player_state.id),
                        game_id=GAME_ID,
                        seat_order=seat_order,
                        name=player_state.name,
                        controller_type=player_state.kind,
                        state=player_state.model_dump(mode="json"),
                    )
                )
            await session.execute(
                ai_profiles.insert().values(
                    id=ai_profile_id,
                    game_id=GAME_ID,
                    player_id=PLAYER_ID,
                    persona_name="Test Persona",
                    strategy_profile={"risk_tolerance": 0.4},
                    persona_summary={"summary": "A deterministic test AI."},
                )
            )

    return OrchestratorFixture(
        game_id=GAME_ID,
        player_id=PLAYER_ID,
        ai_profile_id=ai_profile_id,
        state=state,
    )


async def delete_game(session_factory: async_sessionmaker, game_id: UUID = GAME_ID) -> None:
    async with session_factory() as session:
        async with session.begin():
            await session.execute(games.delete().where(games.c.id == game_id))


async def fetch_ai_decision_rows(
    session_factory: async_sessionmaker,
    game_id: UUID = GAME_ID,
) -> list[dict[str, Any]]:
    async with session_factory() as session:
        result = await session.execute(
            sa.select(ai_decisions)
            .where(ai_decisions.c.game_id == game_id)
            .order_by(ai_decisions.c.created_at.desc(), ai_decisions.c.id.desc())
        )
        return [dict(row) for row in result.mappings().all()]


async def fetch_ai_self_dialogue_rows(
    session_factory: async_sessionmaker,
    game_id: UUID = GAME_ID,
) -> list[dict[str, Any]]:
    async with session_factory() as session:
        result = await session.execute(
            sa.select(ai_self_dialogue)
            .where(ai_self_dialogue.c.game_id == game_id)
            .order_by(ai_self_dialogue.c.created_at, ai_self_dialogue.c.id)
        )
        return [dict(row) for row in result.mappings().all()]


async def fetch_ai_memory_rows(
    session_factory: async_sessionmaker,
    game_id: UUID = GAME_ID,
) -> list[dict[str, Any]]:
    async with session_factory() as session:
        result = await session.execute(
            sa.select(ai_memory_entries)
            .where(ai_memory_entries.c.game_id == game_id)
            .order_by(ai_memory_entries.c.created_at, ai_memory_entries.c.id)
        )
        return [dict(row) for row in result.mappings().all()]


async def count_events(session_factory: async_sessionmaker, game_id: UUID = GAME_ID) -> int:
    async with session_factory() as session:
        result = await session.execute(
            sa.select(sa.func.count()).select_from(game_events).where(game_events.c.game_id == game_id)
        )
        return int(result.scalar_one())


def valid_action_output(state: GameState) -> dict[str, Any]:
    return {
        "decision_type": "action_decision",
        "game_id": str(GAME_ID),
        "player_id": str(PLAYER_ID),
        "expected_state_hash": state.state_hash(),
        "expected_event_sequence": state.event_sequence,
        "action": {"type": "ROLL_DICE", "payload": {}},
        "self_dialogue": {
            "status": "provided",
            "text": "Opening tempo matters, so rolling is the only useful action.",
        },
        "memory_updates": [],
        "confidence": 0.81,
        "rationale": "The turn is at START_TURN and a roll is available.",
    }


def valid_action_output_with_memory(state: GameState) -> dict[str, Any]:
    output = valid_action_output(state)
    output["memory_updates"] = [
        {
            "visibility": "private",
            "category": "strategic_belief",
            "importance": 7,
            "content": "Early rolling is tempo-positive when no negotiation is active.",
            "metadata": {"stage": "8.2", "source": "schema-valid-output"},
        },
        {
            "visibility": "table",
            "category": "deal_history",
            "importance": 4,
            "content": "No deals have been offered before the first roll.",
            "metadata": {"stage": "8.2", "visible_to_table": True},
        },
    ]
    return output


def valid_memory_update_output(state: GameState) -> dict[str, Any]:
    output = {
        key: value
        for key, value in valid_action_output_with_memory(state).items()
        if key not in {"action", "expected_event_sequence", "expected_state_hash"}
    }
    output["decision_type"] = "memory_update"
    output["rationale"] = "This non-mutating decision only records trusted memory."
    return output


def decision_request(fixture: OrchestratorFixture) -> CodexExecAIDecisionRequest:
    return CodexExecAIDecisionRequest(
        game_id=fixture.game_id,
        player_id=fixture.player_id,
        ai_profile_id=fixture.ai_profile_id,
        decision_type="action_decision",
        phase=fixture.state.turn.phase.value,
        state_hash=fixture.state.state_hash(),
        prompt_context={
            "caller_context": "stage 7.3 fake subprocess request",
            "legal_actions": [{"type": "ROLL_DICE", "payload": {}}],
        },
        timeout_seconds=7,
    )


def test_builds_verified_codex_exec_command_and_writes_schema(tmp_path: Path) -> None:
    evidence = "Codex exec command forces no approval prompts"
    schema_path = write_ai_output_schema_file(
        tmp_path / "agent_decision.schema.json",
        decision_type="action_decision",
    )
    sandbox_dir = tmp_path / "ai-sandbox"
    last_message_path = tmp_path / "last-message.json"

    command = build_codex_exec_command(
        codex_executable="codex",
        schema_file=schema_path,
        sandbox_dir=sandbox_dir,
        output_last_message_path=last_message_path,
    )

    assert command[:4] == ["codex", "-a", "never", "exec"], evidence
    assert "--skip-git-repo-check" in command
    assert "--json" in command
    assert "--ephemeral" in command
    assert command.count("--disable") == 3
    assert "plugins" in command
    assert "plugin_hooks" in command
    assert "shell_snapshot" in command
    assert command[command.index("--model") + 1] == DEFAULT_AI_MODEL
    assert DEFAULT_AI_MODEL == "gpt-5.4-mini"
    config_values = [command[index + 1] for index, value in enumerate(command[:-1]) if value == "-c"]
    assert "mcp_servers.robinhood-trading.enabled=false" in config_values
    assert LIGHT_REASONING_CONFIG in config_values
    assert LIGHT_REASONING_CONFIG == 'model_reasoning_effort="low"'
    assert 'model_reasoning_effort="light"' not in config_values
    assert command[command.index("--output-schema") + 1] == str(schema_path)
    assert command[command.index("-C") + 1] == str(sandbox_dir)
    assert command[command.index("--output-last-message") + 1] == str(last_message_path)
    assert "-" not in command
    assert command[-1] == str(last_message_path)
    written_schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert written_schema["type"] == "object"
    assert written_schema["properties"]["decision_type"]["const"] == "action_decision"
    assert "oneOf" not in written_schema
    assert "$defs" not in written_schema


def test_default_schema_file_is_runtime_generated_not_checked_in_schema() -> None:
    assert DEFAULT_AI_SCHEMA_FILE.parent.name == "runtime"
    assert DEFAULT_AI_SCHEMA_FILE.name == "agent_decision.schema.json"


def test_default_runtime_schema_file_is_ignored_by_git() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    runtime_schema_path = DEFAULT_AI_SCHEMA_FILE.resolve().relative_to(repo_root)

    completed = subprocess.run(
        ["git", "check-ignore", "-q", "--", runtime_schema_path.as_posix()],
        cwd=repo_root,
        check=False,
    )

    assert completed.returncode == 0


def test_subprocess_wrapper_uses_stdin_stdout_timeout_and_json_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_popen(command: Sequence[str], **kwargs: Any) -> "_CompletedFakePopen":
        captured["command"] = list(command)
        captured["kwargs"] = kwargs
        return _CompletedFakePopen(captured)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    result = CodexSubprocessRunner().run(
        ["codex", "exec", "--json", "-"],
        stdin="prompt on stdin",
        timeout_seconds=3,
        output_last_message_path=Path("unused.json"),
    )

    assert captured["command"] == ["codex", "exec", "--json", "-"]
    assert captured["kwargs"]["stdin"] == subprocess.PIPE
    assert captured["kwargs"]["stdout"] == subprocess.PIPE
    assert captured["kwargs"]["stderr"] == subprocess.PIPE
    assert captured["kwargs"]["text"] is True
    assert captured["kwargs"]["encoding"] == "utf-8"
    if os.name == "nt":
        assert captured["kwargs"]["creationflags"] == subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        assert captured["kwargs"]["start_new_session"] is True
    assert captured["communicate"]["input"] == "prompt on stdin"
    assert captured["communicate"]["timeout"] is None
    assert captured["wait_timeout"] == 3
    assert result.stdout == '{"type":"session_configured"}\n'
    assert result.stderr == ""
    assert result.returncode == 0


def test_subprocess_wrapper_passes_configured_codex_home_and_preserves_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, Any] = {}
    configured_codex_home = tmp_path / "configured-codex-home"
    monkeypatch.setenv("CODEX_HOME", "inherited-codex-home")
    monkeypatch.setenv("MONOPOLY_CODEX_HOME_TEST_ENV", "preserved")

    def fake_popen(command: Sequence[str], **kwargs: Any) -> "_CompletedFakePopen":
        captured["command"] = list(command)
        captured["kwargs"] = kwargs
        return _CompletedFakePopen(captured)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    result = CodexSubprocessRunner(codex_home=configured_codex_home).run(
        ["codex", "exec", "--json", "-"],
        stdin="prompt on stdin",
        timeout_seconds=3,
        output_last_message_path=Path("unused.json"),
    )

    assert captured["command"] == ["codex", "exec", "--json", "-"]
    env = captured["kwargs"]["env"]
    assert env["CODEX_HOME"] == str(configured_codex_home)
    assert env["MONOPOLY_CODEX_HOME_TEST_ENV"] == "preserved"
    assert result.returncode == 0


class _CompletedFakePopen:
    returncode = 0

    def __init__(self, captured: dict[str, Any]) -> None:
        self._captured = captured

    def communicate(self, input: str | None = None, timeout: float | None = None) -> tuple[str, str]:
        self._captured["communicate"] = {"input": input, "timeout": timeout}
        return '{"type":"session_configured"}\n', ""

    def wait(self, timeout: float | None = None) -> int:
        self._captured["wait_timeout"] = timeout
        return self.returncode

    def poll(self) -> int:
        return self.returncode

    def kill(self) -> None:
        self._captured["killed"] = True

    def terminate(self) -> None:
        self._captured["terminated"] = True


def test_prompt_construction_keeps_caller_context_without_building_stage_7_4_pack() -> None:
    request = CodexExecAIDecisionRequest(
        game_id=GAME_ID,
        player_id=PLAYER_ID,
        decision_type="negotiation_message",
        phase="NEGOTIATION",
        state_hash="state-hash",
        prompt_context={"caller_supplied": {"summary": "offer received"}},
    )

    prompt = build_prompt(request)

    assert "decision_type: negotiation_message" in prompt
    assert str(GAME_ID) in prompt
    assert str(PLAYER_ID) in prompt
    assert '"caller_supplied"' in prompt
    assert "Return exactly one JSON object" in prompt
    assert "No fallback" in prompt
    assert "context pack" not in prompt.lower()


def test_jsonl_event_parser_extracts_assistant_output_and_rejects_invalid_lines() -> None:
    final = {"decision_type": "self_dialogue", "game_id": str(GAME_ID), "player_id": str(PLAYER_ID)}
    events = parse_codex_jsonl_events(
        "\n".join(
            [
                json.dumps({"type": "session_configured"}),
                json.dumps(
                    {
                        "type": "item_completed",
                        "item": {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": json.dumps(final)}],
                        },
                    }
                ),
            ]
        )
    )

    assert events.final_assistant_output == json.dumps(final)
    assert events.events[0]["type"] == "session_configured"

    with pytest.raises(ValueError, match="line 2"):
        parse_codex_jsonl_events('{"type":"ok"}\nnot-json')


@pytest.mark.asyncio
async def test_codex_exec_orchestrator_persists_valid_raw_and_parsed_output(
    session_factory: async_sessionmaker,
    tmp_path: Path,
) -> None:
    fixture = await create_ai_game(session_factory)
    runner = FakeCodexRunner(final_output=valid_action_output(fixture.state))
    try:
        result = await request_codex_ai_decision(
            session_factory,
            decision_request(fixture),
            runner=runner,
            schema_file=tmp_path / "schema.json",
            sandbox_dir=tmp_path / "sandbox",
            work_dir=tmp_path / "work",
        )
        rows = await fetch_ai_decision_rows(session_factory)

        assert result.status == "validated"
        assert result.accepted_event_id is None
        assert result.rejected_action_id is None
        assert len(rows) == 1
        assert rows[0]["id"] == result.ai_decision_id
        assert rows[0]["status"] == "validated"
        assert rows[0]["decision_type"] == "action_decision"
        assert rows[0]["ai_profile_id"] == fixture.ai_profile_id
        assert rows[0]["phase"] == "START_TURN"
        assert rows[0]["state_hash"] == fixture.state.state_hash()
        assert rows[0]["prompt_context"]["caller_context"] == "stage 7.3 fake subprocess request"
        assert rows[0]["prompt_context_hash"] == result.prompt_context_hash
        assert "session_configured" in rows[0]["raw_output"]
        assert "item_completed" in rows[0]["raw_output"]
        assert rows[0]["raw_output"] != json.dumps(valid_action_output(fixture.state))
        assert rows[0]["parsed_output"]["action"]["type"] == "ROLL_DICE"
        assert rows[0]["validation_result"]["status"] == "valid"
        assert rows[0]["accepted_event_id"] is None
        assert rows[0]["rejected_action_id"] is None
        assert await count_events(session_factory) == 0

        call = runner.calls[0]
        assert call["command"][-1] == "-"
        assert "--output-schema" in call["command"]
        assert "--json" in call["command"]
        assert "--ephemeral" in call["command"]
        assert call["command"][call["command"].index("--model") + 1] == "gpt-5.4-mini"
        assert 'model_reasoning_effort="low"' in call["command"]
        assert 'model_reasoning_effort="light"' not in call["command"]
        assert "stage 7.3 fake subprocess request" in call["stdin"]
    finally:
        await delete_game(session_factory)


@pytest.mark.asyncio
async def test_stage_8_2_memory_trusted_non_mutating_ai_output_writes_entries_linked_to_decision(
    session_factory: async_sessionmaker,
    tmp_path: Path,
) -> None:
    fixture = await create_ai_game(session_factory)
    ai_output = valid_memory_update_output(fixture.state)
    runner = FakeCodexRunner(final_output=ai_output)
    try:
        result = await enforce_ai_output(
            session_factory,
            AIOutputEnforcementRequest(
                game_id=fixture.game_id,
                player_id=fixture.player_id,
                ai_profile_id=fixture.ai_profile_id,
                decision_type="memory_update",
                mandatory=False,
                request_context={"caller_context": "stage 8.2 finalization memory test"},
                timeout_seconds=7,
            ),
            runner=runner,
            schema_file=tmp_path / "schema.json",
            sandbox_dir=tmp_path / "sandbox",
            work_dir=tmp_path / "work",
        )
        memory_rows = await fetch_ai_memory_rows(session_factory)

        assert result.status == "validated"
        assert len(memory_rows) == 2
        rows_by_category = {row["category"]: row for row in memory_rows}
        updates_by_category = {
            update["category"]: update for update in ai_output["memory_updates"]
        }
        assert set(rows_by_category) == {"strategic_belief", "deal_history"}
        for category, update in updates_by_category.items():
            row = rows_by_category[category]
            assert row["game_id"] == fixture.game_id
            assert row["player_id"] == fixture.player_id
            assert row["ai_profile_id"] == fixture.ai_profile_id
            assert row["source_decision_id"] == result.ai_decision_id
            assert row["source_event_id"] is None
            assert row["source_negotiation_message_id"] is None
            assert row["visibility"] == update["visibility"]
            assert row["content"] == update["content"]
            assert row["importance"] == update["importance"]
            assert row["metadata_blob"]["memory_update"] == update["metadata"]
            assert row["metadata_blob"]["trusted_ai_output"] is True
            assert row["metadata_blob"]["ai_decision_status"] == "validated"
            assert row["created_at"] is not None
            assert row["updated_at"] is not None
    finally:
        await delete_game(session_factory)


@pytest.mark.asyncio
async def test_stage_8_2_memory_untrusted_malformed_output_does_not_write_entries(
    session_factory: async_sessionmaker,
    tmp_path: Path,
) -> None:
    fixture = await create_ai_game(session_factory)
    ai_output = valid_action_output_with_memory(fixture.state)
    del ai_output["self_dialogue"]
    runner = FakeCodexRunner(final_output=ai_output)
    try:
        result = await request_codex_ai_decision(
            session_factory,
            decision_request(fixture),
            runner=runner,
            schema_file=tmp_path / "schema.json",
            sandbox_dir=tmp_path / "sandbox",
            work_dir=tmp_path / "work",
        )
        memory_rows = await fetch_ai_memory_rows(session_factory)

        assert result.status == "rejected"
        assert result.validation_result["reason_code"] == "malformed_ai_output"
        assert memory_rows == []
    finally:
        await delete_game(session_factory)


@pytest.mark.asyncio
async def test_stage_8_1_self_dialogue_valid_ai_decision_writes_linked_row(
    session_factory: async_sessionmaker,
    tmp_path: Path,
) -> None:
    fixture = await create_ai_game(session_factory)
    ai_output = valid_action_output(fixture.state)
    runner = FakeCodexRunner(final_output=ai_output)
    try:
        result = await request_codex_ai_decision(
            session_factory,
            decision_request(fixture),
            runner=runner,
            schema_file=tmp_path / "schema.json",
            sandbox_dir=tmp_path / "sandbox",
            work_dir=tmp_path / "work",
        )
        decision_rows = await fetch_ai_decision_rows(session_factory)
        dialogue_rows = await fetch_ai_self_dialogue_rows(session_factory)

        assert result.status == "validated"
        assert len(decision_rows) == 1
        assert len(dialogue_rows) == 1
        dialogue = dialogue_rows[0]
        assert dialogue["game_id"] == fixture.game_id
        assert dialogue["player_id"] == fixture.player_id
        assert dialogue["ai_decision_id"] == result.ai_decision_id
        assert dialogue["ai_decision_id"] == decision_rows[0]["id"]
        assert dialogue["phase"] == fixture.state.turn.phase.value
        assert dialogue["state_hash"] == fixture.state.state_hash()
        assert dialogue["content"] == ai_output["self_dialogue"]["text"]
        assert dialogue["payload"] == {**ai_output["self_dialogue"], "reason": None}
        assert dialogue["created_at"] is not None
    finally:
        await delete_game(session_factory)


@pytest.mark.asyncio
async def test_stage_8_1_self_dialogue_invalid_ai_decision_writes_rejected_row(
    session_factory: async_sessionmaker,
    tmp_path: Path,
) -> None:
    fixture = await create_ai_game(session_factory)
    ai_output = valid_action_output(fixture.state)
    ai_output["memory_updates"] = [
        {
            "visibility": "private",
            "category": "mistake_lesson",
            "importance": 6,
            "content": "This malformed output must not persist memory.",
            "metadata": {"stage": "8.2"},
        }
    ]
    del ai_output["self_dialogue"]
    runner = FakeCodexRunner(final_output=ai_output)
    try:
        result = await request_codex_ai_decision(
            session_factory,
            decision_request(fixture),
            runner=runner,
            schema_file=tmp_path / "schema.json",
            sandbox_dir=tmp_path / "sandbox",
            work_dir=tmp_path / "work",
        )
        decision_rows = await fetch_ai_decision_rows(session_factory)
        dialogue_rows = await fetch_ai_self_dialogue_rows(session_factory)
        memory_rows = await fetch_ai_memory_rows(session_factory)

        assert result.status == "rejected"
        assert result.validation_result["reason_code"] == "malformed_ai_output"
        assert len(decision_rows) == 1
        assert len(dialogue_rows) == 1
        assert memory_rows == []
        dialogue = dialogue_rows[0]
        assert dialogue["game_id"] == fixture.game_id
        assert dialogue["player_id"] == fixture.player_id
        assert dialogue["ai_decision_id"] == result.ai_decision_id
        assert dialogue["ai_decision_id"] == decision_rows[0]["id"]
        assert dialogue["phase"] == fixture.state.turn.phase.value
        assert dialogue["state_hash"] == fixture.state.state_hash()
        assert dialogue["content"].startswith("Self-dialogue rejected:")
        assert dialogue["payload"]["status"] == "rejected"
        assert dialogue["payload"]["reason_code"] == "malformed_ai_output"
        assert dialogue["payload"]["source_status"] == "rejected"
        assert dialogue["payload"]["validation_errors"][0]["code"] == "malformed_ai_output"
    finally:
        await delete_game(session_factory)


@pytest.mark.asyncio
async def test_malformed_process_output_is_rejected_without_mutating_game_state(
    session_factory: async_sessionmaker,
    tmp_path: Path,
) -> None:
    fixture = await create_ai_game(session_factory)
    runner = FakeCodexRunner(
        final_output={
            "decision_type": "action_decision",
            "game_id": str(GAME_ID),
            "player_id": str(PLAYER_ID),
            "action": {"type": "ROLL_DICE", "payload": {}},
        }
    )
    try:
        result = await request_codex_ai_decision(
            session_factory,
            decision_request(fixture),
            runner=runner,
            schema_file=tmp_path / "schema.json",
            sandbox_dir=tmp_path / "sandbox",
            work_dir=tmp_path / "work",
        )
        rows = await fetch_ai_decision_rows(session_factory)

        assert result.status == "rejected"
        assert result.accepted_event_id is None
        assert result.rejected_action_id is None
        assert result.validation_result["reason_code"] == "malformed_ai_output"
        assert result.validation_result["no_substitute_move"] is True
        assert result.validation_result["substitute_move"] is None
        assert rows[0]["status"] == "rejected"
        assert "session_configured" in rows[0]["raw_output"]
        assert "item_completed" in rows[0]["raw_output"]
        assert rows[0]["raw_output"] != json.dumps(runner.final_output)
        assert rows[0]["parsed_output"]["action"]["type"] == "ROLL_DICE"
        assert rows[0]["validation_result"]["reason_code"] == "malformed_ai_output"
        assert rows[0]["accepted_event_id"] is None
        assert rows[0]["rejected_action_id"] is None
        assert await count_events(session_factory) == 0
    finally:
        await delete_game(session_factory)


@pytest.mark.asyncio
async def test_non_object_malformed_ai_output_preserves_decoded_value_without_events(
    session_factory: async_sessionmaker,
    tmp_path: Path,
) -> None:
    fixture = await create_ai_game(session_factory)
    decoded_value = "schema-invalid scalar output"
    runner = FakeCodexRunner(final_output=json.dumps(decoded_value))
    try:
        result = await request_codex_ai_decision(
            session_factory,
            decision_request(fixture),
            runner=runner,
            schema_file=tmp_path / "schema.json",
            sandbox_dir=tmp_path / "sandbox",
            work_dir=tmp_path / "work",
        )
        rows = await fetch_ai_decision_rows(session_factory)

        assert result.status == "rejected"
        assert result.validation_result["reason_code"] == "malformed_ai_output"
        assert result.parsed_output == decoded_value
        assert result.accepted_event_id is None
        assert result.rejected_action_id is None
        assert len(rows) == 1
        assert rows[0]["status"] == "rejected"
        assert "session_configured" in rows[0]["raw_output"]
        assert "item_completed" in rows[0]["raw_output"]
        assert rows[0]["raw_output"] != json.dumps(decoded_value)
        assert rows[0]["parsed_output"] == decoded_value
        assert rows[0]["validation_result"]["reason_code"] == "malformed_ai_output"
        assert rows[0]["accepted_event_id"] is None
        assert rows[0]["rejected_action_id"] is None
        assert await count_events(session_factory) == 0
    finally:
        await delete_game(session_factory)


@pytest.mark.asyncio
async def test_invalid_jsonl_output_is_rejected_without_substitute_move(
    session_factory: async_sessionmaker,
    tmp_path: Path,
) -> None:
    fixture = await create_ai_game(session_factory)
    runner = FakeCodexRunner(stdout='{"type":"session_configured"}\nnot-json')
    try:
        result = await request_codex_ai_decision(
            session_factory,
            decision_request(fixture),
            runner=runner,
            schema_file=tmp_path / "schema.json",
            sandbox_dir=tmp_path / "sandbox",
            work_dir=tmp_path / "work",
        )
        rows = await fetch_ai_decision_rows(session_factory)

        assert result.status == "rejected"
        assert result.validation_result["reason_code"] == "malformed_ai_output"
        assert result.validation_result["no_substitute_move"] is True
        assert rows[0]["raw_output"] == '{"type":"session_configured"}\nnot-json'
        assert rows[0]["parsed_output"] is None
        assert rows[0]["accepted_event_id"] is None
        assert rows[0]["rejected_action_id"] is None
        assert await count_events(session_factory) == 0
    finally:
        await delete_game(session_factory)


@pytest.mark.asyncio
async def test_timeout_persists_audit_record_and_no_fallback_move(
    session_factory: async_sessionmaker,
    tmp_path: Path,
) -> None:
    fixture = await create_ai_game(session_factory)
    runner = FakeCodexRunner(timeout=True)
    try:
        result = await request_codex_ai_decision(
            session_factory,
            decision_request(fixture),
            runner=runner,
            schema_file=tmp_path / "schema.json",
            sandbox_dir=tmp_path / "sandbox",
            work_dir=tmp_path / "work",
        )
        rows = await fetch_ai_decision_rows(session_factory)

        assert result.status == "timeout"
        assert result.raw_output == ""
        assert result.validation_result["reason_code"] == "codex_exec_timeout"
        assert result.validation_result["no_substitute_move"] is True
        assert result.accepted_event_id is None
        assert result.rejected_action_id is None
        assert rows[0]["status"] == "timeout"
        assert rows[0]["validation_result"]["timeout_seconds"] == 7
        assert rows[0]["accepted_event_id"] is None
        assert rows[0]["rejected_action_id"] is None
        assert await count_events(session_factory) == 0
    finally:
        await delete_game(session_factory)


@pytest.mark.asyncio
async def test_process_error_persists_audit_record_and_no_fallback_move(
    session_factory: async_sessionmaker,
    tmp_path: Path,
) -> None:
    fixture = await create_ai_game(session_factory)
    runner = FakeCodexRunner(stdout='{"type":"error","message":"boom"}\n', stderr="boom", returncode=2)
    try:
        result = await request_codex_ai_decision(
            session_factory,
            decision_request(fixture),
            runner=runner,
            schema_file=tmp_path / "schema.json",
            sandbox_dir=tmp_path / "sandbox",
            work_dir=tmp_path / "work",
        )
        rows = await fetch_ai_decision_rows(session_factory)

        assert result.status == "process_error"
        assert result.validation_result["reason_code"] == "codex_exec_process_error"
        assert result.validation_result["returncode"] == 2
        assert result.validation_result["stderr"] == "boom"
        assert result.validation_result["no_substitute_move"] is True
        assert rows[0]["status"] == "process_error"
        assert rows[0]["raw_output"] == '{"type":"error","message":"boom"}\n'
        assert rows[0]["accepted_event_id"] is None
        assert rows[0]["rejected_action_id"] is None
        assert await count_events(session_factory) == 0
    finally:
        await delete_game(session_factory)


@pytest.mark.asyncio
async def test_codex_launch_failure_persists_process_error_audit_without_action_ids(
    session_factory: async_sessionmaker,
    tmp_path: Path,
) -> None:
    evidence = "Codex launch failures are audited as process errors"
    fixture = await create_ai_game(session_factory)
    base_request = decision_request(fixture)
    prompt_context = dict(base_request.prompt_context)
    prompt_context["evidence"] = evidence
    runner = LaunchFailingCodexRunner(FileNotFoundError("codex executable missing"))
    try:
        result = await request_codex_ai_decision(
            session_factory,
            replace(base_request, prompt_context=prompt_context),
            runner=runner,
            schema_file=tmp_path / "schema.json",
            sandbox_dir=tmp_path / "sandbox",
            work_dir=tmp_path / "work",
        )
        rows = await fetch_ai_decision_rows(session_factory)

        assert result.status == "process_error"
        assert result.raw_output == ""
        assert result.parsed_output is None
        assert result.validation_result["reason_code"] == "codex_exec_process_error"
        assert result.validation_result["error_type"] == "FileNotFoundError"
        assert result.validation_result["no_substitute_move"] is True
        assert result.validation_result["substitute_move"] is None
        assert result.accepted_event_id is None
        assert result.rejected_action_id is None
        assert rows[0]["status"] == "process_error"
        assert rows[0]["prompt_context"]["evidence"] == evidence
        assert rows[0]["raw_output"] == ""
        assert rows[0]["parsed_output"] is None
        assert rows[0]["validation_result"]["reason_code"] == "codex_exec_process_error"
        assert rows[0]["validation_result"]["error_type"] == "FileNotFoundError"
        assert rows[0]["validation_result"]["substitute_move"] is None
        assert rows[0]["accepted_event_id"] is None
        assert rows[0]["rejected_action_id"] is None
        assert await count_events(session_factory) == 0
    finally:
        await delete_game(session_factory)
