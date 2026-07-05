"""Local-only MCP tool handlers for Codex-facing backend access."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

import httpx
import sqlalchemy as sa
from fastapi import FastAPI
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.games import CreateDealRequest, _deal_proposed_by_player_id, _prepare_deal_terms
from app.ai.context_pack import public_game_state_summary
from app.db.metadata import contracts, games, obligations, players
from app.main import create_app
from app.rag.retrieval import RetrievalSearchResult, search_retrieval
from app.rules.state import GameState


JsonDict = dict[str, Any]
JsonMapping = Mapping[str, Any]

REQUIRED_LOCAL_MCP_TOOL_NAMES: tuple[str, ...] = (
    "get_game_state",
    "get_legal_actions",
    "search_rules",
    "search_memory",
    "inspect_contract",
    "validate_deal_draft",
    "submit_action",
)
RULE_SOURCE_TYPES = ("rules", "house_rules", "contract_examples")
DEFAULT_RULE_SOURCE_TYPES = RULE_SOURCE_TYPES
MEMORY_SOURCE_TYPES = ("ai_memory", "negotiation_history", "past_decision")


class LocalMCPToolError(ValueError):
    """Raised when a local MCP tool cannot execute with the supplied payload."""


@dataclass(frozen=True, slots=True)
class LocalMCPToolDefinition:
    name: str
    description: str
    input_schema: JsonDict
    mutates_game_state: bool = False

    def as_public_payload(self) -> JsonDict:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
            "transport": "stdio",
            "local_only": True,
            "mutates_game_state": self.mutates_game_state,
        }


@dataclass(slots=True)
class LocalMCPContext:
    """Runtime dependencies for local MCP tools."""

    api_app: FastAPI | None = None
    _owned_api_app: FastAPI | None = field(default=None, init=False, repr=False)

    def resolve_api_app(self) -> FastAPI:
        if self.api_app is not None:
            return self.api_app
        if self._owned_api_app is None:
            self._owned_api_app = create_app()
        return self._owned_api_app

    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        app = self.resolve_api_app()
        raw_session_factory = getattr(app.state, "database_session_factory")
        return cast(async_sessionmaker[AsyncSession], raw_session_factory)

    async def close(self) -> None:
        if self._owned_api_app is not None:
            await self._owned_api_app.state.database_engine.dispose()
            self._owned_api_app = None


def list_local_tools() -> list[JsonDict]:
    return [definition.as_public_payload() for definition in _TOOL_DEFINITIONS.values()]


async def call_local_tool(
    name: str,
    arguments: JsonMapping | None,
    *,
    context: LocalMCPContext | None = None,
) -> JsonDict:
    if name not in _TOOL_DEFINITIONS:
        raise LocalMCPToolError(f"unknown local MCP tool: {name}")
    tool_context = context or LocalMCPContext()
    tool_arguments = dict(arguments or {})
    _validate_required_arguments(name, tool_arguments)

    if name == "get_game_state":
        return await _get_game_state(tool_context, tool_arguments)
    if name == "get_legal_actions":
        return await _get_legal_actions(tool_context, tool_arguments)
    if name == "search_rules":
        return await _search_rules(tool_context, tool_arguments)
    if name == "search_memory":
        return await _search_memory(tool_context, tool_arguments)
    if name == "inspect_contract":
        return await _inspect_contract(tool_context, tool_arguments)
    if name == "validate_deal_draft":
        return await _validate_deal_draft(tool_context, tool_arguments)
    if name == "submit_action":
        return await _submit_action(tool_context, tool_arguments)
    raise LocalMCPToolError(f"unimplemented local MCP tool: {name}")


async def _get_game_state(context: LocalMCPContext, arguments: JsonMapping) -> JsonDict:
    game_id = _required_uuid_text(arguments, "game_id")
    source_path = f"/games/{game_id}/state"
    payload = await _fastapi_json_request(context, "GET", source_path)
    body = _required_mapping(payload, "body")
    raw_state = _required_mapping(body, "state")
    raw_state.pop("state_hash", None)
    state = GameState.model_validate(raw_state)
    return {
        "tool": "get_game_state",
        "source_path": source_path,
        "game_id": body["game_id"],
        "state": public_game_state_summary(state),
        "state_hash": body["state_hash"],
        "event_sequence": body["event_sequence"],
    }


async def _get_legal_actions(context: LocalMCPContext, arguments: JsonMapping) -> JsonDict:
    game_id = _required_uuid_text(arguments, "game_id")
    actor_player_id = _required_uuid_text(arguments, "actor_player_id")
    source_path = f"/games/{game_id}/legal-actions"
    payload = await _fastapi_json_request(
        context,
        "GET",
        source_path,
        params={"actor_player_id": actor_player_id},
    )
    return {"tool": "get_legal_actions", "source_path": source_path, **payload["body"]}


async def _search_rules(context: LocalMCPContext, arguments: JsonMapping) -> JsonDict:
    source_types = _source_types_argument(
        arguments.get("source_types"),
        default=DEFAULT_RULE_SOURCE_TYPES,
        allowed=RULE_SOURCE_TYPES,
        field="source_types",
    )
    return await _search(
        context,
        arguments,
        tool="search_rules",
        source_types=source_types,
    )


async def _search_memory(context: LocalMCPContext, arguments: JsonMapping) -> JsonDict:
    game_id = UUID(_required_uuid_text(arguments, "game_id"))
    player_id = _required_uuid_text(arguments, "player_id")
    source_types = _source_types_argument(
        arguments.get("source_types"),
        default=MEMORY_SOURCE_TYPES,
        allowed=MEMORY_SOURCE_TYPES,
        field="source_types",
    )
    session_factory = context.session_factory()
    async with session_factory() as session:
        await _ensure_game_exists(session, game_id)
        player_errors = await _player_reference_errors(
            session,
            game_id=game_id,
            player_ids=[player_id],
        )
    if player_errors:
        raise LocalMCPToolError(player_errors[0]["message"])
    return await _search(
        context,
        arguments,
        tool="search_memory",
        source_types=source_types,
    )


async def _search(
    context: LocalMCPContext,
    arguments: JsonMapping,
    *,
    tool: str,
    source_types: tuple[str, ...],
) -> JsonDict:
    query_text = _required_nonblank_text(arguments, "query_text")
    game_id = _optional_uuid_text(arguments.get("game_id"), "game_id")
    player_id = _optional_uuid_text(arguments.get("player_id"), "player_id")
    phase = _optional_nonblank_text(arguments.get("phase"), "phase")
    limit = _bounded_int(arguments.get("limit", 6), "limit", minimum=1, maximum=20)

    session_factory = context.session_factory()
    async with session_factory() as session:
        results = await search_retrieval(
            session,
            query_text=query_text,
            game_id=game_id,
            player_id=player_id,
            phase=phase,
            source_types=source_types,
            limit=limit,
            query_context={"source": "local_mcp", "tool": tool},
            audit=False,
        )

    return {
        "tool": tool,
        "retrieval_engine": "stage_9_2_local_retrieval",
        "local_only": True,
        "query_text": query_text,
        "game_id": game_id,
        "player_id": player_id,
        "phase": phase,
        "source_types": list(source_types),
        "results": [_retrieval_result_payload(result) for result in results],
    }


async def _inspect_contract(context: LocalMCPContext, arguments: JsonMapping) -> JsonDict:
    game_id = UUID(_required_uuid_text(arguments, "game_id"))
    contract_id = UUID(_required_uuid_text(arguments, "contract_id"))

    session_factory = context.session_factory()
    async with session_factory() as session:
        await _ensure_game_exists(session, game_id)
        contract_result = await session.execute(
            sa.select(contracts).where(
                contracts.c.game_id == game_id,
                contracts.c.id == contract_id,
            )
        )
        contract_row = contract_result.mappings().one_or_none()
        obligation_result = await session.execute(
            sa.select(obligations)
            .where(
                obligations.c.game_id == game_id,
                obligations.c.contract_id == contract_id,
            )
            .order_by(obligations.c.created_at, obligations.c.id)
        )

    if contract_row is None:
        return {
            "tool": "inspect_contract",
            "local_only": True,
            "game_id": str(game_id),
            "contract_id": str(contract_id),
            "found": False,
            "contract": None,
            "obligations": [],
        }
    return {
        "tool": "inspect_contract",
        "local_only": True,
        "game_id": str(game_id),
        "contract_id": str(contract_id),
        "found": True,
        "contract": _json_safe(dict(contract_row)),
        "obligations": [_json_safe(dict(row)) for row in obligation_result.mappings().all()],
    }


async def _validate_deal_draft(context: LocalMCPContext, arguments: JsonMapping) -> JsonDict:
    game_id = UUID(_required_uuid_text(arguments, "game_id"))
    draft = _required_mapping(arguments, "draft")
    validation_errors: list[JsonDict] = []

    try:
        payload = CreateDealRequest.model_validate(draft)
    except ValidationError as exc:
        validation_errors.extend(_pydantic_validation_errors(exc))
        return _deal_draft_result(
            game_id=game_id,
            valid=False,
            reason_code="invalid_deal_draft",
            validation_errors=validation_errors,
        )

    proposed_by_player_id = _deal_proposed_by_player_id(payload)
    participant_player_ids = [str(player_id) for player_id in payload.participant_player_ids or []]

    session_factory = context.session_factory()
    async with session_factory() as session:
        await _ensure_game_exists(session, game_id)
        validation_errors.extend(
            await _player_reference_errors(
                session,
                game_id=game_id,
                player_ids=[str(proposed_by_player_id), *participant_player_ids],
            )
        )

    prepared_terms = _prepare_deal_terms(
        payload.terms,
        participant_player_ids=participant_player_ids,
    )
    validation_errors.extend(dict(error) for error in prepared_terms.validation_errors)
    valid = not validation_errors
    return _deal_draft_result(
        game_id=game_id,
        valid=valid,
        reason_code=None
        if valid
        else "invalid_structured_deal"
        if prepared_terms.structured_deal
        else _reason_code(validation_errors),
        validation_errors=validation_errors,
        prepared_terms=prepared_terms.terms if valid else None,
        structured_deal=prepared_terms.structured_deal,
    )


async def _submit_action(context: LocalMCPContext, arguments: JsonMapping) -> JsonDict:
    game_id = _required_uuid_text(arguments, "game_id")
    idempotency_key = _required_nonblank_text(arguments, "idempotency_key")
    action = _required_mapping(arguments, "action")
    source_path = f"/games/{game_id}/actions"
    payload = await _fastapi_json_request(
        context,
        "POST",
        source_path,
        json_payload=dict(action),
        headers={"Idempotency-Key": idempotency_key},
        raise_for_status=False,
    )
    return {
        "tool": "submit_action",
        "source_path": source_path,
        "status_code": payload["status_code"],
        "response": payload["body"],
    }


async def _fastapi_json_request(
    context: LocalMCPContext,
    method: str,
    path: str,
    *,
    json_payload: JsonMapping | None = None,
    params: JsonMapping | None = None,
    headers: JsonMapping | None = None,
    raise_for_status: bool = True,
) -> JsonDict:
    app = context.resolve_api_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://local-mcp",
    ) as client:
        response = await client.request(
            method,
            path,
            json=json_payload,
            params=params,
            headers={str(key): str(value) for key, value in (headers or {}).items()},
        )
    body = response.json()
    if raise_for_status and response.status_code >= 400:
        raise LocalMCPToolError(f"FastAPI request {method} {path} failed: {body}")
    return {"status_code": response.status_code, "body": body}


async def _ensure_game_exists(session: AsyncSession, game_id: UUID) -> None:
    result = await session.execute(sa.select(games.c.id).where(games.c.id == game_id))
    if result.scalar_one_or_none() is None:
        raise LocalMCPToolError(f"game not found: {game_id}")


async def _player_reference_errors(
    session: AsyncSession,
    *,
    game_id: UUID,
    player_ids: Sequence[str],
) -> list[JsonDict]:
    requested_ids = sorted({player_id for player_id in player_ids if player_id})
    if not requested_ids:
        return []
    result = await session.execute(
        sa.select(players.c.id).where(
            players.c.game_id == game_id,
            players.c.id.in_([UUID(player_id) for player_id in requested_ids]),
        )
    )
    found_ids = {str(player_id) for player_id in result.scalars().all()}
    return [
        {
            "code": "player_not_in_game",
            "message": "player must belong to the game",
            "field": "player_id",
        }
        for player_id in requested_ids
        if player_id not in found_ids
    ]


def _retrieval_result_payload(result: RetrievalSearchResult) -> JsonDict:
    return {
        "index_entry_id": str(result.index_entry_id),
        "source_type": result.source_type,
        "source_id": result.source_id,
        "title": result.title,
        "text": result.text,
        "metadata": _json_safe(result.metadata),
        "rank": result.rank,
        "score": result.score,
        "fts_rank": result.fts_rank,
        "vector_similarity": result.vector_similarity,
        "ranking": _json_safe(result.ranking),
        "retrieved_context": _json_safe(result.retrieved_context),
    }


def _deal_draft_result(
    *,
    game_id: UUID,
    valid: bool,
    reason_code: str | None,
    validation_errors: Sequence[JsonMapping],
    prepared_terms: JsonMapping | None = None,
    structured_deal: bool | None = None,
) -> JsonDict:
    return {
        "tool": "validate_deal_draft",
        "game_id": str(game_id),
        "valid": valid,
        "reason_code": reason_code,
        "validation_errors": [dict(error) for error in validation_errors],
        "structured_deal": structured_deal,
        "prepared_terms": None if prepared_terms is None else _json_safe(prepared_terms),
        "created_deal": False,
        "created_contract": False,
        "created_obligation": False,
        "created_event": False,
        "mutated_negotiation": False,
    }


def _validate_required_arguments(name: str, arguments: JsonMapping) -> None:
    schema = _TOOL_DEFINITIONS[name].input_schema
    required = schema.get("required", [])
    if not isinstance(required, Sequence):
        return
    missing = [field for field in required if field not in arguments]
    if missing:
        raise LocalMCPToolError(f"{name} missing required argument(s): {', '.join(missing)}")


def _required_mapping(arguments: JsonMapping, field_name: str) -> JsonDict:
    value = arguments.get(field_name)
    if not isinstance(value, Mapping):
        raise LocalMCPToolError(f"{field_name} must be an object")
    return dict(value)


def _required_uuid_text(arguments: JsonMapping, field_name: str) -> str:
    value = _required_nonblank_text(arguments, field_name)
    try:
        return str(UUID(value))
    except ValueError as exc:
        raise LocalMCPToolError(f"{field_name} must be a UUID string") from exc


def _optional_uuid_text(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    text = _required_text_value(value, field_name)
    if not text:
        return None
    try:
        return str(UUID(text))
    except ValueError as exc:
        raise LocalMCPToolError(f"{field_name} must be a UUID string") from exc


def _required_nonblank_text(arguments: JsonMapping, field_name: str) -> str:
    value = arguments.get(field_name)
    text = _required_text_value(value, field_name)
    if not text:
        raise LocalMCPToolError(f"{field_name} must not be blank")
    return text


def _optional_nonblank_text(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    text = _required_text_value(value, field_name)
    return text or None


def _required_text_value(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise LocalMCPToolError(f"{field_name} must be a string")
    return value.strip()


def _bounded_int(value: Any, field_name: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise LocalMCPToolError(f"{field_name} must be an integer")
    if value < minimum or value > maximum:
        raise LocalMCPToolError(f"{field_name} must be between {minimum} and {maximum}")
    return value


def _source_types_argument(
    value: Any,
    *,
    default: Sequence[str],
    allowed: Sequence[str],
    field: str,
) -> tuple[str, ...]:
    if value is None:
        return tuple(default)
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        raise LocalMCPToolError(f"{field} must be a list of strings")
    values = tuple(str(item) for item in value)
    invalid = sorted(set(values) - set(allowed))
    if invalid:
        raise LocalMCPToolError(f"{field} contains unsupported values: {invalid}")
    if not values:
        raise LocalMCPToolError(f"{field} must not be empty")
    return values


def _pydantic_validation_errors(exc: ValidationError) -> list[JsonDict]:
    errors: list[JsonDict] = []
    for error in exc.errors():
        loc = ".".join(str(item) for item in error.get("loc", ()))
        message = str(error.get("msg", "invalid deal draft"))
        errors.append({"code": "invalid_deal_draft", "message": message, "field": loc or None})
    return errors


def _reason_code(validation_errors: Sequence[JsonMapping]) -> str:
    if not validation_errors:
        return "invalid_deal_draft"
    first_code = validation_errors[0].get("code")
    return str(first_code) if isinstance(first_code, str) and first_code else "invalid_deal_draft"


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, str | int | bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("MCP JSON payload must not contain NaN or Infinity")
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("MCP JSON payload must not contain NaN or Infinity")
        return number
    if isinstance(value, Mapping):
        return {
            str(key): _json_safe(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_json_safe(item) for item in value]
    return str(value)


def _schema_ref(description: str, properties: JsonMapping, required: Sequence[str]) -> JsonDict:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "description": description,
        "additionalProperties": False,
        "properties": dict(properties),
        "required": list(required),
    }


UUID_SCHEMA: JsonDict = {"type": "string", "format": "uuid"}
LIMIT_SCHEMA: JsonDict = {"type": "integer", "minimum": 1, "maximum": 20, "default": 6}
SOURCE_TYPES_RULE_SCHEMA: JsonDict = {
    "type": "array",
    "items": {"type": "string", "enum": list(RULE_SOURCE_TYPES)},
    "minItems": 1,
    "uniqueItems": True,
}
SOURCE_TYPES_MEMORY_SCHEMA: JsonDict = {
    "type": "array",
    "items": {"type": "string", "enum": list(MEMORY_SOURCE_TYPES)},
    "minItems": 1,
    "uniqueItems": True,
}

_TOOL_DEFINITIONS: dict[str, LocalMCPToolDefinition] = {
    "get_game_state": LocalMCPToolDefinition(
        name="get_game_state",
        description="Read the current replayed game state through the local FastAPI API.",
        input_schema=_schema_ref(
            "Fetch current game state by game id.",
            {"game_id": UUID_SCHEMA},
            ("game_id",),
        ),
    ),
    "get_legal_actions": LocalMCPToolDefinition(
        name="get_legal_actions",
        description="Read backend-generated legal actions for one actor.",
        input_schema=_schema_ref(
            "Fetch legal actions through the FastAPI legal-actions endpoint.",
            {"game_id": UUID_SCHEMA, "actor_player_id": UUID_SCHEMA},
            ("game_id", "actor_player_id"),
        ),
    ),
    "search_rules": LocalMCPToolDefinition(
        name="search_rules",
        description="Search indexed local rules, house rules, and contract examples.",
        input_schema=_schema_ref(
            "Search local static retrieval sources without mutating game state.",
            {
                "query_text": {"type": "string", "minLength": 1},
                "game_id": UUID_SCHEMA,
                "player_id": UUID_SCHEMA,
                "phase": {"type": "string", "minLength": 1},
                "source_types": SOURCE_TYPES_RULE_SCHEMA,
                "limit": LIMIT_SCHEMA,
            },
            ("query_text",),
        ),
    ),
    "search_memory": LocalMCPToolDefinition(
        name="search_memory",
        description="Search indexed local AI memory and history with Stage 9.2 visibility filters.",
        input_schema=_schema_ref(
            "Search game-scoped memory retrieval sources for one visible player scope.",
            {
                "query_text": {"type": "string", "minLength": 1},
                "game_id": UUID_SCHEMA,
                "player_id": UUID_SCHEMA,
                "phase": {"type": "string", "minLength": 1},
                "source_types": SOURCE_TYPES_MEMORY_SCHEMA,
                "limit": LIMIT_SCHEMA,
            },
            ("query_text", "game_id", "player_id"),
        ),
    ),
    "inspect_contract": LocalMCPToolDefinition(
        name="inspect_contract",
        description="Read one persisted contract and its obligations.",
        input_schema=_schema_ref(
            "Inspect a contract by game id and contract id.",
            {"game_id": UUID_SCHEMA, "contract_id": UUID_SCHEMA},
            ("game_id", "contract_id"),
        ),
    ),
    "validate_deal_draft": LocalMCPToolDefinition(
        name="validate_deal_draft",
        description="Validate a deal draft without creating deal, contract, obligation, or event rows.",
        input_schema=_schema_ref(
            "Validate a draft create-deal payload without mutation.",
            {
                "game_id": UUID_SCHEMA,
                "draft": {
                    "type": "object",
                    "additionalProperties": True,
                    "description": "CreateDealRequest-shaped draft payload.",
                },
            },
            ("game_id", "draft"),
        ),
    ),
    "submit_action": LocalMCPToolDefinition(
        name="submit_action",
        description="Submit one action through the local FastAPI validation endpoint.",
        input_schema=_schema_ref(
            "Submit an action through /games/{game_id}/actions with Idempotency-Key.",
            {
                "game_id": UUID_SCHEMA,
                "idempotency_key": {"type": "string", "minLength": 1},
                "action": {
                    "type": "object",
                    "additionalProperties": True,
                    "required": [
                        "actor_id",
                        "type",
                        "payload",
                        "expected_state_hash",
                        "expected_event_sequence",
                    ],
                    "properties": {
                        "actor_id": {"type": "string", "minLength": 1},
                        "type": {"type": "string", "minLength": 1},
                        "payload": {"type": "object", "additionalProperties": True},
                        "expected_state_hash": {"type": "string", "minLength": 1},
                        "expected_event_sequence": {"type": "integer", "minimum": 0},
                    },
                },
            },
            ("game_id", "idempotency_key", "action"),
        ),
        mutates_game_state=True,
    ),
}
