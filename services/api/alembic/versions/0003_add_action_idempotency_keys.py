"""add action idempotency keys

This revision adds durable submitted-action idempotency records for Phase 4.5
transaction and concurrency safety.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision: str = "0003_add_action_idempotency_keys"
down_revision: str | None = "0002_create_domain_audit_schema"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.create_table(
        "action_idempotency_keys",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
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
        sa.Column("response_payload", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_event_sequence_start", sa.BigInteger, nullable=True),
        sa.Column("created_event_sequence_end", sa.BigInteger, nullable=True),
        sa.Column(
            "rejected_action_id",
            UUID(as_uuid=True),
            sa.ForeignKey("rejected_actions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("game_id", "idempotency_key", name="uq_action_idempotency_keys_game_key"),
        sa.CheckConstraint(
            "created_event_sequence_start IS NULL OR created_event_sequence_start > 0",
            name="ck_action_idempotency_keys_sequence_start_positive",
        ),
        sa.CheckConstraint(
            "created_event_sequence_end IS NULL OR created_event_sequence_end >= created_event_sequence_start",
            name="ck_action_idempotency_keys_sequence_end_after_start",
        ),
    )
    op.create_index(
        "ix_action_idempotency_keys_game_key",
        "action_idempotency_keys",
        ["game_id", "idempotency_key"],
    )
    op.create_index(
        "ix_action_idempotency_keys_rejected_action_id",
        "action_idempotency_keys",
        ["rejected_action_id"],
    )
    op.create_index(
        "ix_action_idempotency_keys_actor_created_at",
        "action_idempotency_keys",
        ["actor_player_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_action_idempotency_keys_actor_created_at", table_name="action_idempotency_keys")
    op.drop_index("ix_action_idempotency_keys_rejected_action_id", table_name="action_idempotency_keys")
    op.drop_index("ix_action_idempotency_keys_game_key", table_name="action_idempotency_keys")
    op.drop_table("action_idempotency_keys")
