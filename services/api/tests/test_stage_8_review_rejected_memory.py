"""Phase 8 review regressions for rejected AI memory reuse."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.ai.context_pack import build_ai_context_pack_from_db
from app.ai.orchestrator import CodexExecProcessResult, CodexExecRunner
from app.core.config import Settings
from app.db.metadata import (
    ai_decisions,
    ai_memory_entries,
    games,
    metadata,
)
from app.main import create_app


TEST_DATABASE_URL = os.getenv(
    "MONOPOLY_TEST_DATABASE_URL",
    "postgresql+asyncpg://monopoly:monopoly@127.0.0.1:5432/monopoly_ai_game",
)


class QueueFakeCodexRunner(CodexExecRunner):
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
async def test_stage_8_review_rejected_memory_enforcement_rejection_does_not_create_context_memory(
    api_app: FastAPI,
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
    tmp_path: Path,
) -> None:
    created = await create_game(client, ai_first=True)
    game_id = created["id"]
    ai_player_id = created["players"][0]["id"]
    state = await get_state(client, game_id)
    poison = "Rejected illegal action memory must not enter future prompt context."
    ai_output = valid_action_output(game_id, ai_player_id, state)
    ai_output["action"] = {
        "type": "BUY_PROPERTY",
        "payload": {"property_id": "property_boardwalk"},
    }
    ai_output["memory_updates"] = [memory_update(poison, category="strategic_belief")]
    runner = QueueFakeCodexRunner([ai_output])
    install_fake_runner(api_app, runner, tmp_path)

    try:
        response = await client.post(
            f"/games/{game_id}/ai/step",
            json={"player_id": ai_player_id, "decision_type": "action_decision", "mandatory": True},
        )
        body = response.json()
        memory_rows = await fetch_rows(session_factory, ai_memory_entries, game_id)
        decision_rows = await fetch_rows(session_factory, ai_decisions, game_id)
        pack = await context_pack(session_factory, game_id=game_id, player_id=ai_player_id)
        snippet_text = json.dumps(pack["memory"]["snippets"], sort_keys=True)

        assert response.status_code == 200, response.text
        assert body["status"] == "blocked"
        assert body["reason_code"] == "illegal_action"
        assert len(runner.calls) == 1
        assert decision_rows[0]["status"] == "rejected"
        assert body["rejected_action_id"] == str(decision_rows[0]["rejected_action_id"])
        assert memory_rows == []
        assert poison not in snippet_text
    finally:
        await delete_game(session_factory, game_id)


@pytest.mark.asyncio
async def test_stage_8_review_rejected_memory_accepted_action_persists_and_links_memory(
    api_app: FastAPI,
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
    tmp_path: Path,
) -> None:
    created = await create_game(client, ai_first=True)
    game_id = created["id"]
    ai_player_id = created["players"][0]["id"]
    state = await get_state(client, game_id)
    memory_text = "Accepted roll memory should remain available after event acceptance."
    ai_output = valid_action_output(game_id, ai_player_id, state)
    ai_output["memory_updates"] = [memory_update(memory_text, category="long_term_plan")]
    runner = QueueFakeCodexRunner([ai_output])
    install_fake_runner(api_app, runner, tmp_path)

    try:
        response = await client.post(
            f"/games/{game_id}/ai/step",
            json={"player_id": ai_player_id, "decision_type": "action_decision", "mandatory": True},
        )
        body = response.json()
        memory_rows = await fetch_rows(session_factory, ai_memory_entries, game_id)
        decision_rows = await fetch_rows(session_factory, ai_decisions, game_id)
        pack = await context_pack(session_factory, game_id=game_id, player_id=ai_player_id)
        snippet_text = json.dumps(pack["memory"]["snippets"], sort_keys=True)

        assert response.status_code == 200, response.text
        assert body["status"] == "accepted"
        assert len(runner.calls) == 1
        assert decision_rows[0]["status"] == "accepted"
        assert len(memory_rows) == 1
        row = memory_rows[0]
        assert row["source_decision_id"] == UUID(body["ai_decision_id"])
        assert row["source_event_id"] == UUID(body["accepted_event_id"])
        assert row["source_negotiation_message_id"] is None
        assert row["metadata_blob"]["ai_decision_status"] == "accepted"
        assert row["content"] == memory_text
        assert memory_text in snippet_text
    finally:
        await delete_game(session_factory, game_id)


@pytest.mark.asyncio
async def test_stage_8_review_rejected_memory_negotiation_application_rejection_does_not_create_context_memory(
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
    deal = await create_human_deal(client, game_id, negotiation["id"], human_player_id, ai_player_id)
    poison = "Rejected duplicate acceptance memory must not enter future prompt context."
    first_accept = accept_reject_output(game_id, ai_player_id, negotiation["id"], deal["id"])
    second_accept = accept_reject_output(game_id, ai_player_id, negotiation["id"], deal["id"])
    second_accept["memory_updates"] = [memory_update(poison, category="deal_history")]
    runner = QueueFakeCodexRunner([first_accept, second_accept])
    install_fake_runner(api_app, runner, tmp_path)

    try:
        request_payload = {
            "player_id": ai_player_id,
            "decision_type": "accept_reject",
            "negotiation_id": negotiation["id"],
            "mandatory": False,
        }
        accepted_response = await client.post(f"/games/{game_id}/ai/step", json=request_payload)
        rejected_response = await client.post(f"/games/{game_id}/ai/step", json=request_payload)
        rejected_body = rejected_response.json()
        memory_rows = await fetch_rows(session_factory, ai_memory_entries, game_id)
        pack = await context_pack(
            session_factory,
            game_id=game_id,
            player_id=ai_player_id,
            decision_type="accept_reject",
            negotiation_id=negotiation["id"],
        )
        snippet_text = json.dumps(pack["memory"]["snippets"], sort_keys=True)

        assert accepted_response.status_code == 200, accepted_response.text
        assert accepted_response.json()["status"] == "done"
        assert rejected_response.status_code == 200, rejected_response.text
        assert rejected_body["status"] == "rejected"
        assert rejected_body["reason_code"] == "deal_already_accepted_by_player"
        assert len(runner.calls) == 2
        assert memory_rows == []
        assert poison not in snippet_text
    finally:
        await delete_game(session_factory, game_id)


@pytest.mark.asyncio
async def test_stage_8_review_rejected_memory_legacy_rejected_rows_are_excluded_from_context_and_compaction(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
) -> None:
    created = await create_game(client, ai_first=True)
    game_id = created["id"]
    ai_player_id = created["players"][0]["id"]
    ai_profile_id = (await get_profiles(client, game_id))[0]["ai_profile_id"]
    rejected_decision_id = uuid4()
    accepted_decision_ids = [uuid4(), uuid4()]
    rejected_memory_id = uuid4()
    poison = "Legacy rejected memory poison must not be selected or compacted."

    try:
        await insert_legacy_memory_rows(
            session_factory,
            game_id=game_id,
            player_id=ai_player_id,
            ai_profile_id=ai_profile_id,
            rejected_decision_id=rejected_decision_id,
            accepted_decision_ids=accepted_decision_ids,
            rejected_memory_id=rejected_memory_id,
            rejected_content=poison,
        )
        pack = await context_pack(
            session_factory,
            game_id=game_id,
            player_id=ai_player_id,
            max_memory_snippets=8,
            memory_compaction_threshold=2,
        )
        memory_rows = await fetch_rows(session_factory, ai_memory_entries, game_id)
        snippet_text = json.dumps(pack["memory"]["snippets"], sort_keys=True)
        summary_rows = [
            row
            for row in memory_rows
            if isinstance(row["metadata_blob"].get("compaction"), Mapping)
            and row["metadata_blob"]["compaction"].get("is_summary") is True
        ]
        rejected_row = next(row for row in memory_rows if row["id"] == rejected_memory_id)

        assert poison not in snippet_text
        assert rejected_row["superseded_by_memory_id"] is None
        assert all(poison not in row["content"] for row in summary_rows)
        assert all(
            str(rejected_memory_id)
            not in row["metadata_blob"]["compaction"].get("source_memory_ids", [])
            for row in summary_rows
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
        json={"seed": f"stage-8-review-rejected-memory-{uuid4()}", "players": players},
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
            "context": {"topic": "stage 8 review rejected memory"},
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
            "terms": structured_terms(human_player_id, ai_player_id),
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


def accept_reject_output(
    game_id: str,
    ai_player_id: str,
    negotiation_id: str,
    deal_id: str,
) -> dict[str, Any]:
    return {
        **base_output(game_id, ai_player_id, "accept_reject"),
        "negotiation_id": negotiation_id,
        "accept_reject": {
            "deal_id": deal_id,
            "decision": "accept",
            "message": "This is acceptable.",
        },
    }


def base_output(game_id: str, ai_player_id: str, decision_type: str) -> dict[str, Any]:
    return {
        "decision_type": decision_type,
        "game_id": game_id,
        "player_id": ai_player_id,
        "self_dialogue": {
            "status": "provided",
            "text": "Stage 8 review regression decision.",
        },
        "memory_updates": [],
        "confidence": 0.73,
        "rationale": "The fake runner returns one schema-valid decision.",
    }


def memory_update(content: str, *, category: str) -> dict[str, Any]:
    return {
        "visibility": "private",
        "category": category,
        "importance": 9,
        "content": content,
        "metadata": {"stage": "8-review", "expected_reuse": False},
    }


def structured_terms(human_player_id: str, ai_player_id: str) -> dict[str, Any]:
    return {
        "kind": "structured_deal",
        "deal_schema_version": 1,
        "participants": [human_player_id, ai_player_id],
        "terms": [
            {
                "kind": "immediate_cash_transfer",
                "instrument_id": "stage-8-review-cash",
                "from_player_id": human_player_id,
                "to_player_id": ai_player_id,
                "amount": 50,
            }
        ],
    }


async def context_pack(
    session_factory: async_sessionmaker,
    *,
    game_id: str,
    player_id: str,
    decision_type: str = "action_decision",
    negotiation_id: str | None = None,
    max_memory_snippets: int = 12,
    memory_compaction_threshold: int = 25,
) -> dict[str, Any]:
    async with session_factory() as session:
        return await build_ai_context_pack_from_db(
            session,
            game_id=game_id,
            player_id=player_id,
            session_factory=session_factory,
            decision_type=decision_type,
            negotiation_id=negotiation_id,
            max_memory_snippets=max_memory_snippets,
            memory_compaction_threshold=memory_compaction_threshold,
        )


async def insert_legacy_memory_rows(
    session_factory: async_sessionmaker,
    *,
    game_id: str,
    player_id: str,
    ai_profile_id: str,
    rejected_decision_id: UUID,
    accepted_decision_ids: Sequence[UUID],
    rejected_memory_id: UUID,
    rejected_content: str,
) -> None:
    game_uuid = UUID(game_id)
    player_uuid = UUID(player_id)
    profile_uuid = UUID(ai_profile_id)
    created_at = datetime(2026, 7, 5, 12, 0, tzinfo=UTC)
    decision_rows = [
        (rejected_decision_id, "rejected"),
        *[(decision_id, "accepted") for decision_id in accepted_decision_ids],
    ]

    async with session_factory() as session:
        async with session.begin():
            for index, (decision_id, status) in enumerate(decision_rows):
                await session.execute(
                    ai_decisions.insert().values(
                        id=decision_id,
                        game_id=game_uuid,
                        player_id=player_uuid,
                        ai_profile_id=profile_uuid,
                        decision_type="action_decision",
                        status=status,
                        phase="START_TURN",
                        state_hash=f"stage-8-review-legacy-{index}",
                        prompt_context_hash=f"stage-8-review-legacy-{decision_id}",
                        prompt_context={"stage": "8-review"},
                        raw_output="{}",
                        parsed_output={"memory_updates": []},
                        validation_result={"status": status},
                        created_at=created_at + timedelta(seconds=index),
                    )
                )

            await session.execute(
                ai_memory_entries.insert().values(
                    id=rejected_memory_id,
                    game_id=game_uuid,
                    player_id=player_uuid,
                    ai_profile_id=profile_uuid,
                    source_decision_id=rejected_decision_id,
                    category="deal_history",
                    visibility="private",
                    content=rejected_content,
                    importance=0,
                    metadata_blob={
                        "schema_version": "ai-memory-v1",
                        "trusted_ai_output": True,
                        "ai_decision_status": "rejected",
                    },
                    created_at=created_at,
                )
            )
            for index, decision_id in enumerate(accepted_decision_ids, start=1):
                await session.execute(
                    ai_memory_entries.insert().values(
                        game_id=game_uuid,
                        player_id=player_uuid,
                        ai_profile_id=profile_uuid,
                        source_decision_id=decision_id,
                        category="strategic_belief",
                        visibility="private",
                        content=f"Accepted legacy memory {index}.",
                        importance=10,
                        metadata_blob={
                            "schema_version": "ai-memory-v1",
                            "trusted_ai_output": True,
                            "ai_decision_status": "accepted",
                        },
                        created_at=created_at + timedelta(seconds=index),
                    )
                )


async def fetch_rows(
    session_factory: async_sessionmaker,
    table: sa.Table,
    game_id: str | UUID,
) -> list[dict[str, Any]]:
    async with session_factory() as session:
        result = await session.execute(
            sa.select(table).where(table.c.game_id == UUID(str(game_id))).order_by(table.c.created_at, table.c.id)
        )
        return [dict(row) for row in result.mappings().all()]


async def delete_game(session_factory: async_sessionmaker, game_id: str | UUID) -> None:
    async with session_factory() as session:
        async with session.begin():
            await session.execute(games.delete().where(games.c.id == UUID(str(game_id))))
