"""create foundation_metadata table

The foundation_metadata table is a tiny non-domain table used in Phase 1 Stage 1.3 to prove
Alembic can create and inspect the local Postgres schema before game tables exist.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision: str = "0001_create_foundation_metadata"
down_revision: str | None = None
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.create_table(
        "foundation_metadata",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(length=100), nullable=False, unique=True),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_table("foundation_metadata")
