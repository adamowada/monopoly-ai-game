"""Stage 7.5 evidence:

- Schema validation
- Legal action validation
- Deal validation
- Phase/timing validation
- Rejected AI output records
- One Codex subprocess attempt per AI decision request
- Invalid, malformed, or timed-out mandatory AI decision marks the game AI_BLOCKED
- Invalid, malformed, or timed-out non-mandatory AI negotiation response stores a rejection audit record and consumes that AI's response opportunity
- Legal AI actions commit exactly like legal human actions
- Illegal AI actions are rejected exactly like illegal human actions
- The system never substitutes a safe move, random move, or default move
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.ai import enforcement as enforcement_module
from app.ai.decision_schema import (
    AIDecisionValidationError,
    ActionDecisionOutput,
    validate_ai_decision_output,
)
from app.ai.enforcement import AIOutputEnforcementRequest, enforce_ai_output
from app.ai.orchestrator import (
    CodexExecAIDecisionResult,
    CodexExecProcessResult,
    CodexExecRunner,
    CodexExecTimeoutError,
)
from app.db.metadata import (
    ai_decisions,
    ai_profiles,
    contracts,
    deals,
    game_events,
    games,
    metadata,
    negotiations,
    players,
    rejected_actions,
)
from app.rules.actions import GameAction, execute_action, list_legal_actions
from app.rules.state import GameState, PlayerSetup, create_initial_game_state


TEST_DATABASE_URL = os.getenv(
    "MONOPOLY_TEST_DATABASE_URL",
    "postgresql+asyncpg://monopoly:monopoly@127.0.0.1:5432/monopoly_ai_game",
)

GAME_ID = UUID("00000000-0000-0000-0000-000000007501")
AI_PLAYER_ID = UUID("00000000-0000-0000-0000-000000007502")
HUMAN_PLAYER_ID = UUID("00000000-0000-0000-0000-000000007503")
AI_PROFILE_ID = UUID("00000000-0000-0000-0000-000000007504")
NEGOTIATION_ID = UUID("00000000-0000-0000-0000-000000007505")
DEAL_ID = UUID("00000000-0000-0000-0000-000000007506")


@dataclass(frozen=True)
class EnforcementFixture:
    game_id: UUID
    ai_player_id: UUID
    human_player_id: UUID
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


@pytest.mark.asyncio
async def test_legal_ai_action_commits_exactly_like_human_action_and_attempts_once(
    session_factory: async_sessionmaker,
    tmp_path: Path,
) -> None:
    fixture = await create_ai_game(session_factory)
    legal_roll = next(
        action
        for action in list_legal_actions(fixture.state, str(AI_PLAYER_ID))
        if action.type == "ROLL_DICE"
    )
    human_equivalent = execute_action(
        fixture.state,
        GameAction(
            actor_id=legal_roll.actor_id,
            type=legal_roll.type,
            payload=legal_roll.payload,
            expected_state_hash=legal_roll.expected_state_hash,
            expected_event_sequence=legal_roll.expected_event_sequence,
        ),
        "human-equivalent",
    )
    runner = FakeCodexRunner(final_output=valid_action_output(fixture.state))

    try:
        result = await enforce_ai_output(
            session_factory,
            AIOutputEnforcementRequest(
                game_id=GAME_ID,
                player_id=AI_PLAYER_ID,
                ai_profile_id=AI_PROFILE_ID,
                decision_type="action_decision",
                mandatory=True,
                timeout_seconds=7,
            ),
            runner=runner,
            schema_file=tmp_path / "schema.json",
            sandbox_dir=tmp_path / "sandbox",
            work_dir=tmp_path / "work",
        )
        events = await fetch_rows(session_factory, game_events)
        decisions = await fetch_rows(session_factory, ai_decisions)

        assert len(runner.calls) == 1
        assert "context_pack_schema_version" in runner.calls[0]["stdin"]
        assert result.status == "accepted"
        assert result.accepted_event_id == events[0]["id"]
        assert result.rejected_action_id is None
        assert [event["event_type"] for event in events] == [
            event.type for event in human_equivalent.events
        ]
        assert [event["payload"] for event in events] == [
            event.payload.model_dump(mode="json") for event in human_equivalent.events
        ]
        assert decisions[0]["accepted_event_id"] == events[0]["id"]
        assert decisions[0]["rejected_action_id"] is None
        assert "session_configured" in decisions[0]["raw_output"]
        assert "item_completed" in decisions[0]["raw_output"]
        assert decisions[0]["raw_output"] != json.dumps(valid_action_output(fixture.state))
        assert decisions[0]["parsed_output"]["action"]["type"] == "ROLL_DICE"
        assert decisions[0]["prompt_context"]["context_pack_schema_version"] == "ai-context-pack-v1"
        assert await table_count(session_factory, rejected_actions) == 0
    finally:
        await delete_game(session_factory)


@pytest.mark.asyncio
async def test_request_once_forwards_codex_home_to_codex_decision_request(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    state = create_initial_game_state(
        seed="phase-7-codex-home-forwarding",
        game_id=str(GAME_ID),
        players=(
            PlayerSetup(id=str(AI_PLAYER_ID), name="Grace", kind="ai"),
            PlayerSetup(id=str(HUMAN_PLAYER_ID), name="Ada", kind="human"),
        ),
    )
    codex_home = tmp_path / "configured-codex-home"
    captured: dict[str, Any] = {}

    async def fake_request_codex_ai_decision(*args: Any, **kwargs: Any) -> CodexExecAIDecisionResult:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return CodexExecAIDecisionResult(
            ai_decision_id=DEAL_ID,
            status="timeout",
            raw_output="",
            parsed_output=None,
            validation_result={
                "status": "rejected",
                "reason_code": "codex_exec_timeout",
                "no_substitute_move": True,
                "substitute_move": None,
            },
            prompt_context_hash="codex-home-forwarding",
        )

    monkeypatch.setattr(
        enforcement_module,
        "request_codex_ai_decision",
        fake_request_codex_ai_decision,
    )
    session_factory_stub = cast(async_sessionmaker[AsyncSession], object())

    result = await enforcement_module._request_once(
        session_factory_stub,
        AIOutputEnforcementRequest(
            game_id=GAME_ID,
            player_id=AI_PLAYER_ID,
            ai_profile_id=AI_PROFILE_ID,
            decision_type="action_decision",
            timeout_seconds=7,
        ),
        enforcement_module._PromptContext(
            state=state,
            context_pack={"legal_actions": [{"type": "ROLL_DICE", "payload": {}}]},
        ),
        runner=None,
        codex_executable="codex",
        schema_file=None,
        sandbox_dir=None,
        work_dir=None,
        codex_home=codex_home,
    )

    assert result.prompt_context_hash == "codex-home-forwarding"
    assert captured["kwargs"]["codex_home"] == codex_home


@pytest.mark.asyncio
async def test_ai_blocked_after_codex_returns_rejects_valid_action_without_event_mutation(
    session_factory: async_sessionmaker,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = await create_ai_game(session_factory)
    parsed_output = valid_action_output(fixture.state)
    raw_output = json.dumps(parsed_output)
    validation_result = {
        "status": "valid",
        "schema": "AI_OUTPUT_SCHEMA",
        "no_substitute_move": True,
        "substitute_move": None,
    }

    async def fake_request_once(*args: Any, **kwargs: Any) -> CodexExecAIDecisionResult:
        del kwargs
        patched_session_factory = args[0]
        request = args[1]
        prompt_context = args[2]
        async with patched_session_factory() as session:
            async with session.begin():
                insert_result = await session.execute(
                    ai_decisions.insert()
                    .values(
                        game_id=request.game_id,
                        player_id=request.player_id,
                        ai_profile_id=request.ai_profile_id,
                        negotiation_id=request.negotiation_id,
                        decision_type=request.decision_type,
                        status="validated",
                        phase=prompt_context.state.turn.phase.value,
                        state_hash=prompt_context.state.state_hash(),
                        prompt_context_hash="blocked-after-codex",
                        prompt_context=dict(prompt_context.context_pack),
                        raw_output=raw_output,
                        parsed_output=parsed_output,
                        validation_result=validation_result,
                        accepted_event_id=None,
                        rejected_action_id=None,
                    )
                    .returning(ai_decisions.c.id)
                )
                decision_id = insert_result.scalar_one()
                await session.execute(
                    games.update()
                    .where(games.c.id == request.game_id)
                    .values(status="AI_BLOCKED", updated_at=sa.func.now())
                )
        return CodexExecAIDecisionResult(
            ai_decision_id=decision_id,
            status="validated",
            raw_output=raw_output,
            parsed_output=parsed_output,
            validation_result=validation_result,
            prompt_context_hash="blocked-after-codex",
        )

    monkeypatch.setattr(enforcement_module, "_request_once", fake_request_once)

    try:
        result = await enforce_ai_output(
            session_factory,
            AIOutputEnforcementRequest(
                game_id=GAME_ID,
                player_id=AI_PLAYER_ID,
                ai_profile_id=AI_PROFILE_ID,
                decision_type="action_decision",
                mandatory=True,
                timeout_seconds=7,
            ),
            schema_file=tmp_path / "schema.json",
            sandbox_dir=tmp_path / "sandbox",
            work_dir=tmp_path / "work",
        )
        rejections = await fetch_rows(session_factory, rejected_actions)
        decisions = await fetch_rows(session_factory, ai_decisions)
        game = await fetch_game(session_factory)

        assert result.status == "rejected"
        assert result.accepted_event_id is None
        assert result.rejected_action_id == rejections[0]["id"]
        assert result.game_status == "AI_BLOCKED"
        assert game["status"] == "AI_BLOCKED"
        assert rejections[0]["reason_code"] == "game_ai_blocked"
        assert rejections[0]["payload"]["no_substitute_move"] is True
        assert rejections[0]["payload"]["substitute_move"] is None
        assert decisions[0]["status"] == "rejected"
        assert decisions[0]["accepted_event_id"] is None
        assert decisions[0]["rejected_action_id"] == rejections[0]["id"]
        assert decisions[0]["validation_result"]["reason_code"] == "game_ai_blocked"
        assert decisions[0]["validation_result"]["no_substitute_move"] is True
        assert decisions[0]["validation_result"]["substitute_move"] is None
        assert await table_count(session_factory, game_events) == 0
    finally:
        await delete_game(session_factory)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("label", "final_output", "expected_reason"),
    (
        (
            "illegal",
            lambda state: invalid_action_output(state, "BUY_PROPERTY", {"property_id": "property_boardwalk"}),
            "illegal_action",
        ),
        (
            "stale",
            lambda state: {
                **valid_action_output(state),
                "expected_state_hash": "not-the-current-state",
            },
            "stale_action",
        ),
        (
            "mistimed",
            lambda state: valid_action_output(state),
            "mistimed_action",
        ),
    ),
)
async def test_illegal_stale_and_mistimed_ai_actions_are_rejected_like_human_actions(
    session_factory: async_sessionmaker,
    tmp_path: Path,
    label: str,
    final_output: Any,
    expected_reason: str,
) -> None:
    fixture = await create_ai_game(session_factory, ai_current=label != "mistimed")
    output = final_output(fixture.state)
    runner = FakeCodexRunner(final_output=output)

    try:
        result = await enforce_ai_output(
            session_factory,
            AIOutputEnforcementRequest(
                game_id=GAME_ID,
                player_id=AI_PLAYER_ID,
                ai_profile_id=AI_PROFILE_ID,
                decision_type="action_decision",
                mandatory=True,
                timeout_seconds=7,
            ),
            runner=runner,
            schema_file=tmp_path / f"{label}-schema.json",
            sandbox_dir=tmp_path / f"{label}-sandbox",
            work_dir=tmp_path / f"{label}-work",
        )
        rejections = await fetch_rows(session_factory, rejected_actions)
        decisions = await fetch_rows(session_factory, ai_decisions)

        assert len(runner.calls) == 1
        assert result.status == "rejected"
        assert result.accepted_event_id is None
        assert result.rejected_action_id == rejections[0]["id"]
        assert rejections[0]["reason_code"] == expected_reason
        assert rejections[0]["legal_action_context"]["legal_actions"]
        assert rejections[0]["payload"]["no_substitute_move"] is True
        assert rejections[0]["payload"]["substitute_move"] is None
        assert decisions[0]["rejected_action_id"] == rejections[0]["id"]
        assert decisions[0]["accepted_event_id"] is None
        assert decisions[0]["validation_result"]["no_substitute_move"] is True
        assert decisions[0]["validation_result"]["substitute_move"] is None
        assert decisions[0]["validation_result"]["legal_action_validation"] is False
        assert await table_count(session_factory, game_events) == 0
    finally:
        await delete_game(session_factory)


@pytest.mark.asyncio
async def test_invalid_deal_proposal_is_rejected_without_deal_contract_or_event_mutation(
    session_factory: async_sessionmaker,
    tmp_path: Path,
) -> None:
    fixture = await create_ai_game(session_factory)
    await insert_negotiation(session_factory, fixture)
    runner = FakeCodexRunner(final_output=invalid_deal_proposal_output())

    try:
        result = await enforce_ai_output(
            session_factory,
            AIOutputEnforcementRequest(
                game_id=GAME_ID,
                player_id=AI_PLAYER_ID,
                ai_profile_id=AI_PROFILE_ID,
                decision_type="deal_proposal",
                negotiation_id=NEGOTIATION_ID,
                mandatory=False,
                timeout_seconds=7,
            ),
            runner=runner,
            schema_file=tmp_path / "schema.json",
            sandbox_dir=tmp_path / "sandbox",
            work_dir=tmp_path / "work",
        )
        rejections = await fetch_rows(session_factory, rejected_actions)

        assert len(runner.calls) == 1
        assert result.status == "rejected"
        assert rejections[0]["reason_code"] == "invalid_structured_deal"
        assert rejections[0]["validation_errors"]
        assert await table_count(session_factory, deals) == 0
        assert await table_count(session_factory, contracts) == 0
        assert await table_count(session_factory, game_events) == 0
    finally:
        await delete_game(session_factory)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("label", "runner", "reason_code"),
    (
        (
            "malformed",
            FakeCodexRunner(
                final_output={
                    "decision_type": "action_decision",
                    "game_id": str(GAME_ID),
                    "player_id": str(AI_PLAYER_ID),
                    "action": {"type": "ROLL_DICE", "payload": {}},
                }
            ),
            "malformed_ai_output",
        ),
        ("timeout", FakeCodexRunner(timeout=True), "codex_exec_timeout"),
        (
            "process-error",
            FakeCodexRunner(
                stdout='{"type":"error","message":"boom"}\n',
                stderr="boom",
                returncode=2,
            ),
            "codex_exec_process_error",
        ),
    ),
)
async def test_mandatory_malformed_timeout_and_process_error_mark_ai_blocked(
    session_factory: async_sessionmaker,
    tmp_path: Path,
    label: str,
    runner: FakeCodexRunner,
    reason_code: str,
) -> None:
    await create_ai_game(session_factory)

    try:
        result = await enforce_ai_output(
            session_factory,
            AIOutputEnforcementRequest(
                game_id=GAME_ID,
                player_id=AI_PLAYER_ID,
                ai_profile_id=AI_PROFILE_ID,
                decision_type="action_decision",
                mandatory=True,
                timeout_seconds=7,
            ),
            runner=runner,
            schema_file=tmp_path / f"{label}-schema.json",
            sandbox_dir=tmp_path / f"{label}-sandbox",
            work_dir=tmp_path / f"{label}-work",
        )
        rejections = await fetch_rows(session_factory, rejected_actions)
        decisions = await fetch_rows(session_factory, ai_decisions)
        game = await fetch_game(session_factory)

        assert len(runner.calls) == 1
        assert result.status == "rejected"
        assert result.rejected_action_id == rejections[0]["id"]
        assert game["status"] == "AI_BLOCKED"
        assert rejections[0]["reason_code"] == reason_code
        assert rejections[0]["payload"]["no_substitute_move"] is True
        assert rejections[0]["payload"]["substitute_move"] is None
        assert decisions[0]["validation_result"]["reason_code"] == reason_code
        assert decisions[0]["validation_result"]["no_substitute_move"] is True
        assert decisions[0]["validation_result"]["substitute_move"] is None
        assert await table_count(session_factory, game_events) == 0
    finally:
        await delete_game(session_factory)


@pytest.mark.asyncio
async def test_non_mandatory_negotiation_failure_consumes_response_without_ai_blocked(
    session_factory: async_sessionmaker,
    tmp_path: Path,
) -> None:
    fixture = await create_ai_game(session_factory)
    await insert_negotiation(session_factory, fixture)
    runner = FakeCodexRunner(timeout=True)

    try:
        result = await enforce_ai_output(
            session_factory,
            AIOutputEnforcementRequest(
                game_id=GAME_ID,
                player_id=AI_PLAYER_ID,
                ai_profile_id=AI_PROFILE_ID,
                decision_type="accept_reject",
                negotiation_id=NEGOTIATION_ID,
                mandatory=False,
                timeout_seconds=7,
            ),
            runner=runner,
            schema_file=tmp_path / "schema.json",
            sandbox_dir=tmp_path / "sandbox",
            work_dir=tmp_path / "work",
        )
        game = await fetch_game(session_factory)
        negotiation = await fetch_negotiation(session_factory)
        rejections = await fetch_rows(session_factory, rejected_actions)

        assert len(runner.calls) == 1
        assert result.status == "rejected"
        assert result.rejected_action_id == rejections[0]["id"]
        assert result.consumed_response_opportunity is True
        assert game["status"] == "active"
        consumed = negotiation["context"]["ai_response_opportunities_consumed"]
        assert consumed[f"round:1:player:{AI_PLAYER_ID}"]["ai_decision_id"] == str(
            result.ai_decision_id
        )
        assert negotiation["context"]["ai_decision_attempts_by_message_id"][
            f"round:1:player:{AI_PLAYER_ID}"
        ] == 1
        assert await table_count(session_factory, game_events) == 0
        assert await table_count(session_factory, deals) == 0
        assert await table_count(session_factory, contracts) == 0
    finally:
        await delete_game(session_factory)


async def create_ai_game(
    session_factory: async_sessionmaker,
    *,
    ai_current: bool = True,
) -> EnforcementFixture:
    player_setup = (
        (
            PlayerSetup(id=str(AI_PLAYER_ID), name="Grace", kind="ai"),
            PlayerSetup(id=str(HUMAN_PLAYER_ID), name="Ada", kind="human"),
        )
        if ai_current
        else (
            PlayerSetup(id=str(HUMAN_PLAYER_ID), name="Ada", kind="human"),
            PlayerSetup(id=str(AI_PLAYER_ID), name="Grace", kind="ai"),
        )
    )
    state = create_initial_game_state(
        seed="phase-7-stage-7.5-enforcement",
        game_id=str(GAME_ID),
        players=player_setup,
    )
    async with session_factory() as session:
        async with session.begin():
            await session.execute(games.delete().where(games.c.id == GAME_ID))
            await session.execute(
                games.insert().values(
                    id=GAME_ID,
                    status="active",
                    ruleset_version=state.ruleset_version,
                    seed="phase-7-stage-7.5-enforcement",
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
                    id=AI_PROFILE_ID,
                    game_id=GAME_ID,
                    player_id=AI_PLAYER_ID,
                    persona_name="Stage 7.5 Persona",
                    strategy_profile={"risk_tolerance": 0.35},
                    persona_summary={"summary": "A validation-focused test AI."},
                )
            )
    return EnforcementFixture(
        game_id=GAME_ID,
        ai_player_id=AI_PLAYER_ID,
        human_player_id=HUMAN_PLAYER_ID,
        ai_profile_id=AI_PROFILE_ID,
        state=state,
    )


async def insert_negotiation(
    session_factory: async_sessionmaker,
    fixture: EnforcementFixture,
) -> None:
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                negotiations.insert().values(
                    id=NEGOTIATION_ID,
                    game_id=fixture.game_id,
                    opened_by_player_id=fixture.human_player_id,
                    status="active",
                    phase=fixture.state.turn.phase.value,
                    round_number=1,
                    context={
                        "participant_player_ids": [
                            str(fixture.ai_player_id),
                            str(fixture.human_player_id),
                        ],
                        "context": {"topic": "stage 7.5 deal validation"},
                        "pending_deal_id": None,
                        "current_deal_id": None,
                        "current_parent_deal_id": None,
                        "current_terms_hash": None,
                        "current_deal_version": None,
                        "current_deal_structured": False,
                        "acceptances": {},
                        "invalidated_acceptances": {},
                        "status_history": [],
                        "cutoff_policy": {
                            "max_rounds": 8,
                            "max_proposals_per_player": 8,
                            "max_active_seconds": 900,
                            "max_ai_decision_attempts": 3,
                            "max_pending_offers_per_player": 4,
                            "negotiation_intensity": "standard",
                        },
                        "proposal_counts_by_player_id": {},
                        "pending_offer_counts_by_player_id": {},
                        "ai_decision_attempts_by_message_id": {},
                        "cutoff_reason": None,
                        "expired_by_cutoff": False,
                    },
                )
            )


def valid_action_output(state: GameState) -> dict[str, Any]:
    return {
        "decision_type": "action_decision",
        "game_id": str(GAME_ID),
        "player_id": str(AI_PLAYER_ID),
        "expected_state_hash": state.state_hash(),
        "expected_event_sequence": state.event_sequence,
        "action": {"type": "ROLL_DICE", "payload": {}},
        "self_dialogue": {
            "status": "provided",
            "text": "Rolling is the legal mandatory turn action.",
        },
        "memory_updates": [],
        "confidence": 0.84,
        "rationale": "The backend legal action list includes ROLL_DICE.",
    }


def invalid_action_output(
    state: GameState,
    action_type: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    output = valid_action_output(state)
    output["action"] = {"type": action_type, "payload": dict(payload)}
    return output


def test_empty_action_payload_string_normalizes_to_empty_object() -> None:
    state = create_initial_game_state(
        seed="empty-payload-normalization",
        game_id=str(GAME_ID),
        players=(
            PlayerSetup(id=str(AI_PLAYER_ID), name="Ada", kind="ai"),
            PlayerSetup(id=str(HUMAN_PLAYER_ID), name="Grace", kind="human"),
        ),
    )
    output = valid_action_output(state)
    output["action"] = {"type": "ROLL_DICE", "payload": ""}

    parsed = validate_ai_decision_output(output)

    assert isinstance(parsed.root, ActionDecisionOutput)
    assert parsed.root.action.payload == {}


def test_slash_action_payload_placeholder_normalizes_to_empty_object() -> None:
    state = create_initial_game_state(
        seed="slash-payload-normalization",
        game_id=str(GAME_ID),
        players=(
            PlayerSetup(id=str(AI_PLAYER_ID), name="Ada", kind="ai"),
            PlayerSetup(id=str(HUMAN_PLAYER_ID), name="Grace", kind="human"),
        ),
    )
    output = valid_action_output(state)
    output["action"] = {"type": "ROLL_DICE", "payload": "/"}

    parsed = validate_ai_decision_output(output)

    assert isinstance(parsed.root, ActionDecisionOutput)
    assert parsed.root.action.payload == {}


def test_empty_action_payload_comment_placeholder_normalizes_to_empty_object() -> None:
    state = create_initial_game_state(
        seed="comment-payload-normalization",
        game_id=str(GAME_ID),
        players=(
            PlayerSetup(id=str(AI_PLAYER_ID), name="Ada", kind="ai"),
            PlayerSetup(id=str(HUMAN_PLAYER_ID), name="Grace", kind="human"),
        ),
    )
    output = valid_action_output(state)
    output["action"] = {"type": "END_TURN", "payload": "/** END_TURN **/"}

    parsed = validate_ai_decision_output(output)

    assert isinstance(parsed.root, ActionDecisionOutput)
    assert parsed.root.action.payload == {}


def test_empty_action_payload_blank_comment_placeholder_normalizes_to_empty_object() -> None:
    state = create_initial_game_state(
        seed="blank-comment-payload-normalization",
        game_id=str(GAME_ID),
        players=(
            PlayerSetup(id=str(AI_PLAYER_ID), name="Ada", kind="ai"),
            PlayerSetup(id=str(HUMAN_PLAYER_ID), name="Grace", kind="human"),
        ),
    )
    output = valid_action_output(state)
    output["action"] = {"type": "END_TURN", "payload": "/**/"}

    parsed = validate_ai_decision_output(output)

    assert isinstance(parsed.root, ActionDecisionOutput)
    assert parsed.root.action.payload == {}


def test_non_json_payload_string_for_payload_required_action_still_rejected() -> None:
    state = create_initial_game_state(
        seed="invalid-payload-normalization",
        game_id=str(GAME_ID),
        players=(
            PlayerSetup(id=str(AI_PLAYER_ID), name="Ada", kind="ai"),
            PlayerSetup(id=str(HUMAN_PLAYER_ID), name="Grace", kind="human"),
        ),
    )
    output = valid_action_output(state)
    output["action"] = {"type": "BUY_PROPERTY", "payload": "not-json"}

    with pytest.raises(AIDecisionValidationError):
        validate_ai_decision_output(output)


def invalid_deal_proposal_output() -> dict[str, Any]:
    return {
        "decision_type": "deal_proposal",
        "game_id": str(GAME_ID),
        "player_id": str(AI_PLAYER_ID),
        "negotiation_id": str(NEGOTIATION_ID),
        "deal": {
            "recipient_player_ids": [str(HUMAN_PLAYER_ID)],
            "terms": {
                "kind": "structured_deal",
                "deal_schema_version": 1,
                "participants": [str(AI_PLAYER_ID)],
                "terms": [],
            },
            "message": "This malformed deal should be rejected without mutation.",
        },
        "self_dialogue": {
            "status": "provided",
            "text": "I am proposing terms that validation must reject.",
        },
        "memory_updates": [],
        "confidence": 0.2,
        "rationale": "This intentionally fails structured deal validation.",
    }


async def fetch_rows(session_factory: async_sessionmaker, table: sa.Table) -> list[dict[str, Any]]:
    async with session_factory() as session:
        order_by = (
            (table.c.sequence,)
            if "sequence" in table.c
            else (table.c.created_at, table.c.id)
        )
        result = await session.execute(
            sa.select(table).where(table.c.game_id == GAME_ID).order_by(*order_by)
        )
        return [dict(row) for row in result.mappings().all()]


async def fetch_game(session_factory: async_sessionmaker) -> dict[str, Any]:
    async with session_factory() as session:
        result = await session.execute(sa.select(games).where(games.c.id == GAME_ID))
        return dict(result.mappings().one())


async def fetch_negotiation(session_factory: async_sessionmaker) -> dict[str, Any]:
    async with session_factory() as session:
        result = await session.execute(
            sa.select(negotiations).where(negotiations.c.id == NEGOTIATION_ID)
        )
        return dict(result.mappings().one())


async def table_count(session_factory: async_sessionmaker, table: sa.Table) -> int:
    async with session_factory() as session:
        result = await session.execute(
            sa.select(sa.func.count()).select_from(table).where(table.c.game_id == GAME_ID)
        )
        return int(result.scalar_one())


async def delete_game(session_factory: async_sessionmaker) -> None:
    async with session_factory() as session:
        async with session.begin():
            await session.execute(games.delete().where(games.c.id == GAME_ID))
