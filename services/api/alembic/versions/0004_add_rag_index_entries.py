"""add rag index entries

This revision adds the durable Phase 9.2 retrieval index table backed by
Postgres full-text search and pgvector embeddings.
"""

from __future__ import annotations

from alembic import op
from pgvector.sqlalchemy import Vector
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID


revision: str = "0004_add_rag_index_entries"
down_revision: str | None = "0003_add_action_idempotency_keys"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None

RAG_EMBEDDING_DIMENSIONS = 64


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "rag_index_entries",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("index_key", sa.String(length=260), nullable=False),
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
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("metadata_blob", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("search_vector", TSVECTOR(), nullable=False),
        sa.Column("embedding", Vector(RAG_EMBEDDING_DIMENSIONS), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("index_key", name="uq_rag_index_entries_index_key"),
    )
    op.create_index(
        "ix_rag_index_entries_game_player_phase",
        "rag_index_entries",
        ["game_id", "player_id", "phase"],
    )
    op.create_index(
        "ix_rag_index_entries_source",
        "rag_index_entries",
        ["source_type", "source_id"],
    )
    op.create_index(
        "ix_rag_index_entries_search_vector",
        "rag_index_entries",
        ["search_vector"],
        postgresql_using="gin",
    )
    op.create_index(
        "ix_rag_index_entries_embedding",
        "rag_index_entries",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_rag_index_entries_embedding", table_name="rag_index_entries")
    op.drop_index("ix_rag_index_entries_search_vector", table_name="rag_index_entries")
    op.drop_index("ix_rag_index_entries_source", table_name="rag_index_entries")
    op.drop_index("ix_rag_index_entries_game_player_phase", table_name="rag_index_entries")
    op.drop_table("rag_index_entries")
