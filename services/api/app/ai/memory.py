"""Persistent AI memory helpers for Stage 8.2."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.decision_schema import AI_MEMORY_CATEGORIES, MemoryUpdatePayload
from app.db.metadata import ai_memory_entries


TRUSTED_MEMORY_METADATA_VERSION = "ai-memory-v1"
MEMORY_VISIBILITIES = frozenset({"private", "public", "table", "audit"})


async def persist_memory_updates_for_trusted_output(
    session: AsyncSession,
    *,
    decision_id: UUID,
    game_id: UUID,
    player_id: UUID,
    ai_profile_id: UUID | None,
    parsed_output: Mapping[str, Any],
    phase: str | None,
    state_hash: str | None,
    ai_decision_status: str,
) -> list[dict[str, Any]]:
    """Persist schema-valid memory updates once for an AI decision."""

    raw_updates = parsed_output.get("memory_updates")
    if not isinstance(raw_updates, Sequence) or isinstance(raw_updates, str | bytes | bytearray):
        return []

    existing_result = await session.execute(
        sa.select(sa.func.count())
        .select_from(ai_memory_entries)
        .where(ai_memory_entries.c.source_decision_id == decision_id)
    )
    if int(existing_result.scalar_one()) > 0:
        return []

    persisted: list[dict[str, Any]] = []
    for raw_update in raw_updates:
        if not isinstance(raw_update, Mapping):
            continue
        update = MemoryUpdatePayload.model_validate(dict(raw_update))
        metadata_blob = {
            "schema_version": TRUSTED_MEMORY_METADATA_VERSION,
            "trusted_ai_output": True,
            "ai_decision_status": ai_decision_status,
            "decision_type": _string_or_none(parsed_output.get("decision_type")),
            "phase": phase,
            "state_hash": state_hash,
            "memory_update": _json_safe_mapping(update.metadata),
        }
        result = await session.execute(
            ai_memory_entries.insert()
            .values(
                game_id=game_id,
                player_id=player_id,
                ai_profile_id=ai_profile_id,
                source_decision_id=decision_id,
                source_event_id=None,
                source_negotiation_message_id=None,
                category=update.category,
                visibility=update.visibility,
                content=update.content,
                importance=update.importance,
                metadata_blob=metadata_blob,
            )
            .returning(ai_memory_entries)
        )
        persisted.append(dict(result.mappings().one()))
    return persisted


async def link_memory_entries_to_decision_evidence(
    session: AsyncSession,
    *,
    decision_id: UUID,
    ai_decision_status: str,
    source_event_id: UUID | None = None,
    source_negotiation_message_id: UUID | None = None,
    rejected_action_id: UUID | None = None,
    evidence_metadata: Mapping[str, Any] | None = None,
) -> int:
    """Attach later event/message/rejection evidence to rows created from a decision."""

    result = await session.execute(
        sa.select(ai_memory_entries)
        .where(ai_memory_entries.c.source_decision_id == decision_id)
        .with_for_update()
    )
    rows = [dict(row) for row in result.mappings().all()]
    if not rows:
        return 0

    count = 0
    for row in rows:
        metadata_blob = _mapping(row.get("metadata_blob"))
        metadata_blob["ai_decision_status"] = ai_decision_status
        if source_event_id is not None:
            metadata_blob["source_event_id"] = str(source_event_id)
        if source_negotiation_message_id is not None:
            metadata_blob["source_negotiation_message_id"] = str(source_negotiation_message_id)
        if rejected_action_id is not None:
            metadata_blob["rejected_action_id"] = str(rejected_action_id)
        if evidence_metadata:
            metadata_blob["evidence"] = _json_safe_mapping(evidence_metadata)

        update_values: dict[str, Any] = {
            "metadata_blob": metadata_blob,
            "updated_at": sa.func.now(),
        }
        if source_event_id is not None:
            update_values["source_event_id"] = source_event_id
        if source_negotiation_message_id is not None:
            update_values["source_negotiation_message_id"] = source_negotiation_message_id

        await session.execute(
            ai_memory_entries.update()
            .where(ai_memory_entries.c.id == row["id"])
            .values(**update_values)
        )
        count += 1
    return count


def _mapping(value: Any) -> dict[str, Any]:
    return _json_safe_mapping(value) if isinstance(value, Mapping) else {}


def _json_safe_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(dict(value), sort_keys=True, default=str, ensure_ascii=True))


def _string_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


__all__ = [
    "AI_MEMORY_CATEGORIES",
    "MEMORY_VISIBILITIES",
    "TRUSTED_MEMORY_METADATA_VERSION",
    "link_memory_entries_to_decision_evidence",
    "persist_memory_updates_for_trusted_output",
]
