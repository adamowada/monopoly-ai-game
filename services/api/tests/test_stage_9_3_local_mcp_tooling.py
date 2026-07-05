"""Stage 9.3 tests for local-only MCP tooling."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
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
    negotiations,
    obligations,
    rejected_actions,
)
from app.main import create_app
from app.mcp.tools import (
    REQUIRED_LOCAL_MCP_TOOL_NAMES,
    LocalMCPContext,
    call_local_tool,
    list_local_tools,
)
from app.rag.retrieval import refresh_rag_index_entries


REPO_ROOT = Path(__file__).resolve().parents[3]
API_ROOT = REPO_ROOT / "services" / "api"
DOCS_PATH = REPO_ROOT / "docs" / "local-mcp.md"
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


def test_stage_9_3_mcp_tool_list_exposes_required_local_tools() -> None:
    tool_payloads = list_local_tools()
    tool_names = {tool["name"] for tool in tool_payloads}

    assert tool_names == set(REQUIRED_LOCAL_MCP_TOOL_NAMES)
    for tool in tool_payloads:
        assert tool["transport"] == "stdio"
        assert tool["local_only"] is True
        assert tool["mutates_game_state"] is (tool["name"] == "submit_action")
        assert tool["inputSchema"]["type"] == "object"
        assert isinstance(tool["inputSchema"]["properties"], dict)


@pytest.mark.asyncio
async def test_stage_9_3_read_tools_return_fastapi_and_retrieval_payloads(
    api_app: FastAPI,
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    created = await create_game(client, player_kinds=("human", "ai"))
    game_id = created["id"]
    human_player_id = created["players"][0]["id"]
    ai_player_id = created["players"][1]["id"]
    contract_id = "00000000-0000-0000-0000-000000009931"
    context = LocalMCPContext(api_app=api_app)
    try:
        await insert_memory_and_contract_fixture(
            session_factory,
            game_id=game_id,
            player_id=ai_player_id,
            contract_id=contract_id,
        )

        state_payload = await call_local_tool("get_game_state", {"game_id": game_id}, context=context)
        legal_payload = await call_local_tool(
            "get_legal_actions",
            {"game_id": game_id, "actor_player_id": human_player_id},
            context=context,
        )
        rules_payload = await call_local_tool(
            "search_rules",
            {"query_text": "Boardwalk hotel rent", "game_id": game_id, "limit": 3},
            context=context,
        )
        memory_payload = await call_local_tool(
            "search_memory",
            {
                "query_text": "stage93 private Boardwalk reminder",
                "game_id": game_id,
                "player_id": ai_player_id,
                "limit": 3,
            },
            context=context,
        )
        contract_payload = await call_local_tool(
            "inspect_contract",
            {"game_id": game_id, "contract_id": contract_id},
            context=context,
        )

        assert state_payload["source_path"] == f"/games/{game_id}/state"
        assert state_payload["state"]["turn"]["phase"] == "START_TURN"
        assert legal_payload["source_path"] == f"/games/{game_id}/legal-actions"
        assert {action["type"] for action in legal_payload["legal_actions"]} >= {
            "ROLL_DICE",
            "DECLARE_BANKRUPTCY",
        }
        assert rules_payload["retrieval_engine"] == "stage_9_2_local_retrieval"
        assert rules_payload["results"][0]["source_type"] == "rules"
        assert "Boardwalk" in rules_payload["results"][0]["text"]
        assert memory_payload["retrieval_engine"] == "stage_9_2_local_retrieval"
        assert memory_payload["results"][0]["source_type"] == "ai_memory"
        assert "stage93 private Boardwalk reminder" in memory_payload["results"][0]["text"]
        assert contract_payload["found"] is True
        assert contract_payload["contract"]["id"] == contract_id
        assert contract_payload["contract"]["terms"]["fixture"] == "stage-9.3"
        assert await table_count(session_factory, retrieval_records_table_name(), game_id) == 0
    finally:
        await delete_game(session_factory, game_id)


@pytest.mark.asyncio
async def test_stage_9_3_submit_action_routes_through_fastapi_validation(
    api_app: FastAPI,
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    created = await create_game(client, player_kinds=("human", "human"))
    game_id = created["id"]
    actor_player_id = created["players"][0]["id"]
    context = LocalMCPContext(api_app=api_app)
    try:
        legal_response = await client.get(
            f"/games/{game_id}/legal-actions",
            params={"actor_player_id": actor_player_id},
        )
        assert legal_response.status_code == 200, legal_response.text
        roll_action = next(
            action for action in legal_response.json()["legal_actions"] if action["type"] == "ROLL_DICE"
        )

        accepted = await call_local_tool(
            "submit_action",
            {
                "game_id": game_id,
                "idempotency_key": "stage-9.3-mcp-roll",
                "action": roll_action,
            },
            context=context,
        )
        idempotent_replay = await call_local_tool(
            "submit_action",
            {
                "game_id": game_id,
                "idempotency_key": "stage-9.3-mcp-roll",
                "action": roll_action,
            },
            context=context,
        )
        stale_rejected = await call_local_tool(
            "submit_action",
            {
                "game_id": game_id,
                "idempotency_key": "stage-9.3-mcp-stale",
                "action": roll_action,
            },
            context=context,
        )

        assert accepted["source_path"] == f"/games/{game_id}/actions"
        assert accepted["status_code"] == 200
        assert accepted["response"]["status"] == "accepted"
        assert idempotent_replay["response"] == accepted["response"]
        assert stale_rejected["status_code"] == 409
        assert stale_rejected["response"]["status"] == "rejected"
        assert stale_rejected["response"]["reason_code"] == "stale_action"
        assert await table_count(session_factory, game_events, game_id) == len(
            accepted["response"]["accepted_events"]
        )
        assert await table_count(session_factory, rejected_actions, game_id) == 1
        assert await table_count(session_factory, action_idempotency_keys, game_id) == 2
    finally:
        await delete_game(session_factory, game_id)


@pytest.mark.asyncio
async def test_stage_9_3_mcp_server_stdio_smoke_lists_and_calls_tools(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await refresh_static_index(session_factory)

    smoke = subprocess.run(
        [sys.executable, "scripts/local_mcp_server.py", "--smoke"],
        cwd=API_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )
    smoke_payload = json.loads(smoke.stdout)

    assert smoke_payload["transport"] == "stdio"
    assert smoke_payload["local_only"] is True
    assert {tool["name"] for tool in smoke_payload["tools"]} == set(REQUIRED_LOCAL_MCP_TOOL_NAMES)

    request_lines = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "stage-9.3-test", "version": "0"},
            },
        },
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "search_rules",
                "arguments": {"query_text": "Boardwalk hotel rent", "limit": 1},
            },
        },
    ]
    env = {**os.environ, "DATABASE_URL": TEST_DATABASE_URL}
    stdio = subprocess.run(
        [sys.executable, "scripts/local_mcp_server.py"],
        input="\n".join(json.dumps(line) for line in request_lines) + "\n",
        cwd=API_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )
    responses = [json.loads(line) for line in stdio.stdout.splitlines()]
    call_response = responses[2]
    call_payload = json.loads(call_response["result"]["content"][0]["text"])

    assert responses[0]["result"]["serverInfo"]["name"] == "monopoly-ai-game-local-mcp"
    assert {tool["name"] for tool in responses[1]["result"]["tools"]} == set(
        REQUIRED_LOCAL_MCP_TOOL_NAMES
    )
    assert call_payload["tool"] == "search_rules"
    assert call_payload["results"][0]["source_type"] == "rules"
    assert "Boardwalk" in call_payload["results"][0]["text"]


@pytest.mark.asyncio
async def test_stage_9_3_validate_deal_draft_reports_validation_without_mutation(
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
        result = await call_local_tool(
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
        after_counts = await mutation_counts(session_factory, game_id)

        assert result["valid"] is False
        assert result["reason_code"] == "invalid_structured_deal"
        assert result["validation_errors"]
        assert any(
            error["field"] == "terms.0.to_player_id" or "to_player_id" in error["field"]
            for error in result["validation_errors"]
        )
        assert result["created_deal"] is False
        assert result["created_contract"] is False
        assert before_counts == after_counts
    finally:
        await delete_game(session_factory, game_id)


def test_stage_9_3_mcp_tools_are_local_only_and_documented() -> None:
    docs = DOCS_PATH.read_text(encoding="utf-8")
    tool_payloads = list_local_tools()
    serialized_schemas = json.dumps(
        {tool["name"]: tool["inputSchema"] for tool in tool_payloads},
        sort_keys=True,
    )

    assert "stdio" in docs
    assert "local-only" in docs
    assert "validation boundary" in docs
    assert "/games/{game_id}/actions" in docs
    assert "Idempotency-Key" in docs
    for tool_name in REQUIRED_LOCAL_MCP_TOOL_NAMES:
        assert tool_name in docs

    assert "remote" not in serialized_schemas.lower()
    assert "url" not in serialized_schemas.lower()
    assert "host" not in serialized_schemas.lower()
    assert all(tool["local_only"] is True for tool in tool_payloads)


async def create_game(
    client: httpx.AsyncClient,
    *,
    player_kinds: tuple[str, str],
) -> dict[str, Any]:
    response = await client.post(
        "/games",
        json={
            "seed": "stage-9.3-local-mcp",
            "players": [
                {"name": "Ada", "kind": player_kinds[0]},
                {"name": "Grace", "kind": player_kinds[1]},
            ],
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return body if isinstance(body, dict) else {}


async def insert_memory_and_contract_fixture(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    game_id: str,
    player_id: str,
    contract_id: str,
) -> None:
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                ai_memory_entries.insert().values(
                    game_id=UUID(game_id),
                    player_id=UUID(player_id),
                    ai_profile_id=None,
                    source_decision_id=None,
                    source_event_id=None,
                    source_negotiation_message_id=None,
                    superseded_by_memory_id=None,
                    category="strategic_belief",
                    visibility="private",
                    content="stage93 private Boardwalk reminder for the acting AI player.",
                    importance=8,
                    metadata_blob={"fixture": "stage-9.3"},
                )
            )
            await session.execute(
                contracts.insert().values(
                    id=UUID(contract_id),
                    game_id=UUID(game_id),
                    deal_id=None,
                    effective_event_id=None,
                    status="active",
                    terms={"fixture": "stage-9.3"},
                )
            )

    async with session_factory() as session:
        await refresh_rag_index_entries(session, game_id=UUID(game_id))
        await session.commit()


async def refresh_static_index(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        await refresh_rag_index_entries(session)
        await session.commit()


async def delete_game(session_factory: async_sessionmaker[AsyncSession], game_id: str) -> None:
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
        "negotiations": await table_count(session_factory, negotiations, game_id),
    }


def retrieval_records_table_name() -> sa.Table:
    return metadata.tables["retrieval_records"]
