"""Stage 9.4 tests for RAG and MCP authority boundaries."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any
from uuid import UUID

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.db.metadata import (
    action_idempotency_keys,
    ai_memory_entries,
    contracts,
    deals,
    game_events,
    games,
    metadata,
    obligations,
    rejected_actions,
)
from app.main import create_app
from app.mcp.tools import LocalMCPContext, call_local_tool
from app.rag.retrieval import refresh_rag_index_entries


TEST_DATABASE_URL = os.getenv(
    "MONOPOLY_TEST_DATABASE_URL",
    "postgresql+asyncpg://monopoly:monopoly@127.0.0.1:5432/monopoly_ai_game",
)


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    async with engine.begin() as connection:
        await connection.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        await connection.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
        await connection.run_sync(metadata.create_all)

    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
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
async def test_stage_9_4_private_memory_isolation_between_players(
    api_app: FastAPI,
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    created = await create_game(client, player_kinds=("ai", "ai"))
    game_id = created["id"]
    first_player_id = created["players"][0]["id"]
    second_player_id = created["players"][1]["id"]
    first_memory_id = "00000000-0000-0000-0000-000000009401"
    second_memory_id = "00000000-0000-0000-0000-000000009402"
    first_marker = "stage94 first private cobalt-plan memory"
    second_marker = "stage94 second private amber-plan memory"
    context = LocalMCPContext(api_app=api_app)
    try:
        await insert_private_memory_fixture(
            session_factory,
            game_id=game_id,
            rows=(
                {
                    "id": first_memory_id,
                    "player_id": first_player_id,
                    "content": first_marker,
                },
                {
                    "id": second_memory_id,
                    "player_id": second_player_id,
                    "content": second_marker,
                },
            ),
        )

        first_own = await call_local_tool(
            "search_memory",
            {
                "query_text": first_marker,
                "game_id": game_id,
                "player_id": first_player_id,
                "source_types": ["ai_memory"],
                "limit": 10,
            },
            context=context,
        )
        first_probe_for_other = await call_local_tool(
            "search_memory",
            {
                "query_text": second_marker,
                "game_id": game_id,
                "player_id": first_player_id,
                "source_types": ["ai_memory"],
                "limit": 10,
            },
            context=context,
        )
        second_own = await call_local_tool(
            "search_memory",
            {
                "query_text": second_marker,
                "game_id": game_id,
                "player_id": second_player_id,
                "source_types": ["ai_memory"],
                "limit": 10,
            },
            context=context,
        )
        second_probe_for_other = await call_local_tool(
            "search_memory",
            {
                "query_text": first_marker,
                "game_id": game_id,
                "player_id": second_player_id,
                "source_types": ["ai_memory"],
                "limit": 10,
            },
            context=context,
        )

        assert first_memory_id in source_ids(first_own)
        assert second_memory_id in source_ids(second_own)
        assert second_memory_id not in source_ids(first_own)
        assert second_memory_id not in source_ids(first_probe_for_other)
        assert first_memory_id not in source_ids(second_own)
        assert first_memory_id not in source_ids(second_probe_for_other)
        assert second_marker not in retrieved_document_text(first_probe_for_other)
        assert first_marker not in retrieved_document_text(second_probe_for_other)
    finally:
        await delete_game(session_factory, game_id)


@pytest.mark.asyncio
async def test_stage_9_4_stale_mcp_action_is_rejected_by_fastapi_authority(
    api_app: FastAPI,
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    created = await create_game(client, player_kinds=("human", "human"))
    game_id = created["id"]
    actor_player_id = created["players"][0]["id"]
    context = LocalMCPContext(api_app=api_app)
    try:
        legal_payload = await call_local_tool(
            "get_legal_actions",
            {"game_id": game_id, "actor_player_id": actor_player_id},
            context=context,
        )
        stale_roll_action = next(
            action
            for action in legal_payload["legal_actions"]
            if action["type"] == "ROLL_DICE"
        )

        accepted = await call_local_tool(
            "submit_action",
            {
                "game_id": game_id,
                "idempotency_key": "stage-9.4-accepted-roll",
                "action": stale_roll_action,
            },
            context=context,
        )
        accepted_state = await current_state(client, game_id)
        accepted_event_count = await table_count(session_factory, game_events, game_id)

        stale_rejected = await call_local_tool(
            "submit_action",
            {
                "game_id": game_id,
                "idempotency_key": "stage-9.4-stale-roll",
                "action": stale_roll_action,
            },
            context=context,
        )
        after_rejection_state = await current_state(client, game_id)

        assert accepted["status_code"] == 200
        assert accepted["response"]["status"] == "accepted"
        assert stale_rejected["source_path"] == f"/games/{game_id}/actions"
        assert stale_rejected["status_code"] == 409
        assert stale_rejected["response"]["status"] == "rejected"
        assert stale_rejected["response"]["reason_code"] == "stale_action"
        assert stale_rejected["response"]["legal_action_context"]["state_hash"] == accepted_state[
            "state_hash"
        ]
        assert stale_rejected["response"]["legal_action_context"]["event_sequence"] == accepted_state[
            "event_sequence"
        ]
        assert after_rejection_state["state_hash"] == accepted_state["state_hash"]
        assert after_rejection_state["event_sequence"] == accepted_state["event_sequence"]
        assert await table_count(session_factory, game_events, game_id) == accepted_event_count
        assert await table_count(session_factory, game_events, game_id) == len(
            accepted["response"]["accepted_events"]
        )
        assert await table_count(session_factory, rejected_actions, game_id) == 1
        assert await table_count(session_factory, action_idempotency_keys, game_id) == 2
    finally:
        await delete_game(session_factory, game_id)


@pytest.mark.asyncio
async def test_stage_9_4_invalid_deal_draft_rejected_without_mutation(
    api_app: FastAPI,
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    created = await create_game(client, player_kinds=("human", "human"))
    game_id = created["id"]
    first_player_id = created["players"][0]["id"]
    second_player_id = created["players"][1]["id"]
    context = LocalMCPContext(api_app=api_app)
    try:
        before_counts = await mutation_counts(session_factory, game_id)
        invalid_structured = await call_local_tool(
            "validate_deal_draft",
            {
                "game_id": game_id,
                "draft": {
                    "proposed_by_player_id": first_player_id,
                    "participant_player_ids": [first_player_id, second_player_id],
                    "terms": {
                        "kind": "structured_deal",
                        "deal_schema_version": 1,
                        "participants": [first_player_id, second_player_id],
                        "terms": [
                            {
                                "kind": "immediate_cash_transfer",
                                "from_player_id": first_player_id,
                                "to_player_id": first_player_id,
                                "amount": 50,
                            }
                        ],
                    },
                },
            },
            context=context,
        )
        invalid_freeform = await call_local_tool(
            "validate_deal_draft",
            {
                "game_id": game_id,
                "draft": {
                    "participant_player_ids": [first_player_id, second_player_id],
                    "terms": {"cash_offer": 10, "note": "stage94 missing proposer"},
                },
            },
            context=context,
        )
        after_counts = await mutation_counts(session_factory, game_id)

        assert invalid_structured["valid"] is False
        assert invalid_structured["reason_code"] == "invalid_structured_deal"
        assert invalid_structured["structured_deal"] is True
        assert any(
            error["field"] == "terms.0.to_player_id" or "to_player_id" in error["field"]
            for error in invalid_structured["validation_errors"]
        )
        assert invalid_freeform["valid"] is False
        assert invalid_freeform["reason_code"] == "invalid_deal_draft"
        assert invalid_freeform["validation_errors"]
        assert all(result["created_deal"] is False for result in (invalid_structured, invalid_freeform))
        assert all(
            result["created_contract"] is False
            for result in (invalid_structured, invalid_freeform)
        )
        assert all(
            result["created_obligation"] is False
            for result in (invalid_structured, invalid_freeform)
        )
        assert all(result["created_event"] is False for result in (invalid_structured, invalid_freeform))
        assert before_counts == after_counts
    finally:
        await delete_game(session_factory, game_id)


@pytest.mark.asyncio
async def test_stage_9_4_legal_actions_match_fastapi_authority(
    api_app: FastAPI,
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    created = await create_game(client, player_kinds=("human", "human"))
    game_id = created["id"]
    actor_player_id = created["players"][0]["id"]
    context = LocalMCPContext(api_app=api_app)
    try:
        fastapi_response = await client.get(
            f"/games/{game_id}/legal-actions",
            params={"actor_player_id": actor_player_id},
        )
        assert fastapi_response.status_code == 200, fastapi_response.text
        fastapi_payload = fastapi_response.json()

        mcp_payload = await call_local_tool(
            "get_legal_actions",
            {"game_id": game_id, "actor_player_id": actor_player_id},
            context=context,
        )

        assert mcp_payload["source_path"] == f"/games/{game_id}/legal-actions"
        assert mcp_payload["game_id"] == fastapi_payload["game_id"]
        assert mcp_payload["actor_player_id"] == fastapi_payload["actor_player_id"]
        assert mcp_payload["state_hash"] == fastapi_payload["state_hash"]
        assert mcp_payload["event_sequence"] == fastapi_payload["event_sequence"]
        assert mcp_payload["legal_actions"] == fastapi_payload["legal_actions"]
    finally:
        await delete_game(session_factory, game_id)


async def create_game(
    client: httpx.AsyncClient,
    *,
    player_kinds: Sequence[str],
) -> dict[str, Any]:
    player_names = ("Ada", "Grace", "Linus", "Katherine", "Donald")
    response = await client.post(
        "/games",
        json={
            "seed": "stage-9.4-rag-mcp-boundaries",
            "players": [
                {"name": player_names[index], "kind": player_kind}
                for index, player_kind in enumerate(player_kinds)
            ],
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return body if isinstance(body, dict) else {}


async def insert_private_memory_fixture(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    game_id: str,
    rows: Sequence[Mapping[str, str]],
) -> None:
    async with session_factory() as session:
        async with session.begin():
            for row in rows:
                await session.execute(
                    ai_memory_entries.insert().values(
                        id=UUID(row["id"]),
                        game_id=UUID(game_id),
                        player_id=UUID(row["player_id"]),
                        ai_profile_id=None,
                        source_decision_id=None,
                        source_event_id=None,
                        source_negotiation_message_id=None,
                        superseded_by_memory_id=None,
                        category="strategic_belief",
                        visibility="private",
                        content=row["content"],
                        importance=8,
                        metadata_blob={"fixture": "stage-9.4"},
                    )
                )

    async with session_factory() as session:
        await refresh_rag_index_entries(session, game_id=UUID(game_id))
        await session.commit()


def source_ids(payload: Mapping[str, Any]) -> set[str]:
    results = payload.get("results")
    if not isinstance(results, Sequence) or isinstance(results, str | bytes | bytearray):
        return set()
    return {
        str(result["source_id"])
        for result in results
        if isinstance(result, Mapping) and "source_id" in result
    }


def retrieved_document_text(payload: Mapping[str, Any]) -> str:
    results = payload.get("results")
    if not isinstance(results, Sequence) or isinstance(results, str | bytes | bytearray):
        return ""
    document_fragments: list[str] = []
    for result in results:
        if not isinstance(result, Mapping):
            continue
        document_fragments.append(str(result.get("title", "")))
        document_fragments.append(str(result.get("text", "")))
    return "\n".join(document_fragments)


async def current_state(client: httpx.AsyncClient, game_id: str) -> dict[str, Any]:
    response = await client.get(f"/games/{game_id}/state")
    assert response.status_code == 200, response.text
    body = response.json()
    return body if isinstance(body, dict) else {}


async def delete_game(
    session_factory: async_sessionmaker[AsyncSession],
    game_id: str,
) -> None:
    async with session_factory() as session:
        async with session.begin():
            await session.execute(games.delete().where(games.c.id == UUID(game_id)))


async def table_count(
    session_factory: async_sessionmaker[AsyncSession],
    table: sa.Table,
    game_id: str,
) -> int:
    async with session_factory() as session:
        result = await session.execute(
            sa.select(sa.func.count()).select_from(table).where(table.c.game_id == UUID(game_id))
        )
        return int(result.scalar_one())


async def mutation_counts(
    session_factory: async_sessionmaker[AsyncSession],
    game_id: str,
) -> Mapping[str, int]:
    return {
        "deals": await table_count(session_factory, deals, game_id),
        "contracts": await table_count(session_factory, contracts, game_id),
        "obligations": await table_count(session_factory, obligations, game_id),
        "events": await table_count(session_factory, game_events, game_id),
    }
