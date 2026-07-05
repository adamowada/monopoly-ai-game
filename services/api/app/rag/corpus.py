from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.metadata import (
    ai_decisions,
    ai_memory_entries,
    deals,
    negotiation_messages,
    negotiations,
)


SourceType = Literal[
    "rules",
    "house_rules",
    "contract_examples",
    "ai_memory",
    "negotiation_history",
    "past_decision",
]

SOURCE_TYPES: tuple[SourceType, ...] = (
    "rules",
    "house_rules",
    "contract_examples",
    "ai_memory",
    "negotiation_history",
    "past_decision",
)

REPO_ROOT = Path(__file__).resolve().parents[4]
CONTENT_RULES_DIR = REPO_ROOT / "content" / "rules"
CLASSIC_RULES_PATH = CONTENT_RULES_DIR / "classic_monopoly.json"
HOUSE_RULES_PATH = CONTENT_RULES_DIR / "house_rules_and_deviations.json"
CONTRACT_EXAMPLES_PATH = CONTENT_RULES_DIR / "contract_examples.json"


@dataclass(frozen=True)
class CorpusDocument:
    document_id: str
    source_type: SourceType
    source_id: str
    title: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "title": self.title,
            "text": self.text,
            "metadata": _json_safe(self.metadata),
        }


def build_static_local_corpus(
    *,
    content_rules_dir: Path = CONTENT_RULES_DIR,
) -> list[CorpusDocument]:
    """Build deterministic local static documents without database access."""

    documents = [
        *build_rules_corpus(content_rules_dir / "classic_monopoly.json"),
        *build_house_rule_corpus(content_rules_dir / "house_rules_and_deviations.json"),
        *build_contract_example_corpus(content_rules_dir / "contract_examples.json"),
    ]
    return _sort_documents(documents)


def build_rules_corpus(path: Path = CLASSIC_RULES_PATH) -> list[CorpusDocument]:
    data = _read_json(path)
    version = _string_value(data.get("version"), "unknown-ruleset")
    currency = _mapping(data.get("currency"))
    documents: list[CorpusDocument] = [
        _document(
            source_type="rules",
            source_id=f"ruleset_{version}",
            title=f"Ruleset {version}",
            text=(
                f"Classic Monopoly ruleset {version}. Currency "
                f"{currency.get('name', 'Game dollars')} uses symbol "
                f"{currency.get('symbol', '$')}."
            ),
            metadata={"kind": "ruleset", "version": version, "file": str(path)},
        )
    ]

    for space in _sequence(data.get("board")):
        documents.append(_board_space_document(space, path=path, version=version))
    for group in _sequence(data.get("property_groups")):
        documents.append(_property_group_document(group, path=path, version=version))
    for property_data in _sequence(data.get("properties")):
        documents.append(_property_document(property_data, path=path, version=version))

    decks = _mapping(data.get("decks"))
    for deck_name in ("chance", "community_chest"):
        for card in _sequence(decks.get(deck_name)):
            documents.append(_card_document(card, path=path, version=version))

    bank_inventory = _mapping(data.get("bank_inventory"))
    documents.append(
        _document(
            source_type="rules",
            source_id="bank_inventory",
            title="Classic Bank Inventory",
            text=(
                "Classic bank inventory contains "
                f"{bank_inventory.get('houses')} houses and {bank_inventory.get('hotels')} hotels."
            ),
            metadata={
                "kind": "bank_inventory",
                "version": version,
                "bank_inventory": bank_inventory,
                "file": str(path),
            },
        )
    )

    return _sort_documents(documents)


def build_house_rule_corpus(path: Path = HOUSE_RULES_PATH) -> list[CorpusDocument]:
    data = _read_json(path)
    version = _string_value(data.get("version"), "house-rules")
    documents = []
    for entry in _sequence(data.get("entries")):
        source_id = _required_string(entry, "id")
        title = _string_value(entry.get("title"), source_id)
        text = _normalize_text(f"{title}. {_string_value(entry.get('text'), '')}")
        documents.append(
            _document(
                source_type="house_rules",
                source_id=source_id,
                title=title,
                text=text,
                metadata={
                    "kind": "house_rule_or_deviation",
                    "version": version,
                    "category": entry.get("category"),
                    "entry_metadata": _json_safe(entry.get("metadata") or {}),
                    "file": str(path),
                },
            )
        )
    return _sort_documents(documents)


def build_contract_example_corpus(path: Path = CONTRACT_EXAMPLES_PATH) -> list[CorpusDocument]:
    data = _read_json(path)
    version = _string_value(data.get("version"), "contract-examples")
    documents = []
    for example in _sequence(data.get("examples")):
        source_id = _required_string(example, "id")
        title = _string_value(example.get("title"), source_id)
        instruments = _sequence(example.get("instruments"))
        text = _normalize_text(
            " ".join(
                [
                    title,
                    _string_value(example.get("summary"), ""),
                    _string_value(example.get("text"), ""),
                    f"Instruments: {_json_dumps(instruments)}.",
                    f"Validation notes: {_json_dumps(example.get('validation_notes') or [])}.",
                ]
            )
        )
        documents.append(
            _document(
                source_type="contract_examples",
                source_id=source_id,
                title=title,
                text=text,
                metadata={
                    "kind": "contract_example",
                    "version": version,
                    "parties": _json_safe(example.get("parties") or []),
                    "instruments": _json_safe(instruments),
                    "validation_notes": _json_safe(example.get("validation_notes") or []),
                    "file": str(path),
                },
            )
        )
    return _sort_documents(documents)


def build_ai_memory_corpus(memory_rows: Sequence[Mapping[str, Any]]) -> list[CorpusDocument]:
    documents = []
    for row in memory_rows:
        source_id = _row_id(row)
        category = _string_value(row.get("category"), "memory")
        visibility = _string_value(row.get("visibility"), "private")
        importance = row.get("importance")
        content = _string_value(row.get("content"), "")
        text = _normalize_text(
            f"AI memory {category}. Visibility {visibility}. "
            f"Importance {importance}. Content: {content}"
        )
        documents.append(
            _document(
                source_type="ai_memory",
                source_id=source_id,
                title=f"AI Memory: {category}",
                text=text,
                metadata=_row_metadata(
                    row,
                    row_type="ai_memory_entry",
                    include_keys=(
                        "game_id",
                        "player_id",
                        "ai_profile_id",
                        "source_decision_id",
                        "source_event_id",
                        "source_negotiation_message_id",
                        "superseded_by_memory_id",
                        "category",
                        "visibility",
                        "importance",
                        "metadata_blob",
                        "created_at",
                        "updated_at",
                    ),
                ),
            )
        )
    return _sort_documents(documents)


def build_negotiation_history_corpus(
    *,
    negotiation_rows: Sequence[Mapping[str, Any]] = (),
    message_rows: Sequence[Mapping[str, Any]] = (),
    deal_rows: Sequence[Mapping[str, Any]] = (),
) -> list[CorpusDocument]:
    documents: list[CorpusDocument] = []
    for row in negotiation_rows:
        source_id = _row_id(row)
        title = f"Negotiation {source_id}"
        text = _normalize_text(
            f"Negotiation {source_id}. Status {_string_value(row.get('status'), 'unknown')}. "
            f"Phase {_string_value(row.get('phase'), 'unknown')}. "
            f"Round {row.get('round_number')}. Context: {_json_dumps(row.get('context') or {})}."
        )
        documents.append(
            _document(
                source_type="negotiation_history",
                source_id=source_id,
                title=title,
                text=text,
                metadata=_row_metadata(
                    row,
                    row_type="negotiation",
                    include_keys=(
                        "game_id",
                        "opened_by_player_id",
                        "status",
                        "phase",
                        "round_number",
                        "context",
                        "created_at",
                        "updated_at",
                        "closed_at",
                    ),
                ),
            )
        )

    for row in message_rows:
        source_id = _row_id(row)
        body = _string_value(row.get("body"), "")
        text = _normalize_text(
            f"Negotiation message {source_id}. Negotiation {row.get('negotiation_id')}. "
            f"Type {_string_value(row.get('message_type'), 'message')}. "
            f"From {row.get('sender_player_id')} to {row.get('recipient_player_id')}. "
            f"Body: {body}. Payload: {_json_dumps(row.get('payload') or {})}."
        )
        documents.append(
            _document(
                source_type="negotiation_history",
                source_id=source_id,
                title=f"Negotiation Message: {_string_value(row.get('message_type'), 'message')}",
                text=text,
                metadata=_row_metadata(
                    row,
                    row_type="negotiation_message",
                    include_keys=(
                        "game_id",
                        "negotiation_id",
                        "sender_player_id",
                        "recipient_player_id",
                        "message_type",
                        "payload",
                        "created_at",
                    ),
                ),
            )
        )

    for row in deal_rows:
        source_id = _row_id(row)
        text = _normalize_text(
            f"Negotiation deal {source_id}. Negotiation {row.get('negotiation_id')}. "
            f"Status {_string_value(row.get('status'), 'unknown')}. Version {row.get('version')}. "
            f"Proposed by {row.get('proposed_by_player_id')}. "
            f"Terms: {_json_dumps(row.get('terms') or {})}. "
            f"Validation errors: {_json_dumps(row.get('validation_errors') or [])}."
        )
        documents.append(
            _document(
                source_type="negotiation_history",
                source_id=source_id,
                title=f"Negotiation Deal: version {row.get('version')}",
                text=text,
                metadata=_row_metadata(
                    row,
                    row_type="deal",
                    include_keys=(
                        "game_id",
                        "negotiation_id",
                        "proposed_by_player_id",
                        "parent_deal_id",
                        "status",
                        "version",
                        "terms",
                        "validation_errors",
                        "created_at",
                        "updated_at",
                        "accepted_at",
                    ),
                ),
            )
        )
    return _sort_documents(documents)


def build_past_decision_corpus(decision_rows: Sequence[Mapping[str, Any]]) -> list[CorpusDocument]:
    documents = []
    for row in decision_rows:
        source_id = _row_id(row)
        decision_type = _string_value(row.get("decision_type"), "ai_decision")
        status = _string_value(row.get("status"), "unknown")
        phase = _string_value(row.get("phase"), "unknown")
        text = _normalize_text(
            f"Past AI decision {source_id}. Type {decision_type}. Status {status}. "
            f"Phase {phase}. State hash {row.get('state_hash')}. "
            f"Prompt context hash {row.get('prompt_context_hash')}. "
            f"Parsed output: {_json_dumps(row.get('parsed_output') or {})}. "
            f"Validation result: {_json_dumps(row.get('validation_result') or {})}. "
            f"Raw output: {_string_value(row.get('raw_output'), '')}"
        )
        documents.append(
            _document(
                source_type="past_decision",
                source_id=source_id,
                title=f"Past AI Decision: {decision_type} {status}",
                text=text,
                metadata=_row_metadata(
                    row,
                    row_type="ai_decision",
                    include_keys=(
                        "game_id",
                        "player_id",
                        "ai_profile_id",
                        "negotiation_id",
                        "accepted_event_id",
                        "rejected_action_id",
                        "decision_type",
                        "status",
                        "phase",
                        "state_hash",
                        "prompt_context_hash",
                        "prompt_context",
                        "parsed_output",
                        "validation_result",
                        "created_at",
                    ),
                ),
            )
        )
    return _sort_documents(documents)


async def load_ai_memory_corpus_from_db(
    session: AsyncSession,
    *,
    game_id: str | UUID,
    player_id: str | UUID | None = None,
    limit: int | None = None,
) -> list[CorpusDocument]:
    statement = sa.select(ai_memory_entries).where(ai_memory_entries.c.game_id == _coerce_uuid(game_id))
    if player_id is not None:
        statement = statement.where(ai_memory_entries.c.player_id == _coerce_uuid(player_id))
    statement = statement.order_by(ai_memory_entries.c.created_at, ai_memory_entries.c.id)
    if limit is not None:
        statement = statement.limit(limit)
    result = await session.execute(statement)
    return build_ai_memory_corpus(_string_key_rows(result.mappings()))


async def load_negotiation_history_corpus_from_db(
    session: AsyncSession,
    *,
    game_id: str | UUID,
    negotiation_id: str | UUID | None = None,
    limit_per_table: int | None = None,
) -> list[CorpusDocument]:
    game_uuid = _coerce_uuid(game_id)
    negotiation_statement = sa.select(negotiations).where(negotiations.c.game_id == game_uuid)
    message_statement = sa.select(negotiation_messages).where(negotiation_messages.c.game_id == game_uuid)
    deal_statement = sa.select(deals).where(deals.c.game_id == game_uuid)

    if negotiation_id is not None:
        negotiation_uuid = _coerce_uuid(negotiation_id)
        negotiation_statement = negotiation_statement.where(negotiations.c.id == negotiation_uuid)
        message_statement = message_statement.where(
            negotiation_messages.c.negotiation_id == negotiation_uuid
        )
        deal_statement = deal_statement.where(deals.c.negotiation_id == negotiation_uuid)

    negotiation_statement = negotiation_statement.order_by(negotiations.c.created_at, negotiations.c.id)
    message_statement = message_statement.order_by(negotiation_messages.c.created_at, negotiation_messages.c.id)
    deal_statement = deal_statement.order_by(deals.c.created_at, deals.c.id)
    if limit_per_table is not None:
        negotiation_statement = negotiation_statement.limit(limit_per_table)
        message_statement = message_statement.limit(limit_per_table)
        deal_statement = deal_statement.limit(limit_per_table)

    negotiation_result = await session.execute(negotiation_statement)
    message_result = await session.execute(message_statement)
    deal_result = await session.execute(deal_statement)
    return build_negotiation_history_corpus(
        negotiation_rows=_string_key_rows(negotiation_result.mappings()),
        message_rows=_string_key_rows(message_result.mappings()),
        deal_rows=_string_key_rows(deal_result.mappings()),
    )


async def load_past_decision_corpus_from_db(
    session: AsyncSession,
    *,
    game_id: str | UUID,
    player_id: str | UUID | None = None,
    limit: int | None = None,
) -> list[CorpusDocument]:
    statement = sa.select(ai_decisions).where(ai_decisions.c.game_id == _coerce_uuid(game_id))
    if player_id is not None:
        statement = statement.where(ai_decisions.c.player_id == _coerce_uuid(player_id))
    statement = statement.order_by(ai_decisions.c.created_at, ai_decisions.c.id)
    if limit is not None:
        statement = statement.limit(limit)
    result = await session.execute(statement)
    return build_past_decision_corpus(_string_key_rows(result.mappings()))


def _board_space_document(
    space: Mapping[str, Any],
    *,
    path: Path,
    version: str,
) -> CorpusDocument:
    source_id = _required_string(space, "id")
    name = _string_value(space.get("name"), source_id)
    space_type = _string_value(space.get("type"), "space")
    text = _normalize_text(
        f"Board space {name} at position {space.get('position')} is type {space_type}. "
        f"Property id {space.get('property_id')}. Deck {space.get('deck')}. "
        f"Tax amount {space.get('amount')}."
    )
    return _document(
        source_type="rules",
        source_id=source_id,
        title=f"Board Space: {name}",
        text=text,
        metadata={"kind": "board_space", "version": version, "space": space, "file": str(path)},
    )


def _property_group_document(
    group: Mapping[str, Any],
    *,
    path: Path,
    version: str,
) -> CorpusDocument:
    source_id = _required_string(group, "id")
    name = _string_value(group.get("name"), source_id)
    text = _normalize_text(
        f"Property group {name} ({source_id}) is kind {group.get('kind')} "
        f"with color {group.get('color')}. Properties: "
        f"{', '.join(str(item) for item in _sequence(group.get('property_ids')))}. "
        f"House cost {group.get('house_cost')}."
    )
    return _document(
        source_type="rules",
        source_id=source_id,
        title=f"Property Group: {name}",
        text=text,
        metadata={"kind": "property_group", "version": version, "group": group, "file": str(path)},
    )


def _property_document(
    property_data: Mapping[str, Any],
    *,
    path: Path,
    version: str,
) -> CorpusDocument:
    source_id = _required_string(property_data, "id")
    name = _string_value(property_data.get("name"), source_id)
    property_kind = _string_value(property_data.get("kind"), "property")
    rent_text = _property_rent_text(property_data)
    text = _normalize_text(
        f"Property {name} ({source_id}) is a {property_kind} in group "
        f"{property_data.get('group')} at board position {property_data.get('board_position')}. "
        f"Price {property_data.get('price')}. Mortgage value "
        f"{property_data.get('mortgage_value')}. {rent_text}"
    )
    return _document(
        source_type="rules",
        source_id=source_id,
        title=f"Property: {name}",
        text=text,
        metadata={
            "kind": "property",
            "version": version,
            "property": property_data,
            "file": str(path),
        },
    )


def _card_document(card: Mapping[str, Any], *, path: Path, version: str) -> CorpusDocument:
    source_id = _required_string(card, "id")
    title = _string_value(card.get("title"), source_id)
    text = _normalize_text(
        f"Card {title} ({source_id}) belongs to {card.get('deck')}. "
        f"Description: {_string_value(card.get('description'), '')}. "
        f"Effect: {_json_dumps(card.get('effect') or {})}."
    )
    return _document(
        source_type="rules",
        source_id=source_id,
        title=f"Card: {title}",
        text=text,
        metadata={"kind": "card", "version": version, "card": card, "file": str(path)},
    )


def _property_rent_text(property_data: Mapping[str, Any]) -> str:
    kind = _string_value(property_data.get("kind"), "")
    if kind == "street":
        rents = list(_sequence(property_data.get("rents")))
        if len(rents) == 6:
            return (
                f"Base rent {rents[0]}; one house rent {rents[1]}; "
                f"two houses rent {rents[2]}; three houses rent {rents[3]}; "
                f"four houses rent {rents[4]}; hotel rent {rents[5]}. "
                f"House cost {property_data.get('house_cost')}; hotel cost "
                f"{property_data.get('hotel_cost')}."
            )
    if kind == "railroad":
        rents = list(_sequence(property_data.get("rent_by_owned_count")))
        if rents:
            return (
                "Railroad rent by owned count: "
                + ", ".join(f"{index + 1} owned rent {rent}" for index, rent in enumerate(rents))
                + "."
            )
    if kind == "utility":
        multipliers = list(_sequence(property_data.get("rent_multipliers")))
        if len(multipliers) == 2:
            return (
                "Utility rent uses dice total multipliers: "
                f"one utility {multipliers[0]}x, both utilities {multipliers[1]}x."
            )
    return "No rent table is defined."


def _document(
    *,
    source_type: SourceType,
    source_id: str,
    title: str,
    text: str,
    metadata: Mapping[str, Any] | None = None,
) -> CorpusDocument:
    normalized_text = _normalize_text(text)
    normalized_title = _normalize_text(title)
    normalized_source_id = _normalize_identifier(source_id)
    return CorpusDocument(
        document_id=f"{source_type}:{normalized_source_id}",
        source_type=source_type,
        source_id=normalized_source_id,
        title=normalized_title,
        text=normalized_text,
        metadata=dict(_json_safe(metadata or {})),
    )


def _read_json(path: Path) -> Mapping[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, Mapping):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> Sequence[Any]:
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return value
    return ()


def _required_string(row: Mapping[str, Any], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"missing required string field {key}")
    return value


def _string_value(value: Any, fallback: str) -> str:
    if value is None:
        return fallback
    text = str(value)
    return text if text else fallback


def _row_id(row: Mapping[str, Any]) -> str:
    value = row.get("id")
    if value is None:
        raise ValueError("dynamic corpus row is missing id")
    return _normalize_identifier(str(value))


def _row_metadata(
    row: Mapping[str, Any],
    *,
    row_type: str,
    include_keys: Sequence[str],
) -> dict[str, Any]:
    metadata = {"row_type": row_type}
    for key in include_keys:
        if key in row:
            metadata[key] = row[key]
    return _json_safe(metadata)


def _string_key_rows(rows: Iterable[Mapping[Any, Any]]) -> list[dict[str, Any]]:
    return [{str(key): value for key, value in row.items()} for row in rows]


def _normalize_identifier(value: str) -> str:
    normalized = _normalize_text(value)
    if not normalized:
        raise ValueError("corpus source id cannot be empty")
    return normalized


def _normalize_text(value: str) -> str:
    return " ".join(str(value).split())


def _sort_documents(documents: Sequence[CorpusDocument]) -> list[CorpusDocument]:
    return sorted(documents, key=lambda document: document.document_id)


def _json_dumps(value: Any) -> str:
    return json.dumps(_json_safe(value), sort_keys=True, ensure_ascii=True)


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        return {
            str(key): _json_safe(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, set | frozenset):
        return [_json_safe(item) for item in sorted(value, key=str)]
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_json_safe(item) for item in value]
    return str(value)


def _coerce_uuid(value: str | UUID) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))
