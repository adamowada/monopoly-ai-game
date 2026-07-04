from __future__ import annotations

import sqlalchemy as sa


metadata = sa.MetaData()

foundation_metadata = sa.Table(
    "foundation_metadata",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("key", sa.String(length=100), nullable=False, unique=True),
    sa.Column("value", sa.Text, nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
)
