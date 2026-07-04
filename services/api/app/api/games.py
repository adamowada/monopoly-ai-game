from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

import sqlalchemy as sa
from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette import status

from app.db.metadata import action_idempotency_keys, deals, games, negotiations, players, rejected_actions
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


class CreateNegotiationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    opened_by_player_id: UUID
    participant_player_ids: list[UUID] = Field(min_length=2, max_length=5)
    context: dict[str, Any] = Field(default_factory=dict)

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
    context: Mapping[str, Any]
    created_at: Any
    updated_at: Any
    closed_at: Any | None


class CreateDealRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposed_by_player_id: UUID
    negotiation_id: UUID | None = None
    parent_deal_id: UUID | None = None
    terms: dict[str, Any] = Field(min_length=1)


class DealResponse(BaseModel):
    id: UUID
    game_id: UUID
    negotiation_id: UUID | None
    proposed_by_player_id: UUID | None
    parent_deal_id: UUID | None
    status: str
    version: int
    terms: Mapping[str, Any]
    validation_errors: Sequence[Mapping[str, Any]] | None
    created_at: Any
    updated_at: Any
    accepted_at: Any | None


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
    await _ensure_game_exists(session_factory, game_id)
    await _ensure_player_ids_in_game(
        session_factory,
        game_id,
        [payload.opened_by_player_id, *payload.participant_player_ids],
    )
    state = await _load_replayed_state(session_factory, game_id)

    stored_context = {
        "participant_player_ids": [str(player_id) for player_id in payload.participant_player_ids],
        "context": dict(payload.context),
    }
    async with session_factory() as session:
        async with session.begin():
            result = await session.execute(
                negotiations.insert()
                .values(
                    game_id=game_id,
                    opened_by_player_id=payload.opened_by_player_id,
                    status="opened",
                    phase=state.turn.phase.value,
                    round_number=0,
                    context=stored_context,
                )
                .returning(negotiations)
            )
            row = dict(result.mappings().one())

    return _negotiation_response(row)


@router.post("/{game_id}/deals", response_model=DealResponse, status_code=status.HTTP_201_CREATED)
async def create_deal(game_id: UUID, request: Request, payload: CreateDealRequest) -> DealResponse:
    session_factory = _session_factory(request)
    await _ensure_game_exists(session_factory, game_id)
    await _ensure_player_in_game(session_factory, game_id, payload.proposed_by_player_id)
    if payload.negotiation_id is not None:
        await _ensure_negotiation_in_game(session_factory, game_id, payload.negotiation_id)
    if payload.parent_deal_id is not None:
        await _ensure_deal_in_game(session_factory, game_id, payload.parent_deal_id)

    version = await _next_deal_version(session_factory, game_id, payload.negotiation_id)
    async with session_factory() as session:
        async with session.begin():
            result = await session.execute(
                deals.insert()
                .values(
                    game_id=game_id,
                    negotiation_id=payload.negotiation_id,
                    proposed_by_player_id=payload.proposed_by_player_id,
                    parent_deal_id=payload.parent_deal_id,
                    status="proposed",
                    version=version,
                    terms=dict(payload.terms),
                    validation_errors=None,
                )
                .returning(deals)
            )
            row = dict(result.mappings().one())

    return _deal_response(row)


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


def _negotiation_response(row: Mapping[str, Any]) -> NegotiationResponse:
    stored_context = row["context"] or {}
    participant_ids = stored_context.get("participant_player_ids", [])
    context = stored_context.get("context", {})
    return NegotiationResponse(
        id=row["id"],
        game_id=row["game_id"],
        opened_by_player_id=row["opened_by_player_id"],
        participant_player_ids=[UUID(str(player_id)) for player_id in participant_ids],
        status=row["status"],
        phase=row["phase"],
        round_number=row["round_number"],
        context=context if isinstance(context, Mapping) else {},
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        closed_at=row["closed_at"],
    )


def _deal_response(row: Mapping[str, Any]) -> DealResponse:
    return DealResponse(
        id=row["id"],
        game_id=row["game_id"],
        negotiation_id=row["negotiation_id"],
        proposed_by_player_id=row["proposed_by_player_id"],
        parent_deal_id=row["parent_deal_id"],
        status=row["status"],
        version=row["version"],
        terms=row["terms"],
        validation_errors=row["validation_errors"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        accepted_at=row["accepted_at"],
    )


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
