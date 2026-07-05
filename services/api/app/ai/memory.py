"""Persistent AI memory helpers for Stage 8.2 and Stage 8.3."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.decision_schema import AI_MEMORY_CATEGORIES, MemoryUpdatePayload
from app.db.metadata import ai_decisions, ai_memory_entries


TRUSTED_MEMORY_METADATA_VERSION = "ai-memory-v1"
MEMORY_COMPACTION_METADATA_VERSION = "ai-memory-compaction-v1"
MEMORY_CONTEXT_SCORING_VERSION = "ai-memory-context-score-v1"
MEMORY_VISIBILITIES = frozenset({"private", "public", "table", "audit"})
MEMORY_COMPACTION_REASON_ON_DEMAND = "on_demand_context_pack"
MEMORY_COMPACTION_REASON_SCHEDULED = "scheduled_25_decisions"
MEMORY_COMPACTION_DECISION_INTERVAL = 25
MEMORY_COMPACTION_THRESHOLD = 25
MEMORY_COMPACTION_TARGET_RAW_COUNT = 12
MEMORY_COMPACTION_BATCH_SIZE = 20

_CATEGORY_WEIGHTS: dict[str, int] = {
    "strategic_belief": 80,
    "long_term_plan": 75,
    "opportunity": 65,
    "player_trust_model": 55,
    "promise_made": 50,
    "promise_received": 50,
    "threat": 45,
    "grudge": 40,
    "mistake_lesson": 35,
    "deal_history": 20,
}
_PROTECTED_RAW_CATEGORIES = frozenset({"strategic_belief", "long_term_plan", "opportunity"})
_SUMMARY_SCORE_BONUS = 1000
_SUPERSEDED_RAW_PENALTY = 700


@dataclass(frozen=True, slots=True)
class MemoryContextScore:
    score: int
    inputs: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class MemoryCompactionResult:
    reason: str
    decision_count: int | None = None
    summary_memory_id: UUID | None = None
    source_memory_ids: tuple[UUID, ...] = ()

    @property
    def created_summary(self) -> bool:
        return self.summary_memory_id is not None


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


def score_memory_entry_for_context(
    row: Mapping[str, Any],
    *,
    recency_rank: int,
    total_rows: int,
) -> MemoryContextScore:
    """Return a deterministic prompt-selection score for one memory row."""

    stored_importance = _clamped_importance(row.get("importance"))
    category = _string_or_none(row.get("category")) or "unknown"
    category_weight = _CATEGORY_WEIGHTS.get(category, 0)
    source_links = _source_links(row)
    source_link_score = (
        (3 if source_links["source_decision_id"] else 0)
        + (5 if source_links["source_event_id"] else 0)
        + (4 if source_links["source_negotiation_message_id"] else 0)
    )
    bounded_total = max(1, total_rows)
    bounded_rank = max(0, min(recency_rank, bounded_total - 1))
    recency_score = int((bounded_rank / bounded_total) * 30)
    compacted_summary = _is_compacted_summary(row)
    superseded = row.get("superseded_by_memory_id") is not None
    protected_raw = _is_protected_raw_memory(row)

    score = (stored_importance * 100) + category_weight + source_link_score + recency_score
    if compacted_summary:
        score += _SUMMARY_SCORE_BONUS
    if superseded and not protected_raw and not compacted_summary:
        score -= _SUPERSEDED_RAW_PENALTY

    inputs = {
        "algorithm": MEMORY_CONTEXT_SCORING_VERSION,
        "stored_importance": stored_importance,
        "category": category,
        "category_weight": category_weight,
        "source_links": source_links,
        "source_link_score": source_link_score,
        "recency_rank": bounded_rank,
        "total_rows": bounded_total,
        "recency_score": recency_score,
        "compacted_summary": compacted_summary,
        "superseded": superseded,
        "protected_raw": protected_raw,
    }
    return MemoryContextScore(score=score, inputs=_json_safe_mapping(inputs))


def select_memory_rows_for_context(
    rows: Sequence[Mapping[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    """Select bounded context memories by deterministic Stage 8.3 score."""

    if limit < 1:
        return []

    rows_by_recency = _rows_oldest_first(rows)
    total_rows = len(rows_by_recency)
    scored_rows: list[dict[str, Any]] = []
    for recency_rank, row in enumerate(rows_by_recency):
        score = score_memory_entry_for_context(
            row,
            recency_rank=recency_rank,
            total_rows=total_rows,
        )
        row_copy = dict(row)
        row_copy["context_score"] = score.score
        row_copy["context_scoring_inputs"] = dict(score.inputs)
        metadata_blob = _mapping(row_copy.get("metadata_blob", row_copy.get("metadata")))
        metadata_blob["context_selection"] = {
            "algorithm": MEMORY_CONTEXT_SCORING_VERSION,
            "score": score.score,
        }
        row_copy["metadata_blob"] = metadata_blob
        scored_rows.append(row_copy)

    return sorted(scored_rows, key=_context_selection_sort_key)[:limit]


async def compact_memory_after_scheduled_decision_if_due(
    session: AsyncSession,
    *,
    game_id: UUID,
    player_id: UUID,
) -> MemoryCompactionResult:
    """Run Stage 8.3 scheduled compaction after every 25 AI decisions for a player."""

    result = await session.execute(
        sa.select(sa.func.count())
        .select_from(ai_decisions)
        .where(ai_decisions.c.game_id == game_id, ai_decisions.c.player_id == player_id)
    )
    decision_count = int(result.scalar_one())
    if decision_count < MEMORY_COMPACTION_DECISION_INTERVAL:
        return MemoryCompactionResult(
            reason=MEMORY_COMPACTION_REASON_SCHEDULED,
            decision_count=decision_count,
        )
    if decision_count % MEMORY_COMPACTION_DECISION_INTERVAL != 0:
        return MemoryCompactionResult(
            reason=MEMORY_COMPACTION_REASON_SCHEDULED,
            decision_count=decision_count,
        )

    result = await compact_memory_for_player(
        session,
        game_id=game_id,
        player_id=player_id,
        reason=MEMORY_COMPACTION_REASON_SCHEDULED,
    )
    return MemoryCompactionResult(
        reason=result.reason,
        decision_count=decision_count,
        summary_memory_id=result.summary_memory_id,
        source_memory_ids=result.source_memory_ids,
    )


async def compact_memory_for_player(
    session: AsyncSession,
    *,
    game_id: UUID,
    player_id: UUID,
    reason: str,
    compaction_threshold: int = MEMORY_COMPACTION_THRESHOLD,
    target_raw_count: int = MEMORY_COMPACTION_TARGET_RAW_COUNT,
    batch_size: int = MEMORY_COMPACTION_BATCH_SIZE,
) -> MemoryCompactionResult:
    """Create a deterministic summary row for low-value raw memories."""

    result = await session.execute(
        sa.select(ai_memory_entries)
        .where(ai_memory_entries.c.game_id == game_id, ai_memory_entries.c.player_id == player_id)
        .order_by(ai_memory_entries.c.created_at, ai_memory_entries.c.id)
        .with_for_update()
    )
    rows = [dict(row) for row in result.mappings().all()]
    raw_rows = [
        row
        for row in rows
        if not _is_compacted_summary(row) and row.get("superseded_by_memory_id") is None
    ]
    if len(raw_rows) <= max(1, compaction_threshold):
        return MemoryCompactionResult(reason=reason)

    scored_rows = _score_rows(raw_rows)
    eligible_rows = [
        row for row in scored_rows if not _is_protected_raw_memory(row)
    ]
    if not eligible_rows:
        return MemoryCompactionResult(reason=reason)

    source_count = min(
        max(1, len(raw_rows) - max(1, target_raw_count)),
        max(1, batch_size),
        len(eligible_rows),
    )
    source_rows = sorted(
        eligible_rows,
        key=lambda row: (
            _int_or_zero(row.get("context_score")),
            _string_or_none(row.get("created_at")) or "",
            _row_id(row),
        ),
    )[:source_count]
    source_ids = tuple(_coerce_uuid(row["id"]) for row in source_rows)
    summary_metadata = _summary_metadata(
        reason=reason,
        source_rows=source_rows,
        source_ids=source_ids,
    )

    insert_result = await session.execute(
        ai_memory_entries.insert()
        .values(
            game_id=game_id,
            player_id=player_id,
            ai_profile_id=_summary_profile_id(source_rows),
            source_decision_id=None,
            source_event_id=None,
            source_negotiation_message_id=None,
            category=_summary_category(source_rows),
            visibility=_summary_visibility(source_rows),
            content=_summary_content(source_rows, reason=reason),
            importance=_summary_importance(source_rows),
            metadata_blob=summary_metadata,
        )
        .returning(ai_memory_entries.c.id)
    )
    summary_id = insert_result.scalar_one()

    for row in source_rows:
        metadata_blob = _mapping(row.get("metadata_blob"))
        history = metadata_blob.setdefault("compaction_history", [])
        if not isinstance(history, list):
            history = []
            metadata_blob["compaction_history"] = history
        history.append(
            {
                "schema_version": MEMORY_COMPACTION_METADATA_VERSION,
                "reason": reason,
                "superseded_by_memory_id": str(summary_id),
            }
        )
        metadata_blob["compaction"] = {
            "is_summary": False,
            "reason": reason,
            "superseded_by_memory_id": str(summary_id),
        }
        await session.execute(
            ai_memory_entries.update()
            .where(ai_memory_entries.c.id == row["id"])
            .values(
                superseded_by_memory_id=summary_id,
                metadata_blob=_json_safe_mapping(metadata_blob),
                updated_at=sa.func.now(),
            )
        )

    return MemoryCompactionResult(
        reason=reason,
        summary_memory_id=summary_id,
        source_memory_ids=source_ids,
    )


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


def _summary_metadata(
    *,
    reason: str,
    source_rows: Sequence[Mapping[str, Any]],
    source_ids: Sequence[UUID],
) -> dict[str, Any]:
    source_id_strings = [str(source_id) for source_id in source_ids]
    source_decision_ids = _unique_source_ids(source_rows, "source_decision_id")
    source_event_ids = _unique_source_ids(source_rows, "source_event_id")
    source_message_ids = _unique_source_ids(source_rows, "source_negotiation_message_id")
    scoring_inputs_by_memory_id = {
        str(row["id"]): {
            **_mapping(row.get("context_scoring_inputs")),
            "score": _int_or_zero(row.get("context_score")),
        }
        for row in source_rows
    }
    category_counts: dict[str, int] = {}
    for row in source_rows:
        category = _string_or_none(row.get("category")) or "unknown"
        category_counts[category] = category_counts.get(category, 0) + 1

    return _json_safe_mapping(
        {
            "schema_version": MEMORY_COMPACTION_METADATA_VERSION,
            "compaction": {
                "is_summary": True,
                "reason": reason,
                "source_memory_ids": source_id_strings,
                "source_count": len(source_id_strings),
                "source_decision_ids": source_decision_ids,
                "source_event_ids": source_event_ids,
                "source_negotiation_message_ids": source_message_ids,
                "source_category_counts": dict(sorted(category_counts.items())),
                "scoring_algorithm": MEMORY_CONTEXT_SCORING_VERSION,
                "scoring_inputs_by_memory_id": scoring_inputs_by_memory_id,
            },
        }
    )


def _summary_content(source_rows: Sequence[Mapping[str, Any]], *, reason: str) -> str:
    rows_for_summary = sorted(
        source_rows,
        key=lambda row: (
            -_int_or_zero(row.get("context_score")),
            _string_or_none(row.get("created_at")) or "",
            _row_id(row),
        ),
    )
    categories: dict[str, int] = {}
    for row in rows_for_summary:
        category = _string_or_none(row.get("category")) or "unknown"
        categories[category] = categories.get(category, 0) + 1
    category_text = ", ".join(
        f"{category}:{count}" for category, count in sorted(categories.items())
    )
    bullets = [
        (
            f"- {row.get('category')} importance {_clamped_importance(row.get('importance'))}: "
            f"{_truncate(str(row.get('content') or ''), 160)}"
        )
        for row in rows_for_summary[:6]
    ]
    return "\n".join(
        [
            f"Compacted memory summary ({len(source_rows)} source entries; reason {reason}).",
            f"Categories: {category_text}.",
            "Key retained points:",
            *bullets,
        ]
    )


def _summary_importance(source_rows: Sequence[Mapping[str, Any]]) -> int:
    if not source_rows:
        return 4
    max_importance = max(_clamped_importance(row.get("importance")) for row in source_rows)
    return max(4, min(10, max_importance + 1))


def _summary_category(source_rows: Sequence[Mapping[str, Any]]) -> str:
    categories: dict[str, int] = {}
    for row in source_rows:
        category = _string_or_none(row.get("category")) or "deal_history"
        categories[category] = categories.get(category, 0) + 1
    return sorted(categories, key=lambda category: (-categories[category], category))[0]


def _summary_visibility(source_rows: Sequence[Mapping[str, Any]]) -> str:
    visibilities = [_string_or_none(row.get("visibility")) or "private" for row in source_rows]
    if "private" in visibilities:
        return "private"
    return sorted(visibilities)[0] if visibilities else "private"


def _summary_profile_id(source_rows: Sequence[Mapping[str, Any]]) -> UUID | None:
    for row in sorted(source_rows, key=lambda item: (_string_or_none(item.get("created_at")) or "", _row_id(item)), reverse=True):
        value = row.get("ai_profile_id")
        if value is not None:
            return _coerce_uuid(value)
    return None


def _score_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows_by_recency = _rows_oldest_first(rows)
    total_rows = len(rows_by_recency)
    scored: list[dict[str, Any]] = []
    for recency_rank, row in enumerate(rows_by_recency):
        score = score_memory_entry_for_context(
            row,
            recency_rank=recency_rank,
            total_rows=total_rows,
        )
        row_copy = dict(row)
        row_copy["context_score"] = score.score
        row_copy["context_scoring_inputs"] = dict(score.inputs)
        scored.append(row_copy)
    return scored


def _rows_oldest_first(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return sorted(rows, key=lambda row: (_string_or_none(row.get("created_at")) or "", _row_id(row)))


def _context_selection_sort_key(row: Mapping[str, Any]) -> tuple[int, str, str]:
    return (
        -_int_or_zero(row.get("context_score")),
        _reverse_lexical(_string_or_none(row.get("created_at")) or ""),
        _row_id(row),
    )


def _is_protected_raw_memory(row: Mapping[str, Any]) -> bool:
    category = _string_or_none(row.get("category")) or ""
    return category in _PROTECTED_RAW_CATEGORIES and _clamped_importance(row.get("importance")) >= 8


def _is_compacted_summary(row: Mapping[str, Any]) -> bool:
    metadata_blob = _mapping(row.get("metadata_blob", row.get("metadata")))
    compaction = metadata_blob.get("compaction")
    return isinstance(compaction, Mapping) and compaction.get("is_summary") is True


def _source_links(row: Mapping[str, Any]) -> dict[str, bool]:
    return {
        "source_decision_id": row.get("source_decision_id") is not None,
        "source_event_id": row.get("source_event_id") is not None,
        "source_negotiation_message_id": row.get("source_negotiation_message_id") is not None,
    }


def _unique_source_ids(rows: Sequence[Mapping[str, Any]], key: str) -> list[str]:
    values = {_string_or_none(row.get(key)) for row in rows}
    return sorted(value for value in values if value is not None)


def _mapping(value: Any) -> dict[str, Any]:
    return _json_safe_mapping(value) if isinstance(value, Mapping) else {}


def _json_safe_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(dict(value), sort_keys=True, default=str, ensure_ascii=True))


def _string_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _int_or_zero(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    return 0


def _clamped_importance(value: Any) -> int:
    return max(0, min(10, _int_or_zero(value)))


def _row_id(row: Mapping[str, Any]) -> str:
    return _string_or_none(row.get("id")) or ""


def _reverse_lexical(value: str) -> str:
    return "".join(chr(0x10FFFF - ord(char)) for char in value)


def _truncate(value: str, limit: int) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 3)].rstrip() + "..."


def _coerce_uuid(value: UUID | str) -> UUID:
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


__all__ = [
    "AI_MEMORY_CATEGORIES",
    "MEMORY_COMPACTION_DECISION_INTERVAL",
    "MEMORY_COMPACTION_METADATA_VERSION",
    "MEMORY_COMPACTION_REASON_ON_DEMAND",
    "MEMORY_COMPACTION_REASON_SCHEDULED",
    "MEMORY_COMPACTION_THRESHOLD",
    "MEMORY_CONTEXT_SCORING_VERSION",
    "MEMORY_VISIBILITIES",
    "MemoryCompactionResult",
    "MemoryContextScore",
    "compact_memory_after_scheduled_decision_if_due",
    "compact_memory_for_player",
    "TRUSTED_MEMORY_METADATA_VERSION",
    "link_memory_entries_to_decision_evidence",
    "persist_memory_updates_for_trusted_output",
    "score_memory_entry_for_context",
    "select_memory_rows_for_context",
]
