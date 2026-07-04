from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette import status

from app.db.persistence import EventPersistence, GameNotFoundError
from app.db.rejected_actions import RejectedActionAudit, RejectedActionRecord
from app.rules.actions import (
    ActionValidationError,
    GameAction,
    list_legal_actions,
    validate_action,
)
from app.rules.state import GameState


router = APIRouter(prefix="/games", tags=["rejected-actions"])


class ActionSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(min_length=1)
    type: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    expected_state_hash: str = Field(min_length=1)
    expected_event_sequence: int = Field(ge=0)


class RejectedActionResponse(BaseModel):
    id: UUID
    game_id: UUID
    actor_player_id: UUID | None
    action_type: str
    payload: Mapping[str, Any]
    reason_code: str
    validation_errors: Sequence[Mapping[str, Any]]
    legal_action_context: Mapping[str, Any] | None
    phase: str | None
    state_hash: str | None
    created_at: Any


class RejectedActionsResponse(BaseModel):
    rejected_actions: list[RejectedActionResponse]


@router.post("/{game_id}/actions")
async def submit_action(game_id: UUID, request: Request) -> JSONResponse:
    session_factory = _session_factory(request)
    persistence = EventPersistence(session_factory)
    audit = RejectedActionAudit(session_factory)

    try:
        state = await persistence.replay_from_latest_snapshot(game_id)
    except GameNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="game not found") from exc

    raw_payload, parse_errors = await _request_payload(request)
    if parse_errors:
        return await _persist_and_respond_to_rejection(
            audit=audit,
            game_id=game_id,
            state=state,
            raw_payload=raw_payload,
            actor_id=_raw_actor_id(raw_payload),
            action_type=_raw_action_type(raw_payload),
            submitted_payload=_raw_action_payload(raw_payload),
            validation_errors=parse_errors,
        )

    try:
        submission = ActionSubmission.model_validate(raw_payload)
    except ValidationError as exc:
        return await _persist_and_respond_to_rejection(
            audit=audit,
            game_id=game_id,
            state=state,
            raw_payload=raw_payload,
            actor_id=_raw_actor_id(raw_payload),
            action_type=_raw_action_type(raw_payload),
            submitted_payload=_raw_action_payload(raw_payload),
            validation_errors=_pydantic_errors(exc),
        )

    action = GameAction(
        actor_id=submission.actor_id,
        type=submission.type,
        payload=submission.payload,
        expected_state_hash=submission.expected_state_hash,
        expected_event_sequence=submission.expected_event_sequence,
    )
    try:
        validate_action(state, action)
    except ActionValidationError as exc:
        return await _persist_and_respond_to_rejection(
            audit=audit,
            game_id=game_id,
            state=state,
            raw_payload=submission.model_dump(mode="json"),
            actor_id=submission.actor_id,
            action_type=submission.type,
            submitted_payload=submission.payload,
            validation_errors=[issue.model_dump(mode="json") for issue in exc.errors],
        )

    return JSONResponse(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        content={
            "status": "not_implemented",
            "reason_code": "action_execution_not_implemented",
            "message": "Legal action execution is reserved for Phase 4 Stage 4.4.",
            "state_hash": state.state_hash(),
            "event_sequence": state.event_sequence,
        },
    )


@router.get("/{game_id}/rejected-actions", response_model=RejectedActionsResponse)
async def list_rejections(
    game_id: UUID,
    request: Request,
    actor_player_id: UUID | None = Query(default=None),
) -> RejectedActionsResponse:
    audit = RejectedActionAudit(_session_factory(request))
    records = await audit.list_rejected_actions(game_id, actor_player_id=actor_player_id)
    return RejectedActionsResponse(
        rejected_actions=[_response_record(record) for record in records],
    )


async def _persist_and_respond_to_rejection(
    *,
    audit: RejectedActionAudit,
    game_id: UUID,
    state: GameState,
    raw_payload: object,
    actor_id: str | None,
    action_type: str,
    submitted_payload: Mapping[str, Any],
    validation_errors: Sequence[Mapping[str, Any]],
) -> JSONResponse:
    reason_code = _reason_code(validation_errors)
    actor_player_id = await audit.resolve_actor_player_id(game_id=game_id, actor_id=actor_id)
    legal_action_context = _legal_action_context(state, actor_id)
    record = await audit.persist_rejected_action(
        game_id=game_id,
        actor_player_id=actor_player_id,
        action_type=action_type,
        payload=submitted_payload,
        reason_code=reason_code,
        validation_errors=validation_errors,
        legal_action_context=legal_action_context,
        phase=state.turn.phase.value,
        state_hash=state.state_hash(),
    )

    return JSONResponse(
        status_code=_status_code_for_reason(reason_code),
        content={
            "status": "rejected",
            "rejected_action_id": str(record.id),
            "reason_code": reason_code,
            "validation_errors": [dict(error) for error in validation_errors],
            "legal_action_context": legal_action_context,
            "submitted_action": raw_payload,
        },
    )


async def _request_payload(request: Request) -> tuple[object, list[dict[str, str]]]:
    try:
        payload: object = await request.json()
    except json.JSONDecodeError as exc:
        return {}, [
            {
                "code": "malformed_action",
                "message": f"request body must be valid JSON: {exc.msg}",
                "field": "body",
            }
        ]

    if not isinstance(payload, Mapping):
        return payload, [
            {
                "code": "malformed_action",
                "message": "request body must be a JSON object",
                "field": "body",
            }
        ]
    return dict(payload), []


def _raw_actor_id(raw_payload: object) -> str | None:
    if not isinstance(raw_payload, Mapping):
        return None
    actor_id = raw_payload.get("actor_id")
    return actor_id if isinstance(actor_id, str) else None


def _raw_action_type(raw_payload: object) -> str:
    if not isinstance(raw_payload, Mapping):
        return "MALFORMED_ACTION"
    action_type = raw_payload.get("type")
    return action_type if isinstance(action_type, str) and action_type else "MALFORMED_ACTION"


def _raw_action_payload(raw_payload: object) -> Mapping[str, Any]:
    if not isinstance(raw_payload, Mapping):
        return {"submitted": raw_payload}
    submitted_payload = raw_payload.get("payload", {})
    if isinstance(submitted_payload, Mapping):
        return dict(submitted_payload)
    return {"submitted_payload": submitted_payload}


def _pydantic_errors(exc: ValidationError) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    for error in exc.errors():
        field = ".".join(str(part) for part in error["loc"])
        errors.append(
            {
                "code": "malformed_action",
                "message": str(error["msg"]),
                "field": field,
            }
        )
    return errors


def _reason_code(validation_errors: Sequence[Mapping[str, Any]]) -> str:
    if not validation_errors:
        return "illegal_action"
    first_code = validation_errors[0].get("code")
    return first_code if isinstance(first_code, str) and first_code else "illegal_action"


def _status_code_for_reason(reason_code: str) -> int:
    if reason_code in {"stale_action", "mistimed_action"}:
        return status.HTTP_409_CONFLICT
    if reason_code == "unknown_action":
        return status.HTTP_400_BAD_REQUEST
    return 422


def _legal_action_context(state: GameState, actor_id: str | None) -> dict[str, Any]:
    legal_actions = [] if actor_id is None else list_legal_actions(state, actor_id)
    return {
        "actor_id": actor_id,
        "current_player_id": state.turn.current_player_id,
        "phase": state.turn.phase.value,
        "state_hash": state.state_hash(),
        "event_sequence": state.event_sequence,
        "legal_actions": [action.model_dump(mode="json") for action in legal_actions],
    }


def _response_record(record: RejectedActionRecord) -> RejectedActionResponse:
    return RejectedActionResponse.model_validate(record.model_dump())


def _session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    return request.app.state.database_session_factory


__all__ = ["router"]
