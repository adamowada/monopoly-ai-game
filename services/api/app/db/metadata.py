from __future__ import annotations

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy import event
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID


metadata = sa.MetaData()
RAG_EMBEDDING_DIMENSIONS = 64


@event.listens_for(metadata, "before_create")
def create_postgres_extensions(target: sa.MetaData, connection: sa.Connection, **_: object) -> None:
    if connection.dialect.name != "postgresql":
        return
    connection.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    connection.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))


def uuid_pk() -> sa.Column:
    return sa.Column(
        "id",
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )


def required_jsonb(name: str) -> sa.Column:
    return sa.Column(name, JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"))


def optional_jsonb(name: str) -> sa.Column:
    return sa.Column(name, JSONB, nullable=True)


def created_at() -> sa.Column:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )


def updated_at() -> sa.Column:
    return sa.Column(
        "updated_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )


foundation_metadata = sa.Table(
    "foundation_metadata",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("key", sa.String(length=100), nullable=False, unique=True),
    sa.Column("value", sa.Text, nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
)

games = sa.Table(
    "games",
    metadata,
    uuid_pk(),
    sa.Column("status", sa.String(length=50), nullable=False, server_default=sa.text("'setup'")),
    sa.Column("ruleset_version", sa.String(length=50), nullable=False, server_default=sa.text("'classic-v1'")),
    sa.Column("seed", sa.String(length=100), nullable=True),
    sa.Column("current_phase", sa.String(length=80), nullable=True),
    required_jsonb("settings"),
    required_jsonb("initial_state"),
    created_at(),
    updated_at(),
)

players = sa.Table(
    "players",
    metadata,
    uuid_pk(),
    sa.Column(
        "game_id",
        UUID(as_uuid=True),
        sa.ForeignKey("games.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("seat_order", sa.Integer, nullable=False),
    sa.Column("name", sa.String(length=100), nullable=False),
    sa.Column("controller_type", sa.String(length=30), nullable=False),
    sa.Column("status", sa.String(length=50), nullable=False, server_default=sa.text("'active'")),
    required_jsonb("state"),
    created_at(),
    updated_at(),
    sa.UniqueConstraint("game_id", "seat_order", name="uq_players_game_seat_order"),
    sa.UniqueConstraint("game_id", "name", name="uq_players_game_name"),
    sa.Index("ix_players_game_id", "game_id"),
    sa.Index("ix_players_game_seat_order", "game_id", "seat_order"),
)

game_events = sa.Table(
    "game_events",
    metadata,
    uuid_pk(),
    sa.Column(
        "game_id",
        UUID(as_uuid=True),
        sa.ForeignKey("games.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("sequence", sa.BigInteger, nullable=False),
    sa.Column(
        "actor_player_id",
        UUID(as_uuid=True),
        sa.ForeignKey("players.id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Column("event_type", sa.String(length=120), nullable=False),
    required_jsonb("payload"),
    sa.Column("state_hash", sa.String(length=128), nullable=True),
    created_at(),
    sa.UniqueConstraint("game_id", "sequence", name="uq_game_events_game_sequence"),
    sa.CheckConstraint("sequence > 0", name="ck_game_events_sequence_positive"),
    sa.Index("ix_game_events_game_sequence", "game_id", "sequence"),
    sa.Index("ix_game_events_actor_player_id", "actor_player_id"),
    sa.Index("ix_game_events_game_created_at", "game_id", "created_at"),
    sa.Index("ix_game_events_event_type", "event_type"),
)

game_snapshots = sa.Table(
    "game_snapshots",
    metadata,
    uuid_pk(),
    sa.Column(
        "game_id",
        UUID(as_uuid=True),
        sa.ForeignKey("games.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "last_event_id",
        UUID(as_uuid=True),
        sa.ForeignKey("game_events.id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Column("event_sequence", sa.BigInteger, nullable=False),
    required_jsonb("state_payload"),
    sa.Column("state_hash", sa.String(length=128), nullable=False),
    created_at(),
    sa.UniqueConstraint("game_id", "event_sequence", name="uq_game_snapshots_game_sequence"),
    sa.CheckConstraint("event_sequence >= 0", name="ck_game_snapshots_event_sequence_nonnegative"),
    sa.Index("ix_game_snapshots_game_sequence", "game_id", "event_sequence"),
    sa.Index("ix_game_snapshots_last_event_id", "last_event_id"),
)

rejected_actions = sa.Table(
    "rejected_actions",
    metadata,
    uuid_pk(),
    sa.Column(
        "game_id",
        UUID(as_uuid=True),
        sa.ForeignKey("games.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "actor_player_id",
        UUID(as_uuid=True),
        sa.ForeignKey("players.id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Column("action_type", sa.String(length=120), nullable=False),
    required_jsonb("payload"),
    sa.Column("reason_code", sa.String(length=120), nullable=False),
    required_jsonb("validation_errors"),
    optional_jsonb("legal_action_context"),
    sa.Column("phase", sa.String(length=80), nullable=True),
    sa.Column("state_hash", sa.String(length=128), nullable=True),
    created_at(),
    sa.Index("ix_rejected_actions_game_actor_created_at", "game_id", "actor_player_id", "created_at"),
    sa.Index("ix_rejected_actions_reason_code", "reason_code"),
    sa.Index("ix_rejected_actions_game_phase", "game_id", "phase"),
)

action_idempotency_keys = sa.Table(
    "action_idempotency_keys",
    metadata,
    uuid_pk(),
    sa.Column(
        "game_id",
        UUID(as_uuid=True),
        sa.ForeignKey("games.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "actor_player_id",
        UUID(as_uuid=True),
        sa.ForeignKey("players.id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Column("idempotency_key", sa.String(length=255), nullable=False),
    sa.Column("request_hash", sa.String(length=128), nullable=False),
    sa.Column("status", sa.String(length=50), nullable=False),
    required_jsonb("response_payload"),
    sa.Column("created_event_sequence_start", sa.BigInteger, nullable=True),
    sa.Column("created_event_sequence_end", sa.BigInteger, nullable=True),
    sa.Column(
        "rejected_action_id",
        UUID(as_uuid=True),
        sa.ForeignKey("rejected_actions.id", ondelete="SET NULL"),
        nullable=True,
    ),
    created_at(),
    updated_at(),
    sa.UniqueConstraint("game_id", "idempotency_key", name="uq_action_idempotency_keys_game_key"),
    sa.CheckConstraint(
        "created_event_sequence_start IS NULL OR created_event_sequence_start > 0",
        name="ck_action_idempotency_keys_sequence_start_positive",
    ),
    sa.CheckConstraint(
        "created_event_sequence_end IS NULL OR created_event_sequence_end >= created_event_sequence_start",
        name="ck_action_idempotency_keys_sequence_end_after_start",
    ),
    sa.Index("ix_action_idempotency_keys_game_key", "game_id", "idempotency_key"),
    sa.Index("ix_action_idempotency_keys_rejected_action_id", "rejected_action_id"),
    sa.Index("ix_action_idempotency_keys_actor_created_at", "actor_player_id", "created_at"),
)

negotiations = sa.Table(
    "negotiations",
    metadata,
    uuid_pk(),
    sa.Column(
        "game_id",
        UUID(as_uuid=True),
        sa.ForeignKey("games.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "opened_by_player_id",
        UUID(as_uuid=True),
        sa.ForeignKey("players.id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Column("status", sa.String(length=50), nullable=False, server_default=sa.text("'opened'")),
    sa.Column("phase", sa.String(length=80), nullable=True),
    sa.Column("round_number", sa.Integer, nullable=False, server_default=sa.text("0")),
    optional_jsonb("context"),
    created_at(),
    updated_at(),
    sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
    sa.Index("ix_negotiations_game_status", "game_id", "status"),
    sa.Index("ix_negotiations_opened_by_player_id", "opened_by_player_id"),
    sa.Index("ix_negotiations_game_created_at", "game_id", "created_at"),
)

negotiation_messages = sa.Table(
    "negotiation_messages",
    metadata,
    uuid_pk(),
    sa.Column(
        "game_id",
        UUID(as_uuid=True),
        sa.ForeignKey("games.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "negotiation_id",
        UUID(as_uuid=True),
        sa.ForeignKey("negotiations.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "sender_player_id",
        UUID(as_uuid=True),
        sa.ForeignKey("players.id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Column(
        "recipient_player_id",
        UUID(as_uuid=True),
        sa.ForeignKey("players.id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Column("message_type", sa.String(length=80), nullable=False),
    sa.Column("body", sa.Text, nullable=True),
    required_jsonb("payload"),
    created_at(),
    sa.Index("ix_negotiation_messages_negotiation_created_at", "negotiation_id", "created_at"),
    sa.Index("ix_negotiation_messages_game_created_at", "game_id", "created_at"),
    sa.Index("ix_negotiation_messages_sender_player_id", "sender_player_id"),
)

deals = sa.Table(
    "deals",
    metadata,
    uuid_pk(),
    sa.Column(
        "game_id",
        UUID(as_uuid=True),
        sa.ForeignKey("games.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "negotiation_id",
        UUID(as_uuid=True),
        sa.ForeignKey("negotiations.id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Column(
        "proposed_by_player_id",
        UUID(as_uuid=True),
        sa.ForeignKey("players.id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Column(
        "parent_deal_id",
        UUID(as_uuid=True),
        sa.ForeignKey("deals.id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Column("status", sa.String(length=50), nullable=False, server_default=sa.text("'proposed'")),
    sa.Column("version", sa.Integer, nullable=False, server_default=sa.text("1")),
    required_jsonb("terms"),
    optional_jsonb("validation_errors"),
    created_at(),
    updated_at(),
    sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
    sa.UniqueConstraint("negotiation_id", "version", name="uq_deals_negotiation_version"),
    sa.Index("ix_deals_game_status", "game_id", "status"),
    sa.Index("ix_deals_negotiation_status", "negotiation_id", "status"),
    sa.Index("ix_deals_proposed_by_player_id", "proposed_by_player_id"),
)

contracts = sa.Table(
    "contracts",
    metadata,
    uuid_pk(),
    sa.Column(
        "game_id",
        UUID(as_uuid=True),
        sa.ForeignKey("games.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "deal_id",
        UUID(as_uuid=True),
        sa.ForeignKey("deals.id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Column(
        "effective_event_id",
        UUID(as_uuid=True),
        sa.ForeignKey("game_events.id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Column("status", sa.String(length=50), nullable=False, server_default=sa.text("'active'")),
    required_jsonb("terms"),
    created_at(),
    updated_at(),
    sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
    sa.Index("ix_contracts_game_status", "game_id", "status"),
    sa.Index("ix_contracts_deal_id", "deal_id"),
    sa.Index("ix_contracts_effective_event_id", "effective_event_id"),
)

obligations = sa.Table(
    "obligations",
    metadata,
    uuid_pk(),
    sa.Column(
        "game_id",
        UUID(as_uuid=True),
        sa.ForeignKey("games.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "contract_id",
        UUID(as_uuid=True),
        sa.ForeignKey("contracts.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "owed_by_player_id",
        UUID(as_uuid=True),
        sa.ForeignKey("players.id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Column(
        "owed_to_player_id",
        UUID(as_uuid=True),
        sa.ForeignKey("players.id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Column(
        "settled_event_id",
        UUID(as_uuid=True),
        sa.ForeignKey("game_events.id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Column("status", sa.String(length=50), nullable=False, server_default=sa.text("'pending'")),
    sa.Column("obligation_type", sa.String(length=80), nullable=False),
    optional_jsonb("schedule"),
    required_jsonb("terms"),
    sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
    created_at(),
    updated_at(),
    sa.Index("ix_obligations_contract_status", "contract_id", "status"),
    sa.Index("ix_obligations_game_status", "game_id", "status"),
    sa.Index("ix_obligations_owed_by_player_id", "owed_by_player_id"),
    sa.Index("ix_obligations_owed_to_player_id", "owed_to_player_id"),
)

ai_profiles = sa.Table(
    "ai_profiles",
    metadata,
    uuid_pk(),
    sa.Column(
        "game_id",
        UUID(as_uuid=True),
        sa.ForeignKey("games.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "player_id",
        UUID(as_uuid=True),
        sa.ForeignKey("players.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("persona_name", sa.String(length=120), nullable=True),
    required_jsonb("strategy_profile"),
    optional_jsonb("persona_summary"),
    created_at(),
    updated_at(),
    sa.UniqueConstraint("game_id", "player_id", name="uq_ai_profiles_game_player"),
    sa.Index("ix_ai_profiles_game_player", "game_id", "player_id"),
    sa.Index("ix_ai_profiles_player_id", "player_id"),
)

ai_decisions = sa.Table(
    "ai_decisions",
    metadata,
    uuid_pk(),
    sa.Column(
        "game_id",
        UUID(as_uuid=True),
        sa.ForeignKey("games.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "player_id",
        UUID(as_uuid=True),
        sa.ForeignKey("players.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "ai_profile_id",
        UUID(as_uuid=True),
        sa.ForeignKey("ai_profiles.id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Column(
        "negotiation_id",
        UUID(as_uuid=True),
        sa.ForeignKey("negotiations.id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Column(
        "accepted_event_id",
        UUID(as_uuid=True),
        sa.ForeignKey("game_events.id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Column(
        "rejected_action_id",
        UUID(as_uuid=True),
        sa.ForeignKey("rejected_actions.id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Column("decision_type", sa.String(length=80), nullable=False),
    sa.Column("status", sa.String(length=50), nullable=False),
    sa.Column("phase", sa.String(length=80), nullable=True),
    sa.Column("state_hash", sa.String(length=128), nullable=True),
    sa.Column("prompt_context_hash", sa.String(length=128), nullable=True),
    optional_jsonb("prompt_context"),
    sa.Column("raw_output", sa.Text, nullable=True),
    optional_jsonb("parsed_output"),
    optional_jsonb("validation_result"),
    created_at(),
    sa.CheckConstraint(
        "accepted_event_id IS NULL OR rejected_action_id IS NULL",
        name="ck_ai_decisions_single_commit_outcome",
    ),
    sa.UniqueConstraint("accepted_event_id", name="uq_ai_decisions_accepted_event_id"),
    sa.UniqueConstraint("rejected_action_id", name="uq_ai_decisions_rejected_action_id"),
    sa.Index("ix_ai_decisions_game_player_created_at", "game_id", "player_id", "created_at"),
    sa.Index("ix_ai_decisions_negotiation_id", "negotiation_id"),
    sa.Index("ix_ai_decisions_accepted_event_id", "accepted_event_id"),
    sa.Index("ix_ai_decisions_rejected_action_id", "rejected_action_id"),
    sa.Index("ix_ai_decisions_prompt_context_hash", "prompt_context_hash"),
)

ai_self_dialogue = sa.Table(
    "ai_self_dialogue",
    metadata,
    uuid_pk(),
    sa.Column(
        "game_id",
        UUID(as_uuid=True),
        sa.ForeignKey("games.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "player_id",
        UUID(as_uuid=True),
        sa.ForeignKey("players.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "ai_decision_id",
        UUID(as_uuid=True),
        sa.ForeignKey("ai_decisions.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("phase", sa.String(length=80), nullable=True),
    sa.Column("state_hash", sa.String(length=128), nullable=True),
    sa.Column("content", sa.Text, nullable=True),
    optional_jsonb("payload"),
    created_at(),
    sa.Index("ix_ai_self_dialogue_decision_id", "ai_decision_id"),
    sa.Index("ix_ai_self_dialogue_game_player_created_at", "game_id", "player_id", "created_at"),
)

ai_memory_entries = sa.Table(
    "ai_memory_entries",
    metadata,
    uuid_pk(),
    sa.Column(
        "game_id",
        UUID(as_uuid=True),
        sa.ForeignKey("games.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "player_id",
        UUID(as_uuid=True),
        sa.ForeignKey("players.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "ai_profile_id",
        UUID(as_uuid=True),
        sa.ForeignKey("ai_profiles.id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Column(
        "source_decision_id",
        UUID(as_uuid=True),
        sa.ForeignKey("ai_decisions.id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Column(
        "source_event_id",
        UUID(as_uuid=True),
        sa.ForeignKey("game_events.id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Column(
        "source_negotiation_message_id",
        UUID(as_uuid=True),
        sa.ForeignKey("negotiation_messages.id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Column(
        "superseded_by_memory_id",
        UUID(as_uuid=True),
        sa.ForeignKey("ai_memory_entries.id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Column("category", sa.String(length=80), nullable=False),
    sa.Column("visibility", sa.String(length=30), nullable=False, server_default=sa.text("'private'")),
    sa.Column("content", sa.Text, nullable=False),
    sa.Column("importance", sa.Integer, nullable=False, server_default=sa.text("0")),
    required_jsonb("metadata_blob"),
    created_at(),
    updated_at(),
    sa.Index("ix_ai_memory_entries_game_player_created_at", "game_id", "player_id", "created_at"),
    sa.Index("ix_ai_memory_entries_source_decision_id", "source_decision_id"),
    sa.Index("ix_ai_memory_entries_source_event_id", "source_event_id"),
    sa.Index("ix_ai_memory_entries_category", "category"),
)

rag_index_entries = sa.Table(
    "rag_index_entries",
    metadata,
    uuid_pk(),
    sa.Column("index_key", sa.String(length=260), nullable=False, unique=True),
    sa.Column("document_id", sa.String(length=260), nullable=False),
    sa.Column(
        "game_id",
        UUID(as_uuid=True),
        sa.ForeignKey("games.id", ondelete="CASCADE"),
        nullable=True,
    ),
    sa.Column(
        "player_id",
        UUID(as_uuid=True),
        sa.ForeignKey("players.id", ondelete="CASCADE"),
        nullable=True,
    ),
    sa.Column("phase", sa.String(length=80), nullable=True),
    sa.Column("source_type", sa.String(length=80), nullable=False),
    sa.Column("source_id", sa.String(length=160), nullable=False),
    sa.Column("title", sa.Text, nullable=False),
    sa.Column("text", sa.Text, nullable=False),
    required_jsonb("metadata_blob"),
    sa.Column("search_vector", TSVECTOR, nullable=False),
    sa.Column("embedding", Vector(RAG_EMBEDDING_DIMENSIONS), nullable=False),
    created_at(),
    updated_at(),
    sa.UniqueConstraint("index_key", name="uq_rag_index_entries_index_key"),
    sa.Index("ix_rag_index_entries_game_player_phase", "game_id", "player_id", "phase"),
    sa.Index("ix_rag_index_entries_source", "source_type", "source_id"),
    sa.Index(
        "ix_rag_index_entries_search_vector",
        "search_vector",
        postgresql_using="gin",
    ),
    sa.Index(
        "ix_rag_index_entries_embedding",
        "embedding",
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    ),
)

retrieval_records = sa.Table(
    "retrieval_records",
    metadata,
    uuid_pk(),
    sa.Column(
        "game_id",
        UUID(as_uuid=True),
        sa.ForeignKey("games.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "player_id",
        UUID(as_uuid=True),
        sa.ForeignKey("players.id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Column(
        "ai_decision_id",
        UUID(as_uuid=True),
        sa.ForeignKey("ai_decisions.id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Column(
        "memory_entry_id",
        UUID(as_uuid=True),
        sa.ForeignKey("ai_memory_entries.id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Column("query_text", sa.Text, nullable=False),
    optional_jsonb("query_context"),
    required_jsonb("retrieved_context"),
    sa.Column("source_type", sa.String(length=80), nullable=True),
    sa.Column("source_id", sa.String(length=160), nullable=True),
    sa.Column("rank", sa.Integer, nullable=True),
    sa.Column("score", sa.Numeric(precision=12, scale=6), nullable=True),
    created_at(),
    sa.Index("ix_retrieval_records_game_player_created_at", "game_id", "player_id", "created_at"),
    sa.Index("ix_retrieval_records_decision_id", "ai_decision_id"),
    sa.Index("ix_retrieval_records_memory_entry_id", "memory_entry_id"),
    sa.Index("ix_retrieval_records_source", "source_type", "source_id"),
)
