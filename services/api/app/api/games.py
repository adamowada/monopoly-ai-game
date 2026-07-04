from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

import sqlalchemy as sa
from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette import status

from app.db.metadata import (
    action_idempotency_keys,
    deals,
    games,
    negotiation_messages,
    negotiations,
    players,
    rejected_actions,
)
from app.db.persistence import (
    AcceptedEventRecord,
    AcceptedEventTemplate,
    EventPersistence,
    GameNotFoundError,
    StaleEventSequenceError,
)
from app.db.rejected_actions import RejectedActionAudit, RejectedActionRecord
from app.rules.actions import (
    ActionValidationError,
    GameAction,
    execute_action,
    list_legal_actions,
)
from app.rules.state import GameState, PlayerSetup, create_initial_game_state


router = APIRouter(prefix="/games", tags=["games"])


NEGOTIATION_STATUS_OPENED = "opened"
NEGOTIATION_STATUS_ACTIVE = "active"
NEGOTIATION_STATUS_COUNTERED = "countered"
NEGOTIATION_STATUS_ACCEPTED = "accepted"
NEGOTIATION_STATUS_REJECTED = "rejected"
NEGOTIATION_STATUS_EXPIRED = "expired"
NEGOTIATION_STATUS_EXECUTED = "executed"
NEGOTIATION_TERMINAL_STATUSES = frozenset(
    {
        NEGOTIATION_STATUS_REJECTED,
        NEGOTIATION_STATUS_EXPIRED,
        NEGOTIATION_STATUS_EXECUTED,
    }
)

DEAL_STATUS_PROPOSED = "proposed"
DEAL_STATUS_ACCEPTED = "accepted"
DEAL_STATUS_REJECTED = "rejected"
DEAL_STATUS_EXPIRED = "expired"

AUDIT_STATUS_CHANGED = "NEGOTIATION_STATUS_CHANGED"
AUDIT_DEAL_ACCEPTED = "NEGOTIATION_DEAL_ACCEPTED"
AUDIT_DEAL_REJECTED = "NEGOTIATION_DEAL_REJECTED"
AUDIT_EXPIRED_BY_CUTOFF = "NEGOTIATION_EXPIRED_BY_CUTOFF"
AUDIT_MESSAGE_SENT = "NEGOTIATION_MESSAGE_SENT"
AUDIT_DEAL_PROPOSED = "NEGOTIATION_DEAL_PROPOSED"
AUDIT_COUNTEROFFER_PROPOSED = "NEGOTIATION_COUNTEROFFER_PROPOSED"
AUDIT_ACCEPTANCES_INVALIDATED = "NEGOTIATION_ACCEPTANCES_INVALIDATED"
AUDIT_DEAL_VALIDATION_FAILED = "NEGOTIATION_DEAL_VALIDATION_FAILED"

MESSAGE_TYPE_FREEFORM = "freeform_message"
STRUCTURED_DEAL_KIND = "structured_deal"
DEAL_SCHEMA_VERSION = 1

NEGOTIATION_CUTOFF_DEFAULTS: dict[str, int | str] = {
    "max_rounds": 8,
    "max_proposals_per_player": 8,
    "max_active_seconds": 900,
    "max_ai_decision_attempts": 3,
    "max_pending_offers_per_player": 4,
    "negotiation_intensity": "standard",
}

CUTOFF_MAX_ROUNDS = "negotiation_cutoff_max_rounds"
CUTOFF_MAX_PROPOSALS_PER_PLAYER = "negotiation_cutoff_max_proposals_per_player"
CUTOFF_MAX_ACTIVE_SECONDS = "negotiation_cutoff_max_active_seconds"
CUTOFF_MAX_AI_DECISION_ATTEMPTS = "negotiation_cutoff_max_ai_decision_attempts"
CUTOFF_MAX_PENDING_OFFERS_PER_PLAYER = "negotiation_cutoff_max_pending_offers_per_player"


class PlayerCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    kind: Literal["human", "ai"]


class CreateGameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seed: str | None = Field(default=None, min_length=1, max_length=100)
    players: list[PlayerCreateRequest] = Field(min_length=2, max_length=5)
    settings: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_unique_player_names(self) -> "CreateGameRequest":
        names = [player.name for player in self.players]
        if len(set(names)) != len(names):
            raise ValueError("player names must be unique within a game")
        return self


class PlayerRecordResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    game_id: UUID
    seat_order: int
    name: str
    controller_type: str
    status: str
    state: Mapping[str, Any]
    created_at: Any
    updated_at: Any


class GameMetadataResponse(BaseModel):
    id: UUID
    status: str
    ruleset_version: str
    seed: str | None
    current_phase: str | None
    settings: Mapping[str, Any]
    players: list[PlayerRecordResponse]
    created_at: Any
    updated_at: Any


class GameStateResponse(BaseModel):
    game_id: UUID
    state: Mapping[str, Any]
    state_hash: str
    event_sequence: int


class LegalActionResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    actor_id: str
    type: str
    payload: Mapping[str, Any]
    expected_state_hash: str
    expected_event_sequence: int
    description: str | None = None
    action_schema: Mapping[str, Any] = Field(alias="schema")


class LegalActionsResponse(BaseModel):
    game_id: UUID
    actor_player_id: UUID
    legal_actions: list[LegalActionResponse]
    state_hash: str
    event_sequence: int


class ActionSubmission(BaseModel):
    model_config = ConfigDict(extra="ignore")

    actor_id: str = Field(min_length=1)
    type: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    expected_state_hash: str = Field(min_length=1)
    expected_event_sequence: int = Field(ge=0)


class AcceptedEventResponse(BaseModel):
    id: UUID
    game_id: UUID
    sequence: int
    actor_player_id: UUID | None
    event_type: str
    payload: Mapping[str, Any]
    state_hash: str
    created_at: Any


class ActionAcceptedResponse(BaseModel):
    status: Literal["accepted"]
    game_id: UUID
    accepted_events: list[AcceptedEventResponse]
    state: Mapping[str, Any]
    state_hash: str
    event_sequence: int


class EventsResponse(BaseModel):
    events: list[AcceptedEventResponse]


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


class ValidationIssueResponse(BaseModel):
    code: str
    message: str
    field: str | None = None


class LifecycleRejectedResponse(BaseModel):
    status: Literal["rejected"]
    reason_code: str
    validation_errors: list[ValidationIssueResponse]


class CreateNegotiationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    opened_by_player_id: UUID
    participant_player_ids: list[UUID] = Field(min_length=2, max_length=5)
    context: dict[str, Any] = Field(default_factory=dict)
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def validate_unique_participants(self) -> "CreateNegotiationRequest":
        if len(set(self.participant_player_ids)) != len(self.participant_player_ids):
            raise ValueError("participant_player_ids must be unique")
        if self.opened_by_player_id not in self.participant_player_ids:
            raise ValueError("opened_by_player_id must be a participant")
        return self


class NegotiationResponse(BaseModel):
    id: UUID
    game_id: UUID
    opened_by_player_id: UUID | None
    participant_player_ids: list[UUID]
    status: str
    phase: str | None
    round_number: int
    pending_deal_id: UUID | None
    current_deal_id: UUID | None
    current_terms_hash: str | None
    current_deal_version: int | None
    acceptances: Mapping[str, list[UUID]]
    invalidated_acceptances: Mapping[str, list[UUID]]
    status_history: list[Mapping[str, Any]]
    expires_at: Any | None
    context: Mapping[str, Any]
    cutoff_policy: Mapping[str, Any]
    proposal_counts_by_player_id: Mapping[str, int]
    pending_offer_counts_by_player_id: Mapping[str, int]
    ai_decision_attempts_by_message_id: Mapping[str, int]
    cutoff_reason: str | None
    expired_by_cutoff: bool
    created_at: Any
    updated_at: Any
    closed_at: Any | None


class NegotiationsResponse(BaseModel):
    negotiations: list[NegotiationResponse]


class CreateNegotiationMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sender_player_id: UUID | None = None
    author_player_id: UUID | None = None
    body: str = Field(min_length=1, max_length=4000)

    @model_validator(mode="after")
    def validate_sender_alias(self) -> "CreateNegotiationMessageRequest":
        if self.sender_player_id is None and self.author_player_id is None:
            raise ValueError("sender_player_id is required")
        if (
            self.sender_player_id is not None
            and self.author_player_id is not None
            and self.sender_player_id != self.author_player_id
        ):
            raise ValueError("sender_player_id and author_player_id must match")
        if self.sender_player_id is None:
            self.sender_player_id = self.author_player_id
        return self


class NegotiationMessageResponse(BaseModel):
    id: UUID
    game_id: UUID
    negotiation_id: UUID
    sender_player_id: UUID | None
    author_player_id: UUID | None
    recipient_player_id: UUID | None
    message_type: str
    body: str | None
    payload: Mapping[str, Any]
    created_at: Any


class NegotiationMessagesResponse(BaseModel):
    messages: list[NegotiationMessageResponse]


class NegotiationMessageMutationResponse(BaseModel):
    status: Literal["ok"]
    message: NegotiationMessageResponse


class CreateDealRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposed_by_player_id: UUID | None = None
    proposer_player_id: UUID | None = None
    participant_player_ids: list[UUID] | None = None
    negotiation_id: UUID | None = None
    parent_deal_id: UUID | None = None
    terms: dict[str, Any] | list[dict[str, Any]] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_proposer_alias(self) -> "CreateDealRequest":
        if self.proposed_by_player_id is None and self.proposer_player_id is None:
            raise ValueError("proposed_by_player_id is required")
        if (
            self.proposed_by_player_id is not None
            and self.proposer_player_id is not None
            and self.proposed_by_player_id != self.proposer_player_id
        ):
            raise ValueError("proposed_by_player_id and proposer_player_id must match")
        if self.proposed_by_player_id is None:
            self.proposed_by_player_id = self.proposer_player_id
        return self


class DealResponse(BaseModel):
    id: UUID
    game_id: UUID
    negotiation_id: UUID | None
    proposed_by_player_id: UUID | None
    proposer_player_id: UUID | None
    participant_player_ids: list[UUID]
    parent_deal_id: UUID | None
    status: str
    version: int
    deal_version: int
    terms: Mapping[str, Any]
    structured_deal: bool
    deal_schema_version: int | None
    terms_hash: str
    eligible_for_contract: bool
    validation_errors: Sequence[Mapping[str, Any]] | None
    created_at: Any
    updated_at: Any
    accepted_at: Any | None


class DealDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    player_id: UUID | None = None


class AiDecisionAttemptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    player_id: UUID | None = None


class AiStepRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    player_id: UUID
    request_context: dict[str, Any] = Field(default_factory=dict)


class AiStepNotImplementedResponse(BaseModel):
    status: Literal["not_implemented"]
    reason_code: Literal["ai_runtime_not_implemented"]
    game_id: UUID
    player_id: UUID
    message: str


@router.post("", response_model=GameMetadataResponse, status_code=status.HTTP_201_CREATED)
async def create_game(request: Request, payload: CreateGameRequest) -> GameMetadataResponse:
    session_factory = _session_factory(request)
    game_id = uuid4()
    seed = payload.seed or f"game-{game_id}"
    player_ids = [uuid4() for _ in payload.players]
    player_setups = tuple(
        PlayerSetup(id=str(player_id), name=player.name, kind=player.kind)
        for player_id, player in zip(player_ids, payload.players, strict=True)
    )
    initial_state = create_initial_game_state(seed=seed, players=player_setups, game_id=str(game_id))
    settings = dict(payload.settings)

    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                games.insert().values(
                    id=game_id,
                    status="active",
                    ruleset_version=initial_state.ruleset_version,
                    seed=seed,
                    current_phase=initial_state.turn.phase.value,
                    settings=settings,
                    initial_state=initial_state.model_dump(mode="json"),
                )
            )
            for seat_order, player_state in enumerate(initial_state.players):
                await session.execute(
                    players.insert().values(
                        id=UUID(player_state.id),
                        game_id=game_id,
                        seat_order=seat_order,
                        name=player_state.name,
                        controller_type=player_state.kind,
                        state=player_state.model_dump(mode="json"),
                    )
                )

    return await _load_game_metadata(session_factory, game_id)


@router.get("/{game_id}", response_model=GameMetadataResponse)
async def get_game(game_id: UUID, request: Request) -> GameMetadataResponse:
    return await _load_game_metadata(_session_factory(request), game_id)


@router.get("/{game_id}/state", response_model=GameStateResponse)
async def get_game_state(game_id: UUID, request: Request) -> GameStateResponse:
    state = await _load_replayed_state(_session_factory(request), game_id)
    return _state_response(game_id, state)


@router.get("/{game_id}/legal-actions", response_model=LegalActionsResponse)
async def get_legal_actions(
    game_id: UUID,
    request: Request,
    actor_player_id: UUID = Query(...),
) -> LegalActionsResponse:
    session_factory = _session_factory(request)
    await _ensure_player_in_game(session_factory, game_id, actor_player_id)
    state = await _load_replayed_state(session_factory, game_id)
    legal_actions = list_legal_actions(state, str(actor_player_id))
    return LegalActionsResponse(
        game_id=game_id,
        actor_player_id=actor_player_id,
        legal_actions=[
            LegalActionResponse.model_validate(action.model_dump(mode="json"))
            for action in legal_actions
        ],
        state_hash=state.state_hash(),
        event_sequence=state.event_sequence,
    )


@router.post("/{game_id}/actions", response_model=ActionAcceptedResponse | dict[str, Any])
async def submit_action(
    game_id: UUID,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> JSONResponse:
    normalized_idempotency_key = idempotency_key.strip()
    if not normalized_idempotency_key:
        return _missing_idempotency_key_response()

    session_factory = _session_factory(request)
    persistence = EventPersistence(session_factory)
    raw_body = await request.body()
    raw_payload, parse_errors = _request_payload_from_body(raw_body)
    request_hash = _request_hash(raw_body=raw_body, raw_payload=raw_payload, parse_errors=parse_errors)

    async with session_factory() as session:
        async with session.begin():
            try:
                state = await persistence.replay_current_state_for_update(session, game_id)
            except GameNotFoundError as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="game not found") from exc

            existing_idempotency = await _load_idempotency_key(
                session=session,
                game_id=game_id,
                idempotency_key=normalized_idempotency_key,
            )
            if existing_idempotency is not None:
                if existing_idempotency["request_hash"] != request_hash:
                    return _idempotency_conflict_response(raw_payload)
                response_payload = dict(existing_idempotency["response_payload"])
                return JSONResponse(
                    status_code=_status_code_for_persisted_response(response_payload),
                    content=response_payload,
                )

            if parse_errors:
                return await _persist_idempotent_rejection_response(
                    session=session,
                    game_id=game_id,
                    state=state,
                    idempotency_key=normalized_idempotency_key,
                    request_hash=request_hash,
                    raw_payload=raw_payload,
                    actor_id=_raw_actor_id(raw_payload),
                    action_type=_raw_action_type(raw_payload),
                    submitted_payload=_raw_action_payload(raw_payload),
                    validation_errors=parse_errors,
                )

            try:
                submission = ActionSubmission.model_validate(raw_payload)
            except ValidationError as exc:
                return await _persist_idempotent_rejection_response(
                    session=session,
                    game_id=game_id,
                    state=state,
                    idempotency_key=normalized_idempotency_key,
                    request_hash=request_hash,
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
                execution = execute_action(state, action, f"api-{game_id}-{state.event_sequence}")
                result = await persistence.append_accepted_events_to_locked_state(
                    session=session,
                    game_id=game_id,
                    state=state,
                    actor_player_id=submission.actor_id,
                    event_templates=[
                        AcceptedEventTemplate(
                            event_type=event.type,
                            payload=event.payload.model_dump(mode="json"),
                        )
                        for event in execution.events
                    ],
                    expected_base_sequence=submission.expected_event_sequence,
                    expected_base_state_hash=submission.expected_state_hash,
                )
            except ActionValidationError as exc:
                return await _persist_idempotent_rejection_response(
                    session=session,
                    game_id=game_id,
                    state=state,
                    idempotency_key=normalized_idempotency_key,
                    request_hash=request_hash,
                    raw_payload=submission.model_dump(mode="json"),
                    actor_id=submission.actor_id,
                    action_type=submission.type,
                    submitted_payload=submission.payload,
                    validation_errors=[issue.model_dump(mode="json") for issue in exc.errors],
                )
            except StaleEventSequenceError:
                return await _persist_idempotent_rejection_response(
                    session=session,
                    game_id=game_id,
                    state=state,
                    idempotency_key=normalized_idempotency_key,
                    request_hash=request_hash,
                    raw_payload=submission.model_dump(mode="json"),
                    actor_id=submission.actor_id,
                    action_type=submission.type,
                    submitted_payload=submission.payload,
                    validation_errors=[
                        {
                            "code": "stale_action",
                            "message": "action expected state no longer matches current state",
                            "field": "expected_state_hash",
                        }
                    ],
                )

            response_payload = ActionAcceptedResponse(
                status="accepted",
                game_id=game_id,
                accepted_events=[_event_response(record) for record in result.events],
                state=_state_payload(result.state),
                state_hash=result.state.state_hash(),
                event_sequence=result.state.event_sequence,
            ).model_dump(mode="json")
            await _persist_idempotency_key(
                session=session,
                game_id=game_id,
                actor_player_id=await _resolve_actor_player_id_in_session(
                    session=session,
                    game_id=game_id,
                    actor_id=submission.actor_id,
                ),
                idempotency_key=normalized_idempotency_key,
                request_hash=request_hash,
                outcome_status="accepted",
                response_payload=response_payload,
                created_event_sequence_start=result.events[0].sequence,
                created_event_sequence_end=result.events[-1].sequence,
                rejected_action_id=None,
            )
            return JSONResponse(status_code=status.HTTP_200_OK, content=response_payload)


@router.get("/{game_id}/events", response_model=EventsResponse)
async def list_events(game_id: UUID, request: Request) -> EventsResponse:
    try:
        records = await EventPersistence(_session_factory(request)).list_accepted_events(game_id)
    except GameNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="game not found") from exc
    return EventsResponse(events=[_event_response(record) for record in records])


@router.get("/{game_id}/rejected-actions", response_model=RejectedActionsResponse)
async def list_rejections(
    game_id: UUID,
    request: Request,
    actor_player_id: UUID | None = Query(default=None),
) -> RejectedActionsResponse:
    session_factory = _session_factory(request)
    await _ensure_game_exists(session_factory, game_id)
    audit = RejectedActionAudit(session_factory)
    records = await audit.list_rejected_actions(game_id, actor_player_id=actor_player_id)
    return RejectedActionsResponse(
        rejected_actions=[_rejected_response(record) for record in records],
    )


@router.get("/{game_id}/negotiations", response_model=NegotiationsResponse)
async def list_negotiations(game_id: UUID, request: Request) -> NegotiationsResponse:
    session_factory = _session_factory(request)
    await _ensure_game_exists(session_factory, game_id)
    async with session_factory() as session:
        result = await session.execute(
            sa.select(negotiations)
            .where(negotiations.c.game_id == game_id)
            .order_by(negotiations.c.updated_at.desc(), negotiations.c.created_at.desc())
        )
        rows = [dict(row) for row in result.mappings().all()]
    return NegotiationsResponse(negotiations=[_negotiation_response(row) for row in rows])


@router.get("/{game_id}/negotiations/{negotiation_id}", response_model=NegotiationResponse)
async def get_negotiation(
    game_id: UUID,
    negotiation_id: UUID,
    request: Request,
) -> NegotiationResponse:
    row = await _load_negotiation_in_game(_session_factory(request), game_id, negotiation_id)
    return _negotiation_response(row)


@router.get(
    "/{game_id}/negotiations/{negotiation_id}/messages",
    response_model=NegotiationMessagesResponse,
)
async def list_negotiation_messages(
    game_id: UUID,
    negotiation_id: UUID,
    request: Request,
) -> NegotiationMessagesResponse:
    session_factory = _session_factory(request)
    await _ensure_game_exists(session_factory, game_id)
    async with session_factory() as session:
        negotiation_row = await _load_negotiation_row_for_update(
            session=session,
            game_id=game_id,
            negotiation_id=negotiation_id,
        )
        if negotiation_row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="negotiation not found")
        result = await session.execute(
            sa.select(negotiation_messages)
            .where(
                negotiation_messages.c.game_id == game_id,
                negotiation_messages.c.negotiation_id == negotiation_id,
                negotiation_messages.c.message_type == MESSAGE_TYPE_FREEFORM,
            )
            .order_by(negotiation_messages.c.created_at, negotiation_messages.c.id)
        )
        rows = [dict(row) for row in result.mappings().all()]
    return NegotiationMessagesResponse(messages=[_negotiation_message_response(row) for row in rows])


@router.post(
    "/{game_id}/negotiations/{negotiation_id}/messages",
    response_model=NegotiationMessageMutationResponse | LifecycleRejectedResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_negotiation_message(
    game_id: UUID,
    negotiation_id: UUID,
    request: Request,
    payload: CreateNegotiationMessageRequest,
) -> NegotiationMessageMutationResponse | JSONResponse:
    session_factory = _session_factory(request)
    await _ensure_game_exists(session_factory, game_id)
    sender_player_id = _message_sender_player_id(payload)
    await _ensure_player_in_game(session_factory, game_id, sender_player_id)

    async with session_factory() as session:
        async with session.begin():
            negotiation_row = await _load_negotiation_row_for_update(
                session=session,
                game_id=game_id,
                negotiation_id=negotiation_id,
            )
            if negotiation_row is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="negotiation not found")
            terminal_response = _reject_if_negotiation_terminal(negotiation_row["status"])
            if terminal_response is not None:
                return terminal_response

            context = _normalized_negotiation_context(negotiation_row)
            cutoff_response = await _expire_if_active_time_cutoff(
                session=session,
                game_id=game_id,
                negotiation_row=negotiation_row,
                context=context,
                actor_player_id=sender_player_id,
            )
            if cutoff_response is not None:
                return cutoff_response
            if str(sender_player_id) not in context["participant_player_ids"]:
                return _lifecycle_rejection_response(
                    "sender_not_participant",
                    "sender_player_id must be a negotiation participant",
                    field="sender_player_id",
                )

            result = await session.execute(
                negotiation_messages.insert()
                .values(
                    game_id=game_id,
                    negotiation_id=negotiation_id,
                    sender_player_id=sender_player_id,
                    recipient_player_id=None,
                    message_type=MESSAGE_TYPE_FREEFORM,
                    body=payload.body.strip(),
                    payload={
                        "message_type": MESSAGE_TYPE_FREEFORM,
                        "sender_player_id": str(sender_player_id),
                    },
                )
                .returning(negotiation_messages)
            )
            row = dict(result.mappings().one())
            await session.execute(
                negotiations.update()
                .where(negotiations.c.id == negotiation_id)
                .values(updated_at=sa.func.now())
            )
            await _insert_negotiation_audit_message(
                session=session,
                game_id=game_id,
                negotiation_id=negotiation_id,
                sender_player_id=sender_player_id,
                message_type=AUDIT_MESSAGE_SENT,
                payload={
                    "message_id": str(row["id"]),
                    "message_type": MESSAGE_TYPE_FREEFORM,
                    "sender_player_id": str(sender_player_id),
                    "changed_game_state": False,
                    "created_contract": False,
                    "created_game_event": False,
                },
            )

    return NegotiationMessageMutationResponse(
        status="ok",
        message=_negotiation_message_response(row),
    )


@router.post(
    "/{game_id}/negotiations",
    response_model=NegotiationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_negotiation(
    game_id: UUID,
    request: Request,
    payload: CreateNegotiationRequest,
) -> NegotiationResponse:
    session_factory = _session_factory(request)
    game_settings = await _load_game_settings(session_factory, game_id)
    await _ensure_player_ids_in_game(
        session_factory,
        game_id,
        [payload.opened_by_player_id, *payload.participant_player_ids],
    )
    state = await _load_replayed_state(session_factory, game_id)

    stored_context = _initial_negotiation_context(
        payload,
        cutoff_policy=_negotiation_cutoff_policy(game_settings),
    )
    async with session_factory() as session:
        async with session.begin():
            result = await session.execute(
                negotiations.insert()
                .values(
                    game_id=game_id,
                    opened_by_player_id=payload.opened_by_player_id,
                    status=NEGOTIATION_STATUS_OPENED,
                    phase=state.turn.phase.value,
                    round_number=0,
                    context=stored_context,
                )
                .returning(negotiations)
            )
            row = dict(result.mappings().one())

    return _negotiation_response(row)


@router.post(
    "/{game_id}/deals",
    response_model=DealResponse | LifecycleRejectedResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_deal(
    game_id: UUID,
    request: Request,
    payload: CreateDealRequest,
) -> DealResponse | JSONResponse:
    session_factory = _session_factory(request)
    proposed_by_player_id = _deal_proposed_by_player_id(payload)
    await _ensure_game_exists(session_factory, game_id)
    await _ensure_player_in_game(session_factory, game_id, proposed_by_player_id)
    if payload.participant_player_ids:
        await _ensure_player_ids_in_game(session_factory, game_id, payload.participant_player_ids)

    async with session_factory() as session:
        async with session.begin():
            negotiation_row: dict[str, Any] | None = None
            next_negotiation_status: str | None = None
            next_round_number: int | None = None
            context: dict[str, Any] | None = None
            participant_player_ids: list[str] = [
                str(player_id) for player_id in payload.participant_player_ids or []
            ]

            if payload.negotiation_id is not None:
                negotiation_row = await _load_negotiation_row_for_update(
                    session=session,
                    game_id=game_id,
                    negotiation_id=payload.negotiation_id,
                )
                if negotiation_row is None:
                    return _lifecycle_rejection_response(
                        "negotiation_not_found",
                        "negotiation does not belong to game",
                        field="negotiation_id",
                    )

                terminal_response = _reject_if_negotiation_cannot_receive_proposal(
                    negotiation_row["status"]
                )
                if terminal_response is not None:
                    return terminal_response

                context = _normalized_negotiation_context(negotiation_row)
                cutoff_response = await _expire_if_active_time_cutoff(
                    session=session,
                    game_id=game_id,
                    negotiation_row=negotiation_row,
                    context=context,
                    actor_player_id=proposed_by_player_id,
                )
                if cutoff_response is not None:
                    return cutoff_response

                participant_ids = set(context["participant_player_ids"])
                participant_player_ids = list(context["participant_player_ids"])
                if str(proposed_by_player_id) not in participant_ids:
                    return _lifecycle_rejection_response(
                        "proposer_not_participant",
                        "proposed_by_player_id must be a negotiation participant",
                        field="proposed_by_player_id",
                    )
                if payload.participant_player_ids and set(participant_player_ids) != {
                    str(player_id) for player_id in payload.participant_player_ids
                }:
                    return _lifecycle_rejection_response(
                        "participants_must_match_negotiation",
                        "participant_player_ids must match the negotiation participants",
                        field="participant_player_ids",
                    )

                current_deal_id = context.get("current_deal_id")
                if payload.parent_deal_id is None and current_deal_id is not None:
                    return _lifecycle_rejection_response(
                        "parent_deal_id_required",
                        "a changed proposal must reference the current deal as parent_deal_id",
                        field="parent_deal_id",
                    )

                if payload.parent_deal_id is not None:
                    parent_deal = await _load_deal_row_for_update(
                        session=session,
                        game_id=game_id,
                        deal_id=payload.parent_deal_id,
                    )
                    if parent_deal is None or parent_deal["negotiation_id"] != payload.negotiation_id:
                        return _lifecycle_rejection_response(
                            "parent_deal_not_current",
                            "parent_deal_id must belong to this negotiation",
                            field="parent_deal_id",
                        )
                    if current_deal_id != str(payload.parent_deal_id):
                        return _lifecycle_rejection_response(
                            "parent_deal_not_current",
                            "parent_deal_id must match the current proposal",
                            field="parent_deal_id",
                        )
                    next_negotiation_status = NEGOTIATION_STATUS_COUNTERED
                    next_round_number = int(negotiation_row["round_number"]) + 1
                else:
                    next_negotiation_status = NEGOTIATION_STATUS_ACTIVE
                    next_round_number = max(int(negotiation_row["round_number"]) + 1, 1)

                await _refresh_negotiation_offer_counts(
                    session=session,
                    game_id=game_id,
                    negotiation_id=payload.negotiation_id,
                    context=context,
                )
                proposal_cutoff_reason = _proposal_cutoff_reason(
                    context=context,
                    proposed_by_player_id=str(proposed_by_player_id),
                    next_round_number=next_round_number,
                )
                if proposal_cutoff_reason is not None:
                    return await _expire_negotiation_by_cutoff_and_reject(
                        session=session,
                        game_id=game_id,
                        negotiation_row=negotiation_row,
                        context=context,
                        cutoff_reason=proposal_cutoff_reason,
                        actor_player_id=proposed_by_player_id,
                    )

            elif payload.parent_deal_id is not None:
                parent_deal = await _load_deal_row_for_update(
                    session=session,
                    game_id=game_id,
                    deal_id=payload.parent_deal_id,
                )
                if parent_deal is None:
                    return _lifecycle_rejection_response(
                        "parent_deal_not_found",
                        "parent deal does not belong to game",
                        field="parent_deal_id",
                    )

            prepared_terms = _prepare_deal_terms(
                payload.terms,
                participant_player_ids=participant_player_ids,
            )
            if prepared_terms.validation_errors:
                await _persist_invalid_deal(
                    session=session,
                    game_id=game_id,
                    negotiation_id=payload.negotiation_id,
                    proposed_by_player_id=proposed_by_player_id,
                    parent_deal_id=payload.parent_deal_id,
                    raw_terms=payload.terms,
                    validation_errors=prepared_terms.validation_errors,
                )
                if payload.negotiation_id is not None:
                    await _insert_negotiation_audit_message(
                        session=session,
                        game_id=game_id,
                        negotiation_id=payload.negotiation_id,
                        sender_player_id=proposed_by_player_id,
                        message_type=AUDIT_DEAL_VALIDATION_FAILED,
                        payload={
                            "validation_errors": prepared_terms.validation_errors,
                            "structured_deal": prepared_terms.structured_deal,
                        },
                    )
                return _lifecycle_rejection_response_from_errors(
                    "invalid_structured_deal",
                    prepared_terms.validation_errors,
                )

            version = await _next_deal_version_in_session(
                session=session,
                game_id=game_id,
                negotiation_id=payload.negotiation_id,
            )
            result = await session.execute(
                deals.insert()
                .values(
                    game_id=game_id,
                    negotiation_id=payload.negotiation_id,
                    proposed_by_player_id=proposed_by_player_id,
                    parent_deal_id=payload.parent_deal_id,
                    status="proposed",
                    version=version,
                    terms=prepared_terms.terms,
                    validation_errors=None,
                )
                .returning(deals)
            )
            row = dict(result.mappings().one())

            if negotiation_row is not None and context is not None:
                if next_negotiation_status is None or next_round_number is None:
                    raise RuntimeError("negotiation proposal transition was not resolved")
                previous_current_deal_id = context.get("current_deal_id")
                previous_terms_hash = context.get("current_terms_hash")
                previous_acceptances = (
                    list(context.get("acceptances", {}).get(str(previous_current_deal_id), []))
                    if previous_current_deal_id is not None
                    else []
                )
                changed_counteroffer = (
                    payload.parent_deal_id is not None
                    and previous_terms_hash is not None
                    and previous_terms_hash != prepared_terms.terms_hash
                )
                if changed_counteroffer and previous_current_deal_id and previous_acceptances:
                    invalidated = context.setdefault("invalidated_acceptances", {})
                    invalidated[str(previous_current_deal_id)] = previous_acceptances
                context["pending_deal_id"] = str(row["id"])
                context["current_deal_id"] = str(row["id"])
                context["current_parent_deal_id"] = (
                    None if payload.parent_deal_id is None else str(payload.parent_deal_id)
                )
                context["current_terms_hash"] = prepared_terms.terms_hash
                context["current_deal_version"] = version
                context["current_deal_structured"] = prepared_terms.structured_deal
                context.setdefault("acceptances", {})[str(row["id"])] = []
                _increment_count(
                    context.setdefault("proposal_counts_by_player_id", {}),
                    str(proposed_by_player_id),
                )
                _increment_count(
                    context.setdefault("pending_offer_counts_by_player_id", {}),
                    str(proposed_by_player_id),
                )
                if negotiation_row["status"] != next_negotiation_status:
                    _append_status_history(
                        context,
                        from_status=negotiation_row["status"],
                        to_status=next_negotiation_status,
                        deal_id=str(row["id"]),
                        round_number=next_round_number,
                    )

                await session.execute(
                    negotiations.update()
                    .where(negotiations.c.id == negotiation_row["id"])
                    .values(
                        status=next_negotiation_status,
                        round_number=next_round_number,
                        context=context,
                        updated_at=sa.func.now(),
                    )
                )
                await _insert_negotiation_audit_message(
                    session=session,
                    game_id=game_id,
                    negotiation_id=negotiation_row["id"],
                    sender_player_id=proposed_by_player_id,
                    message_type=(
                        AUDIT_COUNTEROFFER_PROPOSED
                        if payload.parent_deal_id is not None
                        else AUDIT_DEAL_PROPOSED
                    ),
                    payload={
                        "deal_id": str(row["id"]),
                        "parent_deal_id": None
                        if payload.parent_deal_id is None
                        else str(payload.parent_deal_id),
                        "deal_version": version,
                        "version": version,
                        "terms_hash": prepared_terms.terms_hash,
                        "structured_deal": prepared_terms.structured_deal,
                        "counteroffer": payload.parent_deal_id is not None,
                    },
                )
                if changed_counteroffer and previous_current_deal_id and previous_acceptances:
                    await _insert_negotiation_audit_message(
                        session=session,
                        game_id=game_id,
                        negotiation_id=negotiation_row["id"],
                        sender_player_id=proposed_by_player_id,
                        message_type=AUDIT_ACCEPTANCES_INVALIDATED,
                        payload={
                            "deal_id": str(previous_current_deal_id),
                            "replacement_deal_id": str(row["id"]),
                            "invalidated_acceptances": previous_acceptances,
                            "previous_terms_hash": previous_terms_hash,
                            "current_terms_hash": prepared_terms.terms_hash,
                            "exact_term_acceptance": True,
                        },
                    )
                if negotiation_row["status"] != next_negotiation_status:
                    await _insert_negotiation_audit_message(
                        session=session,
                        game_id=game_id,
                        negotiation_id=negotiation_row["id"],
                        sender_player_id=proposed_by_player_id,
                        message_type=AUDIT_STATUS_CHANGED,
                        payload={
                            "from_status": negotiation_row["status"],
                            "to_status": next_negotiation_status,
                            "deal_id": str(row["id"]),
                            "round_number": next_round_number,
                        },
                    )

    return _deal_response(row)


@router.post(
    "/{game_id}/deals/{deal_id}/accept",
    response_model=DealResponse | LifecycleRejectedResponse,
)
async def accept_deal(
    game_id: UUID,
    deal_id: UUID,
    request: Request,
    payload: DealDecisionRequest | None = None,
) -> DealResponse | JSONResponse:
    return await _record_deal_acceptance_or_rejection(
        game_id=game_id,
        deal_id=deal_id,
        request=request,
        payload=payload,
        decision="accept",
    )


@router.post(
    "/{game_id}/deals/{deal_id}/reject",
    response_model=DealResponse | LifecycleRejectedResponse,
)
async def reject_deal(
    game_id: UUID,
    deal_id: UUID,
    request: Request,
    payload: DealDecisionRequest | None = None,
) -> DealResponse | JSONResponse:
    return await _record_deal_acceptance_or_rejection(
        game_id=game_id,
        deal_id=deal_id,
        request=request,
        payload=payload,
        decision="reject",
    )


@router.post(
    "/{game_id}/negotiations/{negotiation_id}/expire",
    response_model=NegotiationResponse | LifecycleRejectedResponse,
)
async def expire_negotiation(
    game_id: UUID,
    negotiation_id: UUID,
    request: Request,
) -> NegotiationResponse | JSONResponse:
    session_factory = _session_factory(request)
    await _ensure_game_exists(session_factory, game_id)
    async with session_factory() as session:
        async with session.begin():
            negotiation_row = await _load_negotiation_row_for_update(
                session=session,
                game_id=game_id,
                negotiation_id=negotiation_id,
            )
            if negotiation_row is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="negotiation not found",
                )
            terminal_response = _reject_if_negotiation_terminal(negotiation_row["status"])
            if terminal_response is not None:
                return terminal_response

            context = _normalized_negotiation_context(negotiation_row)
            current_deal_id = _uuid_or_none(context.get("current_deal_id"))
            if current_deal_id is not None:
                await session.execute(
                    deals.update()
                    .where(
                        deals.c.game_id == game_id,
                        deals.c.id == current_deal_id,
                        deals.c.status == DEAL_STATUS_PROPOSED,
                    )
                    .values(status=DEAL_STATUS_EXPIRED, updated_at=sa.func.now())
                )

            context["expired_at"] = _audit_time_marker()
            _append_status_history(
                context,
                from_status=negotiation_row["status"],
                to_status=NEGOTIATION_STATUS_EXPIRED,
                deal_id=context.get("current_deal_id"),
                round_number=negotiation_row["round_number"],
            )
            await session.execute(
                negotiations.update()
                .where(negotiations.c.id == negotiation_id)
                .values(
                    status=NEGOTIATION_STATUS_EXPIRED,
                    context=context,
                    updated_at=sa.func.now(),
                    closed_at=sa.func.now(),
                )
            )
            await _insert_negotiation_audit_message(
                session=session,
                game_id=game_id,
                negotiation_id=negotiation_id,
                sender_player_id=None,
                message_type=AUDIT_STATUS_CHANGED,
                payload={
                    "from_status": negotiation_row["status"],
                    "to_status": NEGOTIATION_STATUS_EXPIRED,
                    "deal_id": context.get("current_deal_id"),
                    "round_number": negotiation_row["round_number"],
                },
            )
            updated = await _load_negotiation_row_by_id(session, negotiation_id)
            if updated is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="negotiation not found",
                )
    return _negotiation_response(updated)


@router.post(
    "/{game_id}/negotiations/{negotiation_id}/execute",
    response_model=NegotiationResponse | LifecycleRejectedResponse,
)
async def execute_negotiation(
    game_id: UUID,
    negotiation_id: UUID,
    request: Request,
) -> NegotiationResponse | JSONResponse:
    session_factory = _session_factory(request)
    await _ensure_game_exists(session_factory, game_id)
    async with session_factory() as session:
        async with session.begin():
            negotiation_row = await _load_negotiation_row_for_update(
                session=session,
                game_id=game_id,
                negotiation_id=negotiation_id,
            )
            if negotiation_row is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="negotiation not found",
                )
            terminal_response = _reject_if_negotiation_terminal(negotiation_row["status"])
            if terminal_response is not None:
                return terminal_response
            context = _normalized_negotiation_context(negotiation_row)
            cutoff_response = await _expire_if_active_time_cutoff(
                session=session,
                game_id=game_id,
                negotiation_row=negotiation_row,
                context=context,
                actor_player_id=None,
            )
            if cutoff_response is not None:
                return cutoff_response
            if negotiation_row["status"] != NEGOTIATION_STATUS_ACCEPTED:
                return _lifecycle_rejection_response(
                    "negotiation_not_accepted",
                    "negotiation can execute only after all participants accept the current deal",
                    field="status",
                )

            current_deal_id = _uuid_or_none(context.get("current_deal_id"))
            if current_deal_id is None:
                return _lifecycle_rejection_response(
                    "current_deal_missing",
                    "negotiation has no current deal to execute",
                    field="current_deal_id",
                )
            current_deal = await _load_deal_row_for_update(
                session=session,
                game_id=game_id,
                deal_id=current_deal_id,
            )
            if current_deal is None or current_deal["status"] != DEAL_STATUS_ACCEPTED:
                return _lifecycle_rejection_response(
                    "current_deal_not_accepted",
                    "current deal must be accepted before execution",
                    field="current_deal_id",
                )
            current_deal_terms = (
                current_deal["terms"] if isinstance(current_deal["terms"], Mapping) else {}
            )
            if context.get("current_terms_hash") != _terms_hash_for_response(current_deal_terms):
                return _lifecycle_rejection_response(
                    "exact_term_acceptance_required",
                    "accepted current deal must match the negotiation terms_hash",
                    field="current_terms_hash",
                )
            missing_acceptances = _missing_acceptances(context, str(current_deal_id))
            if missing_acceptances:
                return _lifecycle_rejection_response(
                    "missing_acceptances",
                    "all negotiation participants must accept the current deal before execution",
                    field="acceptances",
                )

            _append_status_history(
                context,
                from_status=negotiation_row["status"],
                to_status=NEGOTIATION_STATUS_EXECUTED,
                deal_id=str(current_deal_id),
                round_number=negotiation_row["round_number"],
            )
            await session.execute(
                negotiations.update()
                .where(negotiations.c.id == negotiation_id)
                .values(
                    status=NEGOTIATION_STATUS_EXECUTED,
                    context=context,
                    updated_at=sa.func.now(),
                    closed_at=sa.func.now(),
                )
            )
            await _insert_negotiation_audit_message(
                session=session,
                game_id=game_id,
                negotiation_id=negotiation_id,
                sender_player_id=None,
                message_type=AUDIT_STATUS_CHANGED,
                payload={
                    "from_status": negotiation_row["status"],
                    "to_status": NEGOTIATION_STATUS_EXECUTED,
                    "deal_id": str(current_deal_id),
                    "round_number": negotiation_row["round_number"],
                },
            )
            updated = await _load_negotiation_row_by_id(session, negotiation_id)
            if updated is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="negotiation not found",
                )
    return _negotiation_response(updated)


@router.post(
    "/{game_id}/negotiations/{negotiation_id}/messages/{message_id}/ai-decision-attempts",
    response_model=NegotiationResponse | LifecycleRejectedResponse,
)
async def record_negotiation_ai_decision_attempt(
    game_id: UUID,
    negotiation_id: UUID,
    message_id: UUID,
    request: Request,
    payload: AiDecisionAttemptRequest | None = None,
) -> NegotiationResponse | JSONResponse:
    session_factory = _session_factory(request)
    await _ensure_game_exists(session_factory, game_id)
    if payload is not None and payload.player_id is not None:
        await _ensure_player_in_game(session_factory, game_id, payload.player_id)

    async with session_factory() as session:
        async with session.begin():
            negotiation_row = await _load_negotiation_row_for_update(
                session=session,
                game_id=game_id,
                negotiation_id=negotiation_id,
            )
            if negotiation_row is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="negotiation not found",
                )
            terminal_response = _reject_if_negotiation_terminal(negotiation_row["status"])
            if terminal_response is not None:
                return terminal_response

            message_row = await _load_negotiation_message_row_for_update(
                session=session,
                game_id=game_id,
                negotiation_id=negotiation_id,
                message_id=message_id,
            )
            if message_row is None:
                return _lifecycle_rejection_response(
                    "negotiation_message_not_found",
                    "message_id must reference a message in this negotiation",
                    field="message_id",
                )

            context = _normalized_negotiation_context(negotiation_row)
            cutoff_response = await _expire_if_active_time_cutoff(
                session=session,
                game_id=game_id,
                negotiation_row=negotiation_row,
                context=context,
                actor_player_id=None if payload is None else payload.player_id,
            )
            if cutoff_response is not None:
                return cutoff_response

            message_key = str(message_id)
            attempt_counts = context.setdefault("ai_decision_attempts_by_message_id", {})
            attempt_count = _increment_count(attempt_counts, message_key)
            max_attempts = int(context["cutoff_policy"]["max_ai_decision_attempts"])
            if attempt_count > max_attempts:
                return await _expire_negotiation_by_cutoff_and_reject(
                    session=session,
                    game_id=game_id,
                    negotiation_row=negotiation_row,
                    context=context,
                    cutoff_reason=CUTOFF_MAX_AI_DECISION_ATTEMPTS,
                    actor_player_id=None if payload is None else payload.player_id,
                )

            await session.execute(
                negotiations.update()
                .where(negotiations.c.id == negotiation_id)
                .values(context=context, updated_at=sa.func.now())
            )
            updated = await _load_negotiation_row_by_id(session, negotiation_id)
            if updated is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="negotiation not found",
                )
    return _negotiation_response(updated)


@router.post(
    "/{game_id}/ai/step",
    response_model=AiStepNotImplementedResponse,
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
)
async def ai_step(
    game_id: UUID,
    request: Request,
    payload: AiStepRequest,
) -> AiStepNotImplementedResponse:
    session_factory = _session_factory(request)
    await _ensure_game_exists(session_factory, game_id)
    await _ensure_player_in_game(session_factory, game_id, payload.player_id)
    return AiStepNotImplementedResponse(
        status="not_implemented",
        reason_code="ai_runtime_not_implemented",
        game_id=game_id,
        player_id=payload.player_id,
        message="The Codex AI runtime is scheduled for Phase 7 and is not implemented in Stage 4.4.",
    )


@router.get(
    "/{game_id}/events/stream",
    response_class=StreamingResponse,
    responses={
        200: {
            "description": "Server-sent stream of existing accepted game events.",
            "content": {"text/event-stream": {}},
        }
    },
)
async def stream_events(game_id: UUID, request: Request) -> StreamingResponse:
    try:
        records = await EventPersistence(_session_factory(request)).list_accepted_events(game_id)
    except GameNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="game not found") from exc

    async def event_stream() -> AsyncIterator[str]:
        for record in records:
            data = json.dumps(_event_response(record).model_dump(mode="json"), separators=(",", ":"))
            yield f"id: {record.sequence}\nevent: game_event\ndata: {data}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"cache-control": "no-store"},
    )


async def _load_game_metadata(
    session_factory: async_sessionmaker[AsyncSession],
    game_id: UUID,
) -> GameMetadataResponse:
    async with session_factory() as session:
        game_result = await session.execute(sa.select(games).where(games.c.id == game_id))
        game_row = game_result.mappings().first()
        if game_row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="game not found")

        player_result = await session.execute(
            sa.select(players).where(players.c.game_id == game_id).order_by(players.c.seat_order)
        )
        player_rows = [dict(row) for row in player_result.mappings().all()]

    game = dict(game_row)
    return GameMetadataResponse(
        id=game["id"],
        status=game["status"],
        ruleset_version=game["ruleset_version"],
        seed=game["seed"],
        current_phase=game["current_phase"],
        settings=game["settings"],
        players=[PlayerRecordResponse.model_validate(row) for row in player_rows],
        created_at=game["created_at"],
        updated_at=game["updated_at"],
    )


async def _load_replayed_state(
    session_factory: async_sessionmaker[AsyncSession],
    game_id: UUID,
) -> GameState:
    try:
        return await EventPersistence(session_factory).replay_from_latest_snapshot(game_id)
    except GameNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="game not found") from exc


async def _load_game_settings(
    session_factory: async_sessionmaker[AsyncSession],
    game_id: UUID,
) -> Mapping[str, Any]:
    async with session_factory() as session:
        result = await session.execute(sa.select(games.c.settings).where(games.c.id == game_id))
        settings_row = result.scalar_one_or_none()
    if settings_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="game not found")
    return dict(settings_row)


@dataclass(frozen=True)
class PreparedDealTerms:
    terms: dict[str, Any]
    terms_hash: str
    structured_deal: bool
    deal_schema_version: int | None
    participant_player_ids: list[str]
    validation_errors: list[dict[str, str]]


def _message_sender_player_id(payload: CreateNegotiationMessageRequest) -> UUID:
    if payload.sender_player_id is None:
        raise RuntimeError("message sender was not resolved")
    return payload.sender_player_id


def _deal_proposed_by_player_id(payload: CreateDealRequest) -> UUID:
    if payload.proposed_by_player_id is None:
        raise RuntimeError("deal proposer was not resolved")
    return payload.proposed_by_player_id


def _prepare_deal_terms(
    raw_terms: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    *,
    participant_player_ids: Sequence[str],
) -> PreparedDealTerms:
    validation_errors: list[dict[str, str]] = []
    if isinstance(raw_terms, Mapping):
        source = _json_safe_mapping(raw_terms)
    elif isinstance(raw_terms, Sequence) and not isinstance(raw_terms, (str, bytes)):
        source: dict[str, Any] = {
            "kind": STRUCTURED_DEAL_KIND,
            "deal_schema_version": DEAL_SCHEMA_VERSION,
            "participants": list(participant_player_ids),
            "terms": [dict(term) for term in raw_terms if isinstance(term, Mapping)],
        }
        if len(source["terms"]) != len(raw_terms):
            validation_errors.append(
                _deal_validation_error("invalid_structured_deal", "each term must be an object", "terms")
            )
    else:
        source = {}
        validation_errors.append(
            _deal_validation_error("invalid_structured_deal", "terms must be an object or list", "terms")
        )

    if source.get("kind") != STRUCTURED_DEAL_KIND:
        terms = _json_safe_mapping(source)
        return PreparedDealTerms(
            terms=terms,
            terms_hash=_canonical_hash(terms),
            structured_deal=False,
            deal_schema_version=None,
            participant_player_ids=[],
            validation_errors=[],
        )

    raw_version = source.get("deal_schema_version")
    if isinstance(raw_version, bool) or not isinstance(raw_version, int) or raw_version < 1:
        validation_errors.append(
            _deal_validation_error(
                "invalid_structured_deal",
                "deal_schema_version must be a positive integer",
                "deal_schema_version",
            )
        )
        deal_schema_version: int | None = None
    else:
        deal_schema_version = raw_version

    raw_participants = source.get("participants", source.get("participant_player_ids"))
    participants = _normalized_uuid_strings(raw_participants)
    if len(participants) < 2:
        validation_errors.append(
            _deal_validation_error(
                "invalid_structured_deal",
                "participants must include at least two players",
                "participants",
            )
        )
    if len(set(participants)) != len(participants):
        validation_errors.append(
            _deal_validation_error(
                "invalid_structured_deal",
                "participants must be unique",
                "participants",
            )
        )
    if raw_participants is not None and len(participants) != _sequence_length(raw_participants):
        validation_errors.append(
            _deal_validation_error(
                "invalid_structured_deal",
                "participants must be player UUID strings",
                "participants",
            )
        )
    if participant_player_ids and set(participants) != set(participant_player_ids):
        validation_errors.append(
            _deal_validation_error(
                "invalid_structured_deal",
                "participants must match the negotiation participants",
                "participants",
            )
        )

    raw_items = source.get("terms")
    term_items: list[dict[str, Any]] = []
    if not isinstance(raw_items, Sequence) or isinstance(raw_items, (str, bytes, Mapping)):
        validation_errors.append(
            _deal_validation_error("invalid_structured_deal", "terms must be a list", "terms")
        )
    else:
        for index, item in enumerate(raw_items):
            if not isinstance(item, Mapping):
                validation_errors.append(
                    _deal_validation_error(
                        "invalid_structured_deal",
                        "each term must be an object",
                        f"terms.{index}",
                    )
                )
                continue
            term = _json_safe_mapping(item)
            term_kind = term.get("kind")
            if not isinstance(term_kind, str) or not term_kind.strip():
                validation_errors.append(
                    _deal_validation_error(
                        "invalid_structured_deal",
                        "each term must include a non-empty kind",
                        f"terms.{index}.kind",
                    )
                )
                continue
            term["kind"] = term_kind.strip()
            term_items.append(term)
    if not term_items:
        validation_errors.append(
            _deal_validation_error(
                "invalid_structured_deal",
                "terms must include at least one structured term",
                "terms",
            )
        )

    ordered_participants = _ordered_participants(participants, participant_player_ids)
    canonical_terms: dict[str, Any] = {
        "kind": STRUCTURED_DEAL_KIND,
        "deal_schema_version": deal_schema_version or DEAL_SCHEMA_VERSION,
        "participants": ordered_participants,
        "terms": term_items,
    }
    terms_hash = _canonical_hash(canonical_terms)
    canonical_terms["terms_hash"] = terms_hash
    return PreparedDealTerms(
        terms=canonical_terms,
        terms_hash=terms_hash,
        structured_deal=True,
        deal_schema_version=deal_schema_version,
        participant_player_ids=ordered_participants,
        validation_errors=validation_errors,
    )


def _deal_validation_error(code: str, message: str, field: str) -> dict[str, str]:
    return {"code": code, "message": message, "field": field}


def _normalized_uuid_strings(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, Mapping)):
        return []
    normalized: list[str] = []
    for item in value:
        try:
            normalized.append(str(UUID(str(item))))
        except (TypeError, ValueError):
            continue
    return normalized


def _sequence_length(value: object) -> int:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, Mapping)):
        return 0
    return len(value)


def _ordered_participants(participants: Sequence[str], negotiation_order: Sequence[str]) -> list[str]:
    participant_set = set(participants)
    if negotiation_order:
        return [player_id for player_id in negotiation_order if player_id in participant_set]
    return [str(player_id) for player_id in participants]


def _json_safe_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    safe = _json_safe(value)
    if not isinstance(safe, dict):
        raise TypeError("expected JSON object")
    return safe


def _json_safe(value: object) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_json_safe(item) for item in value]
    return value


def _canonical_hash(value: Mapping[str, Any]) -> str:
    serialized = json.dumps(
        _json_safe_mapping(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


async def _persist_invalid_deal(
    *,
    session: AsyncSession,
    game_id: UUID,
    negotiation_id: UUID | None,
    proposed_by_player_id: UUID,
    parent_deal_id: UUID | None,
    raw_terms: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    validation_errors: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any]:
    version = await _next_rejected_deal_version_in_session(
        session=session,
        game_id=game_id,
        negotiation_id=negotiation_id,
    )
    terms = _json_safe(raw_terms)
    if not isinstance(terms, (dict, list)):
        terms = {"submitted_terms": terms}
    result = await session.execute(
        deals.insert()
        .values(
            game_id=game_id,
            negotiation_id=negotiation_id,
            proposed_by_player_id=proposed_by_player_id,
            parent_deal_id=parent_deal_id,
            status=DEAL_STATUS_REJECTED,
            version=version,
            terms=terms,
            validation_errors=[dict(error) for error in validation_errors],
        )
        .returning(deals)
    )
    return dict(result.mappings().one())


def _initial_negotiation_context(
    payload: CreateNegotiationRequest,
    *,
    cutoff_policy: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "participant_player_ids": [str(player_id) for player_id in payload.participant_player_ids],
        "context": dict(payload.context),
        "pending_deal_id": None,
        "current_deal_id": None,
        "current_parent_deal_id": None,
        "current_terms_hash": None,
        "current_deal_version": None,
        "current_deal_structured": False,
        "acceptances": {},
        "invalidated_acceptances": {},
        "status_history": [
            {
                "from_status": None,
                "to_status": NEGOTIATION_STATUS_OPENED,
                "deal_id": None,
                "round_number": 0,
            }
        ],
        "expires_at": payload.expires_at.isoformat() if payload.expires_at is not None else None,
        "cutoff_policy": dict(cutoff_policy),
        "proposal_counts_by_player_id": {},
        "pending_offer_counts_by_player_id": {},
        "ai_decision_attempts_by_message_id": {},
        "cutoff_reason": None,
        "expired_by_cutoff": False,
    }


def _negotiation_cutoff_policy(settings: object) -> dict[str, Any]:
    source: Mapping[str, Any]
    if isinstance(settings, Mapping):
        nested = settings.get("negotiation_cutoffs")
        source = nested if isinstance(nested, Mapping) else settings
    else:
        source = {}

    intensity = source.get(
        "negotiation_intensity",
        NEGOTIATION_CUTOFF_DEFAULTS["negotiation_intensity"],
    )
    if not isinstance(intensity, str) or not intensity.strip():
        intensity = str(NEGOTIATION_CUTOFF_DEFAULTS["negotiation_intensity"])

    return {
        "max_rounds": _positive_int_setting(source, "max_rounds"),
        "max_proposals_per_player": _positive_int_setting(source, "max_proposals_per_player"),
        "max_active_seconds": _positive_int_setting(source, "max_active_seconds"),
        "max_ai_decision_attempts": _positive_int_setting(source, "max_ai_decision_attempts"),
        "max_pending_offers_per_player": _positive_int_setting(
            source,
            "max_pending_offers_per_player",
        ),
        "negotiation_intensity": intensity.strip(),
    }


def _positive_int_setting(source: Mapping[str, Any], key: str) -> int:
    value = source.get(key)
    if isinstance(value, int) and value >= 1:
        return value
    default = NEGOTIATION_CUTOFF_DEFAULTS[key]
    if not isinstance(default, int):
        raise TypeError(f"{key} default must be an integer")
    return default


def _count_mapping(value: object) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    counts: dict[str, int] = {}
    for key, count in value.items():
        if isinstance(count, int) and count >= 0:
            counts[str(key)] = count
    return counts


def _increment_count(counts: Mapping[str, int] | dict[str, int], key: str) -> int:
    if not isinstance(counts, dict):
        raise TypeError("cutoff counters must be mutable dictionaries")
    next_count = int(counts.get(key, 0)) + 1
    counts[key] = next_count
    return next_count


def _normalized_negotiation_context(row: Mapping[str, Any]) -> dict[str, Any]:
    stored_context = row["context"] or {}
    participant_ids = stored_context.get("participant_player_ids", [])
    if not isinstance(participant_ids, Sequence) or isinstance(participant_ids, str):
        participant_ids = []

    public_context = stored_context.get("context", {})
    if not isinstance(public_context, Mapping):
        public_context = {}

    raw_acceptances = stored_context.get("acceptances", {})
    if not isinstance(raw_acceptances, Mapping):
        raw_acceptances = {}
    acceptances: dict[str, list[str]] = {}
    for deal_id, player_ids in raw_acceptances.items():
        if isinstance(player_ids, Sequence) and not isinstance(player_ids, str):
            acceptances[str(deal_id)] = [str(player_id) for player_id in player_ids]

    raw_invalidated_acceptances = stored_context.get("invalidated_acceptances", {})
    if not isinstance(raw_invalidated_acceptances, Mapping):
        raw_invalidated_acceptances = {}
    invalidated_acceptances: dict[str, list[str]] = {}
    for deal_id, player_ids in raw_invalidated_acceptances.items():
        if isinstance(player_ids, Sequence) and not isinstance(player_ids, str):
            invalidated_acceptances[str(deal_id)] = [str(player_id) for player_id in player_ids]

    raw_status_history = stored_context.get("status_history", [])
    status_history = [
        dict(item) for item in raw_status_history if isinstance(item, Mapping)
    ] if isinstance(raw_status_history, Sequence) and not isinstance(raw_status_history, str) else []

    cutoff_policy = _negotiation_cutoff_policy(stored_context.get("cutoff_policy", {}))

    return {
        "participant_player_ids": [str(player_id) for player_id in participant_ids],
        "context": dict(public_context),
        "pending_deal_id": _string_or_none(stored_context.get("pending_deal_id")),
        "current_deal_id": _string_or_none(stored_context.get("current_deal_id")),
        "current_parent_deal_id": _string_or_none(stored_context.get("current_parent_deal_id")),
        "current_terms_hash": _string_or_none(stored_context.get("current_terms_hash")),
        "current_deal_version": _int_or_none(stored_context.get("current_deal_version")),
        "current_deal_structured": stored_context.get("current_deal_structured") is True,
        "acceptances": acceptances,
        "invalidated_acceptances": invalidated_acceptances,
        "status_history": status_history,
        "expires_at": _string_or_none(stored_context.get("expires_at")),
        "cutoff_policy": cutoff_policy,
        "proposal_counts_by_player_id": _count_mapping(
            stored_context.get("proposal_counts_by_player_id")
        ),
        "pending_offer_counts_by_player_id": _count_mapping(
            stored_context.get("pending_offer_counts_by_player_id")
        ),
        "ai_decision_attempts_by_message_id": _count_mapping(
            stored_context.get("ai_decision_attempts_by_message_id")
        ),
        "cutoff_reason": _string_or_none(stored_context.get("cutoff_reason")),
        "expired_by_cutoff": stored_context.get("expired_by_cutoff") is True,
        **{
            key: value
            for key, value in stored_context.items()
            if key
            not in {
                "participant_player_ids",
                "context",
                "pending_deal_id",
                "current_deal_id",
                "current_parent_deal_id",
                "current_terms_hash",
                "current_deal_version",
                "current_deal_structured",
                "acceptances",
                "invalidated_acceptances",
                "status_history",
                "expires_at",
                "cutoff_policy",
                "proposal_counts_by_player_id",
                "pending_offer_counts_by_player_id",
                "ai_decision_attempts_by_message_id",
                "cutoff_reason",
                "expired_by_cutoff",
            }
        },
    }


async def _load_negotiation_in_game(
    session_factory: async_sessionmaker[AsyncSession],
    game_id: UUID,
    negotiation_id: UUID,
) -> Mapping[str, Any]:
    async with session_factory() as session:
        result = await session.execute(
            sa.select(negotiations).where(
                negotiations.c.game_id == game_id,
                negotiations.c.id == negotiation_id,
            )
        )
        row = result.mappings().first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="negotiation not found")
    return dict(row)


async def _load_negotiation_row_for_update(
    *,
    session: AsyncSession,
    game_id: UUID,
    negotiation_id: UUID,
) -> dict[str, Any] | None:
    result = await session.execute(
        sa.select(negotiations)
        .where(negotiations.c.game_id == game_id, negotiations.c.id == negotiation_id)
        .with_for_update()
    )
    row = result.mappings().first()
    return None if row is None else dict(row)


async def _load_negotiation_row_by_id(
    session: AsyncSession,
    negotiation_id: UUID,
) -> dict[str, Any] | None:
    result = await session.execute(sa.select(negotiations).where(negotiations.c.id == negotiation_id))
    row = result.mappings().first()
    return None if row is None else dict(row)


async def _load_deal_row_for_update(
    *,
    session: AsyncSession,
    game_id: UUID,
    deal_id: UUID,
) -> dict[str, Any] | None:
    result = await session.execute(
        sa.select(deals)
        .where(deals.c.game_id == game_id, deals.c.id == deal_id)
        .with_for_update()
    )
    row = result.mappings().first()
    return None if row is None else dict(row)


async def _load_deal_row_by_id(session: AsyncSession, deal_id: UUID) -> dict[str, Any] | None:
    result = await session.execute(sa.select(deals).where(deals.c.id == deal_id))
    row = result.mappings().first()
    return None if row is None else dict(row)


async def _load_negotiation_message_row_for_update(
    *,
    session: AsyncSession,
    game_id: UUID,
    negotiation_id: UUID,
    message_id: UUID,
) -> dict[str, Any] | None:
    result = await session.execute(
        sa.select(negotiation_messages)
        .where(
            negotiation_messages.c.game_id == game_id,
            negotiation_messages.c.negotiation_id == negotiation_id,
            negotiation_messages.c.id == message_id,
        )
        .with_for_update()
    )
    row = result.mappings().first()
    return None if row is None else dict(row)


async def _refresh_negotiation_offer_counts(
    *,
    session: AsyncSession,
    game_id: UUID,
    negotiation_id: UUID,
    context: dict[str, Any],
) -> None:
    result = await session.execute(
        sa.select(
            deals.c.proposed_by_player_id,
            deals.c.status,
            sa.func.count().label("deal_count"),
        )
        .where(deals.c.game_id == game_id, deals.c.negotiation_id == negotiation_id)
        .group_by(deals.c.proposed_by_player_id, deals.c.status)
    )
    proposal_counts: dict[str, int] = {}
    pending_counts: dict[str, int] = {}
    for row in result.mappings().all():
        proposer_id = row["proposed_by_player_id"]
        if proposer_id is None:
            continue
        proposer_key = str(proposer_id)
        count = int(row["deal_count"])
        proposal_counts[proposer_key] = proposal_counts.get(proposer_key, 0) + count
        if row["status"] == DEAL_STATUS_PROPOSED:
            pending_counts[proposer_key] = pending_counts.get(proposer_key, 0) + count

    context["proposal_counts_by_player_id"] = proposal_counts
    context["pending_offer_counts_by_player_id"] = pending_counts


def _proposal_cutoff_reason(
    *,
    context: Mapping[str, Any],
    proposed_by_player_id: str,
    next_round_number: int,
) -> str | None:
    cutoff_policy = context["cutoff_policy"]
    if next_round_number > int(cutoff_policy["max_rounds"]):
        return CUTOFF_MAX_ROUNDS

    proposal_counts = _count_mapping(context.get("proposal_counts_by_player_id"))
    if proposal_counts.get(proposed_by_player_id, 0) + 1 > int(
        cutoff_policy["max_proposals_per_player"]
    ):
        return CUTOFF_MAX_PROPOSALS_PER_PLAYER

    pending_counts = _count_mapping(context.get("pending_offer_counts_by_player_id"))
    if pending_counts.get(proposed_by_player_id, 0) + 1 > int(
        cutoff_policy["max_pending_offers_per_player"]
    ):
        return CUTOFF_MAX_PENDING_OFFERS_PER_PLAYER

    return None


async def _expire_if_active_time_cutoff(
    *,
    session: AsyncSession,
    game_id: UUID,
    negotiation_row: Mapping[str, Any],
    context: dict[str, Any],
    actor_player_id: UUID | None,
) -> JSONResponse | None:
    cutoff_reason = _active_time_cutoff_reason(negotiation_row, context)
    if cutoff_reason is None:
        return None
    return await _expire_negotiation_by_cutoff_and_reject(
        session=session,
        game_id=game_id,
        negotiation_row=negotiation_row,
        context=context,
        cutoff_reason=cutoff_reason,
        actor_player_id=actor_player_id,
    )


def _active_time_cutoff_reason(
    negotiation_row: Mapping[str, Any],
    context: Mapping[str, Any],
) -> str | None:
    now = datetime.now(UTC)
    expires_at = _parse_datetime_or_none(context.get("expires_at"))
    if expires_at is not None and expires_at <= now:
        return CUTOFF_MAX_ACTIVE_SECONDS

    created_at = _parse_datetime_or_none(negotiation_row.get("created_at"))
    max_active_seconds = int(context["cutoff_policy"]["max_active_seconds"])
    if created_at is not None and created_at + timedelta(seconds=max_active_seconds) <= now:
        return CUTOFF_MAX_ACTIVE_SECONDS
    return None


def _parse_datetime_or_none(value: object) -> datetime | None:
    parsed: datetime | None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


async def _expire_negotiation_by_cutoff_and_reject(
    *,
    session: AsyncSession,
    game_id: UUID,
    negotiation_row: Mapping[str, Any],
    context: dict[str, Any],
    cutoff_reason: str,
    actor_player_id: UUID | None,
) -> JSONResponse:
    await _expire_negotiation_by_cutoff(
        session=session,
        game_id=game_id,
        negotiation_row=negotiation_row,
        context=context,
        cutoff_reason=cutoff_reason,
        actor_player_id=actor_player_id,
    )
    return _lifecycle_rejection_response(
        cutoff_reason,
        _cutoff_reason_message(cutoff_reason),
        field="negotiation_cutoff",
    )


async def _expire_negotiation_by_cutoff(
    *,
    session: AsyncSession,
    game_id: UUID,
    negotiation_row: Mapping[str, Any],
    context: dict[str, Any],
    cutoff_reason: str,
    actor_player_id: UUID | None,
) -> None:
    current_deal_id = _uuid_or_none(context.get("current_deal_id"))
    if current_deal_id is not None:
        await session.execute(
            deals.update()
            .where(
                deals.c.game_id == game_id,
                deals.c.id == current_deal_id,
                deals.c.status == DEAL_STATUS_PROPOSED,
            )
            .values(status=DEAL_STATUS_EXPIRED, updated_at=sa.func.now())
        )

    context["cutoff_reason"] = cutoff_reason
    context["expired_by_cutoff"] = True
    context["expired_at"] = _audit_time_marker()
    _append_status_history(
        context,
        from_status=str(negotiation_row["status"]),
        to_status=NEGOTIATION_STATUS_EXPIRED,
        deal_id=context.get("current_deal_id"),
        round_number=int(negotiation_row["round_number"]),
    )
    await session.execute(
        negotiations.update()
        .where(negotiations.c.id == negotiation_row["id"])
        .values(
            status=NEGOTIATION_STATUS_EXPIRED,
            context=context,
            updated_at=sa.func.now(),
            closed_at=sa.func.now(),
        )
    )
    await _insert_negotiation_audit_message(
        session=session,
        game_id=game_id,
        negotiation_id=negotiation_row["id"],
        sender_player_id=actor_player_id,
        message_type=AUDIT_EXPIRED_BY_CUTOFF,
        payload={
            "cutoff_reason": cutoff_reason,
            "from_status": negotiation_row["status"],
            "to_status": NEGOTIATION_STATUS_EXPIRED,
            "deal_id": context.get("current_deal_id"),
            "round_number": negotiation_row["round_number"],
            "no_substitute_action": True,
            "created_game_event": False,
            "created_contract": False,
            "created_cash_transfer": False,
            "created_property_transfer": False,
        },
    )
    await _insert_negotiation_audit_message(
        session=session,
        game_id=game_id,
        negotiation_id=negotiation_row["id"],
        sender_player_id=actor_player_id,
        message_type=AUDIT_STATUS_CHANGED,
        payload={
            "from_status": negotiation_row["status"],
            "to_status": NEGOTIATION_STATUS_EXPIRED,
            "deal_id": context.get("current_deal_id"),
            "round_number": negotiation_row["round_number"],
            "cutoff_reason": cutoff_reason,
        },
    )


def _cutoff_reason_message(cutoff_reason: str) -> str:
    messages = {
        CUTOFF_MAX_ROUNDS: "proposal would exceed max rounds per negotiation window",
        CUTOFF_MAX_PROPOSALS_PER_PLAYER: (
            "proposal would exceed max proposals per player per negotiation window"
        ),
        CUTOFF_MAX_ACTIVE_SECONDS: "negotiation exceeded max active wall-clock duration",
        CUTOFF_MAX_AI_DECISION_ATTEMPTS: (
            "AI decision attempt would exceed max attempts per negotiation message"
        ),
        CUTOFF_MAX_PENDING_OFFERS_PER_PLAYER: (
            "proposal would exceed max pending offers per player"
        ),
    }
    return messages.get(cutoff_reason, "negotiation expired by cutoff")


async def _next_deal_version_in_session(
    *,
    session: AsyncSession,
    game_id: UUID,
    negotiation_id: UUID | None,
) -> int:
    if negotiation_id is None:
        return 1
    result = await session.execute(
        sa.select(sa.func.coalesce(sa.func.max(deals.c.version), 0)).where(
            deals.c.game_id == game_id,
            deals.c.negotiation_id == negotiation_id,
            deals.c.version > 0,
        )
    )
    return int(result.scalar_one()) + 1


async def _next_rejected_deal_version_in_session(
    *,
    session: AsyncSession,
    game_id: UUID,
    negotiation_id: UUID | None,
) -> int:
    result = await session.execute(
        sa.select(sa.func.coalesce(sa.func.min(deals.c.version), 0)).where(
            deals.c.game_id == game_id,
            deals.c.negotiation_id == negotiation_id,
            deals.c.version < 0,
        )
    )
    return int(result.scalar_one()) - 1


async def _insert_negotiation_audit_message(
    *,
    session: AsyncSession,
    game_id: UUID,
    negotiation_id: UUID,
    sender_player_id: UUID | None,
    message_type: str,
    payload: Mapping[str, Any],
) -> None:
    await session.execute(
        negotiation_messages.insert().values(
            game_id=game_id,
            negotiation_id=negotiation_id,
            sender_player_id=sender_player_id,
            recipient_player_id=None,
            message_type=message_type,
            body=None,
            payload=dict(payload),
        )
    )


def _lifecycle_rejection_response(
    reason_code: str,
    message: str,
    *,
    field: str | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={
            "status": "rejected",
            "reason_code": reason_code,
            "validation_errors": [
                {
                    "code": reason_code,
                    "message": message,
                    "field": field,
                }
            ],
        },
    )


def _lifecycle_rejection_response_from_errors(
    reason_code: str,
    validation_errors: Sequence[Mapping[str, Any]],
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={
            "status": "rejected",
            "reason_code": reason_code,
            "validation_errors": [dict(error) for error in validation_errors],
        },
    )


def _reject_if_negotiation_terminal(negotiation_status: str) -> JSONResponse | None:
    if negotiation_status == NEGOTIATION_STATUS_EXPIRED:
        return _lifecycle_rejection_response(
            "negotiation_expired",
            "expired negotiations do nothing and cannot execute",
            field="status",
        )
    if negotiation_status == NEGOTIATION_STATUS_REJECTED:
        return _lifecycle_rejection_response(
            "negotiation_rejected",
            "rejected negotiations cannot execute",
            field="status",
        )
    if negotiation_status == NEGOTIATION_STATUS_EXECUTED:
        return _lifecycle_rejection_response(
            "negotiation_executed",
            "executed negotiations are terminal",
            field="status",
        )
    return None


def _reject_if_negotiation_cannot_receive_proposal(negotiation_status: str) -> JSONResponse | None:
    terminal_response = _reject_if_negotiation_terminal(negotiation_status)
    if terminal_response is not None:
        return terminal_response
    if negotiation_status == NEGOTIATION_STATUS_ACCEPTED:
        return _lifecycle_rejection_response(
            "negotiation_already_accepted",
            "accepted negotiations cannot receive changed proposals",
            field="status",
        )
    return None


async def _record_deal_acceptance_or_rejection(
    *,
    game_id: UUID,
    deal_id: UUID,
    request: Request,
    payload: DealDecisionRequest | None,
    decision: Literal["accept", "reject"],
) -> DealResponse | JSONResponse:
    session_factory = _session_factory(request)
    await _ensure_game_exists(session_factory, game_id)
    async with session_factory() as session:
        async with session.begin():
            deal_row = await _load_deal_row_for_update(
                session=session,
                game_id=game_id,
                deal_id=deal_id,
            )
            if deal_row is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="deal not found")
            if deal_row["negotiation_id"] is None:
                return _lifecycle_rejection_response(
                    "deal_has_no_negotiation",
                    "deal lifecycle decisions require a negotiation",
                    field="negotiation_id",
                )

            negotiation_row = await _load_negotiation_row_for_update(
                session=session,
                game_id=game_id,
                negotiation_id=deal_row["negotiation_id"],
            )
            if negotiation_row is None:
                return _lifecycle_rejection_response(
                    "negotiation_not_found",
                    "deal negotiation does not belong to game",
                    field="negotiation_id",
                )

            terminal_response = _reject_if_negotiation_terminal(negotiation_row["status"])
            if terminal_response is not None:
                return terminal_response
            if negotiation_row["status"] == NEGOTIATION_STATUS_ACCEPTED:
                return _lifecycle_rejection_response(
                    "negotiation_already_accepted",
                    "accepted negotiations can only execute or expire",
                    field="status",
                )

            context = _normalized_negotiation_context(negotiation_row)
            cutoff_response = await _expire_if_active_time_cutoff(
                session=session,
                game_id=game_id,
                negotiation_row=negotiation_row,
                context=context,
                actor_player_id=None,
            )
            if cutoff_response is not None:
                return cutoff_response

            current_deal_id = context.get("current_deal_id")
            current_terms_hash = context.get("current_terms_hash")
            deal_terms = deal_row["terms"] if isinstance(deal_row["terms"], Mapping) else {}
            deal_terms_hash = _terms_hash_for_response(deal_terms)
            if current_deal_id != str(deal_id) or current_terms_hash != deal_terms_hash:
                return _lifecycle_rejection_response(
                    "exact_term_acceptance_required",
                    "acceptance must target the current deal and exact current terms_hash",
                    field="deal_id",
                )
            if str(deal_id) in context.get("invalidated_acceptances", {}):
                return _lifecycle_rejection_response(
                    "exact_term_acceptance_required",
                    "acceptances invalidated by a changed counteroffer cannot be reused",
                    field="deal_id",
                )
            if deal_row["status"] != DEAL_STATUS_PROPOSED:
                return _lifecycle_rejection_response(
                    f"deal_{deal_row['status']}",
                    "only proposed current deals can be accepted or rejected",
                    field="status",
                )

            actor_player_id = _decision_player_id(payload, context, deal_row)
            if actor_player_id is None:
                return _lifecycle_rejection_response(
                    "player_id_required",
                    "player_id is required when more than one participant could respond",
                    field="player_id",
                )
            if actor_player_id not in context["participant_player_ids"]:
                return _lifecycle_rejection_response(
                    "player_not_participant",
                    "player_id must be a negotiation participant",
                    field="player_id",
                )

            if decision == "reject":
                updated_deal = await _reject_current_deal(
                    session=session,
                    game_id=game_id,
                    deal_row=deal_row,
                    negotiation_row=negotiation_row,
                    context=context,
                    actor_player_id=actor_player_id,
                )
                return _deal_response(updated_deal)

            updated_deal = await _accept_current_deal(
                session=session,
                game_id=game_id,
                deal_row=deal_row,
                negotiation_row=negotiation_row,
                context=context,
                actor_player_id=actor_player_id,
            )
            if isinstance(updated_deal, JSONResponse):
                return updated_deal
            return _deal_response(updated_deal)


async def _accept_current_deal(
    *,
    session: AsyncSession,
    game_id: UUID,
    deal_row: Mapping[str, Any],
    negotiation_row: Mapping[str, Any],
    context: dict[str, Any],
    actor_player_id: str,
) -> Mapping[str, Any] | JSONResponse:
    deal_id = str(deal_row["id"])
    acceptances = context.setdefault("acceptances", {})
    accepted_player_ids = set(acceptances.get(deal_id, []))
    if actor_player_id in accepted_player_ids:
        return _lifecycle_rejection_response(
            "deal_already_accepted_by_player",
            "player has already accepted the current deal",
            field="player_id",
        )

    accepted_player_ids.add(actor_player_id)
    acceptances[deal_id] = [
        player_id for player_id in context["participant_player_ids"] if player_id in accepted_player_ids
    ]
    await _insert_negotiation_audit_message(
        session=session,
        game_id=game_id,
        negotiation_id=negotiation_row["id"],
        sender_player_id=UUID(actor_player_id),
        message_type=AUDIT_DEAL_ACCEPTED,
        payload={
            "deal_id": deal_id,
            "player_id": actor_player_id,
            "accepted_player_ids": acceptances[deal_id],
            "round_number": negotiation_row["round_number"],
            "terms_hash": context.get("current_terms_hash"),
            "current_deal_id": context.get("current_deal_id"),
            "exact_term_acceptance": True,
        },
    )

    missing_acceptances = _missing_acceptances(context, deal_id)
    deal_values: dict[str, Any] = {"updated_at": sa.func.now()}
    negotiation_values: dict[str, Any] = {"context": context, "updated_at": sa.func.now()}
    if not missing_acceptances:
        deal_values.update(status=DEAL_STATUS_ACCEPTED, accepted_at=sa.func.now())
        negotiation_values["status"] = NEGOTIATION_STATUS_ACCEPTED
        if negotiation_row["status"] != NEGOTIATION_STATUS_ACCEPTED:
            _append_status_history(
                context,
                from_status=negotiation_row["status"],
                to_status=NEGOTIATION_STATUS_ACCEPTED,
                deal_id=deal_id,
                round_number=negotiation_row["round_number"],
            )

    await session.execute(deals.update().where(deals.c.id == deal_row["id"]).values(**deal_values))
    await session.execute(
        negotiations.update().where(negotiations.c.id == negotiation_row["id"]).values(**negotiation_values)
    )

    if not missing_acceptances and negotiation_row["status"] != NEGOTIATION_STATUS_ACCEPTED:
        await _insert_negotiation_audit_message(
            session=session,
            game_id=game_id,
            negotiation_id=negotiation_row["id"],
            sender_player_id=UUID(actor_player_id),
            message_type=AUDIT_STATUS_CHANGED,
            payload={
                "from_status": negotiation_row["status"],
                "to_status": NEGOTIATION_STATUS_ACCEPTED,
                "deal_id": deal_id,
                "round_number": negotiation_row["round_number"],
            },
        )

    updated_deal = await _load_deal_row_by_id(session, deal_row["id"])
    if updated_deal is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="deal not found")
    return updated_deal


async def _reject_current_deal(
    *,
    session: AsyncSession,
    game_id: UUID,
    deal_row: Mapping[str, Any],
    negotiation_row: Mapping[str, Any],
    context: dict[str, Any],
    actor_player_id: str,
) -> Mapping[str, Any]:
    deal_id = str(deal_row["id"])
    context["rejected_deal_id"] = deal_id
    context["rejected_by_player_id"] = actor_player_id
    _append_status_history(
        context,
        from_status=negotiation_row["status"],
        to_status=NEGOTIATION_STATUS_REJECTED,
        deal_id=deal_id,
        round_number=negotiation_row["round_number"],
    )
    await session.execute(
        deals.update()
        .where(deals.c.id == deal_row["id"])
        .values(status=DEAL_STATUS_REJECTED, updated_at=sa.func.now())
    )
    await session.execute(
        negotiations.update()
        .where(negotiations.c.id == negotiation_row["id"])
        .values(
            status=NEGOTIATION_STATUS_REJECTED,
            context=context,
            updated_at=sa.func.now(),
            closed_at=sa.func.now(),
        )
    )
    await _insert_negotiation_audit_message(
        session=session,
        game_id=game_id,
        negotiation_id=negotiation_row["id"],
        sender_player_id=UUID(actor_player_id),
        message_type=AUDIT_DEAL_REJECTED,
        payload={
            "deal_id": deal_id,
            "player_id": actor_player_id,
            "round_number": negotiation_row["round_number"],
        },
    )
    await _insert_negotiation_audit_message(
        session=session,
        game_id=game_id,
        negotiation_id=negotiation_row["id"],
        sender_player_id=UUID(actor_player_id),
        message_type=AUDIT_STATUS_CHANGED,
        payload={
            "from_status": negotiation_row["status"],
            "to_status": NEGOTIATION_STATUS_REJECTED,
            "deal_id": deal_id,
            "round_number": negotiation_row["round_number"],
        },
    )
    updated_deal = await _load_deal_row_by_id(session, deal_row["id"])
    if updated_deal is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="deal not found")
    return updated_deal


def _decision_player_id(
    payload: DealDecisionRequest | None,
    context: Mapping[str, Any],
    deal_row: Mapping[str, Any],
) -> str | None:
    if payload is not None and payload.player_id is not None:
        return str(payload.player_id)
    proposer_id = None if deal_row["proposed_by_player_id"] is None else str(deal_row["proposed_by_player_id"])
    non_proposer_ids = [
        player_id for player_id in context["participant_player_ids"] if player_id != proposer_id
    ]
    if len(non_proposer_ids) == 1:
        return non_proposer_ids[0]
    return None


def _missing_acceptances(context: Mapping[str, Any], deal_id: str) -> list[str]:
    accepted_player_ids = set(context["acceptances"].get(deal_id, []))
    return [
        player_id
        for player_id in context["participant_player_ids"]
        if player_id not in accepted_player_ids
    ]


def _append_status_history(
    context: dict[str, Any],
    *,
    from_status: str,
    to_status: str,
    deal_id: str | None,
    round_number: int,
) -> None:
    history = context.setdefault("status_history", [])
    if isinstance(history, list):
        history.append(
            {
                "from_status": from_status,
                "to_status": to_status,
                "deal_id": deal_id,
                "round_number": round_number,
            }
        )


def _uuid_or_none(value: object) -> UUID | None:
    if value is None or value == "":
        return None
    return UUID(str(value))


def _string_or_none(value: object) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def _int_or_none(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _audit_time_marker() -> str:
    return "expired"


async def _ensure_game_exists(
    session_factory: async_sessionmaker[AsyncSession],
    game_id: UUID,
) -> None:
    async with session_factory() as session:
        result = await session.execute(sa.select(games.c.id).where(games.c.id == game_id))
        if result.scalar_one_or_none() is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="game not found")


async def _ensure_player_in_game(
    session_factory: async_sessionmaker[AsyncSession],
    game_id: UUID,
    player_id: UUID,
) -> None:
    await _ensure_player_ids_in_game(session_factory, game_id, [player_id])


async def _ensure_player_ids_in_game(
    session_factory: async_sessionmaker[AsyncSession],
    game_id: UUID,
    player_ids: Sequence[UUID],
) -> None:
    normalized_ids = set(player_ids)
    async with session_factory() as session:
        result = await session.execute(
            sa.select(players.c.id).where(players.c.game_id == game_id, players.c.id.in_(normalized_ids))
        )
        found_ids = set(result.scalars().all())
    missing_ids = normalized_ids - found_ids
    if missing_ids:
        raise HTTPException(
            status_code=422,
            detail=f"unknown player for game: {sorted(str(player_id) for player_id in missing_ids)[0]}",
        )


async def _ensure_negotiation_in_game(
    session_factory: async_sessionmaker[AsyncSession],
    game_id: UUID,
    negotiation_id: UUID,
) -> None:
    async with session_factory() as session:
        result = await session.execute(
            sa.select(negotiations.c.id).where(
                negotiations.c.game_id == game_id,
                negotiations.c.id == negotiation_id,
            )
        )
        if result.scalar_one_or_none() is None:
            raise HTTPException(
                status_code=422,
                detail="negotiation does not belong to game",
            )


async def _ensure_deal_in_game(
    session_factory: async_sessionmaker[AsyncSession],
    game_id: UUID,
    deal_id: UUID,
) -> None:
    async with session_factory() as session:
        result = await session.execute(
            sa.select(deals.c.id).where(deals.c.game_id == game_id, deals.c.id == deal_id)
        )
        if result.scalar_one_or_none() is None:
            raise HTTPException(
                status_code=422,
                detail="parent deal does not belong to game",
            )


async def _next_deal_version(
    session_factory: async_sessionmaker[AsyncSession],
    game_id: UUID,
    negotiation_id: UUID | None,
) -> int:
    if negotiation_id is None:
        return 1

    async with session_factory() as session:
        result = await session.execute(
            sa.select(sa.func.coalesce(sa.func.max(deals.c.version), 0)).where(
                deals.c.game_id == game_id,
                deals.c.negotiation_id == negotiation_id,
                deals.c.version > 0,
            )
        )
        return int(result.scalar_one()) + 1


async def _persist_idempotent_rejection_response(
    *,
    session: AsyncSession,
    game_id: UUID,
    state: GameState,
    idempotency_key: str,
    request_hash: str,
    raw_payload: object,
    actor_id: str | None,
    action_type: str,
    submitted_payload: Mapping[str, Any],
    validation_errors: Sequence[Mapping[str, Any]],
) -> JSONResponse:
    reason_code = _reason_code(validation_errors)
    actor_player_id = await _resolve_actor_player_id_in_session(
        session=session,
        game_id=game_id,
        actor_id=actor_id,
    )
    legal_action_context = _legal_action_context(state, actor_id)
    rejected_row = await _persist_rejected_action_in_session(
        session=session,
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
    response_payload = _rejection_response_payload(
        rejected_action_id=rejected_row["id"],
        reason_code=reason_code,
        validation_errors=validation_errors,
        legal_action_context=legal_action_context,
        submitted_action=raw_payload,
    )
    await _persist_idempotency_key(
        session=session,
        game_id=game_id,
        actor_player_id=actor_player_id,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        outcome_status="rejected",
        response_payload=response_payload,
        created_event_sequence_start=None,
        created_event_sequence_end=None,
        rejected_action_id=rejected_row["id"],
    )
    return JSONResponse(status_code=_status_code_for_reason(reason_code), content=response_payload)


async def _persist_rejected_action_in_session(
    *,
    session: AsyncSession,
    game_id: UUID,
    actor_player_id: UUID | None,
    action_type: str,
    payload: Mapping[str, Any],
    reason_code: str,
    validation_errors: Sequence[Mapping[str, Any]],
    legal_action_context: Mapping[str, Any] | None,
    phase: str | None,
    state_hash: str | None,
) -> Mapping[str, Any]:
    result = await session.execute(
        rejected_actions.insert()
        .values(
            game_id=game_id,
            actor_player_id=actor_player_id,
            action_type=action_type,
            payload=dict(payload),
            reason_code=reason_code,
            validation_errors=[dict(error) for error in validation_errors],
            legal_action_context=None if legal_action_context is None else dict(legal_action_context),
            phase=phase,
            state_hash=state_hash,
        )
        .returning(rejected_actions)
    )
    return dict(result.mappings().one())


async def _load_idempotency_key(
    *,
    session: AsyncSession,
    game_id: UUID,
    idempotency_key: str,
) -> Mapping[str, Any] | None:
    result = await session.execute(
        sa.select(action_idempotency_keys)
        .where(
            action_idempotency_keys.c.game_id == game_id,
            action_idempotency_keys.c.idempotency_key == idempotency_key,
        )
        .with_for_update()
    )
    row = result.mappings().first()
    return None if row is None else dict(row)


async def _persist_idempotency_key(
    *,
    session: AsyncSession,
    game_id: UUID,
    actor_player_id: UUID | None,
    idempotency_key: str,
    request_hash: str,
    outcome_status: str,
    response_payload: Mapping[str, Any],
    created_event_sequence_start: int | None,
    created_event_sequence_end: int | None,
    rejected_action_id: UUID | None,
) -> None:
    await session.execute(
        action_idempotency_keys.insert().values(
            game_id=game_id,
            actor_player_id=actor_player_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            status=outcome_status,
            response_payload=dict(response_payload),
            created_event_sequence_start=created_event_sequence_start,
            created_event_sequence_end=created_event_sequence_end,
            rejected_action_id=rejected_action_id,
        )
    )


async def _resolve_actor_player_id_in_session(
    *,
    session: AsyncSession,
    game_id: UUID,
    actor_id: str | None,
) -> UUID | None:
    if actor_id is None:
        return None
    try:
        normalized_actor_id = UUID(str(actor_id))
    except ValueError:
        return None

    result = await session.execute(
        sa.select(players.c.id).where(
            players.c.game_id == game_id,
            players.c.id == normalized_actor_id,
        )
    )
    return result.scalar_one_or_none()


def _request_payload_from_body(raw_body: bytes) -> tuple[object, list[dict[str, str]]]:
    try:
        payload: object = json.loads(raw_body)
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


def _request_hash(
    *,
    raw_body: bytes,
    raw_payload: object,
    parse_errors: Sequence[Mapping[str, Any]],
) -> str:
    if parse_errors:
        payload_bytes = raw_body
    else:
        payload_bytes = json.dumps(
            raw_payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    return hashlib.sha256(payload_bytes).hexdigest()


def _status_code_for_persisted_response(response_payload: Mapping[str, Any]) -> int:
    if response_payload.get("status") == "accepted":
        return status.HTTP_200_OK
    reason_code = response_payload.get("reason_code")
    if isinstance(reason_code, str):
        return _status_code_for_reason(reason_code)
    return status.HTTP_422_UNPROCESSABLE_ENTITY


def _rejection_response_payload(
    *,
    rejected_action_id: UUID,
    reason_code: str,
    validation_errors: Sequence[Mapping[str, Any]],
    legal_action_context: Mapping[str, Any] | None,
    submitted_action: object,
) -> dict[str, Any]:
    return {
        "status": "rejected",
        "rejected_action_id": str(rejected_action_id),
        "reason_code": reason_code,
        "validation_errors": [dict(error) for error in validation_errors],
        "legal_action_context": legal_action_context,
        "submitted_action": submitted_action,
    }


def _idempotency_conflict_response(raw_payload: object) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={
            "status": "rejected",
            "reason_code": "idempotency_key_conflict",
            "validation_errors": [
                {
                    "code": "idempotency_key_conflict",
                    "message": "idempotency key was already used with a different request body",
                    "field": "Idempotency-Key",
                }
            ],
            "submitted_action": raw_payload,
        },
    )


def _missing_idempotency_key_payload() -> dict[str, Any]:
    return {
        "status": "rejected",
        "reason_code": "missing_idempotency_key",
        "validation_errors": [
            {
                "code": "missing_idempotency_key",
                "message": "POST /games/{game_id}/actions requires an Idempotency-Key header",
                "field": "Idempotency-Key",
            }
        ],
    }


def _missing_idempotency_key_response() -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content=_missing_idempotency_key_payload(),
    )


def _state_response(game_id: UUID, state: GameState) -> GameStateResponse:
    return GameStateResponse(
        game_id=game_id,
        state=_state_payload(state),
        state_hash=state.state_hash(),
        event_sequence=state.event_sequence,
    )


def _state_payload(state: GameState) -> dict[str, Any]:
    return {**state.model_dump(mode="json"), "state_hash": state.state_hash()}


def _event_response(record: AcceptedEventRecord) -> AcceptedEventResponse:
    return AcceptedEventResponse.model_validate(record.model_dump())


def _rejected_response(record: RejectedActionRecord) -> RejectedActionResponse:
    return RejectedActionResponse.model_validate(record.model_dump())


def _negotiation_message_response(row: Mapping[str, Any]) -> NegotiationMessageResponse:
    return NegotiationMessageResponse(
        id=row["id"],
        game_id=row["game_id"],
        negotiation_id=row["negotiation_id"],
        sender_player_id=row["sender_player_id"],
        author_player_id=row["sender_player_id"],
        recipient_player_id=row["recipient_player_id"],
        message_type=row["message_type"],
        body=row["body"],
        payload=row["payload"],
        created_at=row["created_at"],
    )


def _negotiation_response(row: Mapping[str, Any]) -> NegotiationResponse:
    stored_context = _normalized_negotiation_context(row)
    participant_ids = stored_context["participant_player_ids"]
    context = stored_context["context"]
    return NegotiationResponse(
        id=row["id"],
        game_id=row["game_id"],
        opened_by_player_id=row["opened_by_player_id"],
        participant_player_ids=[UUID(str(player_id)) for player_id in participant_ids],
        status=row["status"],
        phase=row["phase"],
        round_number=row["round_number"],
        pending_deal_id=_uuid_or_none(stored_context["pending_deal_id"]),
        current_deal_id=_uuid_or_none(stored_context["current_deal_id"]),
        current_terms_hash=stored_context["current_terms_hash"],
        current_deal_version=stored_context["current_deal_version"],
        acceptances={
            str(deal_id): [UUID(str(player_id)) for player_id in player_ids]
            for deal_id, player_ids in stored_context["acceptances"].items()
        },
        invalidated_acceptances={
            str(deal_id): [UUID(str(player_id)) for player_id in player_ids]
            for deal_id, player_ids in stored_context["invalidated_acceptances"].items()
        },
        status_history=list(stored_context["status_history"]),
        expires_at=stored_context["expires_at"],
        context=context,
        cutoff_policy=dict(stored_context["cutoff_policy"]),
        proposal_counts_by_player_id=dict(stored_context["proposal_counts_by_player_id"]),
        pending_offer_counts_by_player_id=dict(stored_context["pending_offer_counts_by_player_id"]),
        ai_decision_attempts_by_message_id=dict(
            stored_context["ai_decision_attempts_by_message_id"]
        ),
        cutoff_reason=stored_context["cutoff_reason"],
        expired_by_cutoff=stored_context["expired_by_cutoff"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        closed_at=row["closed_at"],
    )


def _deal_response(row: Mapping[str, Any]) -> DealResponse:
    terms = row["terms"]
    terms_mapping = terms if isinstance(terms, Mapping) else {}
    structured_deal = _is_structured_deal_terms(terms_mapping)
    terms_hash = _terms_hash_for_response(terms_mapping)
    return DealResponse(
        id=row["id"],
        game_id=row["game_id"],
        negotiation_id=row["negotiation_id"],
        proposed_by_player_id=row["proposed_by_player_id"],
        proposer_player_id=row["proposed_by_player_id"],
        participant_player_ids=_participant_ids_for_deal_terms(terms_mapping),
        parent_deal_id=row["parent_deal_id"],
        status=row["status"],
        version=row["version"],
        deal_version=row["version"],
        terms=row["terms"],
        structured_deal=structured_deal,
        deal_schema_version=_deal_schema_version_for_terms(terms_mapping),
        terms_hash=terms_hash,
        eligible_for_contract=_eligible_for_contract(row, structured_deal=structured_deal),
        validation_errors=row["validation_errors"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        accepted_at=row["accepted_at"],
    )


def _is_structured_deal_terms(terms: Mapping[str, Any]) -> bool:
    return terms.get("kind") == STRUCTURED_DEAL_KIND and isinstance(terms.get("terms_hash"), str)


def _deal_schema_version_for_terms(terms: Mapping[str, Any]) -> int | None:
    if not _is_structured_deal_terms(terms):
        return None
    version = terms.get("deal_schema_version")
    return version if isinstance(version, int) and not isinstance(version, bool) else None


def _terms_hash_for_response(terms: Mapping[str, Any]) -> str:
    terms_hash = terms.get("terms_hash")
    if isinstance(terms_hash, str) and terms_hash:
        return terms_hash
    return _canonical_hash(_json_safe_mapping(terms))


def _participant_ids_for_deal_terms(terms: Mapping[str, Any]) -> list[UUID]:
    participants = terms.get("participants", terms.get("participant_player_ids", []))
    return [UUID(player_id) for player_id in _normalized_uuid_strings(participants)]


def _eligible_for_contract(
    row: Mapping[str, Any],
    *,
    structured_deal: bool,
) -> bool:
    return row["status"] == DEAL_STATUS_ACCEPTED and structured_deal


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
    if reason_code in {"stale_action", "mistimed_action", "idempotency_key_conflict"}:
        return status.HTTP_409_CONFLICT
    if reason_code == "missing_idempotency_key":
        return status.HTTP_400_BAD_REQUEST
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


def _session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    return request.app.state.database_session_factory


__all__ = ["router"]
