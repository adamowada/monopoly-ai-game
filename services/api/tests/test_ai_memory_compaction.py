"""Stage 8.3 regression coverage for deterministic AI memory compaction."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.ai.context_pack import build_ai_context_pack_from_db
from app.ai.memory import (
    MEMORY_COMPACTION_REASON_ON_DEMAND,
    MEMORY_COMPACTION_REASON_SCHEDULED,
    compact_memory_for_player,
    score_memory_entry_for_context,
)
from app.ai.orchestrator import (
    CodexExecProcessResult,
    CodexExecRunner,
    CodexExecAIDecisionRequest,
    request_codex_ai_decision,
)
from app.core.config import Settings
from app.db.metadata import ai_decisions, ai_memory_entries, ai_profiles, games, metadata, players
from app.main import create_app
from app.rules.state import GameState, PlayerSetup, create_initial_game_state


TEST_DATABASE_URL = os.getenv(
    "MONOPOLY_TEST_DATABASE_URL",
    "postgresql+asyncpg://monopoly:monopoly@127.0.0.1:5432/monopoly_ai_game",
)

GAME_ID = UUID("00000000-0000-0000-0000-000000008301")
AI_PLAYER_ID = UUID("00000000-0000-0000-0000-000000008302")
OTHER_AI_PLAYER_ID = UUID("00000000-0000-0000-0000-000000008303")
HUMAN_PLAYER_ID = UUID("00000000-0000-0000-0000-000000008304")
AI_PROFILE_ID = UUID("00000000-0000-0000-0000-000000008305")
OTHER_AI_PROFILE_ID = UUID("00000000-0000-0000-0000-000000008306")


class FakeCodexRunner(CodexExecRunner):
    def __init__(self, final_output: Mapping[str, Any]) -> None:
        self.final_output = dict(final_output)
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
        output_text = json.dumps(self.final_output)
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
async def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def api_app(engine: AsyncEngine) -> AsyncIterator[FastAPI]:
    app = create_app(Settings(api_env="test", database_url=TEST_DATABASE_URL))
    app.state.database_engine = engine
    app.state.database_session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    yield app


@pytest_asyncio.fixture
async def client(api_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api_app),
        base_url="http://testserver",
    ) as client:
        yield client


def test_stage_8_3_compaction_scores_memory_deterministically() -> None:
    strategic = {
        "id": _uuid(839001),
        "category": "strategic_belief",
        "importance": 9,
        "source_decision_id": _uuid(839002),
        "source_event_id": _uuid(839003),
        "source_negotiation_message_id": None,
        "created_at": _created_at(8),
        "metadata_blob": {"source": "test"},
    }
    low_value = {
        **strategic,
        "id": _uuid(839004),
        "category": "deal_history",
        "importance": 1,
        "source_event_id": None,
        "created_at": _created_at(1),
    }

    first = score_memory_entry_for_context(strategic, recency_rank=8, total_rows=10)
    second = score_memory_entry_for_context(strategic, recency_rank=8, total_rows=10)
    low = score_memory_entry_for_context(low_value, recency_rank=1, total_rows=10)

    assert first == second
    assert first.score > low.score
    assert first.inputs["stored_importance"] == 9
    assert first.inputs["category"] == "strategic_belief"
    assert first.inputs["source_links"]["source_event_id"] is True


@pytest.mark.asyncio
async def test_stage_8_3_compaction_creates_summary_retains_raw_and_links_sources(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    state = _state()
    raw_ids = [_uuid(831000 + index) for index in range(6)]
    await _insert_game_fixture(session_factory, state)
    await _insert_memory_entries(
        session_factory,
        player_id=AI_PLAYER_ID,
        profile_id=AI_PROFILE_ID,
        memory_ids=raw_ids,
        content_prefix="low-value raw source",
        importance=1,
        category="deal_history",
    )

    try:
        async with session_factory() as session:
            async with session.begin():
                result = await compact_memory_for_player(
                    session,
                    game_id=GAME_ID,
                    player_id=AI_PLAYER_ID,
                    reason="stage_8_3_compaction_unit",
                    compaction_threshold=3,
                    target_raw_count=2,
                )

        rows = await _memory_rows(session_factory)
        summary = _single_summary(rows)
        linked_raw = [
            row for row in rows if row["superseded_by_memory_id"] == result.summary_memory_id
        ]

        assert result.summary_memory_id == summary["id"]
        assert set(raw_ids).issubset({row["id"] for row in rows})
        assert linked_raw
        assert {row["id"] for row in linked_raw} == set(result.source_memory_ids)
        assert summary["metadata_blob"]["compaction"]["source_count"] == len(linked_raw)
        assert summary["metadata_blob"]["compaction"]["source_memory_ids"] == [
            str(memory_id) for memory_id in result.source_memory_ids
        ]
        assert summary["metadata_blob"]["compaction"]["reason"] == "stage_8_3_compaction_unit"
    finally:
        await _delete_game(session_factory)


@pytest.mark.asyncio
async def test_stage_8_3_compaction_context_pack_triggers_on_demand_and_keeps_prompt_bounded(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    state = _state()
    low_value_ids = [_uuid(832000 + index) for index in range(24)]
    strategic_id = _uuid(832900)
    await _insert_game_fixture(session_factory, state)
    await _insert_memory_entries(
        session_factory,
        player_id=AI_PLAYER_ID,
        profile_id=AI_PROFILE_ID,
        memory_ids=low_value_ids,
        content_prefix="low-value historical note",
        importance=1,
        category="deal_history",
    )
    await _insert_memory_entries(
        session_factory,
        player_id=AI_PLAYER_ID,
        profile_id=AI_PROFILE_ID,
        memory_ids=[strategic_id],
        content_prefix="Protect Boardwalk monopoly plan",
        importance=10,
        category="strategic_belief",
    )

    try:
        async with session_factory() as session:
            pack = await build_ai_context_pack_from_db(
                session,
                state=state,
                game_id=GAME_ID,
                player_id=AI_PLAYER_ID,
                max_memory_snippets=6,
                memory_compaction_threshold=8,
            )

        rows = await _memory_rows(session_factory)
        summary = _single_summary(rows)
        snippets = pack["memory"]["snippets"]
        snippet_text = json.dumps(snippets, sort_keys=True)
        low_value_snippets = [
            snippet for snippet in snippets if "low-value historical note" in str(snippet["content"])
        ]

        assert summary["metadata_blob"]["compaction"]["reason"] == MEMORY_COMPACTION_REASON_ON_DEMAND
        assert len(snippets) <= 6
        assert "Compacted memory summary" in snippet_text
        assert "Protect Boardwalk monopoly plan" in snippet_text
        assert len(low_value_snippets) < len(low_value_ids)
        assert all(snippet["content"] is not None for snippet in snippets)
    finally:
        await _delete_game(session_factory)


@pytest.mark.asyncio
async def test_stage_8_3_compaction_25_decision_schedule_targets_only_that_player(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    state = _state()
    await _insert_game_fixture(session_factory, state)
    await _insert_prior_decisions(session_factory, player_id=AI_PLAYER_ID, count=24, id_offset=833000)
    await _insert_prior_decisions(
        session_factory,
        player_id=OTHER_AI_PLAYER_ID,
        count=24,
        id_offset=834000,
    )
    await _insert_memory_entries(
        session_factory,
        player_id=AI_PLAYER_ID,
        profile_id=AI_PROFILE_ID,
        memory_ids=[_uuid(835000 + index) for index in range(30)],
        content_prefix="scheduled player memory",
        importance=1,
        category="deal_history",
        source_decision_id=_uuid(833000),
    )
    await _insert_memory_entries(
        session_factory,
        player_id=OTHER_AI_PLAYER_ID,
        profile_id=OTHER_AI_PROFILE_ID,
        memory_ids=[_uuid(836000 + index) for index in range(30)],
        content_prefix="unrelated player memory",
        importance=1,
        category="deal_history",
        source_decision_id=_uuid(834000),
    )

    runner = FakeCodexRunner(_valid_memory_update_output(state, "scheduled compaction marker"))
    try:
        result = await request_codex_ai_decision(
            session_factory,
            CodexExecAIDecisionRequest(
                game_id=GAME_ID,
                player_id=AI_PLAYER_ID,
                ai_profile_id=AI_PROFILE_ID,
                decision_type="memory_update",
                phase=state.turn.phase.value,
                state_hash=state.state_hash(),
                prompt_context={"stage": "8.3", "schedule": "25_decisions"},
                timeout_seconds=7,
            ),
            runner=runner,
            schema_file=tmp_path / "schema.json",
            sandbox_dir=tmp_path / "sandbox",
            work_dir=tmp_path / "work",
        )

        rows = await _memory_rows(session_factory)
        ai_summaries = _summary_rows(rows, player_id=AI_PLAYER_ID)
        other_summaries = _summary_rows(rows, player_id=OTHER_AI_PLAYER_ID)
        other_linked_raw = [
            row
            for row in rows
            if row["player_id"] == OTHER_AI_PLAYER_ID and row["superseded_by_memory_id"] is not None
        ]

        assert result.status == "validated"
        assert len(runner.calls) == 1
        assert len(ai_summaries) == 1
        assert ai_summaries[0]["metadata_blob"]["compaction"]["reason"] == MEMORY_COMPACTION_REASON_SCHEDULED
        assert other_summaries == []
        assert other_linked_raw == []
    finally:
        await _delete_game(session_factory)


@pytest.mark.asyncio
async def test_stage_8_3_compaction_audit_metadata_reconstructs_source_memory_ids(
    session_factory: async_sessionmaker[AsyncSession],
    client: httpx.AsyncClient,
) -> None:
    state = _state()
    raw_ids = [_uuid(837000 + index) for index in range(7)]
    await _insert_game_fixture(session_factory, state)
    await _insert_memory_entries(
        session_factory,
        player_id=AI_PLAYER_ID,
        profile_id=AI_PROFILE_ID,
        memory_ids=raw_ids,
        content_prefix="audit lineage memory",
        importance=1,
        category="mistake_lesson",
    )

    try:
        async with session_factory() as session:
            async with session.begin():
                result = await compact_memory_for_player(
                    session,
                    game_id=GAME_ID,
                    player_id=AI_PLAYER_ID,
                    reason="stage_8_3_compaction_audit",
                    compaction_threshold=3,
                    target_raw_count=2,
                )

        response = await client.get(f"/games/{GAME_ID}/ai/memory")
        body = response.json()
        summary = next(
            entry
            for entry in body["memory_entries"]
            if entry["memory_entry_id"] == str(result.summary_memory_id)
        )
        linked_raw = [
            entry
            for entry in body["memory_entries"]
            if entry["superseded_by_memory_id"] == str(result.summary_memory_id)
        ]

        assert response.status_code == 200
        assert summary["source_decision_id"] is None
        assert summary["metadata"]["compaction"]["source_memory_ids"] == [
            str(memory_id) for memory_id in result.source_memory_ids
        ]
        assert summary["metadata"]["compaction"]["source_count"] == len(linked_raw)
        assert summary["metadata"]["compaction"]["scoring_inputs_by_memory_id"]
        assert {entry["memory_entry_id"] for entry in linked_raw} == {
            str(memory_id) for memory_id in result.source_memory_ids
        }
    finally:
        await _delete_game(session_factory)


def _state() -> GameState:
    return create_initial_game_state(
        seed="phase-8-stage-8.3-memory-compaction",
        game_id=str(GAME_ID),
        players=(
            PlayerSetup(id=str(AI_PLAYER_ID), name="Grace", kind="ai"),
            PlayerSetup(id=str(OTHER_AI_PLAYER_ID), name="Linus", kind="ai"),
            PlayerSetup(id=str(HUMAN_PLAYER_ID), name="Ada", kind="human"),
        ),
    )


async def _insert_game_fixture(
    session_factory: async_sessionmaker[AsyncSession],
    state: GameState,
) -> None:
    async with session_factory() as session:
        async with session.begin():
            await session.execute(games.delete().where(games.c.id == GAME_ID))
            await session.execute(
                games.insert().values(
                    id=GAME_ID,
                    status="active",
                    ruleset_version=state.ruleset_version,
                    seed=state.seed,
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
                    persona_name="Grace Compactor",
                    strategy_profile={"risk_tolerance": 0.4},
                    persona_summary={"summary": "Keeps concise strategic memory."},
                )
            )
            await session.execute(
                ai_profiles.insert().values(
                    id=OTHER_AI_PROFILE_ID,
                    game_id=GAME_ID,
                    player_id=OTHER_AI_PLAYER_ID,
                    persona_name="Linus Observer",
                    strategy_profile={"risk_tolerance": 0.6},
                    persona_summary={"summary": "Unrelated player for scoped compaction."},
                )
            )


async def _insert_prior_decisions(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    player_id: UUID,
    count: int,
    id_offset: int,
) -> None:
    profile_id = AI_PROFILE_ID if player_id == AI_PLAYER_ID else OTHER_AI_PROFILE_ID
    async with session_factory() as session:
        async with session.begin():
            for index in range(count):
                await _insert_decision_row(
                    session,
                    decision_id=_uuid(id_offset + index),
                    player_id=player_id,
                    profile_id=profile_id,
                    label=f"prior-{index}",
                )


async def _insert_memory_entries(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    player_id: UUID,
    profile_id: UUID,
    memory_ids: Sequence[UUID],
    content_prefix: str,
    importance: int,
    category: str,
    source_decision_id: UUID | None = None,
) -> None:
    async with session_factory() as session:
        async with session.begin():
            decision_id = source_decision_id or _uuid(838000 + int(str(memory_ids[0])[-3:]))
            if source_decision_id is None:
                await _insert_decision_row(
                    session,
                    decision_id=decision_id,
                    player_id=player_id,
                    profile_id=profile_id,
                    label=f"source-{memory_ids[0]}",
                )
            for index, memory_id in enumerate(memory_ids):
                await session.execute(
                    ai_memory_entries.insert().values(
                        id=memory_id,
                        game_id=GAME_ID,
                        player_id=player_id,
                        ai_profile_id=profile_id,
                        source_decision_id=decision_id,
                        category=category,
                        visibility="private",
                        content=f"{content_prefix} {index:02d}",
                        importance=importance,
                        metadata_blob={"source": "stage_8_3_compaction_test", "index": index},
                        created_at=_created_at(index),
                    )
                )


async def _insert_decision_row(
    session: AsyncSession,
    *,
    decision_id: UUID,
    player_id: UUID,
    profile_id: UUID,
    label: str,
) -> None:
    await session.execute(
        ai_decisions.insert().values(
            id=decision_id,
            game_id=GAME_ID,
            player_id=player_id,
            ai_profile_id=profile_id,
            decision_type="memory_update",
            status="validated",
            phase="START_TURN",
            state_hash=f"stage-8-3-{label}",
            prompt_context_hash=f"stage-8-3-{label}",
            prompt_context={"stage": "8.3", "label": label},
            raw_output="{}",
            parsed_output={"memory_updates": []},
            validation_result={"status": "valid"},
            created_at=_created_at(int(str(decision_id)[-3:], 16) % 1000),
        )
    )


async def _memory_rows(
    session_factory: async_sessionmaker[AsyncSession],
) -> list[dict[str, Any]]:
    async with session_factory() as session:
        result = await session.execute(
            sa.select(ai_memory_entries)
            .where(ai_memory_entries.c.game_id == GAME_ID)
            .order_by(ai_memory_entries.c.created_at, ai_memory_entries.c.id)
        )
        return [dict(row) for row in result.mappings().all()]


def _single_summary(rows: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    summaries = _summary_rows(rows)
    assert len(summaries) == 1
    return summaries[0]


def _summary_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    player_id: UUID | None = None,
) -> list[Mapping[str, Any]]:
    return [
        row
        for row in rows
        if (player_id is None or row["player_id"] == player_id)
        and isinstance(row["metadata_blob"], Mapping)
        and isinstance(row["metadata_blob"].get("compaction"), Mapping)
        and row["metadata_blob"]["compaction"].get("is_summary") is True
    ]


def _valid_memory_update_output(state: GameState, content: str) -> dict[str, Any]:
    return {
        "decision_type": "memory_update",
        "game_id": str(GAME_ID),
        "player_id": str(AI_PLAYER_ID),
        "self_dialogue": {"status": "provided", "text": "Compaction schedule test."},
        "memory_updates": [
            {
                "visibility": "private",
                "category": "deal_history",
                "importance": 1,
                "content": content,
                "metadata": {"stage": "8.3"},
            }
        ],
        "confidence": 0.7,
        "rationale": f"State {state.state_hash()} is only used for audit linkage.",
    }


async def _delete_game(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        async with session.begin():
            await session.execute(games.delete().where(games.c.id == GAME_ID))


def _created_at(index: int) -> datetime:
    return datetime(2026, 7, 5, 12, 0, tzinfo=UTC) + timedelta(minutes=index)


def _uuid(value: int) -> UUID:
    return UUID(f"00000000-0000-0000-0000-{value:012d}")
