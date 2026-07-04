from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from app.db.metadata import metadata


API_ROOT = Path(__file__).resolve().parents[1]


def test_alembic_has_head_revision_for_foundation_schema() -> None:
    config = Config(str(API_ROOT / "alembic.ini"))
    script = ScriptDirectory.from_config(config)

    head = script.get_current_head()

    assert head is not None
    revision = script.get_revision(head)
    assert revision is not None
    assert "foundation_metadata" in (revision.module.__doc__ or "")


def test_foundation_metadata_table_is_in_sqlalchemy_metadata() -> None:
    table = metadata.tables["foundation_metadata"]

    assert table.c.id.primary_key
    assert table.c.key.unique
    assert table.c.value.nullable
