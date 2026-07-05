"""Postgres-backed local retrieval for Stage 9.2."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.context_pack import VISIBLE_MEMORY_SCOPES
from app.ai.memory import memory_row_is_usable_for_context
from app.db.metadata import (
    RAG_EMBEDDING_DIMENSIONS,
    ai_decisions,
    ai_memory_entries,
    negotiation_messages,
    negotiations,
    rag_index_entries,
    retrieval_records,
)
from app.rag.corpus import (
    SOURCE_TYPES,
    CorpusDocument,
    build_ai_memory_corpus,
    build_static_local_corpus,
    load_negotiation_history_corpus_from_db,
    load_past_decision_corpus_from_db,
)


TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
FTS_WEIGHT = 0.65
VECTOR_WEIGHT = 0.35
DEFAULT_LIMIT = 6
MAX_LIMIT = 20
STATIC_SOURCE_TYPES = frozenset(("rules", "house_rules", "contract_examples"))


@dataclass(frozen=True, slots=True)
class RetrievalSearchResult:
    index_entry_id: UUID
    source_type: str
    source_id: str
    title: str
    text: str
    metadata: dict[str, Any]
    rank: int
    score: float
    fts_rank: float
    vector_similarity: float
    ranking: dict[str, Any]
    memory_entry_id: UUID | None
    retrieved_context: dict[str, Any]

    def to_rule_snippet(self) -> dict[str, Any]:
        return {
            "id": self.source_id,
            "source": self.source_type,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "title": self.title,
            "text": self.text,
            "score": self.score,
            "rank": self.rank,
            "metadata": _json_safe(self.metadata),
            "rag_retrieval": _json_safe(self.retrieved_context),
        }

    def to_memory_row(self) -> dict[str, Any]:
        metadata_blob = _mapping(self.metadata.get("metadata_blob", self.metadata.get("metadata")))
        metadata_blob["rag_retrieval"] = _json_safe(self.retrieved_context)
        return {
            "id": self.source_id,
            "player_id": _string_or_none(self.metadata.get("player_id")),
            "ai_profile_id": _string_or_none(self.metadata.get("ai_profile_id")),
            "visibility": _string_or_none(self.metadata.get("visibility")) or "private",
            "category": _string_or_none(self.metadata.get("category")) or "memory",
            "content": _memory_content_from_text(self.text),
            "importance": _int_or_none(self.metadata.get("importance")),
            "context_score": int(round(self.score * 1000)),
            "metadata_blob": metadata_blob,
            "source_decision_id": _string_or_none(self.metadata.get("source_decision_id")),
            "source_event_id": _string_or_none(self.metadata.get("source_event_id")),
            "source_negotiation_message_id": _string_or_none(
                self.metadata.get("source_negotiation_message_id")
            ),
            "superseded_by_memory_id": _string_or_none(
                self.metadata.get("superseded_by_memory_id")
            ),
            "created_at": _string_or_none(self.metadata.get("created_at")),
        }


async def refresh_rag_index_entries(
    session: AsyncSession,
    *,
    game_id: str | UUID | None = None,
) -> int:
    """Build or refresh durable local RAG index entries from Stage 9.1 corpus sources."""

    documents = build_static_local_corpus()
    game_uuid = None if game_id is None else _coerce_uuid(game_id)
    await _delete_legacy_game_scoped_static_entries(session)

    if game_uuid is not None:
        await session.execute(
            rag_index_entries.delete().where(rag_index_entries.c.game_id == game_uuid)
        )
        documents.extend(await _load_game_documents(session, game_id=game_uuid))

    count = 0
    for document in documents:
        await _upsert_index_document(session, document, game_id=game_uuid)
        count += 1
    return count


async def search_retrieval(
    session: AsyncSession,
    *,
    query_text: str,
    game_id: str | UUID | None = None,
    player_id: str | UUID | None = None,
    phase: str | None = None,
    source_types: Sequence[str] | None = None,
    limit: int = DEFAULT_LIMIT,
    query_context: Mapping[str, Any] | None = None,
    audit: bool = False,
    ai_decision_id: str | UUID | None = None,
) -> list[RetrievalSearchResult]:
    """Search indexed local context with Postgres FTS and pgvector similarity."""

    normalized_query = " ".join(query_text.split())
    if not normalized_query:
        raise ValueError("query_text must contain at least one searchable token")
    query_tokens = TOKEN_PATTERN.findall(normalized_query.lower())
    if not query_tokens:
        raise ValueError("query_text must contain at least one searchable token")
    if limit < 1 or limit > MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT}")

    allowed_source_types = _validate_source_types(source_types)
    game_uuid = None if game_id is None else _coerce_uuid(game_id)
    player_uuid = None if player_id is None else _coerce_uuid(player_id)
    decision_uuid = None if ai_decision_id is None else _coerce_uuid(ai_decision_id)

    query_embedding = embed_text(normalized_query)
    ts_query = sa.func.to_tsquery(
        sa.literal_column("'english'"),
        " | ".join(dict.fromkeys(query_tokens)),
    )
    fts_rank = sa.func.coalesce(
        sa.func.ts_rank_cd(rag_index_entries.c.search_vector, ts_query),
        0.0,
    )
    vector_similarity = 1.0 - rag_index_entries.c.embedding.cosine_distance(query_embedding)
    combined_score = (fts_rank * FTS_WEIGHT) + (vector_similarity * VECTOR_WEIGHT)

    statement = (
        sa.select(
            rag_index_entries.c.id,
            rag_index_entries.c.game_id,
            rag_index_entries.c.player_id,
            rag_index_entries.c.phase,
            rag_index_entries.c.source_type,
            rag_index_entries.c.source_id,
            rag_index_entries.c.title,
            rag_index_entries.c.text,
            rag_index_entries.c.metadata_blob,
            fts_rank.label("fts_rank"),
            vector_similarity.label("vector_similarity"),
            combined_score.label("combined_score"),
        )
        .where(
            _scope_filter(game_uuid),
            rag_index_entries.c.source_type.in_(tuple(sorted(allowed_source_types))),
            _phase_filter(phase),
            _visibility_filter(game_uuid=game_uuid, player_uuid=player_uuid),
            rag_index_entries.c.search_vector.op("@@")(ts_query),
            combined_score > 0,
        )
        .order_by(
            sa.desc(combined_score),
            sa.desc(fts_rank),
            sa.desc(vector_similarity),
            rag_index_entries.c.source_type,
            rag_index_entries.c.source_id,
        )
        .limit(limit)
    )
    result = await session.execute(statement)
    rows = [dict(row) for row in result.mappings().all()]
    results = [
        _result_from_row(row, rank=rank, query_text=normalized_query)
        for rank, row in enumerate(rows, start=1)
    ]

    if audit and results:
        await _persist_retrieval_records(
            session,
            results=results,
            game_id=game_uuid,
            player_id=player_uuid,
            ai_decision_id=decision_uuid,
            query_text=normalized_query,
            query_context=query_context or {},
            source_types=allowed_source_types,
            phase=phase,
        )
    return results


def embed_text(text: str) -> list[float]:
    """Return a deterministic local embedding suitable for pgvector storage."""

    vector = [0.0 for _ in range(RAG_EMBEDDING_DIMENSIONS)]
    tokens = TOKEN_PATTERN.findall(text.lower())
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % RAG_EMBEDDING_DIMENSIONS
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign

    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0 and tokens:
        digest = hashlib.sha256(" ".join(tokens).encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % RAG_EMBEDDING_DIMENSIONS
        vector[index] = 1.0 if digest[4] % 2 == 0 else -1.0
        norm = 1.0
    if norm == 0:
        return vector
    return [round(value / norm, 8) for value in vector]


async def _load_game_documents(
    session: AsyncSession,
    *,
    game_id: UUID,
) -> list[CorpusDocument]:
    memory_documents = await _load_all_ai_memory_documents(session, game_id=game_id)
    negotiation_documents = await load_negotiation_history_corpus_from_db(
        session,
        game_id=game_id,
    )
    decision_documents = await load_past_decision_corpus_from_db(session, game_id=game_id)
    return sorted(
        [*memory_documents, *negotiation_documents, *decision_documents],
        key=lambda document: document.document_id,
    )


async def _load_all_ai_memory_documents(
    session: AsyncSession,
    *,
    game_id: UUID,
) -> list[CorpusDocument]:
    result = await session.execute(
        sa.select(ai_memory_entries, ai_decisions.c.status.label("source_decision_status"))
        .select_from(
            ai_memory_entries.outerjoin(
                ai_decisions,
                ai_memory_entries.c.source_decision_id == ai_decisions.c.id,
            )
        )
        .where(ai_memory_entries.c.game_id == game_id)
        .order_by(ai_memory_entries.c.created_at, ai_memory_entries.c.id)
    )
    rows = [
        dict(row)
        for row in result.mappings().all()
        if memory_row_is_usable_for_context(dict(row))
    ]
    return build_ai_memory_corpus(rows)


async def _upsert_index_document(
    session: AsyncSession,
    document: CorpusDocument,
    *,
    game_id: UUID | None,
) -> None:
    metadata_blob = _index_metadata(document)
    scope_game_id = _uuid_or_none(metadata_blob.get("game_id"))
    if scope_game_id is None and document.source_type not in STATIC_SOURCE_TYPES:
        scope_game_id = game_id
    scope_player_id = _uuid_or_none(metadata_blob.get("player_id"))
    phase = _string_or_none(metadata_blob.get("phase"))
    index_key = _index_key(document, game_id=scope_game_id)
    searchable_text = f"{document.title} {document.text}"
    values = {
        "index_key": index_key,
        "document_id": document.document_id,
        "game_id": scope_game_id,
        "player_id": scope_player_id,
        "phase": phase,
        "source_type": document.source_type,
        "source_id": document.source_id,
        "title": document.title,
        "text": document.text,
        "metadata_blob": metadata_blob,
        "search_vector": sa.func.to_tsvector(sa.literal_column("'english'"), searchable_text),
        "embedding": embed_text(searchable_text),
        "updated_at": sa.func.now(),
    }
    statement = pg_insert(rag_index_entries).values(**values)
    await session.execute(
        statement.on_conflict_do_update(
            constraint="uq_rag_index_entries_index_key",
            set_={
                "document_id": statement.excluded.document_id,
                "game_id": statement.excluded.game_id,
                "player_id": statement.excluded.player_id,
                "phase": statement.excluded.phase,
                "source_type": statement.excluded.source_type,
                "source_id": statement.excluded.source_id,
                "title": statement.excluded.title,
                "text": statement.excluded.text,
                "metadata_blob": statement.excluded.metadata_blob,
                "search_vector": statement.excluded.search_vector,
                "embedding": statement.excluded.embedding,
                "updated_at": sa.func.now(),
            },
        )
    )


def _scope_filter(game_id: UUID | None) -> sa.ColumnElement[bool]:
    if game_id is None:
        return rag_index_entries.c.game_id.is_(None)
    return sa.or_(rag_index_entries.c.game_id.is_(None), rag_index_entries.c.game_id == game_id)


def _phase_filter(phase: str | None) -> sa.ColumnElement[bool]:
    if phase is None:
        return sa.true()
    return sa.or_(rag_index_entries.c.phase.is_(None), rag_index_entries.c.phase == phase)


def _visibility_filter(
    *,
    game_uuid: UUID | None,
    player_uuid: UUID | None,
) -> sa.ColumnElement[bool]:
    if player_uuid is None:
        return sa.and_(
            rag_index_entries.c.source_type != "ai_memory",
            rag_index_entries.c.source_type != "negotiation_history",
            rag_index_entries.c.source_type != "past_decision",
        )

    player_text = str(player_uuid)
    visible_memory_scopes = tuple(sorted(VISIBLE_MEMORY_SCOPES))
    metadata_blob = rag_index_entries.c.metadata_blob
    ai_memory_visible = sa.and_(
        rag_index_entries.c.source_type == "ai_memory",
        sa.or_(
            rag_index_entries.c.player_id == player_uuid,
            metadata_blob["visibility"].astext.in_(visible_memory_scopes),
        ),
    )
    past_decision_visible = sa.and_(
        rag_index_entries.c.source_type == "past_decision",
        rag_index_entries.c.player_id == player_uuid,
    )
    negotiation_visible = sa.and_(
        rag_index_entries.c.source_type == "negotiation_history",
        _negotiation_history_visible(
            game_uuid=game_uuid,
            player_text=player_text,
            metadata_blob=metadata_blob,
        ),
    )
    public_source_visible = rag_index_entries.c.source_type.in_(
        ("rules", "house_rules", "contract_examples")
    )
    return sa.or_(
        public_source_visible,
        ai_memory_visible,
        past_decision_visible,
        negotiation_visible,
    )


def _negotiation_history_visible(
    *,
    game_uuid: UUID | None,
    player_text: str,
    metadata_blob: sa.ColumnElement[Any],
) -> sa.ColumnElement[bool]:
    row_type = metadata_blob["row_type"].astext
    source_id = rag_index_entries.c.source_id
    negotiation_id = metadata_blob["negotiation_id"].astext
    sender_id = metadata_blob["sender_player_id"].astext
    recipient_id = metadata_blob["recipient_player_id"].astext
    opened_by_id = metadata_blob["opened_by_player_id"].astext
    participant_context_visible = metadata_blob["context"].contains(
        {"participant_player_ids": [player_text]}
    )

    visible_message_exists = sa.exists(
        sa.select(sa.literal(1)).where(
            _game_id_filter(negotiation_messages.c.game_id, game_uuid),
            sa.cast(negotiation_messages.c.negotiation_id, sa.String) == source_id,
            sa.or_(
                negotiation_messages.c.recipient_player_id.is_(None),
                sa.cast(negotiation_messages.c.sender_player_id, sa.String) == player_text,
                sa.cast(negotiation_messages.c.recipient_player_id, sa.String) == player_text,
            ),
        )
    )
    negotiation_row_visible = sa.and_(
        row_type == "negotiation",
        sa.or_(
            opened_by_id == player_text,
            participant_context_visible,
            visible_message_exists,
        ),
    )
    message_row_visible = sa.and_(
        row_type == "negotiation_message",
        sa.or_(
            recipient_id.is_(None),
            sender_id == player_text,
            recipient_id == player_text,
        ),
    )
    visible_negotiation_exists = sa.exists(
        sa.select(sa.literal(1)).where(
            _game_id_filter(negotiations.c.game_id, game_uuid),
            sa.cast(negotiations.c.id, sa.String) == negotiation_id,
            sa.or_(
                sa.cast(negotiations.c.opened_by_player_id, sa.String) == player_text,
                negotiations.c.context.contains({"participant_player_ids": [player_text]}),
                sa.exists(
                    sa.select(sa.literal(1)).where(
                        negotiation_messages.c.game_id == negotiations.c.game_id,
                        negotiation_messages.c.negotiation_id == negotiations.c.id,
                        sa.or_(
                            negotiation_messages.c.recipient_player_id.is_(None),
                            sa.cast(negotiation_messages.c.sender_player_id, sa.String)
                            == player_text,
                            sa.cast(negotiation_messages.c.recipient_player_id, sa.String)
                            == player_text,
                        ),
                    )
                ),
            ),
        )
    )
    deal_row_visible = sa.and_(
        row_type == "deal",
        sa.or_(
            metadata_blob["proposed_by_player_id"].astext == player_text,
            visible_negotiation_exists,
        ),
    )
    return sa.or_(negotiation_row_visible, message_row_visible, deal_row_visible)


async def _delete_legacy_game_scoped_static_entries(session: AsyncSession) -> None:
    await session.execute(
        rag_index_entries.delete().where(
            rag_index_entries.c.game_id.is_(None),
            rag_index_entries.c.source_type.in_(tuple(sorted(STATIC_SOURCE_TYPES))),
            rag_index_entries.c.index_key.like("game:%"),
        )
    )


def _game_id_filter(column: sa.ColumnElement[Any], game_uuid: UUID | None) -> sa.ColumnElement[bool]:
    if game_uuid is None:
        return sa.true()
    return column == game_uuid


def _result_from_row(
    row: Mapping[str, Any],
    *,
    rank: int,
    query_text: str,
) -> RetrievalSearchResult:
    metadata_blob = _mapping(row.get("metadata_blob"))
    score = _float_value(row.get("combined_score"))
    fts_rank = _float_value(row.get("fts_rank"))
    vector_similarity = _float_value(row.get("vector_similarity"))
    ranking = {
        "score_formula": "0.65 * fts_rank + 0.35 * vector_similarity",
        "fts_rank": fts_rank,
        "vector_similarity": vector_similarity,
        "combined_score": score,
        "query_text": query_text,
    }
    context = {
        "reason": "rag_retrieval_ranked_match",
        "visibility_allowed": True,
        "document_id": _string_or_none(metadata_blob.get("document_id")),
        "source_type": row["source_type"],
        "source_id": row["source_id"],
        "title": row["title"],
        "text": row["text"],
        "rank": rank,
        "score": score,
        "ranking": ranking,
        "metadata": metadata_blob,
    }
    return RetrievalSearchResult(
        index_entry_id=_coerce_uuid(row["id"]),
        source_type=str(row["source_type"]),
        source_id=str(row["source_id"]),
        title=str(row["title"]),
        text=str(row["text"]),
        metadata=metadata_blob,
        rank=rank,
        score=score,
        fts_rank=fts_rank,
        vector_similarity=vector_similarity,
        ranking=ranking,
        memory_entry_id=_memory_entry_id(str(row["source_type"]), str(row["source_id"])),
        retrieved_context=_json_safe(context),
    )


async def _persist_retrieval_records(
    session: AsyncSession,
    *,
    results: Sequence[RetrievalSearchResult],
    game_id: UUID | None,
    player_id: UUID | None,
    ai_decision_id: UUID | None,
    query_text: str,
    query_context: Mapping[str, Any],
    source_types: frozenset[str],
    phase: str | None,
) -> None:
    if game_id is None:
        return
    base_context = {
        **_json_safe(query_context),
        "phase": phase,
        "source_types": sorted(source_types),
        "retrieval_engine": "postgres_fts_pgvector_local_v1",
    }
    for result in results:
        await session.execute(
            retrieval_records.insert().values(
                game_id=game_id,
                player_id=player_id,
                ai_decision_id=ai_decision_id,
                memory_entry_id=result.memory_entry_id,
                query_text=query_text,
                query_context=base_context,
                retrieved_context=result.retrieved_context,
                source_type=result.source_type,
                source_id=result.source_id,
                rank=result.rank,
                score=result.score,
            )
        )


def _index_metadata(document: CorpusDocument) -> dict[str, Any]:
    metadata_blob = _json_safe(document.metadata)
    metadata_blob["document_id"] = document.document_id
    metadata_blob["source_type"] = document.source_type
    metadata_blob["source_id"] = document.source_id
    return metadata_blob


def _index_key(document: CorpusDocument, *, game_id: UUID | None) -> str:
    if game_id is None:
        return f"static:{document.document_id}"
    return f"game:{game_id}:{document.document_id}"


def _validate_source_types(source_types: Sequence[str] | None) -> frozenset[str]:
    if source_types is None:
        return frozenset(SOURCE_TYPES)
    values = frozenset(str(source_type) for source_type in source_types)
    invalid = values - set(SOURCE_TYPES)
    if invalid:
        raise ValueError(f"source_types contains unsupported values: {sorted(invalid)}")
    if not values:
        raise ValueError("source_types must not be empty")
    return values


def _memory_entry_id(source_type: str, source_id: str) -> UUID | None:
    if source_type != "ai_memory":
        return None
    try:
        return UUID(source_id)
    except ValueError:
        return None


def _memory_content_from_text(text: str) -> str:
    marker = "Content: "
    if marker in text:
        return text.split(marker, 1)[1]
    return text


def _uuid_or_none(value: Any) -> UUID | None:
    if value is None:
        return None
    try:
        return _coerce_uuid(value)
    except (TypeError, ValueError):
        return None


def _coerce_uuid(value: Any) -> UUID:
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


def _mapping(value: Any) -> dict[str, Any]:
    return _json_safe(value) if isinstance(value, Mapping) else {}


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, str | int | bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("retrieval audit JSON must not contain NaN or Infinity")
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("retrieval audit JSON must not contain NaN or Infinity")
        return number
    if isinstance(value, Mapping):
        return {
            str(key): _json_safe(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_json_safe(item) for item in value]
    return json.loads(json.dumps(value, sort_keys=True, default=str, ensure_ascii=True))


def _float_value(value: Any) -> float:
    if isinstance(value, Decimal):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("retrieval scores must be finite")
        return number
    if isinstance(value, int | float):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("retrieval scores must be finite")
        return number
    return 0.0


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


__all__ = [
    "DEFAULT_LIMIT",
    "FTS_WEIGHT",
    "MAX_LIMIT",
    "RetrievalSearchResult",
    "VECTOR_WEIGHT",
    "embed_text",
    "refresh_rag_index_entries",
    "search_retrieval",
]
