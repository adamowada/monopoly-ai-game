from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from pathlib import PureWindowsPath
from typing import Any, cast
from uuid import UUID

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.db.metadata import (
    ai_decisions,
    ai_memory_entries,
    ai_profiles,
    games,
    metadata as db_metadata,
    negotiation_messages,
    negotiations,
    players,
)
from app.rag.corpus import (
    CorpusDocument,
    build_ai_memory_corpus,
    build_contract_example_corpus,
    build_negotiation_history_corpus,
    build_past_decision_corpus,
    build_rules_corpus,
    build_static_local_corpus,
    load_ai_memory_corpus_from_db,
    load_negotiation_history_corpus_from_db,
)
from app.rag.lexical import search_corpus
from app.rules.financial_instruments import combination_deal
from app.rules.static_data import load_classic_monopoly_data


REPO_ROOT = Path(__file__).resolve().parents[3]
CONTENT_RULES = REPO_ROOT / "content" / "rules"
TEST_DATABASE_URL = os.getenv(
    "MONOPOLY_TEST_DATABASE_URL",
    "postgresql+asyncpg://monopoly:monopoly@127.0.0.1:5432/monopoly_ai_game",
)


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    async with engine.begin() as connection:
        await connection.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        await connection.run_sync(db_metadata.create_all)

    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=engine, expire_on_commit=False)


def test_stage_9_1_local_retrieval_corpus_static_sources_include_required_types() -> None:
    documents = build_static_local_corpus(content_rules_dir=CONTENT_RULES)

    assert documents
    assert {"rules", "house_rules", "contract_examples"} <= {
        document.source_type for document in documents
    }
    assert all(document.document_id for document in documents)
    assert all(document.text.strip() for document in documents)


def test_stage_9_1_local_retrieval_corpus_finds_classic_boardwalk_rent_data() -> None:
    documents = build_rules_corpus(CONTENT_RULES / "classic_monopoly.json")

    results = search_corpus(documents, "Boardwalk hotel rent", limit=3)

    assert results
    top = results[0].document
    assert top.source_type == "rules"
    assert top.source_id == "property_boardwalk"
    assert "Boardwalk" in top.text
    assert "hotel rent 2000" in top.text


def test_stage_9_1_local_retrieval_corpus_finds_no_fallback_ai_deviation() -> None:
    documents = build_static_local_corpus(content_rules_dir=CONTENT_RULES)

    results = search_corpus(documents, "no fallback AI deviation substitute move", limit=5)

    assert results
    assert any(
        result.document.source_type == "house_rules"
        and result.document.source_id == "no_fallback_ai_decisions"
        and "No fallback" in result.document.text
        for result in results
    )


def test_stage_9_1_local_retrieval_corpus_finds_rent_share_contract_example() -> None:
    documents = build_contract_example_corpus(CONTENT_RULES / "contract_examples.json")

    results = search_corpus(documents, "rent-share Boardwalk contract example", limit=3)

    assert results
    top = results[0].document
    assert top.source_type == "contract_examples"
    assert top.source_id == "rent_share_boardwalk_example"
    assert "rent-share" in top.text
    assert "Boardwalk" in top.text


def test_stage_9_1_contract_examples_validate_with_backend_financial_instrument_validator() -> None:
    data = json.loads((CONTENT_RULES / "contract_examples.json").read_text(encoding="utf-8"))
    property_ids = {property_data.id for property_data in load_classic_monopoly_data().properties}
    failures: list[str] = []

    for example in data["examples"]:
        example_id = str(example.get("id", "<missing-id>"))
        party_aliases = example.get("parties")
        instruments = example.get("instruments")
        assert isinstance(party_aliases, list), f"{example_id} must define a party list"
        assert isinstance(instruments, list), f"{example_id} must define an instrument list"

        party_id_by_alias = {
            str(alias): f"00000000-0000-4000-8000-{index + 1:012d}"
            for index, alias in enumerate(party_aliases)
        }
        validator_payloads: list[Mapping[str, Any]] = []
        for index, instrument in enumerate(instruments):
            assert isinstance(instrument, dict), (
                f"{example_id} instrument {index} must be an object"
            )
            validator_payloads.append(
                cast(
                    Mapping[str, Any],
                    _resolve_contract_example_party_aliases(instrument, party_id_by_alias),
                )
            )
        _, errors = combination_deal(
            validator_payloads,
            player_ids=list(party_id_by_alias.values()),
            property_ids=property_ids,
            field=f"examples.{example_id}.instruments",
        )
        failures.extend(
            f"{example_id}: {error.field}: {error.message}"
            for error in errors
        )

    assert failures == []


def test_stage_9_1_local_retrieval_corpus_builds_game_derived_documents_from_rows() -> None:
    now = datetime(2026, 7, 5, 12, 0, tzinfo=UTC)
    game_id = UUID("00000000-0000-0000-0000-000000009101")
    ai_player_id = UUID("00000000-0000-0000-0000-000000009102")
    human_player_id = UUID("00000000-0000-0000-0000-000000009103")
    memory_id = UUID("00000000-0000-0000-0000-000000009104")
    negotiation_id = UUID("00000000-0000-0000-0000-000000009105")
    message_id = UUID("00000000-0000-0000-0000-000000009106")
    deal_id = UUID("00000000-0000-0000-0000-000000009107")
    decision_id = UUID("00000000-0000-0000-0000-000000009108")

    memory_docs = build_ai_memory_corpus(
        [
            {
                "id": memory_id,
                "game_id": game_id,
                "player_id": ai_player_id,
                "category": "strategic_belief",
                "visibility": "private",
                "content": "Protect the dark-blue monopoly plan and preserve cash.",
                "importance": 8,
                "metadata_blob": {"evidence": "manual fixture"},
                "created_at": now,
            }
        ]
    )
    negotiation_docs = build_negotiation_history_corpus(
        negotiation_rows=[
            {
                "id": negotiation_id,
                "game_id": game_id,
                "opened_by_player_id": ai_player_id,
                "status": "active",
                "phase": "NEGOTIATION",
                "round_number": 2,
                "context": {"topic": "Boardwalk rent share"},
                "created_at": now,
            }
        ],
        message_rows=[
            {
                "id": message_id,
                "game_id": game_id,
                "negotiation_id": negotiation_id,
                "sender_player_id": ai_player_id,
                "recipient_player_id": human_player_id,
                "message_type": "offer",
                "body": "I will pay cash now for a future rent share.",
                "payload": {"tone": "firm"},
                "created_at": now,
            }
        ],
        deal_rows=[
            {
                "id": deal_id,
                "game_id": game_id,
                "negotiation_id": negotiation_id,
                "proposed_by_player_id": ai_player_id,
                "status": "proposed",
                "version": 1,
                "terms": {
                    "instruments": [
                        {
                            "kind": "rent_share",
                            "property_id": "property_boardwalk",
                            "share_percent": 25,
                        }
                    ]
                },
                "created_at": now,
            }
        ],
    )
    decision_docs = build_past_decision_corpus(
        [
            {
                "id": decision_id,
                "game_id": game_id,
                "player_id": ai_player_id,
                "decision_type": "action_decision",
                "status": "accepted",
                "phase": "ROLL_REQUIRED",
                "state_hash": "state-hash",
                "prompt_context_hash": "prompt-hash",
                "raw_output": '{"decision": "roll"}',
                "parsed_output": {"kind": "action", "action": {"action_type": "ROLL_DICE"}},
                "validation_result": {"status": "accepted"},
                "created_at": now,
            }
        ]
    )

    assert _only(memory_docs).source_type == "ai_memory"
    assert "dark-blue monopoly plan" in _only(memory_docs).text
    assert {document.source_type for document in negotiation_docs} == {"negotiation_history"}
    assert any("future rent share" in document.text for document in negotiation_docs)
    assert any("property_boardwalk" in document.text for document in negotiation_docs)
    assert _only(decision_docs).source_type == "past_decision"
    assert "ROLL_DICE" in _only(decision_docs).text


@pytest.mark.asyncio
async def test_stage_9_1_rag_visibility_filters_db_memory_corpus_preserves_context_rules(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    game_id = UUID("00000000-0000-0000-0000-000000009141")
    actor_id = UUID("00000000-0000-0000-0000-000000009142")
    other_id = UUID("00000000-0000-0000-0000-000000009143")
    profile_id = UUID("00000000-0000-0000-0000-000000009144")
    actor_private_id = UUID("00000000-0000-0000-0000-000000009145")
    other_private_id = UUID("00000000-0000-0000-0000-000000009146")
    public_id = UUID("00000000-0000-0000-0000-000000009147")
    table_id = UUID("00000000-0000-0000-0000-000000009148")
    audit_id = UUID("00000000-0000-0000-0000-000000009149")
    rejected_id = UUID("00000000-0000-0000-0000-00000000914a")
    invalid_id = UUID("00000000-0000-0000-0000-00000000914b")
    accepted_decision_id = UUID("00000000-0000-0000-0000-00000000914c")
    rejected_decision_id = UUID("00000000-0000-0000-0000-00000000914d")
    invalid_decision_id = UUID("00000000-0000-0000-0000-00000000914e")

    await _insert_rag_visibility_game(
        session_factory,
        game_id=game_id,
        player_ids=(actor_id, other_id),
        ai_profile_id=profile_id,
        ai_profile_player_id=actor_id,
    )
    try:
        async with session_factory() as session:
            await _insert_rag_memory_visibility_rows(
                session,
                game_id=game_id,
                actor_id=actor_id,
                other_id=other_id,
                profile_id=profile_id,
                accepted_decision_id=accepted_decision_id,
                rejected_decision_id=rejected_decision_id,
                invalid_decision_id=invalid_decision_id,
                actor_private_id=actor_private_id,
                other_private_id=other_private_id,
                public_id=public_id,
                table_id=table_id,
                audit_id=audit_id,
                rejected_id=rejected_id,
                invalid_id=invalid_id,
            )
            await session.commit()

            actor_documents = await load_ai_memory_corpus_from_db(
                session,
                game_id=game_id,
                player_id=actor_id,
            )
            global_documents = await load_ai_memory_corpus_from_db(session, game_id=game_id)

        assert _source_ids(actor_documents) == {
            str(actor_private_id),
            str(public_id),
            str(table_id),
            str(audit_id),
        }
        assert _source_ids(global_documents) == {str(public_id), str(table_id), str(audit_id)}

        actor_text = _corpus_text(actor_documents)
        assert "actor-owned private memory" in actor_text
        assert "public memory from another player" in actor_text
        assert "table memory from another player" in actor_text
        assert "audit memory from another player" in actor_text
        assert "other-player private memory leak" not in actor_text
        assert "legacy rejected public memory leak" not in actor_text
        assert "legacy invalid public memory leak" not in actor_text

        global_text = _corpus_text(global_documents)
        assert "actor-owned private memory" not in global_text
        assert "other-player private memory leak" not in global_text
    finally:
        await _delete_rag_visibility_game(session_factory, game_id)


@pytest.mark.asyncio
async def test_stage_9_1_rag_visibility_filters_db_negotiation_history_corpus_filters_actor_messages(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    game_id = UUID("00000000-0000-0000-0000-000000009151")
    actor_id = UUID("00000000-0000-0000-0000-000000009152")
    other_id = UUID("00000000-0000-0000-0000-000000009153")
    third_id = UUID("00000000-0000-0000-0000-000000009154")
    profile_id = UUID("00000000-0000-0000-0000-000000009155")
    negotiation_id = UUID("00000000-0000-0000-0000-000000009156")
    broadcast_id = UUID("00000000-0000-0000-0000-000000009157")
    actor_sent_id = UUID("00000000-0000-0000-0000-000000009158")
    actor_received_id = UUID("00000000-0000-0000-0000-000000009159")
    other_direct_id = UUID("00000000-0000-0000-0000-00000000915a")

    await _insert_rag_visibility_game(
        session_factory,
        game_id=game_id,
        player_ids=(actor_id, other_id, third_id),
        ai_profile_id=profile_id,
        ai_profile_player_id=actor_id,
    )
    try:
        async with session_factory() as session:
            await _insert_rag_negotiation_visibility_rows(
                session,
                game_id=game_id,
                negotiation_id=negotiation_id,
                actor_id=actor_id,
                other_id=other_id,
                third_id=third_id,
                broadcast_id=broadcast_id,
                actor_sent_id=actor_sent_id,
                actor_received_id=actor_received_id,
                other_direct_id=other_direct_id,
            )
            await session.commit()

            documents = await load_negotiation_history_corpus_from_db(
                session,
                game_id=game_id,
                player_id=actor_id,
            )

        message_ids = _source_ids_by_row_type(documents, "negotiation_message")
        assert message_ids == {str(broadcast_id), str(actor_sent_id), str(actor_received_id)}

        text = _corpus_text(documents)
        assert "broadcast table message" in text
        assert "actor sent direct message" in text
        assert "actor received direct message" in text
        assert "other participants direct message leak" not in text
    finally:
        await _delete_rag_visibility_game(session_factory, game_id)


def test_stage_9_1_local_retrieval_corpus_jsonl_index_command_is_deterministic(
    tmp_path: Path,
) -> None:
    first_output = tmp_path / "corpus_first.jsonl"
    second_output = tmp_path / "corpus_second.jsonl"
    script = REPO_ROOT / "services" / "api" / "scripts" / "build_rag_index.py"

    for output in (first_output, second_output):
        completed = subprocess.run(
            [sys.executable, str(script), "--output", str(output)],
            cwd=REPO_ROOT,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        assert completed.returncode == 0, completed.stdout

    first_text = first_output.read_text(encoding="utf-8")
    second_text = second_output.read_text(encoding="utf-8")
    assert first_text == second_text

    rows = [json.loads(line) for line in first_text.splitlines()]
    assert rows
    assert {"rules", "house_rules", "contract_examples"} <= {
        row["source_type"] for row in rows
    }
    assert all(row["document_id"].strip() for row in rows)
    assert all(row["text"].strip() for row in rows)
    assert all(isinstance(row["metadata"], dict) for row in rows)


def test_stage_9_1_index_paths_repo_relative(tmp_path: Path) -> None:
    expected_files = {
        "content/rules/classic_monopoly.json",
        "content/rules/contract_examples.json",
        "content/rules/house_rules_and_deviations.json",
    }

    documents = build_static_local_corpus(content_rules_dir=CONTENT_RULES)
    _assert_static_metadata_files_are_repo_relative(
        [document.to_json_dict() for document in documents],
        expected_files=expected_files,
    )

    output = tmp_path / "corpus.jsonl"
    script = REPO_ROOT / "services" / "api" / "scripts" / "build_rag_index.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--output", str(output)],
        cwd=REPO_ROOT,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    assert completed.returncode == 0, completed.stdout

    jsonl_text = output.read_text(encoding="utf-8")
    assert str(REPO_ROOT) not in jsonl_text
    assert str(Path.home()) not in jsonl_text

    rows = [json.loads(line) for line in jsonl_text.splitlines()]
    _assert_static_metadata_files_are_repo_relative(rows, expected_files=expected_files)


def _only(documents: list[CorpusDocument]) -> CorpusDocument:
    assert len(documents) == 1
    return documents[0]


def _source_ids(documents: list[CorpusDocument]) -> set[str]:
    return {document.source_id for document in documents}


def _source_ids_by_row_type(documents: list[CorpusDocument], row_type: str) -> set[str]:
    return {
        document.source_id
        for document in documents
        if document.metadata.get("row_type") == row_type
    }


def _corpus_text(documents: list[CorpusDocument]) -> str:
    return json.dumps([document.to_json_dict() for document in documents], sort_keys=True)


def _assert_static_metadata_files_are_repo_relative(
    rows: list[Mapping[str, Any]],
    *,
    expected_files: set[str],
) -> None:
    file_values = {
        metadata["file"]
        for row in rows
        if row["source_type"] in {"rules", "house_rules", "contract_examples"}
        for metadata in [row["metadata"]]
        if isinstance(metadata, dict) and "file" in metadata
    }

    assert file_values == expected_files
    for file_value in file_values:
        assert isinstance(file_value, str)
        assert not file_value.startswith("/")
        assert "\\" not in file_value
        assert PureWindowsPath(file_value).drive == ""
        assert ".." not in Path(file_value).parts


def _resolve_contract_example_party_aliases(
    value: object,
    party_id_by_alias: Mapping[str, str],
) -> object:
    if isinstance(value, str):
        return party_id_by_alias.get(value, value)
    if isinstance(value, list):
        return [
            _resolve_contract_example_party_aliases(item, party_id_by_alias)
            for item in value
        ]
    if isinstance(value, dict):
        return {
            key: _resolve_contract_example_party_aliases(item, party_id_by_alias)
            for key, item in value.items()
        }
    return value


async def _insert_rag_visibility_game(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    game_id: UUID,
    player_ids: tuple[UUID, ...],
    ai_profile_id: UUID,
    ai_profile_player_id: UUID,
) -> None:
    async with session_factory() as session:
        async with session.begin():
            await session.execute(games.delete().where(games.c.id == game_id))
            await session.execute(
                games.insert().values(
                    id=game_id,
                    status="active",
                    ruleset_version="classic-v1",
                    seed="stage-9-1-rag-visibility-filters",
                    current_phase="START_TURN",
                    settings={},
                    initial_state={},
                )
            )
            for seat_order, player_id in enumerate(player_ids):
                await session.execute(
                    players.insert().values(
                        id=player_id,
                        game_id=game_id,
                        seat_order=seat_order,
                        name=f"Visibility Player {seat_order}",
                        controller_type="ai" if player_id == ai_profile_player_id else "human",
                        state={},
                    )
                )
            await session.execute(
                ai_profiles.insert().values(
                    id=ai_profile_id,
                    game_id=game_id,
                    player_id=ai_profile_player_id,
                    persona_name="Visibility Fixture",
                    strategy_profile={"fixture": "stage-9-1"},
                    persona_summary={"summary": "Visibility regression actor."},
                )
            )


async def _insert_rag_memory_visibility_rows(
    session: AsyncSession,
    *,
    game_id: UUID,
    actor_id: UUID,
    other_id: UUID,
    profile_id: UUID,
    accepted_decision_id: UUID,
    rejected_decision_id: UUID,
    invalid_decision_id: UUID,
    actor_private_id: UUID,
    other_private_id: UUID,
    public_id: UUID,
    table_id: UUID,
    audit_id: UUID,
    rejected_id: UUID,
    invalid_id: UUID,
) -> None:
    now = datetime(2026, 7, 5, 12, 0, tzinfo=UTC)
    for decision_id, player_id, status in (
        (accepted_decision_id, actor_id, "validated"),
        (rejected_decision_id, actor_id, "rejected"),
        (invalid_decision_id, actor_id, "invalid"),
    ):
        await session.execute(
            ai_decisions.insert().values(
                id=decision_id,
                game_id=game_id,
                player_id=player_id,
                ai_profile_id=profile_id,
                decision_type="memory_update",
                status=status,
                phase="START_TURN",
                state_hash=f"stage-9-1-{status}",
                prompt_context_hash=f"stage-9-1-{status}-prompt",
                prompt_context={"fixture": "stage-9-1-rag-visibility-filters"},
                raw_output="{}",
                parsed_output={"memory_updates": []},
                validation_result={"status": status},
                created_at=now,
            )
        )

    rows = [
        (actor_private_id, actor_id, profile_id, accepted_decision_id, "private", "actor-owned private memory"),
        (other_private_id, other_id, None, accepted_decision_id, "private", "other-player private memory leak"),
        (public_id, other_id, None, accepted_decision_id, "public", "public memory from another player"),
        (table_id, other_id, None, accepted_decision_id, "table", "table memory from another player"),
        (audit_id, other_id, None, accepted_decision_id, "audit", "audit memory from another player"),
        (rejected_id, other_id, None, rejected_decision_id, "public", "legacy rejected public memory leak"),
        (invalid_id, other_id, None, invalid_decision_id, "public", "legacy invalid public memory leak"),
    ]
    for memory_id, player_id, ai_profile_id, source_decision_id, visibility, content in rows:
        await session.execute(
            ai_memory_entries.insert().values(
                id=memory_id,
                game_id=game_id,
                player_id=player_id,
                ai_profile_id=ai_profile_id,
                source_decision_id=source_decision_id,
                category="strategic_belief",
                visibility=visibility,
                content=content,
                importance=8,
                metadata_blob={"fixture": "stage-9-1-rag-visibility-filters"},
                created_at=now,
                updated_at=now,
            )
        )


async def _insert_rag_negotiation_visibility_rows(
    session: AsyncSession,
    *,
    game_id: UUID,
    negotiation_id: UUID,
    actor_id: UUID,
    other_id: UUID,
    third_id: UUID,
    broadcast_id: UUID,
    actor_sent_id: UUID,
    actor_received_id: UUID,
    other_direct_id: UUID,
) -> None:
    now = datetime(2026, 7, 5, 12, 0, tzinfo=UTC)
    await session.execute(
        negotiations.insert().values(
            id=negotiation_id,
            game_id=game_id,
            opened_by_player_id=actor_id,
            status="active",
            phase="START_TURN",
            round_number=1,
            context={"topic": "visibility regression"},
            created_at=now,
            updated_at=now,
        )
    )
    messages = [
        (broadcast_id, actor_id, None, "broadcast table message"),
        (actor_sent_id, actor_id, other_id, "actor sent direct message"),
        (actor_received_id, other_id, actor_id, "actor received direct message"),
        (other_direct_id, other_id, third_id, "other participants direct message leak"),
    ]
    for message_id, sender_id, recipient_id, body in messages:
        await session.execute(
            negotiation_messages.insert().values(
                id=message_id,
                game_id=game_id,
                negotiation_id=negotiation_id,
                sender_player_id=sender_id,
                recipient_player_id=recipient_id,
                message_type="freeform_message",
                body=body,
                payload={"fixture": "stage-9-1-rag-visibility-filters"},
                created_at=now,
            )
        )


async def _delete_rag_visibility_game(
    session_factory: async_sessionmaker[AsyncSession],
    game_id: UUID,
) -> None:
    async with session_factory() as session:
        async with session.begin():
            await session.execute(games.delete().where(games.c.id == game_id))
