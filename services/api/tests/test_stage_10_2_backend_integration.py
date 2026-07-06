# pyright: reportAny=false, reportExplicitAny=false, reportImplicitOverride=false, reportImplicitRelativeImport=false, reportImplicitStringConcatenation=false, reportMissingImports=false, reportUnannotatedClassAttribute=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownVariableType=false, reportUnusedCallResult=false, reportUntypedFunctionDecorator=false
from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator, Mapping, Sequence
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.ai.orchestrator import CodexExecProcessResult, CodexExecRunner
from app.core.config import Settings
from app.db.metadata import (
    action_idempotency_keys,
    ai_decisions,
    ai_self_dialogue,
    contracts,
    deals,
    game_events,
    game_snapshots,
    metadata,
    negotiation_messages,
    negotiations,
    obligations,
    players,
    rejected_actions,
)
from app.db.persistence import (
    AcceptedEventTemplate,
    EventPersistence,
    EventPersistenceError,
)
from app.main import create_app


STAGE_10_2_DATABASE_NAME = "monopoly_ai_game_stage10_2"
STAGE_10_2_TEST_DATABASE_URL = os.getenv(
    "STAGE_10_2_TEST_DATABASE_URL",
    "postgresql+asyncpg://monopoly:monopoly@127.0.0.1:5432/"
    f"{STAGE_10_2_DATABASE_NAME}",
)
STAGE_10_2_ADMIN_DATABASE_URL = STAGE_10_2_TEST_DATABASE_URL.replace(
    f"/{STAGE_10_2_DATABASE_NAME}",
    "/postgres",
)


class FakeCodexExecRunner(CodexExecRunner):
    def __init__(self, outputs: Sequence[Mapping[str, Any] | str]) -> None:
        self.outputs = list(outputs)
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
        if not self.outputs:
            raise AssertionError("fake Codex exec runner was called without queued output")

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


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    await _ensure_stage_10_2_database()
    engine = create_async_engine(STAGE_10_2_TEST_DATABASE_URL, pool_pre_ping=True)
    async with engine.begin() as connection:
        await connection.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        await connection.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
        await connection.run_sync(metadata.drop_all)
        await connection.run_sync(metadata.create_all)

    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(bind=engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def api_app(engine: AsyncEngine) -> AsyncIterator[FastAPI]:
    del engine
    app = create_app(
        settings=Settings(
            api_env="test",
            database_url=STAGE_10_2_TEST_DATABASE_URL,
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
async def test_stage_10_2_major_api_endpoints_success_and_failure_paths(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
) -> None:
    health = await client.get("/health")
    malformed_game = await client.post(
        "/games",
        json={"seed": "stage-10.2-bad", "players": [{"name": "Solo", "kind": "human"}]},
    )
    created = await create_game(
        client,
        seed="stage-10.2-major-api",
        players=[
            {"name": "Ada", "kind": "human"},
            {"name": "Grace", "kind": "ai"},
            {"name": "Linus", "kind": "human"},
        ],
    )
    game_id = created["id"]
    human_id = created["players"][0]["id"]
    ai_id = created["players"][1]["id"]
    other_human_id = created["players"][2]["id"]
    missing_game_id = "00000000-0000-0000-0000-000000010204"

    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert malformed_game.status_code == 422
    assert created["status"] == "active"
    assert created["current_phase"] == "START_TURN"
    assert {player["controller_type"] for player in created["players"]} == {"human", "ai"}

    metadata_response = await client.get(f"/games/{game_id}")
    state_response = await client.get(f"/games/{game_id}/state")
    missing_state = await client.get(f"/games/{missing_game_id}/state")
    missing_events = await client.get(f"/games/{missing_game_id}/events")

    assert metadata_response.status_code == 200
    assert metadata_response.json()["id"] == game_id
    assert state_response.status_code == 200
    assert state_response.json()["event_sequence"] == 0
    assert missing_state.status_code == 404
    assert missing_events.status_code == 404

    missing_actor = await client.get(f"/games/{game_id}/legal-actions")
    unknown_actor = await client.get(
        f"/games/{game_id}/legal-actions",
        params={"actor_player_id": "00000000-0000-0000-0000-000000010299"},
    )
    legal_response = await client.get(
        f"/games/{game_id}/legal-actions",
        params={"actor_player_id": human_id},
    )
    roll_action = next(
        action for action in legal_response.json()["legal_actions"] if action["type"] == "ROLL_DICE"
    )

    assert missing_actor.status_code == 422
    assert unknown_actor.status_code == 422
    assert legal_response.status_code == 200
    assert legal_response.json()["state_hash"] == state_response.json()["state_hash"]

    missing_key = await client.post(f"/games/{game_id}/actions", json=roll_action)
    accepted = await client.post(
        f"/games/{game_id}/actions",
        headers={"Idempotency-Key": "stage-10.2-major-roll"},
        json=roll_action,
    )
    idempotent_replay = await client.post(
        f"/games/{game_id}/actions",
        headers={"Idempotency-Key": "stage-10.2-major-roll"},
        json=roll_action,
    )
    idempotency_conflict = await client.post(
        f"/games/{game_id}/actions",
        headers={"Idempotency-Key": "stage-10.2-major-roll"},
        json={**roll_action, "payload": {"different": True}},
    )
    stale = await client.post(
        f"/games/{game_id}/actions",
        headers={"Idempotency-Key": "stage-10.2-stale-roll"},
        json=roll_action,
    )

    assert missing_key.status_code == 400
    assert missing_key.json()["reason_code"] == "missing_idempotency_key"
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "accepted"
    assert idempotent_replay.status_code == 200
    assert idempotent_replay.json() == accepted.json()
    assert idempotency_conflict.status_code == 409
    assert idempotency_conflict.json()["reason_code"] == "idempotency_key_conflict"
    assert stale.status_code == 409
    assert stale.json()["reason_code"] == "stale_action"

    events_response = await client.get(f"/games/{game_id}/events")
    rejections_response = await client.get(f"/games/{game_id}/rejected-actions")
    filtered_rejections = await client.get(
        f"/games/{game_id}/rejected-actions",
        params={"actor_player_id": human_id},
    )
    async with client.stream("GET", f"/games/{game_id}/events/stream") as stream:
        stream_body = (await stream.aread()).decode("utf-8")

    accepted_event_ids = [event["id"] for event in accepted.json()["accepted_events"]]
    assert events_response.status_code == 200
    assert [event["id"] for event in events_response.json()["events"]] == accepted_event_ids
    assert rejections_response.status_code == 200
    assert [row["reason_code"] for row in rejections_response.json()["rejected_actions"]] == [
        "stale_action"
    ]
    assert filtered_rejections.json() == rejections_response.json()
    assert stream.status_code == 200
    assert "event: game_event" in stream_body
    assert accepted.json()["accepted_events"][0]["event_type"] in stream_body

    negotiation = await client.post(
        f"/games/{game_id}/negotiations",
        json={
            "opened_by_player_id": human_id,
            "participant_player_ids": [human_id, other_human_id],
            "context": {"topic": "stage 10.2 major endpoint coverage"},
        },
    )
    bad_negotiation = await client.post(
        f"/games/{game_id}/negotiations",
        json={"opened_by_player_id": ai_id, "participant_player_ids": [ai_id, human_id]},
    )
    negotiation_id = negotiation.json()["id"]
    message = await client.post(
        f"/games/{game_id}/negotiations/{negotiation_id}/messages",
        json={"sender_player_id": human_id, "recipient_player_id": other_human_id, "body": "Deal?"},
    )
    private_messages = await client.get(
        f"/games/{game_id}/negotiations/{negotiation_id}/messages",
        params={"viewer_player_id": other_human_id},
    )
    invalid_deal = await client.post(
        f"/games/{game_id}/deals",
        json={
            "negotiation_id": negotiation_id,
            "proposed_by_player_id": human_id,
            "terms": {
                "kind": "structured_deal",
                "deal_schema_version": 1,
                "participants": [human_id, other_human_id],
                "terms": [
                    {
                        "kind": "immediate_cash_transfer",
                        "from_player_id": human_id,
                        "to_player_id": other_human_id,
                        "amount": 0,
                    }
                ],
            },
        },
    )
    valid_deal = await client.post(
        f"/games/{game_id}/deals",
        json={
            "negotiation_id": negotiation_id,
            "proposed_by_player_id": human_id,
            "terms": {"cash_offer": 10, "participants": [human_id, other_human_id]},
        },
    )
    first_accept = await client.post(
        f"/games/{game_id}/deals/{valid_deal.json()['id']}/accept",
        json={"player_id": human_id},
    )
    second_accept = await client.post(
        f"/games/{game_id}/deals/{valid_deal.json()['id']}/accept",
        json={"player_id": other_human_id},
    )
    executed = await client.post(f"/games/{game_id}/negotiations/{negotiation_id}/execute")
    terminal_reject = await client.post(f"/games/{game_id}/deals/{valid_deal.json()['id']}/reject")

    assert negotiation.status_code == 201
    assert negotiation.json()["status"] == "opened"
    assert bad_negotiation.status_code == 409
    assert bad_negotiation.json()["reason_code"] == "ai_player_requires_codex"
    assert message.status_code == 201
    assert private_messages.status_code == 200
    assert private_messages.json()["messages"][0]["body"] == "Deal?"
    assert invalid_deal.status_code == 422
    assert invalid_deal.json()["reason_code"] == "invalid_structured_deal"
    assert valid_deal.status_code == 201
    assert first_accept.status_code == 200
    assert second_accept.status_code == 200
    assert second_accept.json()["status"] == "accepted"
    assert executed.status_code == 200
    assert executed.json()["status"] == "executed"
    assert terminal_reject.status_code == 422
    assert terminal_reject.json()["reason_code"] == "negotiation_executed"

    endpoint_checks = {
        "negotiations": await client.get(f"/games/{game_id}/negotiations"),
        "negotiation": await client.get(f"/games/{game_id}/negotiations/{negotiation_id}"),
        "contracts": await client.get(f"/games/{game_id}/contracts"),
        "obligations": await client.get(f"/games/{game_id}/obligations"),
        "outcomes": await client.get(f"/games/{game_id}/contracts/outcomes"),
        "ai_profiles": await client.get(f"/games/{game_id}/ai/profiles"),
        "ai_self_dialogue": await client.get(f"/games/{game_id}/ai/self-dialogue"),
        "ai_memory": await client.get(f"/games/{game_id}/ai/memory"),
        "ai_decisions": await client.get(f"/games/{game_id}/ai/decisions"),
        "ai_retrieval": await client.get(f"/games/{game_id}/ai/retrieval-records"),
        "ai_rejected_outputs": await client.get(f"/games/{game_id}/ai/rejected-outputs"),
    }

    assert all(response.status_code == 200 for response in endpoint_checks.values())
    assert endpoint_checks["ai_profiles"].json()["profiles"][0]["player_id"] == ai_id
    assert endpoint_checks["contracts"].json()["contracts"] == []
    assert endpoint_checks["obligations"].json()["obligations"] == []
    assert endpoint_checks["ai_decisions"].json()["decisions"] == []
    assert await table_count(session_factory, game_events, game_id) == len(accepted_event_ids)
    assert await table_count(session_factory, rejected_actions, game_id) == 1
    assert await table_count(session_factory, action_idempotency_keys, game_id) == 2
    assert await table_count(session_factory, negotiations, game_id) == 1
    assert await table_count(session_factory, deals, game_id) == 2
    assert await table_count(session_factory, negotiation_messages, game_id) >= 1


@pytest.mark.asyncio
async def test_stage_10_2_database_transactions_roll_back_failed_event_append(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
) -> None:
    created = await create_game(client, seed="stage-10.2-rollback")
    game_id = created["id"]
    player_id = created["players"][0]["id"]
    persistence = EventPersistence(session_factory, snapshot_interval=1)

    with pytest.raises(EventPersistenceError):
        await persistence.append_accepted_events(
            game_id=game_id,
            actor_player_id=player_id,
            event_templates=[
                AcceptedEventTemplate(
                    event_type="PLAYER_CASH_DELTA",
                    payload={"player_id": player_id, "amount": 25},
                ),
                AcceptedEventTemplate(
                    event_type="PLAYER_CASH_DELTA",
                    payload={"player_id": player_id},
                ),
            ],
            expected_base_sequence=0,
        )

    replayed = await persistence.replay_from_event_zero(game_id)
    state_response = await client.get(f"/games/{game_id}/state")
    player_rows = await fetch_rows(session_factory, players, game_id)

    assert replayed.event_sequence == 0
    assert replayed.players[0].cash == 1500
    assert state_response.json()["event_sequence"] == 0
    assert state_response.json()["state"]["players"][0]["cash"] == 1500
    assert player_rows[0]["state"]["cash"] == 1500
    assert await table_count(session_factory, game_events, game_id) == 0
    assert await table_count(session_factory, game_snapshots, game_id) == 0


@pytest.mark.asyncio
async def test_stage_10_2_event_snapshot_replay_and_rejected_audit_are_separated(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
) -> None:
    created = await create_game(client, seed="stage-10.2-snapshot-replay")
    game_id = created["id"]
    player_id = created["players"][0]["id"]
    persistence = EventPersistence(session_factory, snapshot_interval=2)

    append_result = await persistence.append_accepted_events(
        game_id=game_id,
        actor_player_id=player_id,
        event_templates=[
            AcceptedEventTemplate(
                event_type="PLAYER_CASH_DELTA",
                payload={"player_id": player_id, "amount": 30},
            ),
            AcceptedEventTemplate(
                event_type="PLAYER_CASH_DELTA",
                payload={"player_id": player_id, "amount": -5},
            ),
        ],
        expected_base_sequence=0,
    )
    state = await client.get(f"/games/{game_id}/state")
    rejected = await client.post(
        f"/games/{game_id}/actions",
        headers={"Idempotency-Key": "stage-10.2-illegal-buy"},
        json={
            "actor_id": player_id,
            "type": "BUY_PROPERTY",
            "payload": {"property_id": "property_boardwalk"},
            "expected_state_hash": state.json()["state_hash"],
            "expected_event_sequence": state.json()["event_sequence"],
        },
    )

    from_zero = await persistence.replay_from_event_zero(game_id)
    from_snapshot = await persistence.replay_from_latest_snapshot(game_id)
    snapshot_check = await persistence.verify_game_snapshots(game_id)
    events_response = await client.get(f"/games/{game_id}/events")
    rejections_response = await client.get(f"/games/{game_id}/rejected-actions")
    event_rows = await fetch_rows(session_factory, game_events, game_id)
    rejection_rows = await fetch_rows(session_factory, rejected_actions, game_id)

    assert rejected.status_code == 422
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["reason_code"] == "illegal_action"
    assert [record.sequence for record in append_result.events] == [1, 2]
    assert from_zero.state_hash() == append_result.state.state_hash()
    assert from_snapshot.state_hash() == from_zero.state_hash()
    assert snapshot_check.event_count == 2
    assert snapshot_check.snapshot_count == 1
    assert snapshot_check.replayed_state_hash == snapshot_check.latest_snapshot_state_hash
    assert [event["sequence"] for event in events_response.json()["events"]] == [1, 2]
    assert [event["event_type"] for event in events_response.json()["events"]] == [
        "PLAYER_CASH_DELTA",
        "PLAYER_CASH_DELTA",
    ]
    assert len(rejections_response.json()["rejected_actions"]) == 1
    assert rejections_response.json()["rejected_actions"][0]["action_type"] == "BUY_PROPERTY"
    assert len(event_rows) == 2
    assert len(rejection_rows) == 1
    assert {row["id"] for row in event_rows}.isdisjoint({row["id"] for row in rejection_rows})
    assert all(row["event_type"] != "BUY_PROPERTY" for row in event_rows)
    assert all(row["action_type"] != "PLAYER_CASH_DELTA" for row in rejection_rows)


@pytest.mark.asyncio
async def test_stage_10_2_fake_codex_ai_step_records_decision_without_fallback(
    api_app: FastAPI,
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
    tmp_path: Path,
) -> None:
    created = await create_game(
        client,
        seed="stage-10.2-fake-codex-ai",
        players=[{"name": "Grace", "kind": "ai"}, {"name": "Ada", "kind": "human"}],
    )
    game_id = created["id"]
    ai_player_id = created["players"][0]["id"]
    state = (await client.get(f"/games/{game_id}/state")).json()
    runner = FakeCodexExecRunner([valid_action_ai_output(game_id, ai_player_id, state)])
    install_fake_runner(api_app, runner, tmp_path)

    response = await client.post(
        f"/games/{game_id}/ai/step",
        json={
            "player_id": ai_player_id,
            "decision_type": "action_decision",
            "mandatory": True,
            "request_context": {"stage": "10.2", "runner": "fake CodexExecRunner"},
        },
    )
    body = response.json()
    decision_rows = await fetch_rows(session_factory, ai_decisions, game_id)
    dialogue_rows = await fetch_rows(session_factory, ai_self_dialogue, game_id)
    events_response = await client.get(f"/games/{game_id}/events")
    decisions_response = await client.get(f"/games/{game_id}/ai/decisions")
    rejected_outputs = await client.get(f"/games/{game_id}/ai/rejected-outputs")

    assert response.status_code == 200, response.text
    assert body["status"] == "accepted"
    assert body["accepted_event_id"] == body["accepted_events"][0]["id"]
    assert body["rejected_action_id"] is None
    assert body["outcome"] == {"kind": "action_decision", "status": "accepted"}
    assert len(runner.calls) == 1
    assert "--json" in runner.calls[0]["command"]
    assert "--output-schema" in runner.calls[0]["command"]
    assert runner.calls[0]["command"][-1] == "-"
    assert "fake CodexExecRunner" in runner.calls[0]["stdin"]
    assert await table_count(session_factory, game_events, game_id) == len(body["accepted_events"])
    assert await table_count(session_factory, rejected_actions, game_id) == 0
    assert len(decision_rows) == 1
    assert decision_rows[0]["status"] == "accepted"
    assert decision_rows[0]["accepted_event_id"] == UUID(body["accepted_event_id"])
    assert decision_rows[0]["rejected_action_id"] is None
    assert decision_rows[0]["validation_result"]["no_substitute_move"] is True
    assert decision_rows[0]["validation_result"]["substitute_move"] is None
    assert "session_configured" in decision_rows[0]["raw_output"]
    assert len(dialogue_rows) == 1
    assert dialogue_rows[0]["ai_decision_id"] == decision_rows[0]["id"]
    assert events_response.json()["events"][0]["id"] == body["accepted_event_id"]
    assert decisions_response.json()["decisions"][0]["ai_decision_id"] == str(decision_rows[0]["id"])
    assert decisions_response.json()["decisions"][0]["accepted_event_id"] == body["accepted_event_id"]
    assert rejected_outputs.json()["rejected_outputs"] == []


@pytest.mark.asyncio
async def test_stage_10_2_contract_settlement_integration_accepts_and_rejects(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
) -> None:
    created = await create_game(
        client,
        seed="stage-10.2-contracts",
        players=[
            {"name": "Ada", "kind": "human"},
            {"name": "Grace", "kind": "human"},
            {"name": "Linus", "kind": "human"},
        ],
    )
    game_id = created["id"]
    player_ids = [player["id"] for player in created["players"]]
    negotiation = await create_negotiation(client, game_id, player_ids)
    proposed_structured = await propose_deal(
        client,
        game_id,
        negotiation["id"],
        player_ids[0],
        structured_terms(
            player_ids,
            [
                {
                    "kind": "immediate_cash_transfer",
                    "from_player_id": player_ids[0],
                    "to_player_id": player_ids[1],
                    "amount": 90,
                }
            ],
        ),
    )
    unaccepted_contract = await client.post(
        f"/games/{game_id}/contracts/from-deal",
        json={"deal_id": proposed_structured["id"]},
    )
    accepted_deal = await accept_for_all_players(
        client,
        game_id,
        proposed_structured["id"],
        player_ids,
    )
    created_contract = await client.post(
        f"/games/{game_id}/contracts/from-deal",
        json={"deal_id": accepted_deal["id"]},
    )
    duplicate_contract = await client.post(
        f"/games/{game_id}/contracts/from-deal",
        json={"deal_id": accepted_deal["id"]},
    )
    contract = created_contract.json()["contract"]
    obligation = created_contract.json()["obligations"][0]
    invalid_obligation = await client.post(
        f"/games/{game_id}/contracts/{contract['id']}/settle",
        json={"obligation_id": str(uuid4())},
    )
    missing_contract = await client.post(
        f"/games/{game_id}/contracts/{uuid4()}/settle",
        json={"obligation_id": obligation["id"]},
    )
    settlement = await client.post(
        f"/games/{game_id}/contracts/{contract['id']}/settle",
        json={"obligation_id": obligation["id"]},
    )
    repeated_settlement = await client.post(
        f"/games/{game_id}/contracts/{contract['id']}/settle",
        json={"obligation_id": obligation["id"]},
    )
    enforce_after_close = await client.post(
        f"/games/{game_id}/contracts/enforce",
        json={"trigger_context": {"type": "immediate"}},
    )
    state = (await client.get(f"/games/{game_id}/state")).json()["state"]
    contract_list = await client.get(f"/games/{game_id}/contracts")
    obligation_list = await client.get(f"/games/{game_id}/obligations")
    outcomes = await client.get(f"/games/{game_id}/contracts/outcomes")
    explanation = await client.get(f"/games/{game_id}/contracts/{contract['id']}/explain")
    audit_rows = await fetch_rows(session_factory, negotiation_messages, game_id)

    assert unaccepted_contract.status_code == 422
    assert unaccepted_contract.json()["reason_code"] == "deal_not_contract_eligible"
    assert created_contract.status_code == 201, created_contract.text
    assert duplicate_contract.status_code == 200
    assert duplicate_contract.json()["contract"]["id"] == contract["id"]
    assert invalid_obligation.status_code == 422
    assert invalid_obligation.json()["reason_code"] == "obligation_not_found"
    assert missing_contract.status_code == 404
    assert settlement.status_code == 200, settlement.text
    assert settlement.json()["settled_obligation_ids"] == [obligation["id"]]
    assert settlement.json()["defaulted_obligation_ids"] == []
    assert [event["event_type"] for event in settlement.json()["accepted_events"]] == [
        "PLAYER_CASH_DELTA",
        "PLAYER_CASH_DELTA",
    ]
    assert repeated_settlement.status_code == 200
    assert repeated_settlement.json()["settled_obligation_ids"] == []
    assert repeated_settlement.json()["accepted_events"] == []
    assert enforce_after_close.status_code == 200
    assert enforce_after_close.json()["accepted_events"] == []
    cash_by_player = {player["id"]: player["cash"] for player in state["players"]}
    assert cash_by_player[player_ids[0]] == 1410
    assert cash_by_player[player_ids[1]] == 1590
    assert contract_list.json()["contracts"][0]["status"] == "closed"
    assert obligation_list.json()["obligations"][0]["status"] == "settled"
    assert outcomes.status_code == 200
    assert explanation.status_code == 200
    assert outcomes.json()["outcomes"][0] == explanation.json()["outcomes"][0]
    assert outcomes.json()["outcomes"][0]["decision"]["status"] == "settled"
    assert await table_count(session_factory, contracts, game_id) == 1
    assert await table_count(session_factory, obligations, game_id) == 1
    assert await table_count(session_factory, game_events, game_id) == 2
    assert [row["message_type"] for row in audit_rows].count("CONTRACT_CREATED") == 1
    assert [row["message_type"] for row in audit_rows].count("OBLIGATION_SCHEDULED") == 1
    assert [row["message_type"] for row in audit_rows].count("CONTRACT_SETTLEMENT_EVENT") == 1
    assert await table_count(session_factory, rejected_actions, game_id) == 0


async def _ensure_stage_10_2_database() -> None:
    admin_engine = create_async_engine(
        STAGE_10_2_ADMIN_DATABASE_URL,
        isolation_level="AUTOCOMMIT",
        pool_pre_ping=True,
    )
    try:
        async with admin_engine.connect() as connection:
            result = await connection.execute(
                sa.text("SELECT 1 FROM pg_database WHERE datname = :database_name"),
                {"database_name": STAGE_10_2_DATABASE_NAME},
            )
            if result.scalar_one_or_none() is None:
                await connection.execute(sa.text(f'CREATE DATABASE "{STAGE_10_2_DATABASE_NAME}"'))
    finally:
        await admin_engine.dispose()


async def create_game(
    client: httpx.AsyncClient,
    *,
    seed: str,
    players: Sequence[Mapping[str, str]] | None = None,
) -> dict[str, Any]:
    response = await client.post(
        "/games",
        json={
            "seed": seed,
            "players": list(
                players
                or [
                    {"name": "Ada", "kind": "human"},
                    {"name": "Grace", "kind": "human"},
                ]
            ),
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def create_negotiation(
    client: httpx.AsyncClient,
    game_id: str,
    player_ids: Sequence[str],
) -> dict[str, Any]:
    response = await client.post(
        f"/games/{game_id}/negotiations",
        json={
            "opened_by_player_id": player_ids[0],
            "participant_player_ids": list(player_ids),
            "context": {"topic": "stage 10.2 contract settlement"},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def propose_deal(
    client: httpx.AsyncClient,
    game_id: str,
    negotiation_id: str,
    proposer_id: str,
    terms: Mapping[str, Any],
) -> dict[str, Any]:
    response = await client.post(
        f"/games/{game_id}/deals",
        json={
            "negotiation_id": negotiation_id,
            "proposed_by_player_id": proposer_id,
            "terms": dict(terms),
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def accept_for_all_players(
    client: httpx.AsyncClient,
    game_id: str,
    deal_id: str,
    player_ids: Sequence[str],
) -> dict[str, Any]:
    accepted: dict[str, Any] = {}
    for player_id in player_ids:
        response = await client.post(
            f"/games/{game_id}/deals/{deal_id}/accept",
            json={"player_id": player_id},
        )
        assert response.status_code == 200, response.text
        accepted = response.json()
    assert accepted["status"] == "accepted"
    return accepted


def structured_terms(player_ids: Sequence[str], terms: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "kind": "structured_deal",
        "deal_schema_version": 1,
        "participants": list(player_ids),
        "terms": [dict(term) for term in terms],
    }


def install_fake_runner(
    api_app: FastAPI,
    runner: FakeCodexExecRunner,
    tmp_path: Path,
) -> None:
    api_app.state.codex_ai_runner = runner
    api_app.state.codex_ai_schema_file = tmp_path / "stage-10-2-schema.json"
    api_app.state.codex_ai_sandbox_dir = tmp_path / "stage-10-2-sandbox"
    api_app.state.codex_ai_work_dir = tmp_path / "stage-10-2-work"


def valid_action_ai_output(
    game_id: str,
    ai_player_id: str,
    state_response: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "decision_type": "action_decision",
        "game_id": game_id,
        "player_id": ai_player_id,
        "expected_state_hash": state_response["state_hash"],
        "expected_event_sequence": state_response["event_sequence"],
        "action": {"type": "ROLL_DICE", "payload": {}},
        "self_dialogue": {
            "status": "provided",
            "text": "The legal action list contains ROLL_DICE, so I will roll directly.",
        },
        "memory_updates": [],
        "confidence": 0.84,
        "rationale": "A legal fake Codex decision should commit through backend validation.",
    }


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


async def fetch_rows(
    session_factory: async_sessionmaker,
    table: sa.Table,
    game_id: str | UUID,
) -> list[dict[str, Any]]:
    async with session_factory() as session:
        result = await session.execute(
            sa.select(table)
            .where(table.c.game_id == UUID(str(game_id)))
            .order_by(_order_column(table))
        )
        return [dict(row) for row in result.mappings().all()]


def _order_column(table: sa.Table) -> sa.Column[Any]:
    if "sequence" in table.c:
        return table.c.sequence
    if "seat_order" in table.c:
        return table.c.seat_order
    if "created_at" in table.c:
        return table.c.created_at
    return table.c.id
