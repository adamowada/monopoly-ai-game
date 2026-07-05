"""stage_9_2_retrieval_implementation tests for local Postgres RAG."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.ai.context_pack import build_ai_context_pack_from_db
from app.db.metadata import (
    ai_decisions,
    ai_memory_entries,
    ai_profiles,
    games,
    metadata,
    negotiation_messages,
    negotiations,
    players,
    rag_index_entries,
    retrieval_records,
)
from app.rag.retrieval import refresh_rag_index_entries, search_retrieval
from app.rules.state import GameState, PlayerSetup, create_initial_game_state


TEST_DATABASE_URL = os.getenv(
    "MONOPOLY_TEST_DATABASE_URL",
    "postgresql+asyncpg://monopoly:monopoly@127.0.0.1:5432/monopoly_ai_game",
)

GAME_ID = UUID("00000000-0000-0000-0000-000000009201")
AI_PLAYER_ID = UUID("00000000-0000-0000-0000-000000009202")
OTHER_PLAYER_ID = UUID("00000000-0000-0000-0000-000000009203")
AI_PROFILE_ID = UUID("00000000-0000-0000-0000-000000009204")
OWN_MEMORY_DECISION_ID = UUID("00000000-0000-0000-0000-000000009205")
OTHER_MEMORY_DECISION_ID = UUID("00000000-0000-0000-0000-000000009206")
OWN_PRIVATE_MEMORY_ID = UUID("00000000-0000-0000-0000-000000009207")
OTHER_PRIVATE_MEMORY_ID = UUID("00000000-0000-0000-0000-000000009208")
TABLE_MEMORY_ID = UUID("00000000-0000-0000-0000-000000009209")
NEGOTIATION_ID = UUID("00000000-0000-0000-0000-00000000920a")
NEGOTIATION_MESSAGE_ID = UUID("00000000-0000-0000-0000-00000000920b")
SECOND_GAME_ID = UUID("00000000-0000-0000-0000-00000000920c")


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


def _state() -> GameState:
    return create_initial_game_state(
        seed="phase-9-stage-9.2-retrieval",
        game_id=str(GAME_ID),
        players=(
            PlayerSetup(id=str(AI_PLAYER_ID), name="Grace", kind="ai"),
            PlayerSetup(id=str(OTHER_PLAYER_ID), name="Ada", kind="ai"),
        ),
    )


@pytest.mark.asyncio
async def test_stage_9_2_retrieval_search_combines_postgres_fts_and_pgvector(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    state = _state()
    await _insert_retrieval_fixture(session_factory, state)
    try:
        async with session_factory() as session:
            await refresh_rag_index_entries(session, game_id=GAME_ID)
            await session.commit()

            results = await search_retrieval(
                session,
                query_text="Boardwalk hotel rent",
                game_id=GAME_ID,
                player_id=AI_PLAYER_ID,
                source_types=("rules",),
                phase=state.turn.phase.value,
                limit=5,
                audit=False,
            )
            indexed = await _fetch_index_row(session, source_type="rules", source_id="property_boardwalk")

        assert results
        top = results[0]
        assert top.source_type == "rules"
        assert top.source_id == "property_boardwalk"
        assert top.rank == 1
        assert top.fts_rank > 0
        assert top.vector_similarity > 0
        assert top.score == pytest.approx(top.ranking["combined_score"])
        assert top.ranking["fts_rank"] == pytest.approx(top.fts_rank)
        assert top.ranking["vector_similarity"] == pytest.approx(top.vector_similarity)
        assert top.ranking["score_formula"] == "0.65 * fts_rank + 0.35 * vector_similarity"
        assert "Boardwalk" in top.text
        assert "hotel rent 2000" in top.text
        assert indexed["search_vector"] is not None
        assert indexed["embedding"] is not None
    finally:
        await _delete_game(session_factory)


@pytest.mark.asyncio
async def test_stage_9_2_context_pack_includes_retrieved_rules_and_memories(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    state = _state()
    await _insert_retrieval_fixture(session_factory, state)
    query_text = "Boardwalk hotel rent dark-blue monopoly plan"
    try:
        async with session_factory() as session:
            pack = await build_ai_context_pack_from_db(
                session,
                state=state,
                game_id=GAME_ID,
                player_id=AI_PLAYER_ID,
                decision_type="action_decision",
                caller_request_context={"retrieval_query": query_text},
                max_memory_snippets=2,
                audit_retrieval=True,
            )

        rules_text = json.dumps(pack["rules"]["snippets"], sort_keys=True)
        memory_text = json.dumps(pack["memory"]["snippets"], sort_keys=True)

        assert "property_boardwalk" in rules_text
        assert "hotel rent 2000" in rules_text
        assert "rag_retrieval" in rules_text
        assert "Protect the dark-blue Boardwalk hotel plan." in memory_text
        assert str(OWN_PRIVATE_MEMORY_ID) in memory_text
        assert "Other AI private Boardwalk leak." not in memory_text

        records = await _fetch_retrieval_records(session_factory)
        assert {record["source_type"] for record in records} >= {"rules", "ai_memory"}
        assert all(record["query_text"] == query_text for record in records)
        assert all(record["rank"] is not None for record in records)
        assert all(record["score"] is not None for record in records)
        assert any(record["memory_entry_id"] == OWN_PRIVATE_MEMORY_ID for record in records)
        assert any(
            record["retrieved_context"]["reason"] == "rag_retrieval_ranked_match"
            and "ranking" in record["retrieved_context"]
            for record in records
        )
    finally:
        await _delete_game(session_factory)


@pytest.mark.asyncio
async def test_stage_9_2_retrieval_filters_private_memory_and_persists_audit_records(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    state = _state()
    await _insert_retrieval_fixture(session_factory, state)
    try:
        async with session_factory() as session:
            await refresh_rag_index_entries(session, game_id=GAME_ID)
            await session.commit()

            visible_results = await search_retrieval(
                session,
                query_text="Boardwalk memory table-visible private valuation",
                game_id=GAME_ID,
                player_id=AI_PLAYER_ID,
                source_types=("ai_memory",),
                phase=state.turn.phase.value,
                limit=5,
                query_context={"fixture": "private-memory-filter"},
                audit=True,
            )
            invisible_results = await search_retrieval(
                session,
                query_text="cyanotypeleakmarker",
                game_id=GAME_ID,
                player_id=AI_PLAYER_ID,
                source_types=("ai_memory",),
                phase=state.turn.phase.value,
                limit=5,
                query_context={"fixture": "private-memory-filter"},
                audit=True,
            )
            await session.commit()

        visible_source_ids = {result.source_id for result in visible_results}
        assert str(OWN_PRIVATE_MEMORY_ID) in visible_source_ids
        assert str(TABLE_MEMORY_ID) in visible_source_ids
        assert str(OTHER_PRIVATE_MEMORY_ID) not in visible_source_ids
        assert invisible_results == []

        records = await _fetch_retrieval_records(session_factory)
        memory_records = [record for record in records if record["source_type"] == "ai_memory"]
        assert len(memory_records) == len(visible_results)
        assert {record["memory_entry_id"] for record in memory_records} == {
            OWN_PRIVATE_MEMORY_ID,
            TABLE_MEMORY_ID,
        }
        assert all(record["query_context"]["fixture"] == "private-memory-filter" for record in memory_records)
        assert all(record["retrieved_context"]["visibility_allowed"] is True for record in memory_records)

        async with session_factory() as session:
            with pytest.raises(ValueError, match="query_text"):
                await search_retrieval(
                    session,
                    query_text=" ",
                    game_id=GAME_ID,
                    player_id=AI_PLAYER_ID,
                )
            with pytest.raises(ValueError, match="limit"):
                await search_retrieval(
                    session,
                    query_text="Boardwalk",
                    game_id=GAME_ID,
                    player_id=AI_PLAYER_ID,
                    limit=0,
                )
            with pytest.raises(ValueError, match="source_types"):
                await search_retrieval(
                    session,
                    query_text="Boardwalk",
                    game_id=GAME_ID,
                    player_id=AI_PLAYER_ID,
                    source_types=("not_a_source",),
                )
    finally:
        await _delete_game(session_factory)


@pytest.mark.asyncio
async def test_stage_9_2_retrieval_requires_player_for_negotiation_history_visibility(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    state = _state()
    await _insert_retrieval_fixture(session_factory, state)
    await _insert_negotiation_visibility_fixture(session_factory)
    try:
        async with session_factory() as session:
            await refresh_rag_index_entries(session, game_id=GAME_ID)
            await session.commit()

            player_scoped_results = await search_retrieval(
                session,
                query_text="stage92 visibility marker rent share",
                game_id=GAME_ID,
                player_id=AI_PLAYER_ID,
                source_types=("negotiation_history",),
                phase=state.turn.phase.value,
                limit=5,
                audit=True,
            )
            playerless_results = await search_retrieval(
                session,
                query_text="stage92 visibility marker rent share",
                game_id=GAME_ID,
                player_id=None,
                source_types=("negotiation_history",),
                phase=state.turn.phase.value,
                limit=5,
                audit=True,
            )
            await session.commit()

        assert str(NEGOTIATION_MESSAGE_ID) in {
            result.source_id for result in player_scoped_results
        }
        assert playerless_results == []

        records = await _fetch_retrieval_records(session_factory)
        negotiation_records = [
            record for record in records if record["source_type"] == "negotiation_history"
        ]
        assert len(negotiation_records) == len(player_scoped_results)
        assert all(record["player_id"] == AI_PLAYER_ID for record in negotiation_records)
    finally:
        await _delete_game(session_factory)


@pytest.mark.asyncio
async def test_stage_9_2_refresh_does_not_duplicate_global_static_documents(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    state = _state()
    await _insert_retrieval_fixture(session_factory, state)
    try:
        async with session_factory() as session:
            await session.execute(rag_index_entries.delete())
            await refresh_rag_index_entries(session, game_id=GAME_ID)
            await refresh_rag_index_entries(session, game_id=SECOND_GAME_ID)
            await refresh_rag_index_entries(session, game_id=GAME_ID)
            await session.commit()

            duplicate_rows_result = await session.execute(
                sa.select(
                    rag_index_entries.c.document_id,
                    sa.func.count(rag_index_entries.c.id).label("row_count"),
                )
                .where(
                    rag_index_entries.c.game_id.is_(None),
                    rag_index_entries.c.source_type.in_(
                        ("rules", "house_rules", "contract_examples")
                    ),
                )
                .group_by(rag_index_entries.c.document_id)
                .having(sa.func.count(rag_index_entries.c.id) > 1)
            )
            boardwalk_result = await session.execute(
                sa.select(rag_index_entries.c.index_key, rag_index_entries.c.game_id)
                .where(
                    rag_index_entries.c.source_type == "rules",
                    rag_index_entries.c.source_id == "property_boardwalk",
                )
                .order_by(rag_index_entries.c.index_key)
            )
            search_results = await search_retrieval(
                session,
                query_text="Boardwalk hotel rent",
                game_id=SECOND_GAME_ID,
                player_id=AI_PLAYER_ID,
                source_types=("rules",),
                phase=state.turn.phase.value,
                limit=5,
                audit=False,
            )

        assert duplicate_rows_result.all() == []
        boardwalk_rows = boardwalk_result.all()
        assert len(boardwalk_rows) == 1
        assert boardwalk_rows[0].index_key.startswith("static:")
        assert boardwalk_rows[0].game_id is None
        assert search_results
        assert search_results[0].source_id == "property_boardwalk"
    finally:
        await _delete_game(session_factory)


async def _insert_retrieval_fixture(
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
                    persona_name="Retrieval Fixture",
                    strategy_profile={"monopoly_focus": 0.8},
                    persona_summary={"summary": "Protects dark-blue opportunities."},
                )
            )
            await _insert_memory_rows(session)


async def _insert_memory_rows(session: AsyncSession) -> None:
    now = datetime(2026, 7, 5, 14, 0, tzinfo=UTC)
    for decision_id, player_id in (
        (OWN_MEMORY_DECISION_ID, AI_PLAYER_ID),
        (OTHER_MEMORY_DECISION_ID, OTHER_PLAYER_ID),
    ):
        await session.execute(
            ai_decisions.insert().values(
                id=decision_id,
                game_id=GAME_ID,
                player_id=player_id,
                ai_profile_id=AI_PROFILE_ID if player_id == AI_PLAYER_ID else None,
                decision_type="memory_update",
                status="validated",
                phase="START_TURN",
                state_hash="stage-9-2-retrieval-state",
                prompt_context_hash=f"stage-9-2-{decision_id}",
                prompt_context={"fixture": "stage-9-2"},
                raw_output="{}",
                parsed_output={"memory_updates": []},
                validation_result={"status": "valid"},
                created_at=now,
            )
        )

    rows: Sequence[dict[str, Any]] = (
        {
            "id": OWN_PRIVATE_MEMORY_ID,
            "game_id": GAME_ID,
            "player_id": AI_PLAYER_ID,
            "ai_profile_id": AI_PROFILE_ID,
            "source_decision_id": OWN_MEMORY_DECISION_ID,
            "category": "strategic_belief",
            "visibility": "private",
            "content": "Protect the dark-blue Boardwalk hotel plan.",
            "importance": 9,
            "metadata_blob": {"fixture": "stage-9-2"},
            "created_at": now,
            "updated_at": now,
        },
        {
            "id": OTHER_PRIVATE_MEMORY_ID,
            "game_id": GAME_ID,
            "player_id": OTHER_PLAYER_ID,
            "ai_profile_id": None,
            "source_decision_id": OTHER_MEMORY_DECISION_ID,
            "category": "strategic_belief",
            "visibility": "private",
            "content": "Other AI private Boardwalk leak with cyanotypeleakmarker.",
            "importance": 10,
            "metadata_blob": {"fixture": "stage-9-2"},
            "created_at": now,
            "updated_at": now,
        },
        {
            "id": TABLE_MEMORY_ID,
            "game_id": GAME_ID,
            "player_id": OTHER_PLAYER_ID,
            "ai_profile_id": None,
            "source_decision_id": OTHER_MEMORY_DECISION_ID,
            "category": "deal_history",
            "visibility": "table",
            "content": "Table-visible memory: Ada discussed Boardwalk rent shares.",
            "importance": 6,
            "metadata_blob": {"fixture": "stage-9-2"},
            "created_at": now,
            "updated_at": now,
        },
    )
    for row in rows:
        await session.execute(ai_memory_entries.insert().values(**row))


async def _insert_negotiation_visibility_fixture(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 7, 5, 14, 5, tzinfo=UTC)
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                negotiations.insert().values(
                    id=NEGOTIATION_ID,
                    game_id=GAME_ID,
                    opened_by_player_id=AI_PLAYER_ID,
                    status="active",
                    phase="START_TURN",
                    round_number=1,
                    context={"topic": "stage 9.2 visibility marker"},
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.execute(
                negotiation_messages.insert().values(
                    id=NEGOTIATION_MESSAGE_ID,
                    game_id=GAME_ID,
                    negotiation_id=NEGOTIATION_ID,
                    sender_player_id=AI_PLAYER_ID,
                    recipient_player_id=OTHER_PLAYER_ID,
                    message_type="freeform_message",
                    body="stage92 visibility marker rent share private negotiation",
                    payload={"fixture": "stage-9-2-negotiation-visibility"},
                    created_at=now,
                )
            )


async def _fetch_index_row(
    session: AsyncSession,
    *,
    source_type: str,
    source_id: str,
) -> dict[str, Any]:
    result = await session.execute(
        sa.select(rag_index_entries)
        .where(
            rag_index_entries.c.source_type == source_type,
            rag_index_entries.c.source_id == source_id,
        )
        .limit(1)
    )
    row = result.mappings().one()
    return dict(row)


async def _fetch_retrieval_records(
    session_factory: async_sessionmaker[AsyncSession],
) -> list[dict[str, Any]]:
    async with session_factory() as session:
        result = await session.execute(
            sa.select(retrieval_records)
            .where(retrieval_records.c.game_id == GAME_ID)
            .order_by(retrieval_records.c.created_at, retrieval_records.c.id)
        )
        return [dict(row) for row in result.mappings().all()]


async def _delete_game(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        async with session.begin():
            await session.execute(games.delete().where(games.c.id == GAME_ID))
