from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
import sqlalchemy as sa

from app.db.metadata import metadata


API_ROOT = Path(__file__).resolve().parents[1]
FOUNDATION_REVISION = "0001_create_foundation_metadata"
STAGE_41_REVISION = "0002_create_domain_audit_schema"
REQUIRED_DOMAIN_TABLES = {
    "games",
    "players",
    "game_events",
    "game_snapshots",
    "rejected_actions",
    "negotiations",
    "negotiation_messages",
    "deals",
    "contracts",
    "obligations",
    "ai_profiles",
    "ai_decisions",
    "ai_self_dialogue",
    "ai_memory_entries",
    "retrieval_records",
}
REQUIRED_TABLES = {"foundation_metadata", *REQUIRED_DOMAIN_TABLES}


def index_columns(table_name: str, index_name: str) -> tuple[str, ...]:
    table = metadata.tables[table_name]
    for index in table.indexes:
        if str(index.name) == index_name:
            return tuple(str(column.name) for column in index.columns)
    raise AssertionError(f"{table_name} is missing index {index_name}")


def unique_constraint_columns(table_name: str) -> set[tuple[str, ...]]:
    table = metadata.tables[table_name]
    return {
        tuple(str(column.name) for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }


def test_alembic_head_includes_stage_41_domain_audit_schema() -> None:
    config = Config(str(API_ROOT / "alembic.ini"))
    script = ScriptDirectory.from_config(config)

    head = script.get_current_head()

    assert head == STAGE_41_REVISION
    assert head != FOUNDATION_REVISION
    revision = script.get_revision(head)
    assert revision is not None
    assert revision.down_revision == FOUNDATION_REVISION
    assert "domain and audit schema" in (revision.module.__doc__ or "")


def test_foundation_metadata_table_is_in_sqlalchemy_metadata() -> None:
    table = metadata.tables["foundation_metadata"]

    assert table.c.id.primary_key
    assert table.c.key.unique
    assert table.c.value.nullable


def test_stage_41_tables_are_in_sqlalchemy_metadata() -> None:
    assert REQUIRED_TABLES <= set(metadata.tables)


def test_accepted_events_and_rejected_audits_are_modeled_separately() -> None:
    game_events = metadata.tables["game_events"]
    rejected_actions = metadata.tables["rejected_actions"]

    assert game_events is not rejected_actions
    assert {"id", "game_id", "sequence", "actor_player_id", "event_type", "payload", "state_hash", "created_at"} <= set(
        game_events.c.keys()
    )
    assert {
        "id",
        "game_id",
        "actor_player_id",
        "action_type",
        "payload",
        "reason_code",
        "validation_errors",
        "legal_action_context",
        "phase",
        "state_hash",
        "created_at",
    } <= set(rejected_actions.c.keys())
    assert "sequence" not in rejected_actions.c
    assert "reason_code" not in game_events.c


def test_stage_41_metadata_defines_required_indexes_and_constraints() -> None:
    ai_decisions = metadata.tables["ai_decisions"]

    assert ("game_id", "sequence") in unique_constraint_columns("game_events")

    assert index_columns("game_events", "ix_game_events_game_sequence") == ("game_id", "sequence")
    assert index_columns("game_events", "ix_game_events_actor_player_id") == ("actor_player_id",)
    assert index_columns("rejected_actions", "ix_rejected_actions_game_actor_created_at") == (
        "game_id",
        "actor_player_id",
        "created_at",
    )
    assert index_columns("rejected_actions", "ix_rejected_actions_reason_code") == ("reason_code",)
    assert index_columns("negotiation_messages", "ix_negotiation_messages_negotiation_created_at") == (
        "negotiation_id",
        "created_at",
    )
    assert index_columns("contracts", "ix_contracts_game_status") == ("game_id", "status")
    assert index_columns("ai_memory_entries", "ix_ai_memory_entries_game_player_created_at") == (
        "game_id",
        "player_id",
        "created_at",
    )
    assert index_columns("retrieval_records", "ix_retrieval_records_game_player_created_at") == (
        "game_id",
        "player_id",
        "created_at",
    )
    assert index_columns("ai_decisions", "ix_ai_decisions_game_player_created_at") == (
        "game_id",
        "player_id",
        "created_at",
    )
    assert "accepted_event_id" in ai_decisions.c
    assert "rejected_action_id" in ai_decisions.c
