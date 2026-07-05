"""Stage 7.6 evidence:

- AI turn stepping endpoint
- AI participation in negotiation windows
- AI response to offers
- AI ability to propose complex deals
- mixed human/AI game progresses
- AIs initiate and respond to negotiations
- stalls visible and auditable
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from collections.abc import AsyncIterator, Mapping, Sequence
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa
from fastapi import FastAPI
from starlette.requests import Request
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.ai.orchestrator import (
    CodexExecProcessResult,
    CodexExecRunner,
    CodexExecTimeoutError,
)
from app.api.games import _ai_enforcement_kwargs, _ai_step_in_flight_guard_key
from app.core.config import Settings
from app.db.metadata import (
    action_idempotency_keys,
    ai_decisions,
    deals,
    game_events,
    games,
    metadata,
    negotiation_messages,
    negotiations,
    rejected_actions,
)
from app.main import create_app


TEST_DATABASE_URL = os.getenv(
    "MONOPOLY_TEST_DATABASE_URL",
    "postgresql+asyncpg://monopoly:monopoly@127.0.0.1:5432/monopoly_ai_game",
)


class QueueFakeCodexRunner(CodexExecRunner):
    def __init__(
        self,
        outputs: Sequence[Mapping[str, Any] | str] = (),
        *,
        timeout: bool = False,
    ) -> None:
        self.outputs = list(outputs)
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
        if not self.outputs:
            raise AssertionError("fake Codex runner received more calls than queued outputs")

        output = self.outputs.pop(0)
        output_text = output if isinstance(output, str) else json.dumps(output)
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


class BlockingFirstCodexRunner(QueueFakeCodexRunner):
    def __init__(self, output: Mapping[str, Any] | str) -> None:
        super().__init__([output])
        self.first_call_started = threading.Event()
        self.release_first_call = threading.Event()
        self._call_lock = threading.Lock()
        self._call_count = 0

    def run(
        self,
        command: Sequence[str],
        *,
        stdin: str,
        timeout_seconds: float,
        output_last_message_path: Path | None,
    ) -> CodexExecProcessResult:
        with self._call_lock:
            self._call_count += 1
            call_number = self._call_count
        if call_number != 1:
            raise AssertionError("duplicate concurrent AI step launched Codex")
        self.first_call_started.set()
        if not self.release_first_call.wait(timeout=10):
            raise AssertionError("first fake Codex call was not released")
        return super().run(
            command,
            stdin=stdin,
            timeout_seconds=timeout_seconds,
            output_last_message_path=output_last_message_path,
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


def test_ai_step_guard_key_is_game_scoped_to_avoid_prelock_state_races() -> None:
    game_id = UUID("00000000-0000-0000-0000-000000007601")
    other_game_id = UUID("00000000-0000-0000-0000-000000007602")

    assert _ai_step_in_flight_guard_key(game_id=game_id) == _ai_step_in_flight_guard_key(
        game_id=game_id
    )
    assert _ai_step_in_flight_guard_key(game_id=game_id) != _ai_step_in_flight_guard_key(
        game_id=other_game_id
    )


def test_ai_enforcement_kwargs_forwards_codex_home_from_app_state(tmp_path: Path) -> None:
    app = FastAPI()
    codex_home = tmp_path / "configured-codex-home"
    app.state.codex_home = codex_home
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/games/00000000-0000-0000-0000-000000007601/ai/step",
            "headers": [],
            "app": app,
        }
    )

    kwargs = _ai_enforcement_kwargs(request)

    assert kwargs["codex_home"] == codex_home


@pytest.mark.asyncio
async def test_ai_turn_stepping_endpoint_progresses_mixed_human_ai_game(
    api_app: FastAPI,
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
    tmp_path: Path,
) -> None:
    # AI turn stepping endpoint; mixed human/AI game progresses
    created = await create_game(client, ai_first=True)
    game_id = created["id"]
    ai_player_id = created["players"][0]["id"]
    human_player_id = created["players"][1]["id"]
    state = await get_state(client, game_id)
    runner = QueueFakeCodexRunner([valid_action_output(game_id, ai_player_id, state)])
    install_fake_runner(api_app, runner, tmp_path)

    try:
        human_response = await client.post(
            f"/games/{game_id}/ai/step",
            json={"player_id": human_player_id, "decision_type": "action_decision"},
        )
        response = await client.post(
            f"/games/{game_id}/ai/step",
            json={
                "player_id": ai_player_id,
                "decision_type": "action_decision",
                "mandatory": True,
                "request_context": {"mode": "manual"},
            },
        )

        body = response.json()
        assert human_response.status_code == 409
        assert human_response.json()["reason_code"] == "human_player_not_ai_controlled"
        assert response.status_code == 200, response.text
        assert body["status"] == "accepted"
        assert body["accepted_event_id"] == body["accepted_events"][0]["id"]
        assert body["rejected_action_id"] is None
        assert body["game_status"] == "active"
        assert len(runner.calls) == 1
        assert await table_count(session_factory, game_events, game_id) >= 1
        assert await table_count(session_factory, ai_decisions, game_id) == 1
        assert await table_count(session_factory, rejected_actions, game_id) == 0
    finally:
        await delete_game(session_factory, game_id)


@pytest.mark.asyncio
async def test_concurrent_ai_steps_reject_duplicate_in_flight_before_codex(
    api_app: FastAPI,
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
    tmp_path: Path,
) -> None:
    created = await create_game(client, ai_first=True)
    game_id = created["id"]
    ai_player_id = created["players"][0]["id"]
    state = await get_state(client, game_id)
    runner = BlockingFirstCodexRunner(valid_action_output(game_id, ai_player_id, state))
    install_fake_runner(api_app, runner, tmp_path)

    try:
        request_payload = {
            "player_id": ai_player_id,
            "decision_type": "action_decision",
            "mandatory": True,
        }
        event_count_before = await table_count(session_factory, game_events, game_id)
        first_task = asyncio.create_task(client.post(f"/games/{game_id}/ai/step", json=request_payload))
        duplicate_response: httpx.Response | None = None
        duplicate_error: BaseException | None = None
        try:
            first_started = await asyncio.wait_for(
                asyncio.to_thread(runner.first_call_started.wait, 5),
                timeout=6,
            )
            assert first_started is True

            try:
                duplicate_response = await client.post(f"/games/{game_id}/ai/step", json=request_payload)
            except BaseException as exc:
                duplicate_error = exc
        finally:
            runner.release_first_call.set()
        first_response = await first_task
        if duplicate_error is not None:
            raise duplicate_error
        assert duplicate_response is not None

        accepted_body = first_response.json()
        duplicate_body = duplicate_response.json()

        assert first_response.status_code == 200, first_response.text
        assert duplicate_response.status_code == 422, duplicate_response.text
        assert accepted_body["status"] == "accepted"
        assert duplicate_body["status"] == "rejected"
        assert duplicate_body["reason_code"] == "ai_step_in_flight"
        assert duplicate_body["validation_errors"][0]["code"] == "ai_step_in_flight"
        assert len(runner.calls) == 1
        assert accepted_body["rejected_action_id"] is None
        assert await table_count(session_factory, ai_decisions, game_id) == 1
        assert await table_count(session_factory, rejected_actions, game_id) == 0
        assert await table_count(session_factory, game_events, game_id) == (
            event_count_before + len(accepted_body["accepted_events"])
        )
        assert await game_status(session_factory, game_id) != "AI_BLOCKED"
    finally:
        await delete_game(session_factory, game_id)


@pytest.mark.asyncio
async def test_concurrent_ai_steps_serialize_same_state_across_decision_types_before_codex(
    api_app: FastAPI,
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
    tmp_path: Path,
) -> None:
    created = await create_game(client, ai_first=True)
    game_id = created["id"]
    ai_player_id = created["players"][0]["id"]
    state = await get_state(client, game_id)
    runner = BlockingFirstCodexRunner(valid_action_output(game_id, ai_player_id, state))
    install_fake_runner(api_app, runner, tmp_path)

    try:
        action_payload = {
            "player_id": ai_player_id,
            "decision_type": "action_decision",
            "mandatory": True,
        }
        open_negotiation_payload = {
            "player_id": ai_player_id,
            "decision_type": "open_negotiation",
            "mandatory": False,
        }
        event_count_before = await table_count(session_factory, game_events, game_id)
        action_task = asyncio.create_task(client.post(f"/games/{game_id}/ai/step", json=action_payload))
        open_negotiation_response: httpx.Response | None = None
        open_negotiation_error: BaseException | None = None
        try:
            first_started = await asyncio.wait_for(
                asyncio.to_thread(runner.first_call_started.wait, 5),
                timeout=6,
            )
            assert first_started is True

            try:
                open_negotiation_response = await client.post(
                    f"/games/{game_id}/ai/step",
                    json=open_negotiation_payload,
                )
            except BaseException as exc:
                open_negotiation_error = exc
        finally:
            runner.release_first_call.set()
        action_response = await action_task
        if open_negotiation_error is not None:
            raise open_negotiation_error
        assert open_negotiation_response is not None

        accepted_body = action_response.json()
        rejected_body = open_negotiation_response.json()

        assert action_response.status_code == 200, action_response.text
        assert open_negotiation_response.status_code == 422, open_negotiation_response.text
        assert accepted_body["status"] == "accepted"
        assert rejected_body["status"] == "rejected"
        assert rejected_body["reason_code"] == "ai_step_in_flight"
        assert rejected_body["validation_errors"][0]["code"] == "ai_step_in_flight"
        assert len(runner.calls) == 1
        assert accepted_body["rejected_action_id"] is None
        assert await table_count(session_factory, ai_decisions, game_id) == 1
        assert await table_count(session_factory, rejected_actions, game_id) == 0
        assert await table_count(session_factory, game_events, game_id) == (
            event_count_before + len(accepted_body["accepted_events"])
        )
        assert await game_status(session_factory, game_id) != "AI_BLOCKED"
    finally:
        await delete_game(session_factory, game_id)


@pytest.mark.asyncio
async def test_ai_controlled_player_direct_actions_reject_before_events_and_ai_step_still_accepts(
    api_app: FastAPI,
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
    tmp_path: Path,
) -> None:
    created = await create_game(client, ai_first=True)
    game_id = created["id"]
    ai_player_id = created["players"][0]["id"]
    state = await get_state(client, game_id)
    direct_action = {
        "actor_id": ai_player_id,
        "type": "ROLL_DICE",
        "payload": {},
        "expected_state_hash": state["state_hash"],
        "expected_event_sequence": state["event_sequence"],
    }
    runner = QueueFakeCodexRunner([valid_action_output(game_id, ai_player_id, state)])
    install_fake_runner(api_app, runner, tmp_path)

    try:
        direct_response = await client.post(
            f"/games/{game_id}/actions",
            headers={"Idempotency-Key": "stage-7-6-direct-ai-action"},
            json=direct_action,
        )
        direct_body = direct_response.json()
        rejection_rows = await fetch_rows(session_factory, rejected_actions, game_id)
        idempotency_rows = await fetch_rows(session_factory, action_idempotency_keys, game_id)
        event_count_after_direct_action = await table_count(session_factory, game_events, game_id)
        ai_decision_count_after_direct_action = await table_count(session_factory, ai_decisions, game_id)

        ai_step_response = await client.post(
            f"/games/{game_id}/ai/step",
            json={"player_id": ai_player_id, "decision_type": "action_decision", "mandatory": True},
        )
        ai_step_body = ai_step_response.json()

        assert direct_response.status_code == 409, direct_response.text
        assert direct_body["status"] == "rejected"
        assert direct_body["reason_code"] == "ai_player_requires_codex"
        assert direct_body["validation_errors"][0]["code"] == "ai_player_requires_codex"
        assert direct_body["validation_errors"][0]["field"] == "actor_id"
        assert direct_body["rejected_action_id"] == str(rejection_rows[0]["id"])
        assert direct_body["submitted_action"] == direct_action
        assert "accepted_events" not in direct_body
        assert "fallback_action" not in direct_body
        assert "substitute_action" not in direct_body
        assert "substitute_move" not in direct_body
        assert event_count_after_direct_action == 0
        assert ai_decision_count_after_direct_action == 0
        assert len(rejection_rows) == 1
        assert rejection_rows[0]["actor_player_id"] == UUID(ai_player_id)
        assert rejection_rows[0]["action_type"] == "ROLL_DICE"
        assert rejection_rows[0]["payload"] == {}
        assert rejection_rows[0]["reason_code"] == "ai_player_requires_codex"
        assert rejection_rows[0]["validation_errors"][0]["code"] == "ai_player_requires_codex"
        assert len(idempotency_rows) == 1
        assert idempotency_rows[0]["status"] == "rejected"
        assert idempotency_rows[0]["rejected_action_id"] == rejection_rows[0]["id"]
        assert idempotency_rows[0]["response_payload"]["reason_code"] == "ai_player_requires_codex"
        assert idempotency_rows[0]["response_payload"].get("fallback_action") is None
        assert idempotency_rows[0]["response_payload"].get("substitute_action") is None
        assert idempotency_rows[0]["response_payload"].get("substitute_move") is None

        assert ai_step_response.status_code == 200, ai_step_response.text
        assert ai_step_body["status"] == "accepted"
        assert ai_step_body["rejected_action_id"] is None
        assert ai_step_body["accepted_event_id"] == ai_step_body["accepted_events"][0]["id"]
        assert len(runner.calls) == 1
        assert await table_count(session_factory, game_events, game_id) >= 1
        assert await table_count(session_factory, ai_decisions, game_id) == 1
        assert await table_count(session_factory, rejected_actions, game_id) == 1
    finally:
        await delete_game(session_factory, game_id)


@pytest.mark.asyncio
async def test_direct_ai_negotiation_mutations_reject_before_mutation_without_ai_decisions(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
) -> None:
    created = await create_game(client)
    game_id = created["id"]
    human_player_id = created["players"][0]["id"]
    ai_player_id = created["players"][1]["id"]

    try:
        direct_open_response = await client.post(
            f"/games/{game_id}/negotiations",
            json={
                "opened_by_player_id": ai_player_id,
                "participant_player_ids": [ai_player_id, human_player_id],
                "context": {"topic": "direct AI open bypass"},
            },
        )

        assert_direct_ai_negotiation_rejection(direct_open_response, "opened_by_player_id")
        assert await table_count(session_factory, negotiations, game_id) == 0
        assert await table_count(session_factory, negotiation_messages, game_id) == 0
        assert await table_count(session_factory, deals, game_id) == 0
        assert await table_count(session_factory, ai_decisions, game_id) == 0

        negotiation = await create_negotiation(client, game_id, human_player_id, ai_player_id)
        message_count_before = await table_count(session_factory, negotiation_messages, game_id)
        sender_message_response = await client.post(
            f"/games/{game_id}/negotiations/{negotiation['id']}/messages",
            json={
                "sender_player_id": ai_player_id,
                "recipient_player_id": human_player_id,
                "body": "Direct AI sender should not mutate.",
            },
        )
        author_message_response = await client.post(
            f"/games/{game_id}/negotiations/{negotiation['id']}/messages",
            json={
                "author_player_id": ai_player_id,
                "recipient_player_id": human_player_id,
                "body": "Direct AI author alias should not mutate.",
            },
        )

        assert_direct_ai_negotiation_rejection(sender_message_response, "sender_player_id")
        assert_direct_ai_negotiation_rejection(author_message_response, "sender_player_id")
        assert await table_count(session_factory, negotiation_messages, game_id) == message_count_before
        assert await table_count(session_factory, ai_decisions, game_id) == 0

        deal_count_before = await table_count(session_factory, deals, game_id)
        direct_deal_response = await client.post(
            f"/games/{game_id}/deals",
            json={
                "negotiation_id": negotiation["id"],
                "proposed_by_player_id": ai_player_id,
                "participant_player_ids": [human_player_id, ai_player_id],
                "terms": structured_terms(ai_player_id, human_player_id, cash_from=ai_player_id),
            },
        )

        assert_direct_ai_negotiation_rejection(direct_deal_response, "proposed_by_player_id")
        assert await table_count(session_factory, deals, game_id) == deal_count_before
        unchanged_negotiation = await get_negotiation(client, game_id, negotiation["id"])
        assert unchanged_negotiation["status"] == "opened"
        assert unchanged_negotiation["current_deal_id"] is None
        assert await table_count(session_factory, ai_decisions, game_id) == 0

        human_deal = await create_human_deal(
            client,
            game_id,
            negotiation["id"],
            human_player_id,
            ai_player_id,
        )
        deal_count_before_counteroffer = await table_count(session_factory, deals, game_id)
        direct_counteroffer_response = await client.post(
            f"/games/{game_id}/deals",
            json={
                "negotiation_id": negotiation["id"],
                "proposer_player_id": ai_player_id,
                "parent_deal_id": human_deal["id"],
                "terms": structured_terms(ai_player_id, human_player_id, cash_from=human_player_id, amount=70),
            },
        )

        assert_direct_ai_negotiation_rejection(direct_counteroffer_response, "proposed_by_player_id")
        assert await table_count(session_factory, deals, game_id) == deal_count_before_counteroffer
        assert await table_count(session_factory, ai_decisions, game_id) == 0

        message_count_before_decisions = await table_count(session_factory, negotiation_messages, game_id)
        direct_accept_response = await client.post(
            f"/games/{game_id}/deals/{human_deal['id']}/accept",
            json={"player_id": ai_player_id},
        )
        direct_reject_response = await client.post(
            f"/games/{game_id}/deals/{human_deal['id']}/reject",
            json={"player_id": ai_player_id},
        )
        deal_rows_after_decisions = await fetch_rows(session_factory, deals, game_id)
        negotiation_after_decisions = await get_negotiation(client, game_id, negotiation["id"])

        assert_direct_ai_negotiation_rejection(direct_accept_response, "player_id")
        assert_direct_ai_negotiation_rejection(direct_reject_response, "player_id")
        assert len(deal_rows_after_decisions) == 1
        assert deal_rows_after_decisions[0]["status"] == "proposed"
        assert negotiation_after_decisions["status"] == "active"
        assert negotiation_after_decisions["acceptances"][human_deal["id"]] == []
        assert await table_count(session_factory, negotiation_messages, game_id) == message_count_before_decisions
        assert await table_count(session_factory, ai_decisions, game_id) == 0
    finally:
        await delete_game(session_factory, game_id)


@pytest.mark.asyncio
async def test_direct_ai_negotiation_guard_allows_codex_validated_lifecycle_and_audits(
    api_app: FastAPI,
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
    tmp_path: Path,
) -> None:
    created = await create_game(client)
    game_id = created["id"]
    human_player_id = created["players"][0]["id"]
    ai_player_id = created["players"][1]["id"]
    runner = QueueFakeCodexRunner(
        [
            open_negotiation_output(
                game_id,
                ai_player_id,
                [ai_player_id, human_player_id],
                context={"topic": "direct guard equivalent AI open"},
            ),
            negotiation_message_output(
                game_id,
                ai_player_id,
                human_player_id,
                "00000000-0000-0000-0000-000000000001",
            ),
            deal_proposal_output(
                game_id,
                ai_player_id,
                human_player_id,
                "00000000-0000-0000-0000-000000000001",
            ),
        ]
    )
    install_fake_runner(api_app, runner, tmp_path)

    try:
        direct_open_response = await client.post(
            f"/games/{game_id}/negotiations",
            json={
                "opened_by_player_id": ai_player_id,
                "participant_player_ids": [ai_player_id, human_player_id],
                "context": {"topic": "direct guard equivalent AI open"},
            },
        )
        assert_direct_ai_negotiation_rejection(direct_open_response, "opened_by_player_id")
        assert await table_count(session_factory, ai_decisions, game_id) == 0

        open_response = await client.post(
            f"/games/{game_id}/ai/step",
            json={"player_id": ai_player_id, "decision_type": "open_negotiation", "mandatory": False},
        )
        open_body = open_response.json()
        negotiation_id = open_body["negotiation"]["id"]
        runner.outputs[0] = negotiation_message_output(
            game_id,
            ai_player_id,
            human_player_id,
            negotiation_id,
        )
        runner.outputs[1] = deal_proposal_output(
            game_id,
            ai_player_id,
            human_player_id,
            negotiation_id,
        )

        direct_message_response = await client.post(
            f"/games/{game_id}/negotiations/{negotiation_id}/messages",
            json={
                "sender_player_id": ai_player_id,
                "recipient_player_id": human_player_id,
                "body": "Direct AI message still rejects after AI-opened negotiation.",
            },
        )
        message_response = await client.post(
            f"/games/{game_id}/ai/step",
            json={
                "player_id": ai_player_id,
                "decision_type": "negotiation_message",
                "negotiation_id": negotiation_id,
                "mandatory": False,
            },
        )

        direct_deal_response = await client.post(
            f"/games/{game_id}/deals",
            json={
                "negotiation_id": negotiation_id,
                "proposed_by_player_id": ai_player_id,
                "participant_player_ids": [ai_player_id, human_player_id],
                "terms": structured_terms(ai_player_id, human_player_id, cash_from=ai_player_id),
            },
        )
        deal_response = await client.post(
            f"/games/{game_id}/ai/step",
            json={
                "player_id": ai_player_id,
                "decision_type": "deal_proposal",
                "negotiation_id": negotiation_id,
                "mandatory": False,
            },
        )
        deal_body = deal_response.json()
        deal_id = deal_body["deal"]["id"]
        runner.outputs.append(accept_reject_output(game_id, ai_player_id, negotiation_id, deal_id))

        direct_accept_response = await client.post(
            f"/games/{game_id}/deals/{deal_id}/accept",
            json={"player_id": ai_player_id},
        )
        direct_reject_response = await client.post(
            f"/games/{game_id}/deals/{deal_id}/reject",
            json={"player_id": ai_player_id},
        )
        accept_response = await client.post(
            f"/games/{game_id}/ai/step",
            json={
                "player_id": ai_player_id,
                "decision_type": "accept_reject",
                "negotiation_id": negotiation_id,
                "mandatory": False,
            },
        )

        message_body = message_response.json()
        accept_body = accept_response.json()
        decision_rows = await fetch_rows(session_factory, ai_decisions, game_id)

        assert open_response.status_code == 200, open_response.text
        assert open_body["status"] == "done"
        assert open_body["negotiation"]["opened_by_player_id"] == ai_player_id
        assert_direct_ai_negotiation_rejection(direct_message_response, "sender_player_id")
        assert message_response.status_code == 200, message_response.text
        assert message_body["status"] == "done"
        assert message_body["message"]["sender_player_id"] == ai_player_id
        assert_direct_ai_negotiation_rejection(direct_deal_response, "proposed_by_player_id")
        assert deal_response.status_code == 200, deal_response.text
        assert deal_body["status"] == "done"
        assert deal_body["deal"]["proposed_by_player_id"] == ai_player_id
        assert_direct_ai_negotiation_rejection(direct_accept_response, "player_id")
        assert_direct_ai_negotiation_rejection(direct_reject_response, "player_id")
        assert accept_response.status_code == 200, accept_response.text
        assert accept_body["status"] == "done"
        assert ai_player_id in accept_body["negotiation"]["acceptances"][deal_id]
        assert len(runner.calls) == 4
        assert [row["status"] for row in decision_rows] == ["accepted"] * 4
        assert [row["rejected_action_id"] for row in decision_rows] == [None] * 4
        assert [row["accepted_event_id"] for row in decision_rows] == [None] * 4
        assert [row["validation_result"]["lifecycle_result"]["status"] for row in decision_rows] == [
            "done",
            "done",
            "done",
            "done",
        ]
        assert [row["validation_result"]["lifecycle_result"]["kind"] for row in decision_rows] == [
            "open_negotiation",
            "negotiation_message",
            "deal_proposal",
            "accept_reject",
        ]
        assert await table_count(session_factory, rejected_actions, game_id) == 0
    finally:
        await delete_game(session_factory, game_id)


@pytest.mark.asyncio
async def test_ai_step_persists_caller_request_context_in_prompt_context(
    api_app: FastAPI,
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
    tmp_path: Path,
) -> None:
    # Caller request context reaches AI prompt context
    created = await create_game(client, ai_first=True)
    game_id = created["id"]
    ai_player_id = created["players"][0]["id"]
    state = await get_state(client, game_id)
    request_context = {
        "mode": "manual",
        "selected_deal_id": "00000000-0000-0000-0000-000000000077",
        "ui_state": {
            "panel": "deal-review",
            "filters": ["cash", "rent-share"],
            "draft": {"priority": 2, "needs_attention": True},
        },
    }
    runner = QueueFakeCodexRunner([valid_action_output(game_id, ai_player_id, state)])
    install_fake_runner(api_app, runner, tmp_path)

    try:
        response = await client.post(
            f"/games/{game_id}/ai/step",
            json={
                "player_id": ai_player_id,
                "decision_type": "action_decision",
                "mandatory": True,
                "request_context": request_context,
            },
        )

        body = response.json()
        assert response.status_code == 200, response.text
        assert body["status"] == "accepted"
        assert len(runner.calls) == 1
        assert '"caller_request_context":' in runner.calls[0]["stdin"]
        ai_decision = await fetch_ai_decision(session_factory, UUID(body["ai_decision_id"]))
        assert ai_decision["prompt_context"]["caller_request_context"] == request_context
    finally:
        await delete_game(session_factory, game_id)


@pytest.mark.asyncio
async def test_ai_step_merges_top_level_mode_into_caller_request_context(
    api_app: FastAPI,
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
    tmp_path: Path,
) -> None:
    created = await create_game(client, ai_first=True)
    game_id = created["id"]
    ai_player_id = created["players"][0]["id"]
    state = await get_state(client, game_id)
    runner = QueueFakeCodexRunner([valid_action_output(game_id, ai_player_id, state)])
    install_fake_runner(api_app, runner, tmp_path)

    try:
        response = await client.post(
            f"/games/{game_id}/ai/step",
            json={
                "player_id": ai_player_id,
                "decision_type": "action_decision",
                "mandatory": True,
                "mode": "manual-step",
                "request_context": {"source": "top-level-mode-regression"},
            },
        )

        body = response.json()
        assert response.status_code == 200, response.text
        assert body["status"] == "accepted"
        ai_decision = await fetch_ai_decision(session_factory, UUID(body["ai_decision_id"]))
        caller_request_context = ai_decision["prompt_context"]["caller_request_context"]
        assert caller_request_context["source"] == "top-level-mode-regression"
        assert caller_request_context["mode"] == "manual-step"
    finally:
        await delete_game(session_factory, game_id)


@pytest.mark.asyncio
async def test_ai_step_blocks_and_surfaces_auditable_stalls(
    api_app: FastAPI,
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
    tmp_path: Path,
) -> None:
    # stalls visible and auditable
    created = await create_game(client, ai_first=True)
    game_id = created["id"]
    ai_player_id = created["players"][0]["id"]
    runner = QueueFakeCodexRunner(timeout=True)
    install_fake_runner(api_app, runner, tmp_path)

    try:
        response = await client.post(
            f"/games/{game_id}/ai/step",
            json={"player_id": ai_player_id, "decision_type": "action_decision", "mandatory": True},
        )

        body = response.json()
        assert response.status_code == 200, response.text
        assert body["status"] == "blocked"
        assert body["game_status"] == "AI_BLOCKED"
        assert body["rejected_action_id"] is not None
        assert body["accepted_events"] == []
        assert len(runner.calls) == 1
        assert await table_count(session_factory, rejected_actions, game_id) == 1
        assert await game_status(session_factory, game_id) == "AI_BLOCKED"
    finally:
        await delete_game(session_factory, game_id)


@pytest.mark.asyncio
async def test_action_decision_rejects_non_mandatory_override(
    api_app: FastAPI,
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
    tmp_path: Path,
) -> None:
    created = await create_game(client, ai_first=True)
    game_id = created["id"]
    ai_player_id = created["players"][0]["id"]
    runner = QueueFakeCodexRunner(timeout=True)
    install_fake_runner(api_app, runner, tmp_path)

    try:
        response = await client.post(
            f"/games/{game_id}/ai/step",
            json={
                "player_id": ai_player_id,
                "decision_type": "action_decision",
                "mandatory": False,
            },
        )
        body = response.json()

        assert response.status_code == 422, response.text
        assert body["status"] == "rejected"
        assert body["reason_code"] == "action_decision_mandatory_required"
        assert body["validation_errors"][0]["field"] == "mandatory"
        assert runner.calls == []
        assert await table_count(session_factory, ai_decisions, game_id) == 0
        assert await table_count(session_factory, rejected_actions, game_id) == 0
        assert await game_status(session_factory, game_id) == "active"

        omitted_response = await client.post(
            f"/games/{game_id}/ai/step",
            json={"player_id": ai_player_id, "decision_type": "action_decision"},
        )
        omitted_body = omitted_response.json()

        assert omitted_response.status_code == 200, omitted_response.text
        assert omitted_body["status"] == "blocked"
        assert omitted_body["game_status"] == "AI_BLOCKED"
        assert omitted_body["rejected_action_id"] is not None
        assert len(runner.calls) == 1
        assert await table_count(session_factory, ai_decisions, game_id) == 1
        assert await table_count(session_factory, rejected_actions, game_id) == 1
        assert await game_status(session_factory, game_id) == "AI_BLOCKED"
    finally:
        await delete_game(session_factory, game_id)


@pytest.mark.asyncio
async def test_ai_blocked_games_reject_later_actions_and_ai_steps_before_mutation(
    api_app: FastAPI,
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
    tmp_path: Path,
) -> None:
    # AI_BLOCKED games reject later actions and AI steps before mutation
    created = await create_game(client, ai_first=True)
    game_id = created["id"]
    ai_player_id = created["players"][0]["id"]
    state = await get_state(client, game_id)
    runner = QueueFakeCodexRunner(timeout=True)
    install_fake_runner(api_app, runner, tmp_path)

    try:
        blocked_response = await client.post(
            f"/games/{game_id}/ai/step",
            json={"player_id": ai_player_id, "decision_type": "action_decision", "mandatory": True},
        )
        blocked_body = blocked_response.json()
        event_count_after_block = await table_count(session_factory, game_events, game_id)
        ai_decision_count_after_block = await table_count(session_factory, ai_decisions, game_id)
        rejected_action_count_after_block = await table_count(session_factory, rejected_actions, game_id)

        action_response = await client.post(
            f"/games/{game_id}/actions",
            headers={"Idempotency-Key": "phase-7-ai-blocked-action"},
            json={
                "actor_id": ai_player_id,
                "type": "ROLL_DICE",
                "payload": {},
                "expected_state_hash": state["state_hash"],
                "expected_event_sequence": state["event_sequence"],
            },
        )
        action_body = action_response.json()
        event_count_after_action = await table_count(session_factory, game_events, game_id)

        runner.timeout = False
        runner.outputs.append(valid_action_output(game_id, ai_player_id, state))
        runner_call_count_before_second_step = len(runner.calls)
        ai_step_response = await client.post(
            f"/games/{game_id}/ai/step",
            json={"player_id": ai_player_id, "decision_type": "action_decision", "mandatory": True},
        )
        ai_step_body = ai_step_response.json()

        assert blocked_response.status_code == 200, blocked_response.text
        assert blocked_body["status"] == "blocked"
        assert blocked_body["game_status"] == "AI_BLOCKED"
        assert action_body["status"] == "rejected"
        assert action_body["reason_code"] == "game_ai_blocked"
        assert action_body["validation_errors"][0]["code"] == "game_ai_blocked"
        assert action_body["rejected_action_id"] is not None
        assert ai_step_body["status"] == "rejected"
        assert ai_step_body["reason_code"] == "game_ai_blocked"
        assert ai_step_body["validation_errors"][0]["code"] == "game_ai_blocked"
        assert event_count_after_action == event_count_after_block
        assert await table_count(session_factory, game_events, game_id) == event_count_after_block
        assert await table_count(session_factory, ai_decisions, game_id) == ai_decision_count_after_block
        assert await table_count(session_factory, rejected_actions, game_id) == (
            rejected_action_count_after_block + 1
        )
        assert len(runner.calls) == runner_call_count_before_second_step
        assert runner_call_count_before_second_step == 1
        assert await game_status(session_factory, game_id) == "AI_BLOCKED"
    finally:
        await delete_game(session_factory, game_id)


@pytest.mark.asyncio
async def test_ai_negotiation_message_and_complex_deal_outputs_create_lifecycle_records(
    api_app: FastAPI,
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
    tmp_path: Path,
) -> None:
    # AI participation in negotiation windows; AI ability to propose complex deals
    created = await create_game(client)
    game_id = created["id"]
    human_player_id = created["players"][0]["id"]
    ai_player_id = created["players"][1]["id"]
    negotiation = await create_negotiation(client, game_id, human_player_id, ai_player_id)
    runner = QueueFakeCodexRunner(
        [
            negotiation_message_output(game_id, ai_player_id, human_player_id, negotiation["id"]),
            deal_proposal_output(game_id, ai_player_id, human_player_id, negotiation["id"]),
        ]
    )
    install_fake_runner(api_app, runner, tmp_path)

    try:
        message_response = await client.post(
            f"/games/{game_id}/ai/step",
            json={
                "player_id": ai_player_id,
                "decision_type": "negotiation_message",
                "negotiation_id": negotiation["id"],
                "mandatory": False,
                "request_context": {"mode": "negotiation-window"},
            },
        )
        deal_response = await client.post(
            f"/games/{game_id}/ai/step",
            json={
                "player_id": ai_player_id,
                "decision_type": "deal_proposal",
                "negotiation_id": negotiation["id"],
                "mandatory": False,
            },
        )

        message_body = message_response.json()
        deal_body = deal_response.json()
        assert message_response.status_code == 200, message_response.text
        assert message_body["status"] == "done"
        assert message_body["message"]["body"] == "I can offer cash now and a rent share later."
        assert deal_response.status_code == 200, deal_response.text
        assert deal_body["status"] == "done"
        assert deal_body["deal"]["structured_deal"] is True
        assert [term["kind"] for term in deal_body["deal"]["terms"]["terms"]] == [
            "immediate_cash_transfer",
            "rent_share",
        ]
        assert deal_body["negotiation"]["current_deal_id"] == deal_body["deal"]["id"]
        assert len(runner.calls) == 2
        assert await table_count(session_factory, negotiation_messages, game_id) >= 2
        assert await table_count(session_factory, deals, game_id) == 1
    finally:
        await delete_game(session_factory, game_id)


@pytest.mark.asyncio
async def test_ai_counteroffer_and_accept_reject_step_responds_to_offers(
    api_app: FastAPI,
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
    tmp_path: Path,
) -> None:
    # AI response to offers; AIs initiate and respond to negotiations
    created = await create_game(client)
    game_id = created["id"]
    human_player_id = created["players"][0]["id"]
    ai_player_id = created["players"][1]["id"]
    negotiation = await create_negotiation(client, game_id, human_player_id, ai_player_id)
    first_deal = await create_human_deal(client, game_id, negotiation["id"], human_player_id, ai_player_id)
    runner = QueueFakeCodexRunner(
        [
            counteroffer_output(game_id, ai_player_id, human_player_id, negotiation["id"], first_deal["id"]),
            accept_reject_output(game_id, ai_player_id, negotiation["id"], None),
        ]
    )
    install_fake_runner(api_app, runner, tmp_path)

    try:
        counter_response = await client.post(
            f"/games/{game_id}/ai/step",
            json={
                "player_id": ai_player_id,
                "decision_type": "counteroffer",
                "negotiation_id": negotiation["id"],
                "mandatory": False,
            },
        )
        counter_body = counter_response.json()
        accepted_deal_id = counter_body["deal"]["id"]
        runner.outputs[0] = accept_reject_output(
            game_id,
            ai_player_id,
            negotiation["id"],
            accepted_deal_id,
        )
        accept_response = await client.post(
            f"/games/{game_id}/ai/step",
            json={
                "player_id": ai_player_id,
                "decision_type": "accept_reject",
                "negotiation_id": negotiation["id"],
                "mandatory": False,
            },
        )

        accept_body = accept_response.json()
        assert counter_response.status_code == 200, counter_response.text
        assert counter_body["status"] == "done"
        assert counter_body["deal"]["parent_deal_id"] == first_deal["id"]
        assert counter_body["deal"]["deal_version"] == 2
        assert accept_response.status_code == 200, accept_response.text
        assert accept_body["status"] == "done"
        assert accept_body["deal"]["id"] == accepted_deal_id
        assert ai_player_id in accept_body["negotiation"]["acceptances"][accepted_deal_id]
        assert len(runner.calls) == 2
        assert await table_count(session_factory, deals, game_id) == 2
    finally:
        await delete_game(session_factory, game_id)


@pytest.mark.asyncio
async def test_ai_open_negotiation_step_creates_ai_opened_negotiation(
    api_app: FastAPI,
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
    tmp_path: Path,
) -> None:
    created = await create_game(client)
    game_id = created["id"]
    human_player_id = created["players"][0]["id"]
    ai_player_id = created["players"][1]["id"]
    participant_player_ids = [ai_player_id, human_player_id]
    runner = QueueFakeCodexRunner(
        [
            open_negotiation_output(
                game_id,
                ai_player_id,
                participant_player_ids,
                context={"topic": "AI opens the negotiation"},
            )
        ]
    )
    install_fake_runner(api_app, runner, tmp_path)

    try:
        negotiation_count_before = await table_count(session_factory, negotiations, game_id)
        response = await client.post(
            f"/games/{game_id}/ai/step",
            json={
                "player_id": ai_player_id,
                "decision_type": "open_negotiation",
                "mandatory": False,
                "request_context": {"mode": "ai-initiate-negotiation"},
            },
        )

        body = response.json()
        created_negotiation_id = body["negotiation"]["id"]
        ai_decision = await fetch_ai_decision(session_factory, UUID(body["ai_decision_id"]))

        assert response.status_code == 200, response.text
        assert body["status"] == "done"
        assert body["decision_type"] == "open_negotiation"
        assert body["negotiation_id"] == created_negotiation_id
        assert body["outcome"]["kind"] == "open_negotiation"
        assert body["outcome"]["status"] == "done"
        assert body["outcome"]["negotiation_id"] == created_negotiation_id
        assert body["negotiation"]["opened_by_player_id"] == ai_player_id
        assert body["negotiation"]["participant_player_ids"] == participant_player_ids
        assert body["negotiation"]["context"] == {"topic": "AI opens the negotiation"}
        assert body["accepted_events"] == []
        assert body["accepted_event_id"] is None
        assert body["rejected_action_id"] is None
        assert ai_decision["status"] == "accepted"
        ai_decision_negotiation_id = ai_decision["negotiation_id"]
        assert ai_decision_negotiation_id == UUID(created_negotiation_id)
        assert ai_decision["validation_result"]["lifecycle_result"]["negotiation_id"] == created_negotiation_id
        assert len(runner.calls) == 1
        assert await table_count(session_factory, negotiations, game_id) == negotiation_count_before + 1
        assert await table_count(session_factory, rejected_actions, game_id) == 0
    finally:
        await delete_game(session_factory, game_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("participant_case", "reason_code"),
    [
        ("unknown_participant", "participant_not_in_game"),
        ("missing_acting_ai", "player_not_participant"),
    ],
)
async def test_ai_open_negotiation_invalid_participants_reject_and_audit_without_mutation(
    participant_case: str,
    reason_code: str,
    api_app: FastAPI,
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
    tmp_path: Path,
) -> None:
    created = await create_three_player_game(client)
    game_id = created["id"]
    human_player_id = created["players"][0]["id"]
    acting_ai_player_id = created["players"][1]["id"]
    other_ai_player_id = created["players"][2]["id"]
    participant_player_ids = (
        [acting_ai_player_id, "00000000-0000-0000-0000-00000000d00d"]
        if participant_case == "unknown_participant"
        else [human_player_id, other_ai_player_id]
    )
    runner = QueueFakeCodexRunner(
        [open_negotiation_output(game_id, acting_ai_player_id, participant_player_ids)]
    )
    install_fake_runner(api_app, runner, tmp_path)

    try:
        negotiation_count_before = await table_count(session_factory, negotiations, game_id)
        response = await client.post(
            f"/games/{game_id}/ai/step",
            json={
                "player_id": acting_ai_player_id,
                "decision_type": "open_negotiation",
                "mandatory": False,
            },
        )

        body = response.json()
        ai_decision = await fetch_ai_decision(session_factory, UUID(body["ai_decision_id"]))
        rejected_action = await fetch_rejected_action(session_factory, UUID(body["rejected_action_id"]))

        assert response.status_code == 200, response.text
        assert body["status"] == "rejected"
        assert body["reason_code"] == reason_code
        assert body["validation_errors"][0]["code"] == reason_code
        assert body["negotiation_id"] is None
        assert body["negotiation"] is None
        assert body["accepted_events"] == []
        assert body["accepted_event_id"] is None
        assert len(runner.calls) == 1
        assert await table_count(session_factory, negotiations, game_id) == negotiation_count_before
        assert await table_count(session_factory, rejected_actions, game_id) == 1
        assert ai_decision["status"] == "rejected"
        assert ai_decision["rejected_action_id"] == UUID(body["rejected_action_id"])
        assert rejected_action["reason_code"] == reason_code
        assert rejected_action["action_type"] == "AI_OPEN_NEGOTIATION"
        assert rejected_action["actor_player_id"] == UUID(acting_ai_player_id)
        assert rejected_action["payload"]["ai_output"]["negotiation"]["participant_player_ids"] == participant_player_ids
        assert rejected_action["payload"]["no_substitute_move"] is True
        assert rejected_action["payload"]["substitute_move"] is None
    finally:
        await delete_game(session_factory, game_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "decision_type",
    ["negotiation_message", "deal_proposal", "counteroffer", "accept_reject"],
)
async def test_existing_ai_negotiation_decisions_still_require_negotiation_id_before_codex(
    decision_type: str,
    api_app: FastAPI,
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
    tmp_path: Path,
) -> None:
    created = await create_game(client)
    game_id = created["id"]
    ai_player_id = created["players"][1]["id"]
    runner = QueueFakeCodexRunner([base_output(game_id, ai_player_id, decision_type)])
    install_fake_runner(api_app, runner, tmp_path)

    try:
        ai_decision_count_before = await table_count(session_factory, ai_decisions, game_id)
        response = await client.post(
            f"/games/{game_id}/ai/step",
            json={
                "player_id": ai_player_id,
                "decision_type": decision_type,
                "mandatory": False,
            },
        )

        body = response.json()
        assert response.status_code == 422, response.text
        assert body["status"] == "rejected"
        assert body["reason_code"] == "negotiation_id_required"
        assert body["validation_errors"][0]["field"] == "negotiation_id"
        assert runner.calls == []
        assert await table_count(session_factory, ai_decisions, game_id) == ai_decision_count_before
    finally:
        await delete_game(session_factory, game_id)


@pytest.mark.asyncio
async def test_ai_open_negotiation_rejects_negotiation_id_before_codex(
    api_app: FastAPI,
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
    tmp_path: Path,
) -> None:
    created = await create_game(client)
    game_id = created["id"]
    human_player_id = created["players"][0]["id"]
    ai_player_id = created["players"][1]["id"]
    existing_negotiation = await create_negotiation(client, game_id, human_player_id, ai_player_id)
    runner = QueueFakeCodexRunner(
        [open_negotiation_output(game_id, ai_player_id, [ai_player_id, human_player_id])]
    )
    install_fake_runner(api_app, runner, tmp_path)

    try:
        ai_decision_count_before = await table_count(session_factory, ai_decisions, game_id)
        negotiation_count_before = await table_count(session_factory, negotiations, game_id)
        response = await client.post(
            f"/games/{game_id}/ai/step",
            json={
                "player_id": ai_player_id,
                "decision_type": "open_negotiation",
                "negotiation_id": existing_negotiation["id"],
                "mandatory": False,
            },
        )

        body = response.json()
        assert response.status_code == 422, response.text
        assert body["status"] == "rejected"
        assert body["reason_code"] == "negotiation_id_forbidden"
        assert body["validation_errors"][0]["field"] == "negotiation_id"
        assert runner.calls == []
        assert await table_count(session_factory, ai_decisions, game_id) == ai_decision_count_before
        assert await table_count(session_factory, negotiations, game_id) == negotiation_count_before
    finally:
        await delete_game(session_factory, game_id)


@pytest.mark.asyncio
async def test_rejected_ai_lifecycle_applications_persist_rejected_action_id_and_consume_response_opportunity(
    api_app: FastAPI,
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
    tmp_path: Path,
) -> None:
    # Rejected AI lifecycle applications persist rejected_action_id and consume response opportunity
    created = await create_game(client)
    game_id = created["id"]
    human_player_id = created["players"][0]["id"]
    ai_player_id = created["players"][1]["id"]
    negotiation = await create_negotiation(client, game_id, human_player_id, ai_player_id)
    current_deal = await create_human_deal(
        client,
        game_id,
        negotiation["id"],
        human_player_id,
        ai_player_id,
    )
    runner = QueueFakeCodexRunner(
        [
            accept_reject_output(game_id, ai_player_id, negotiation["id"], current_deal["id"]),
            accept_reject_output(game_id, ai_player_id, negotiation["id"], current_deal["id"]),
        ]
    )
    install_fake_runner(api_app, runner, tmp_path)

    try:
        request_payload = {
            "player_id": ai_player_id,
            "decision_type": "accept_reject",
            "negotiation_id": negotiation["id"],
            "mandatory": False,
        }
        first_accept = await client.post(f"/games/{game_id}/ai/step", json=request_payload)
        response = await client.post(
            f"/games/{game_id}/ai/step",
            json=request_payload,
        )

        body = response.json()
        ai_decision_id = UUID(body["ai_decision_id"])
        rejected_action_id = UUID(body["rejected_action_id"])
        ai_decision = await fetch_ai_decision(session_factory, ai_decision_id)
        rejected_action = await fetch_rejected_action(session_factory, rejected_action_id)
        negotiation_context = await fetch_negotiation_context(session_factory, negotiation["id"])
        attempt_key = f"round:1:player:{ai_player_id}"

        assert first_accept.status_code == 200, first_accept.text
        assert first_accept.json()["status"] == "done"
        assert response.status_code == 200, response.text
        assert body["status"] == "rejected"
        assert body["rejected_action_id"] is not None
        assert body["reason_code"] == "deal_already_accepted_by_player"
        assert body["consumed_response_opportunity"] is True
        assert "ai_response_opportunities_consumed" in body["consumed_negotiation_opportunity"]
        assert attempt_key in body["consumed_negotiation_opportunity"]["ai_response_opportunities_consumed"]
        assert ai_decision["status"] == "rejected"
        assert ai_decision["rejected_action_id"] == rejected_action_id
        assert rejected_action["id"] == rejected_action_id
        assert rejected_action["reason_code"] == "deal_already_accepted_by_player"
        assert rejected_action["action_type"] == "AI_ACCEPT_REJECT"
        assert rejected_action["actor_player_id"] == UUID(ai_player_id)
        assert rejected_action["payload"]["ai_output"]["accept_reject"]["deal_id"] == current_deal["id"]
        assert rejected_action["payload"]["no_substitute_move"] is True
        assert rejected_action["payload"]["substitute_move"] is None
        assert rejected_action["legal_action_context"]["actor_id"] == ai_player_id
        assert rejected_action["phase"] is not None
        assert rejected_action["state_hash"] is not None
        validation_result = ai_decision["validation_result"]
        assert validation_result["status"] == "rejected"
        assert validation_result["reason_code"] == "deal_already_accepted_by_player"
        assert validation_result["validation_errors"][0]["code"] == "deal_already_accepted_by_player"
        assert validation_result["lifecycle_result"]["status"] == "rejected"
        assert validation_result["lifecycle_result"]["reason_code"] == "deal_already_accepted_by_player"
        assert validation_result["no_substitute_move"] is True
        assert validation_result["substitute_move"] is None
        consumed = negotiation_context["ai_response_opportunities_consumed"]
        assert consumed[attempt_key]["ai_decision_id"] == str(ai_decision_id)
        assert consumed[attempt_key]["reason_code"] == "deal_already_accepted_by_player"
        assert consumed[attempt_key]["no_substitute_move"] is True
        assert consumed[attempt_key]["substitute_move"] is None
        assert negotiation_context["ai_decision_attempts_by_message_id"][attempt_key] == 1
    finally:
        await delete_game(session_factory, game_id)


@pytest.mark.asyncio
async def test_mandatory_ai_lifecycle_rejection_marks_game_blocked(
    api_app: FastAPI,
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
    tmp_path: Path,
) -> None:
    created = await create_game(client)
    game_id = created["id"]
    human_player_id = created["players"][0]["id"]
    ai_player_id = created["players"][1]["id"]
    negotiation = await create_negotiation(client, game_id, human_player_id, ai_player_id)
    current_deal = await create_human_deal(
        client,
        game_id,
        negotiation["id"],
        human_player_id,
        ai_player_id,
    )
    runner = QueueFakeCodexRunner(
        [
            accept_reject_output(game_id, ai_player_id, negotiation["id"], current_deal["id"]),
            accept_reject_output(game_id, ai_player_id, negotiation["id"], current_deal["id"]),
        ]
    )
    install_fake_runner(api_app, runner, tmp_path)

    try:
        first_accept = await client.post(
            f"/games/{game_id}/ai/step",
            json={
                "player_id": ai_player_id,
                "decision_type": "accept_reject",
                "negotiation_id": negotiation["id"],
                "mandatory": False,
            },
        )
        response = await client.post(
            f"/games/{game_id}/ai/step",
            json={
                "player_id": ai_player_id,
                "decision_type": "accept_reject",
                "negotiation_id": negotiation["id"],
                "mandatory": True,
            },
        )

        body = response.json()
        ai_decision_id = UUID(body["ai_decision_id"])
        rejected_action_id = UUID(body["rejected_action_id"])
        ai_decision = await fetch_ai_decision(session_factory, ai_decision_id)
        rejected_action = await fetch_rejected_action(session_factory, rejected_action_id)

        assert first_accept.status_code == 200, first_accept.text
        assert first_accept.json()["status"] == "done"
        assert response.status_code == 200, response.text
        assert body["status"] == "blocked"
        assert body["game_status"] == "AI_BLOCKED"
        assert body["rejected_action_id"] is not None
        assert body["reason_code"] == "deal_already_accepted_by_player"
        assert body["validation_errors"][0]["code"] == "deal_already_accepted_by_player"
        assert body["outcome"]["kind"] == "ai_blocked"
        assert body["outcome"]["status"] == "blocked"
        assert body["outcome"]["reason_code"] == "deal_already_accepted_by_player"
        assert body["consumed_response_opportunity"] is False
        assert body["consumed_negotiation_opportunity"] is None
        assert ai_decision["status"] == "rejected"
        assert ai_decision["rejected_action_id"] == rejected_action_id
        assert rejected_action["id"] == rejected_action_id
        assert rejected_action["reason_code"] == "deal_already_accepted_by_player"
        assert rejected_action["action_type"] == "AI_ACCEPT_REJECT"
        assert rejected_action["actor_player_id"] == UUID(ai_player_id)
        assert rejected_action["payload"]["ai_output"]["accept_reject"]["deal_id"] == current_deal["id"]
        assert rejected_action["payload"]["no_substitute_move"] is True
        assert rejected_action["payload"]["substitute_move"] is None
        assert rejected_action["legal_action_context"]["actor_id"] == ai_player_id
        assert rejected_action["phase"] is not None
        assert rejected_action["state_hash"] is not None
        validation_result = ai_decision["validation_result"]
        assert validation_result["status"] == "rejected"
        assert validation_result["reason_code"] == "deal_already_accepted_by_player"
        assert validation_result["validation_errors"][0]["code"] == "deal_already_accepted_by_player"
        assert validation_result["lifecycle_result"]["status"] == "rejected"
        assert validation_result["lifecycle_result"]["reason_code"] == "deal_already_accepted_by_player"
        assert validation_result["no_substitute_move"] is True
        assert validation_result["substitute_move"] is None
        assert await game_status(session_factory, game_id) == "AI_BLOCKED"
    finally:
        await delete_game(session_factory, game_id)


@pytest.mark.asyncio
async def test_consumed_ai_negotiation_opportunities_reject_before_launching_codex(
    api_app: FastAPI,
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
    tmp_path: Path,
) -> None:
    # Consumed AI negotiation opportunities reject before launching Codex
    created = await create_game(client)
    game_id = created["id"]
    human_player_id = created["players"][0]["id"]
    ai_player_id = created["players"][1]["id"]
    negotiation = await create_negotiation(client, game_id, human_player_id, ai_player_id)
    current_deal = await create_human_deal(
        client,
        game_id,
        negotiation["id"],
        human_player_id,
        ai_player_id,
    )
    runner = QueueFakeCodexRunner(
        [
            accept_reject_output(game_id, ai_player_id, negotiation["id"], current_deal["id"]),
            accept_reject_output(game_id, ai_player_id, negotiation["id"], current_deal["id"]),
            accept_reject_output(game_id, ai_player_id, negotiation["id"], current_deal["id"]),
        ]
    )
    install_fake_runner(api_app, runner, tmp_path)

    try:
        request_payload = {
            "player_id": ai_player_id,
            "decision_type": "accept_reject",
            "negotiation_id": negotiation["id"],
            "mandatory": False,
        }
        first_accept = await client.post(f"/games/{game_id}/ai/step", json=request_payload)
        first_response = await client.post(f"/games/{game_id}/ai/step", json=request_payload)
        first_body = first_response.json()
        attempt_key = f"round:1:player:{ai_player_id}"
        consumed_payload = first_body["consumed_negotiation_opportunity"]
        ai_decision_count_after_first_rejection = await table_count(session_factory, ai_decisions, game_id)

        second_response = await client.post(f"/games/{game_id}/ai/step", json=request_payload)
        second_body = second_response.json()

        assert first_accept.status_code == 200, first_accept.text
        assert first_accept.json()["status"] == "done"
        assert first_response.status_code == 200, first_response.text
        assert first_body["status"] == "rejected"
        assert first_body["consumed_response_opportunity"] is True
        assert consumed_payload is not None
        assert attempt_key in consumed_payload["ai_response_opportunities_consumed"]
        assert second_response.status_code == 200, second_response.text
        assert second_body["status"] == "rejected"
        assert second_body["reason_code"] == "ai_response_opportunity_consumed"
        assert second_body["validation_errors"][0]["code"] == "ai_response_opportunity_consumed"
        assert second_body["accepted_events"] == []
        assert second_body["accepted_event_id"] is None
        assert second_body["rejected_action_id"] is None
        assert second_body["consumed_response_opportunity"] is True
        assert second_body["consumed_negotiation_opportunity"] == consumed_payload
        assert attempt_key in second_body["consumed_negotiation_opportunity"]["ai_response_opportunities_consumed"]
        assert len(runner.calls) == 2
        assert ai_decision_count_after_first_rejection == 2
        assert await table_count(session_factory, ai_decisions, game_id) == ai_decision_count_after_first_rejection
    finally:
        await delete_game(session_factory, game_id)


@pytest.mark.asyncio
async def test_accepted_ai_negotiation_requests_reject_before_launching_codex(
    api_app: FastAPI,
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
    tmp_path: Path,
) -> None:
    created = await create_game(client)
    game_id = created["id"]
    human_player_id = created["players"][0]["id"]
    ai_player_id = created["players"][1]["id"]
    negotiation = await create_negotiation(client, game_id, human_player_id, ai_player_id)
    current_deal = await create_human_deal(
        client,
        game_id,
        negotiation["id"],
        human_player_id,
        ai_player_id,
    )
    runner = QueueFakeCodexRunner(
        [
            accept_reject_output(game_id, ai_player_id, negotiation["id"], current_deal["id"]),
            negotiation_message_output(
                game_id,
                ai_player_id,
                human_player_id,
                negotiation["id"],
            )
        ]
    )
    install_fake_runner(api_app, runner, tmp_path)

    try:
        ai_accept = await client.post(
            f"/games/{game_id}/ai/step",
            json={
                "player_id": ai_player_id,
                "decision_type": "accept_reject",
                "negotiation_id": negotiation["id"],
                "mandatory": False,
            },
        )
        human_accept = await client.post(
            f"/games/{game_id}/deals/{current_deal['id']}/accept",
            json={"player_id": human_player_id},
        )
        accepted_negotiation = await get_negotiation(client, game_id, negotiation["id"])
        ai_decision_count_before = await table_count(session_factory, ai_decisions, game_id)
        response = await client.post(
            f"/games/{game_id}/ai/step",
            json={
                "player_id": ai_player_id,
                "decision_type": "negotiation_message",
                "negotiation_id": negotiation["id"],
                "mandatory": False,
            },
        )

        body = response.json()
        assert ai_accept.status_code == 200, ai_accept.text
        assert ai_accept.json()["status"] == "done"
        assert human_accept.status_code == 200, human_accept.text
        assert accepted_negotiation["status"] == "accepted"
        assert response.status_code == 422, response.text
        assert body["status"] == "rejected"
        assert body["reason_code"] == "negotiation_already_accepted"
        assert body["validation_errors"][0]["code"] == "negotiation_already_accepted"
        assert len(runner.calls) == 1
        assert await table_count(session_factory, ai_decisions, game_id) == ai_decision_count_before
    finally:
        await delete_game(session_factory, game_id)


@pytest.mark.asyncio
async def test_invalid_ai_negotiation_requests_reject_before_launching_codex(
    api_app: FastAPI,
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
    tmp_path: Path,
) -> None:
    # Invalid AI negotiation requests reject before launching Codex
    created = await create_three_player_game(client)
    game_id = created["id"]
    human_player_id = created["players"][0]["id"]
    participant_ai_player_id = created["players"][1]["id"]
    nonparticipant_ai_player_id = created["players"][2]["id"]
    expired_negotiation = await create_negotiation(
        client,
        game_id,
        human_player_id,
        participant_ai_player_id,
    )
    active_negotiation = await create_negotiation(
        client,
        game_id,
        human_player_id,
        participant_ai_player_id,
    )
    runner = QueueFakeCodexRunner(
        [
            negotiation_message_output(
                game_id,
                participant_ai_player_id,
                human_player_id,
                expired_negotiation["id"],
            ),
            negotiation_message_output(
                game_id,
                nonparticipant_ai_player_id,
                human_player_id,
                active_negotiation["id"],
            ),
        ]
    )
    install_fake_runner(api_app, runner, tmp_path)

    try:
        expire_response = await client.post(
            f"/games/{game_id}/negotiations/{expired_negotiation['id']}/expire"
        )
        ai_decision_count_before_invalid_requests = await table_count(
            session_factory,
            ai_decisions,
            game_id,
        )

        expired_response = await client.post(
            f"/games/{game_id}/ai/step",
            json={
                "player_id": participant_ai_player_id,
                "decision_type": "negotiation_message",
                "negotiation_id": expired_negotiation["id"],
                "mandatory": False,
            },
        )
        ai_decision_count_after_expired_request = await table_count(
            session_factory,
            ai_decisions,
            game_id,
        )
        nonparticipant_response = await client.post(
            f"/games/{game_id}/ai/step",
            json={
                "player_id": nonparticipant_ai_player_id,
                "decision_type": "negotiation_message",
                "negotiation_id": active_negotiation["id"],
                "mandatory": False,
            },
        )
        ai_decision_count_after_nonparticipant_request = await table_count(
            session_factory,
            ai_decisions,
            game_id,
        )

        expired_body = expired_response.json()
        nonparticipant_body = nonparticipant_response.json()
        assert expire_response.status_code == 200, expire_response.text
        assert expired_response.status_code == 422, expired_response.text
        assert expired_body["status"] == "rejected"
        assert expired_body["reason_code"] == "negotiation_expired"
        assert expired_body["validation_errors"][0]["code"] == "negotiation_expired"
        assert nonparticipant_response.status_code == 422, nonparticipant_response.text
        assert nonparticipant_body["status"] == "rejected"
        assert nonparticipant_body["reason_code"] == "player_not_participant"
        assert nonparticipant_body["validation_errors"][0]["code"] == "player_not_participant"
        assert runner.calls == []
        assert (
            ai_decision_count_after_expired_request
            == ai_decision_count_before_invalid_requests
        )
        assert (
            ai_decision_count_after_nonparticipant_request
            == ai_decision_count_before_invalid_requests
        )
    finally:
        await delete_game(session_factory, game_id)


async def create_game(client: httpx.AsyncClient, *, ai_first: bool = False) -> dict[str, Any]:
    players = (
        [{"name": "Grace", "kind": "ai"}, {"name": "Ada", "kind": "human"}]
        if ai_first
        else [{"name": "Ada", "kind": "human"}, {"name": "Grace", "kind": "ai"}]
    )
    response = await client.post(
        "/games",
        json={"seed": "phase-7-stage-7.6-ai-step", "players": players},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def create_three_player_game(client: httpx.AsyncClient) -> dict[str, Any]:
    response = await client.post(
        "/games",
        json={
            "seed": "phase-7-stage-7.6-invalid-ai-step",
            "players": [
                {"name": "Ada", "kind": "human"},
                {"name": "Grace", "kind": "ai"},
                {"name": "Lin", "kind": "ai"},
            ],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def get_state(client: httpx.AsyncClient, game_id: str) -> dict[str, Any]:
    response = await client.get(f"/games/{game_id}/state")
    assert response.status_code == 200, response.text
    return response.json()


async def get_negotiation(
    client: httpx.AsyncClient,
    game_id: str,
    negotiation_id: str,
) -> dict[str, Any]:
    response = await client.get(f"/games/{game_id}/negotiations/{negotiation_id}")
    assert response.status_code == 200, response.text
    return response.json()


async def create_negotiation(
    client: httpx.AsyncClient,
    game_id: str,
    human_player_id: str,
    ai_player_id: str,
) -> dict[str, Any]:
    response = await client.post(
        f"/games/{game_id}/negotiations",
        json={
            "opened_by_player_id": human_player_id,
            "participant_player_ids": [human_player_id, ai_player_id],
            "context": {"topic": "stage 7.6 ai negotiation"},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def create_human_deal(
    client: httpx.AsyncClient,
    game_id: str,
    negotiation_id: str,
    human_player_id: str,
    ai_player_id: str,
) -> dict[str, Any]:
    response = await client.post(
        f"/games/{game_id}/deals",
        json={
            "negotiation_id": negotiation_id,
            "proposed_by_player_id": human_player_id,
            "participant_player_ids": [human_player_id, ai_player_id],
            "terms": structured_terms(human_player_id, ai_player_id, cash_from=human_player_id),
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def install_fake_runner(api_app: FastAPI, runner: QueueFakeCodexRunner, tmp_path: Path) -> None:
    api_app.state.codex_ai_runner = runner
    api_app.state.codex_ai_schema_file = tmp_path / "schema.json"
    api_app.state.codex_ai_sandbox_dir = tmp_path / "sandbox"
    api_app.state.codex_ai_work_dir = tmp_path / "work"


def valid_action_output(game_id: str, ai_player_id: str, state_response: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **base_output(game_id, ai_player_id, "action_decision"),
        "expected_state_hash": state_response["state_hash"],
        "expected_event_sequence": state_response["event_sequence"],
        "action": {"type": "ROLL_DICE", "payload": {}},
    }


def negotiation_message_output(
    game_id: str,
    ai_player_id: str,
    human_player_id: str,
    negotiation_id: str,
) -> dict[str, Any]:
    return {
        **base_output(game_id, ai_player_id, "negotiation_message"),
        "negotiation_id": negotiation_id,
        "message": {
            "recipient_player_id": human_player_id,
            "body": "I can offer cash now and a rent share later.",
            "metadata": {"mode": "stage-7.6"},
        },
    }


def deal_proposal_output(
    game_id: str,
    ai_player_id: str,
    human_player_id: str,
    negotiation_id: str,
) -> dict[str, Any]:
    return {
        **base_output(game_id, ai_player_id, "deal_proposal"),
        "negotiation_id": negotiation_id,
        "deal": {
            "recipient_player_ids": [human_player_id],
            "terms": structured_terms(ai_player_id, human_player_id, cash_from=ai_player_id),
            "message": "Here is a complex proposal with future rent participation.",
        },
    }


def open_negotiation_output(
    game_id: str,
    ai_player_id: str,
    participant_player_ids: Sequence[str],
    *,
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        **base_output(game_id, ai_player_id, "open_negotiation"),
        "negotiation": {
            "participant_player_ids": list(participant_player_ids),
            "context": dict(context or {"topic": "stage 7.6 AI open negotiation"}),
        },
    }


def counteroffer_output(
    game_id: str,
    ai_player_id: str,
    human_player_id: str,
    negotiation_id: str,
    deal_id: str,
) -> dict[str, Any]:
    return {
        **base_output(game_id, ai_player_id, "counteroffer"),
        "negotiation_id": negotiation_id,
        "counteroffer": {
            "responds_to_deal_id": deal_id,
            "terms": structured_terms(ai_player_id, human_player_id, cash_from=human_player_id, amount=70),
            "message": "I need better cash terms before accepting.",
        },
    }


def accept_reject_output(
    game_id: str,
    ai_player_id: str,
    negotiation_id: str,
    deal_id: str | None,
) -> dict[str, Any]:
    if deal_id is None:
        deal_id = "00000000-0000-0000-0000-000000000001"
    return {
        **base_output(game_id, ai_player_id, "accept_reject"),
        "negotiation_id": negotiation_id,
        "accept_reject": {
            "deal_id": deal_id,
            "decision": "accept",
            "message": "This is now acceptable.",
        },
    }


def base_output(game_id: str, ai_player_id: str, decision_type: str) -> dict[str, Any]:
    return {
        "decision_type": decision_type,
        "game_id": game_id,
        "player_id": ai_player_id,
        "self_dialogue": {"status": "provided", "text": "Stage 7.6 endpoint test decision."},
        "memory_updates": [],
        "confidence": 0.82,
        "rationale": "The fake runner returns one schema-valid decision for endpoint tests.",
    }


def structured_terms(
    from_player_id: str,
    to_player_id: str,
    *,
    cash_from: str,
    amount: int = 50,
) -> dict[str, Any]:
    cash_to = to_player_id if cash_from == from_player_id else from_player_id
    return {
        "kind": "structured_deal",
        "deal_schema_version": 1,
        "participants": [from_player_id, to_player_id],
        "terms": [
            {
                "kind": "immediate_cash_transfer",
                "instrument_id": "stage-7-6-cash-now",
                "from_player_id": cash_from,
                "to_player_id": cash_to,
                "amount": amount,
            },
            {
                "kind": "rent_share",
                "instrument_id": "stage-7-6-rent-share",
                "from_player_id": to_player_id,
                "to_player_id": from_player_id,
                "property_id": "property_mediterranean_avenue",
                "share_percent": 20,
                "duration_turns": 3,
            },
        ],
    }


def assert_direct_ai_negotiation_rejection(response: httpx.Response, field: str) -> None:
    body = response.json()
    assert response.status_code == 409, response.text
    assert body["status"] == "rejected"
    assert body["reason_code"] == "ai_player_requires_codex"
    assert body["validation_errors"][0]["code"] == "ai_player_requires_codex"
    assert body["validation_errors"][0]["field"] == field
    assert "fallback_action" not in body
    assert "substitute_action" not in body
    assert "substitute_move" not in body


async def table_count(
    session_factory: async_sessionmaker,
    table: sa.Table,
    game_id: str | UUID,
) -> int:
    async with session_factory() as session:
        result = await session.execute(
            sa.select(sa.func.count()).select_from(table).where(table.c.game_id == UUID(str(game_id)))
        )
        return int(result.scalar_one())


async def game_status(session_factory: async_sessionmaker, game_id: str | UUID) -> str:
    async with session_factory() as session:
        result = await session.execute(sa.select(games.c.status).where(games.c.id == UUID(str(game_id))))
        return str(result.scalar_one())


async def fetch_ai_decision(
    session_factory: async_sessionmaker,
    ai_decision_id: UUID,
) -> Mapping[str, Any]:
    async with session_factory() as session:
        result = await session.execute(sa.select(ai_decisions).where(ai_decisions.c.id == ai_decision_id))
        row = result.mappings().one()
        return dict(row)


async def fetch_rejected_action(
    session_factory: async_sessionmaker,
    rejected_action_id: UUID,
) -> Mapping[str, Any]:
    async with session_factory() as session:
        result = await session.execute(
            sa.select(rejected_actions).where(rejected_actions.c.id == rejected_action_id)
        )
        row = result.mappings().one()
        return dict(row)


async def fetch_rows(
    session_factory: async_sessionmaker,
    table: sa.Table,
    game_id: str | UUID,
) -> list[dict[str, Any]]:
    async with session_factory() as session:
        result = await session.execute(
            sa.select(table).where(table.c.game_id == UUID(str(game_id))).order_by(table.c.created_at)
        )
        return [dict(row) for row in result.mappings().all()]


async def fetch_negotiation_context(
    session_factory: async_sessionmaker,
    negotiation_id: str | UUID,
) -> Mapping[str, Any]:
    async with session_factory() as session:
        result = await session.execute(
            sa.select(negotiations.c.context).where(negotiations.c.id == UUID(str(negotiation_id)))
        )
        context = result.scalar_one()
    assert isinstance(context, Mapping)
    return context


async def delete_game(session_factory: async_sessionmaker, game_id: str | UUID) -> None:
    async with session_factory() as session:
        async with session.begin():
            await session.execute(games.delete().where(games.c.id == UUID(str(game_id))))
