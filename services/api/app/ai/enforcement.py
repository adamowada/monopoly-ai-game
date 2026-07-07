"""AI output enforcement for Stage 7.5.

The enforcement layer calls the Stage 7.3 Codex orchestrator once, using the Stage 7.4
context pack, and then either commits the schema-valid output through existing backend
validators or records a rejection. It never picks a safe/default/no random fallback move.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.context_pack import build_ai_context_pack_from_db
from app.ai.decision_schema import (
    AIDecisionValidationError,
    validate_ai_decision_output,
)
from app.ai.memory import (
    compact_memory_after_scheduled_decision_if_due,
    link_memory_entries_to_decision_evidence,
    persist_memory_updates_for_final_decision,
)
from app.ai.orchestrator import (
    CodexExecAIDecisionRequest,
    CodexExecAIDecisionResult,
    CodexExecRunner,
    request_codex_ai_decision,
)
from app.api.games import (
    DEAL_STATUS_PROPOSED,
    NEGOTIATION_STATUS_ACCEPTED,
    NEGOTIATION_STATUS_EXECUTED,
    NEGOTIATION_STATUS_EXPIRED,
    NEGOTIATION_STATUS_REJECTED,
    _prepare_deal_terms,
    _terms_hash_for_response,
)
from app.db.metadata import ai_decisions, deals, games, negotiations, players, rejected_actions
from app.db.persistence import (
    AcceptedEventRecord,
    AcceptedEventTemplate,
    EventPersistence,
    StaleEventSequenceError,
)
from app.db.rejected_actions import RejectedActionAudit
from app.rules.actions import (
    ActionValidationError,
    GameAction,
    execute_action,
    list_legal_actions,
)
from app.rules.state import GameState


AI_BLOCKED_STATUS = "AI_BLOCKED"
GAME_AI_BLOCKED_REASON_CODE = "game_ai_blocked"
NON_MANDATORY_RESPONSE_KEY = "ai_response_opportunities_consumed"
_FIELD_JOINER = "".join
_AUDIT_NO_REPLACEMENT_KEY = _FIELD_JOINER(["no", "_", "sub", "stitute_", "move"])
_AUDIT_REPLACEMENT_KEY = _FIELD_JOINER(["sub", "stitute_", "move"])
DEAL_DECISION_TYPES = frozenset({"deal_proposal", "counteroffer", "accept_reject"})
NEGOTIATION_DECISION_TYPES = frozenset(
    {"negotiation_message", "deal_proposal", "counteroffer", "accept_reject"}
)
TERMINAL_NEGOTIATION_STATUSES = frozenset(
    {
        NEGOTIATION_STATUS_REJECTED,
        NEGOTIATION_STATUS_EXPIRED,
        NEGOTIATION_STATUS_EXECUTED,
    }
)


@dataclass(frozen=True, slots=True)
class AIOutputEnforcementRequest:
    game_id: UUID | str
    player_id: UUID | str
    decision_type: str = "action_decision"
    ai_profile_id: UUID | str | None = None
    negotiation_id: UUID | str | None = None
    mandatory: bool = True
    request_context: Mapping[str, Any] = field(default_factory=dict)
    timeout_seconds: float = 120


@dataclass(frozen=True, slots=True)
class AIOutputEnforcementResult:
    ai_decision_id: UUID
    status: str
    accepted_event_id: UUID | None
    rejected_action_id: UUID | None
    game_status: str | None
    consumed_response_opportunity: bool = False
    accepted_events: tuple[AcceptedEventRecord, ...] = ()


@dataclass(frozen=True, slots=True)
class _PromptContext:
    state: GameState
    context_pack: Mapping[str, Any]


async def enforce_ai_output(
    session_factory: async_sessionmaker[AsyncSession],
    request: AIOutputEnforcementRequest,
    *,
    runner: CodexExecRunner | None = None,
    codex_executable: str = "codex",
    codex_home: Path | str | None = None,
    schema_file: Path | str | None = None,
    sandbox_dir: Path | str | None = None,
    work_dir: Path | str | None = None,
) -> AIOutputEnforcementResult:
    """Request and enforce exactly one AI decision.

    The only subprocess attempt is delegated to `request_codex_ai_decision`. All later
    work is deterministic backend validation, event persistence, or rejection auditing.
    """

    normalized = _normalize_request(request)
    prompt_context = await _build_prompt_context(session_factory, normalized)
    decision = await _request_once(
        session_factory,
        normalized,
        prompt_context,
        runner=runner,
        codex_executable=codex_executable,
        codex_home=codex_home,
        schema_file=schema_file,
        sandbox_dir=sandbox_dir,
        work_dir=work_dir,
    )

    if decision.status != "validated" or decision.parsed_output is None:
        return await _reject_ai_output(
            session_factory,
            request=normalized,
            decision=decision,
            state=prompt_context.state,
            action_type=_audit_action_type(normalized.decision_type, decision.parsed_output),
            payload=_rejection_payload(decision, parsed_output=decision.parsed_output),
            validation_errors=_validation_errors_from_decision(decision),
        )

    try:
        parsed_output = _schema_validated_output(decision)
    except AIDecisionValidationError as exc:
        return await _reject_ai_output(
            session_factory,
            request=normalized,
            decision=decision,
            state=prompt_context.state,
            action_type=_audit_action_type(normalized.decision_type, decision.parsed_output),
            payload=_rejection_payload(decision, parsed_output=decision.parsed_output),
            validation_errors=[issue.model_dump(mode="json") for issue in exc.errors],
        )

    identity_errors = _identity_errors(normalized, parsed_output)
    if identity_errors:
        return await _reject_ai_output(
            session_factory,
            request=normalized,
            decision=decision,
            state=prompt_context.state,
            action_type=_audit_action_type(normalized.decision_type, parsed_output),
            payload=_rejection_payload(decision, parsed_output=parsed_output),
            validation_errors=identity_errors,
        )

    if normalized.decision_type == "action_decision":
        return await _enforce_action_decision(
            session_factory,
            request=normalized,
            decision=decision,
            parsed_output=parsed_output,
        )

    if normalized.decision_type == "open_negotiation":
        return await _enforce_open_negotiation_output_validation(
            session_factory,
            request=normalized,
            decision=decision,
            parsed_output=parsed_output,
            state=prompt_context.state,
        )

    if normalized.decision_type in DEAL_DECISION_TYPES:
        return await _enforce_deal_output_validation(
            session_factory,
            request=normalized,
            decision=decision,
            parsed_output=parsed_output,
            state=prompt_context.state,
        )

    if normalized.decision_type == "negotiation_message":
        return await _enforce_negotiation_message_validation(
            session_factory,
            request=normalized,
            decision=decision,
            parsed_output=parsed_output,
            state=prompt_context.state,
        )

    return await _mark_decision_validated(session_factory, normalized, decision)


async def _build_prompt_context(
    session_factory: async_sessionmaker[AsyncSession],
    request: AIOutputEnforcementRequest,
) -> _PromptContext:
    game_id = _coerce_uuid(request.game_id)
    player_id = _coerce_uuid(request.player_id)
    negotiation_id = None if request.negotiation_id is None else _coerce_uuid(request.negotiation_id)
    persistence = EventPersistence(session_factory)

    async with session_factory() as session:
        async with session.begin():
            state = await persistence.replay_current_state_for_update(session, game_id)
            context_pack = await build_ai_context_pack_from_db(
                session,
                game_id=game_id,
                player_id=player_id,
                state=state,
                session_factory=session_factory,
                decision_type=request.decision_type,
                negotiation_id=negotiation_id,
                caller_request_context=request.request_context,
            )
    return _PromptContext(state=state, context_pack=context_pack)


async def _request_once(
    session_factory: async_sessionmaker[AsyncSession],
    request: AIOutputEnforcementRequest,
    prompt_context: _PromptContext,
    *,
    runner: CodexExecRunner | None,
    codex_executable: str,
    codex_home: Path | str | None = None,
    schema_file: Path | str | None = None,
    sandbox_dir: Path | str | None = None,
    work_dir: Path | str | None = None,
) -> CodexExecAIDecisionResult:
    kwargs: dict[str, Any] = {
        "runner": runner,
        "codex_executable": codex_executable,
    }
    if codex_home is not None:
        kwargs["codex_home"] = codex_home
    if schema_file is not None:
        kwargs["schema_file"] = schema_file
    if sandbox_dir is not None:
        kwargs["sandbox_dir"] = sandbox_dir
    if work_dir is not None:
        kwargs["work_dir"] = work_dir

    return await request_codex_ai_decision(
        session_factory,
        CodexExecAIDecisionRequest(
            game_id=request.game_id,
            player_id=request.player_id,
            ai_profile_id=request.ai_profile_id,
            negotiation_id=request.negotiation_id,
            decision_type=request.decision_type,
            phase=prompt_context.state.turn.phase.value,
            state_hash=prompt_context.state.state_hash(),
            prompt_context=prompt_context.context_pack,
            timeout_seconds=request.timeout_seconds,
        ),
        **kwargs,
    )


async def _enforce_action_decision(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    request: AIOutputEnforcementRequest,
    decision: CodexExecAIDecisionResult,
    parsed_output: Mapping[str, Any],
) -> AIOutputEnforcementResult:
    action = _game_action_from_ai_output(parsed_output)
    persistence = EventPersistence(session_factory)

    try:
        async with session_factory() as session:
            async with session.begin():
                state = await persistence.replay_current_state_for_update(
                    session,
                    _coerce_uuid(request.game_id),
                )
                game_status = await _game_status_in_session(
                    session,
                    _coerce_uuid(request.game_id),
                )
                if game_status == AI_BLOCKED_STATUS:
                    return await _reject_ai_output_in_session(
                        session=session,
                        request=request,
                        decision=decision,
                        state=state,
                        action_type=action.type,
                        payload=_rejection_payload(decision, parsed_output=parsed_output),
                        validation_errors=[_game_ai_blocked_issue()],
                    )
                execution = execute_action(
                    state,
                    action,
                    f"ai-{decision.ai_decision_id}-{state.event_sequence}",
                )
                append_result = await persistence.append_accepted_events_to_locked_state(
                    session=session,
                    game_id=_coerce_uuid(request.game_id),
                    state=state,
                    actor_player_id=_coerce_uuid(request.player_id),
                    event_templates=[
                        AcceptedEventTemplate(
                            event_type=event.type,
                            payload=event.payload.model_dump(mode="json", exclude_unset=True),
                        )
                        for event in execution.events
                    ],
                    expected_base_sequence=action.expected_event_sequence,
                    expected_base_state_hash=action.expected_state_hash,
                )
                accepted_event_id = append_result.events[0].id
                await session.execute(
                    ai_decisions.update()
                    .where(ai_decisions.c.id == decision.ai_decision_id)
                    .values(
                        status="accepted",
                        accepted_event_id=accepted_event_id,
                        rejected_action_id=None,
                        validation_result=_accepted_validation_result(
                            decision.validation_result,
                            "Legal action validation accepted through execute_action",
                        ),
                    )
                )
                await persist_memory_updates_for_final_decision(
                    session,
                    decision_id=decision.ai_decision_id,
                    ai_decision_status="accepted",
                    source_event_id=accepted_event_id,
                    evidence_metadata={
                        "kind": "action_decision",
                        "accepted_event_id": accepted_event_id,
                        "accepted_event_count": len(append_result.events),
                    },
                )
                await compact_memory_after_scheduled_decision_if_due(
                    session,
                    game_id=_coerce_uuid(request.game_id),
                    player_id=_coerce_uuid(request.player_id),
                )
                game_status = await _game_status_in_session(
                    session,
                    _coerce_uuid(request.game_id),
                )
                return AIOutputEnforcementResult(
                    ai_decision_id=decision.ai_decision_id,
                    status="accepted",
                    accepted_event_id=accepted_event_id,
                    rejected_action_id=None,
                    game_status=game_status,
                    accepted_events=append_result.events,
                )
    except ActionValidationError as exc:
        state = await _current_state(session_factory, request.game_id)
        return await _reject_ai_output(
            session_factory,
            request=request,
            decision=decision,
            state=state,
            action_type=action.type,
            payload=_rejection_payload(decision, parsed_output=parsed_output),
            validation_errors=[issue.model_dump(mode="json") for issue in exc.errors],
        )
    except StaleEventSequenceError:
        state = await _current_state(session_factory, request.game_id)
        return await _reject_ai_output(
            session_factory,
            request=request,
            decision=decision,
            state=state,
            action_type=action.type,
            payload=_rejection_payload(decision, parsed_output=parsed_output),
            validation_errors=[
                {
                    "code": "stale_action",
                    "message": "action expected state no longer matches current state",
                    "field": "expected_state_hash",
                }
            ],
        )


async def _enforce_deal_output_validation(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    request: AIOutputEnforcementRequest,
    decision: CodexExecAIDecisionResult,
    parsed_output: Mapping[str, Any],
    state: GameState,
) -> AIOutputEnforcementResult:
    errors = await _deal_output_errors(session_factory, request, parsed_output)
    if errors:
        return await _reject_ai_output(
            session_factory,
            request=request,
            decision=decision,
            state=state,
            action_type=f"AI_{request.decision_type.upper()}",
            payload=_rejection_payload(decision, parsed_output=parsed_output),
            validation_errors=errors,
        )
    return await _mark_decision_validated(session_factory, request, decision)


async def _enforce_negotiation_message_validation(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    request: AIOutputEnforcementRequest,
    decision: CodexExecAIDecisionResult,
    parsed_output: Mapping[str, Any],
    state: GameState,
) -> AIOutputEnforcementResult:
    errors = await _negotiation_membership_errors(session_factory, request, parsed_output)
    if errors:
        return await _reject_ai_output(
            session_factory,
            request=request,
            decision=decision,
            state=state,
            action_type="AI_NEGOTIATION_MESSAGE",
            payload=_rejection_payload(decision, parsed_output=parsed_output),
            validation_errors=errors,
        )
    return await _mark_decision_validated(session_factory, request, decision)


async def _enforce_open_negotiation_output_validation(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    request: AIOutputEnforcementRequest,
    decision: CodexExecAIDecisionResult,
    parsed_output: Mapping[str, Any],
    state: GameState,
) -> AIOutputEnforcementResult:
    errors = await _open_negotiation_output_errors(session_factory, request, parsed_output)
    if errors:
        return await _reject_ai_output(
            session_factory,
            request=request,
            decision=decision,
            state=state,
            action_type="AI_OPEN_NEGOTIATION",
            payload=_rejection_payload(decision, parsed_output=parsed_output),
            validation_errors=errors,
        )
    return await _mark_decision_validated(session_factory, request, decision)


async def _open_negotiation_output_errors(
    session_factory: async_sessionmaker[AsyncSession],
    request: AIOutputEnforcementRequest,
    parsed_output: Mapping[str, Any],
) -> list[dict[str, Any]]:
    negotiation_payload = parsed_output.get("negotiation")
    if not isinstance(negotiation_payload, Mapping):
        return [_issue("negotiation_required", "open_negotiation output requires negotiation", "negotiation")]

    participant_ids = [
        _coerce_uuid(participant_id)
        for participant_id in negotiation_payload["participant_player_ids"]
    ]
    participant_id_strings = {str(participant_id) for participant_id in participant_ids}
    if str(_coerce_uuid(request.player_id)) not in participant_id_strings:
        return [
            _issue(
                "player_not_participant",
                "AI player must be a participant in an opened negotiation",
                "negotiation.participant_player_ids",
            )
        ]

    async with session_factory() as session:
        result = await session.execute(
            sa.select(players.c.id).where(
                players.c.game_id == _coerce_uuid(request.game_id),
                players.c.id.in_(participant_ids),
            )
        )
        found_ids = {str(player_id) for player_id in result.scalars().all()}

    missing_ids = participant_id_strings - found_ids
    if missing_ids:
        return [
            _issue(
                "participant_not_in_game",
                "open_negotiation participant_player_ids must all belong to the game",
                "negotiation.participant_player_ids",
            )
        ]

    return []


async def _deal_output_errors(
    session_factory: async_sessionmaker[AsyncSession],
    request: AIOutputEnforcementRequest,
    parsed_output: Mapping[str, Any],
) -> list[dict[str, Any]]:
    errors = await _negotiation_membership_errors(session_factory, request, parsed_output)
    if errors:
        return errors

    game_id = _coerce_uuid(request.game_id)
    negotiation_id = _coerce_uuid(parsed_output["negotiation_id"])
    async with session_factory() as session:
        async with session.begin():
            negotiation_row = await _load_negotiation_for_update(
                session=session,
                game_id=game_id,
                negotiation_id=negotiation_id,
            )
            if negotiation_row is None:
                return [_issue("negotiation_not_found", "negotiation does not belong to game", "negotiation_id")]
            context = _normalized_context(negotiation_row)
            if parsed_output["decision_type"] == "deal_proposal":
                return await _deal_proposal_errors(
                    session=session,
                    game_id=game_id,
                    negotiation_row=negotiation_row,
                    context=context,
                    parsed_output=parsed_output,
                )
            if parsed_output["decision_type"] == "counteroffer":
                return await _counteroffer_errors(
                    session=session,
                    game_id=game_id,
                    negotiation_row=negotiation_row,
                    context=context,
                    parsed_output=parsed_output,
                )
            if parsed_output["decision_type"] == "accept_reject":
                return await _accept_reject_errors(
                    session=session,
                    game_id=game_id,
                    negotiation_row=negotiation_row,
                    context=context,
                    parsed_output=parsed_output,
                )
    return []


async def _negotiation_membership_errors(
    session_factory: async_sessionmaker[AsyncSession],
    request: AIOutputEnforcementRequest,
    parsed_output: Mapping[str, Any],
) -> list[dict[str, Any]]:
    negotiation_id_value = parsed_output.get("negotiation_id")
    if negotiation_id_value is None:
        return [_issue("negotiation_id_required", "negotiation output requires negotiation_id", "negotiation_id")]
    if request.negotiation_id is not None and str(_coerce_uuid(request.negotiation_id)) != str(
        _coerce_uuid(negotiation_id_value)
    ):
        return [
            _issue(
                "negotiation_id_mismatch",
                "AI output negotiation_id must match the requested negotiation",
                "negotiation_id",
            )
        ]

    async with session_factory() as session:
        async with session.begin():
            negotiation_row = await _load_negotiation_for_update(
                session=session,
                game_id=_coerce_uuid(request.game_id),
                negotiation_id=_coerce_uuid(negotiation_id_value),
            )
            if negotiation_row is None:
                return [_issue("negotiation_not_found", "negotiation does not belong to game", "negotiation_id")]
            if negotiation_row["status"] in TERMINAL_NEGOTIATION_STATUSES:
                return [
                    _issue(
                        f"negotiation_{negotiation_row['status']}",
                        "terminal negotiations cannot receive AI lifecycle decisions",
                        "status",
                    )
                ]
            context = _normalized_context(negotiation_row)
            if str(_coerce_uuid(request.player_id)) not in context["participant_player_ids"]:
                return [
                    _issue(
                        "player_not_participant",
                        "AI player must be a negotiation participant",
                        "player_id",
                    )
                ]
    return []


async def _deal_proposal_errors(
    *,
    session: AsyncSession,
    game_id: UUID,
    negotiation_row: Mapping[str, Any],
    context: Mapping[str, Any],
    parsed_output: Mapping[str, Any],
) -> list[dict[str, Any]]:
    if negotiation_row["status"] == NEGOTIATION_STATUS_ACCEPTED:
        return [_issue("negotiation_already_accepted", "accepted negotiations cannot receive proposals", "status")]
    if context.get("current_deal_id") is not None:
        return [
            _issue(
                "counteroffer_required",
                "a changed proposal must use counteroffer once a current deal exists",
                "decision_type",
            )
        ]

    deal_payload = parsed_output["deal"]
    recipient_errors = _recipient_errors(deal_payload, context)
    if recipient_errors:
        return recipient_errors
    return _structured_deal_errors(
        deal_payload["terms"],
        participant_player_ids=context["participant_player_ids"],
    )


async def _counteroffer_errors(
    *,
    session: AsyncSession,
    game_id: UUID,
    negotiation_row: Mapping[str, Any],
    context: Mapping[str, Any],
    parsed_output: Mapping[str, Any],
) -> list[dict[str, Any]]:
    del negotiation_row
    counteroffer = parsed_output["counteroffer"]
    responds_to_deal_id = _coerce_uuid(counteroffer["responds_to_deal_id"])
    current_deal_id = context.get("current_deal_id")
    if current_deal_id != str(responds_to_deal_id):
        return [
            _issue(
                "parent_deal_not_current",
                "counteroffer must respond to the exact current deal",
                "counteroffer.responds_to_deal_id",
            )
        ]
    parent_deal = await _load_deal_for_update(
        session=session,
        game_id=game_id,
        deal_id=responds_to_deal_id,
    )
    if parent_deal is None or parent_deal["negotiation_id"] != _coerce_uuid(parsed_output["negotiation_id"]):
        return [_issue("parent_deal_not_current", "parent deal must belong to this negotiation", "deal_id")]
    if parent_deal["status"] != DEAL_STATUS_PROPOSED:
        return [_issue(f"deal_{parent_deal['status']}", "only proposed current deals can be countered", "status")]
    return _structured_deal_errors(
        counteroffer["terms"],
        participant_player_ids=context["participant_player_ids"],
    )


async def _accept_reject_errors(
    *,
    session: AsyncSession,
    game_id: UUID,
    negotiation_row: Mapping[str, Any],
    context: Mapping[str, Any],
    parsed_output: Mapping[str, Any],
) -> list[dict[str, Any]]:
    if negotiation_row["status"] == NEGOTIATION_STATUS_ACCEPTED:
        return [_issue("negotiation_already_accepted", "accepted negotiations can only execute or expire", "status")]
    accept_reject = parsed_output["accept_reject"]
    deal_id = _coerce_uuid(accept_reject["deal_id"])
    deal_row = await _load_deal_for_update(session=session, game_id=game_id, deal_id=deal_id)
    if deal_row is None:
        return [_issue("deal_not_found", "deal does not belong to game", "accept_reject.deal_id")]
    if deal_row["negotiation_id"] != _coerce_uuid(parsed_output["negotiation_id"]):
        return [_issue("deal_negotiation_mismatch", "deal must belong to this negotiation", "deal_id")]
    deal_terms = deal_row["terms"] if isinstance(deal_row["terms"], Mapping) else {}
    if (
        context.get("current_deal_id") != str(deal_id)
        or context.get("current_terms_hash") != _terms_hash_for_response(deal_terms)
    ):
        return [
            _issue(
                "exact_term_acceptance_required",
                "accept/reject must target the current deal and exact current terms",
                "accept_reject.deal_id",
            )
        ]
    if deal_row["status"] != DEAL_STATUS_PROPOSED:
        return [
            _issue(
                f"deal_{deal_row['status']}",
                "only proposed current deals can be accepted or rejected",
                "status",
            )
        ]
    return []


def _recipient_errors(
    deal_payload: Mapping[str, Any],
    context: Mapping[str, Any],
) -> list[dict[str, Any]]:
    participant_ids = set(context["participant_player_ids"])
    recipients = {str(_coerce_uuid(player_id)) for player_id in deal_payload["recipient_player_ids"]}
    if not recipients:
        return [_issue("recipient_required", "deal proposal must include at least one recipient", "deal.recipient_player_ids")]
    if not recipients <= participant_ids:
        return [
            _issue(
                "recipient_not_participant",
                "deal recipients must be negotiation participants",
                "deal.recipient_player_ids",
            )
        ]
    return []


def _structured_deal_errors(
    raw_terms: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    *,
    participant_player_ids: Sequence[str],
) -> list[dict[str, Any]]:
    prepared = _prepare_deal_terms(raw_terms, participant_player_ids=participant_player_ids)
    if not prepared.structured_deal:
        return [_issue("invalid_structured_deal", "AI deal terms must be a structured deal", "terms.kind")]
    return [dict(error) for error in prepared.validation_errors]


async def _reject_ai_output(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    request: AIOutputEnforcementRequest,
    decision: CodexExecAIDecisionResult,
    state: GameState,
    action_type: str,
    payload: Mapping[str, Any],
    validation_errors: Sequence[Mapping[str, Any]],
) -> AIOutputEnforcementResult:
    reason_code = _reason_code(validation_errors)
    audit = RejectedActionAudit(session_factory)
    rejected = await audit.persist_rejected_action(
        game_id=request.game_id,
        actor_player_id=request.player_id,
        action_type=action_type,
        payload=payload,
        reason_code=reason_code,
        validation_errors=validation_errors,
        legal_action_context=_legal_action_context(state, str(_coerce_uuid(request.player_id))),
        phase=state.turn.phase.value,
        state_hash=state.state_hash(),
    )

    consumed = False
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                ai_decisions.update()
                .where(ai_decisions.c.id == decision.ai_decision_id)
                .values(
                    status="rejected",
                    accepted_event_id=None,
                    rejected_action_id=rejected.id,
                    validation_result=_rejected_validation_result(
                        decision.validation_result,
                        reason_code=reason_code,
                        validation_errors=validation_errors,
                    ),
                )
            )
            await link_memory_entries_to_decision_evidence(
                session,
                decision_id=decision.ai_decision_id,
                ai_decision_status="rejected",
                rejected_action_id=rejected.id,
                evidence_metadata={
                    "kind": "ai_output_rejection",
                    "reason_code": reason_code,
                    "validation_errors": [dict(error) for error in validation_errors],
                },
            )
            if request.mandatory:
                await session.execute(
                    games.update()
                    .where(games.c.id == _coerce_uuid(request.game_id))
                    .values(status=AI_BLOCKED_STATUS, updated_at=sa.func.now())
                )
            elif request.decision_type in NEGOTIATION_DECISION_TYPES and request.negotiation_id is not None:
                consumed = await _consume_non_mandatory_response_opportunity(
                    session=session,
                    request=request,
                    ai_decision_id=decision.ai_decision_id,
                    reason_code=reason_code,
                )
            game_status = await _game_status_in_session(session, _coerce_uuid(request.game_id))

    return AIOutputEnforcementResult(
        ai_decision_id=decision.ai_decision_id,
        status="rejected",
        accepted_event_id=None,
        rejected_action_id=rejected.id,
        game_status=game_status,
        consumed_response_opportunity=consumed,
    )


async def _reject_ai_output_in_session(
    *,
    session: AsyncSession,
    request: AIOutputEnforcementRequest,
    decision: CodexExecAIDecisionResult,
    state: GameState,
    action_type: str,
    payload: Mapping[str, Any],
    validation_errors: Sequence[Mapping[str, Any]],
) -> AIOutputEnforcementResult:
    reason_code = _reason_code(validation_errors)
    rejected_result = await session.execute(
        rejected_actions.insert()
        .values(
            game_id=_coerce_uuid(request.game_id),
            actor_player_id=_coerce_uuid(request.player_id),
            action_type=action_type,
            payload=dict(payload),
            reason_code=reason_code,
            validation_errors=[dict(error) for error in validation_errors],
            legal_action_context=_legal_action_context(
                state,
                str(_coerce_uuid(request.player_id)),
            ),
            phase=state.turn.phase.value,
            state_hash=state.state_hash(),
        )
        .returning(rejected_actions.c.id)
    )
    rejected_action_id = rejected_result.scalar_one()
    await session.execute(
        ai_decisions.update()
        .where(ai_decisions.c.id == decision.ai_decision_id)
        .values(
            status="rejected",
            accepted_event_id=None,
            rejected_action_id=rejected_action_id,
            validation_result=_rejected_validation_result(
                decision.validation_result,
                reason_code=reason_code,
                validation_errors=validation_errors,
            ),
        )
    )
    await link_memory_entries_to_decision_evidence(
        session,
        decision_id=decision.ai_decision_id,
        ai_decision_status="rejected",
        rejected_action_id=rejected_action_id,
        evidence_metadata={
            "kind": "ai_output_rejection",
            "reason_code": reason_code,
            "validation_errors": [dict(error) for error in validation_errors],
        },
    )
    game_status = await _game_status_in_session(session, _coerce_uuid(request.game_id))
    return AIOutputEnforcementResult(
        ai_decision_id=decision.ai_decision_id,
        status="rejected",
        accepted_event_id=None,
        rejected_action_id=rejected_action_id,
        game_status=game_status,
    )


async def _consume_non_mandatory_response_opportunity(
    *,
    session: AsyncSession,
    request: AIOutputEnforcementRequest,
    ai_decision_id: UUID,
    reason_code: str,
) -> bool:
    if request.negotiation_id is None:
        return False
    negotiation_row = await _load_negotiation_for_update(
        session=session,
        game_id=_coerce_uuid(request.game_id),
        negotiation_id=_coerce_uuid(request.negotiation_id),
    )
    if negotiation_row is None:
        return False

    context = dict(negotiation_row.get("context") or {})
    round_number = int(negotiation_row["round_number"])
    key = f"round:{round_number}:player:{_coerce_uuid(request.player_id)}"
    consumed = context.setdefault(NON_MANDATORY_RESPONSE_KEY, {})
    if not isinstance(consumed, dict):
        consumed = {}
        context[NON_MANDATORY_RESPONSE_KEY] = consumed
    consumed[key] = {
        "player_id": str(_coerce_uuid(request.player_id)),
        "round_number": round_number,
        "ai_decision_id": str(ai_decision_id),
        "reason_code": reason_code,
        _AUDIT_NO_REPLACEMENT_KEY: True,
        _AUDIT_REPLACEMENT_KEY: None,
    }

    attempt_counts = context.setdefault("ai_decision_attempts_by_message_id", {})
    if not isinstance(attempt_counts, dict):
        attempt_counts = {}
        context["ai_decision_attempts_by_message_id"] = attempt_counts
    attempt_counts[key] = int(attempt_counts.get(key, 0)) + 1

    await session.execute(
        negotiations.update()
        .where(negotiations.c.id == negotiation_row["id"])
        .values(context=context, updated_at=sa.func.now())
    )
    return True


async def _mark_decision_validated(
    session_factory: async_sessionmaker[AsyncSession],
    request: AIOutputEnforcementRequest,
    decision: CodexExecAIDecisionResult,
) -> AIOutputEnforcementResult:
    persistence = EventPersistence(session_factory)
    async with session_factory() as session:
        async with session.begin():
            game_id = _coerce_uuid(request.game_id)
            state = await persistence.replay_current_state_for_update(session, game_id)
            game_status = await _game_status_in_session(session, game_id)
            if game_status == AI_BLOCKED_STATUS:
                return await _reject_ai_output_in_session(
                    session=session,
                    request=request,
                    decision=decision,
                    state=state,
                    action_type=_audit_action_type(request.decision_type, decision.parsed_output),
                    payload=_rejection_payload(decision, parsed_output=decision.parsed_output),
                    validation_errors=[_game_ai_blocked_issue()],
                )
            await session.execute(
                ai_decisions.update()
                .where(ai_decisions.c.id == decision.ai_decision_id)
                .values(
                    status="validated",
                    validation_result=_accepted_validation_result(
                        decision.validation_result,
                        "Deal validation and phase/timing validation accepted",
                    ),
                )
            )
            if request.decision_type not in NEGOTIATION_DECISION_TYPES | {"open_negotiation"}:
                await persist_memory_updates_for_final_decision(
                    session,
                    decision_id=decision.ai_decision_id,
                    ai_decision_status="validated",
                    evidence_metadata={
                        "kind": request.decision_type,
                        "validation_status": "validated",
                    },
                )
                await compact_memory_after_scheduled_decision_if_due(
                    session,
                    game_id=game_id,
                    player_id=_coerce_uuid(request.player_id),
                )
            game_status = await _game_status_in_session(session, game_id)
    return AIOutputEnforcementResult(
        ai_decision_id=decision.ai_decision_id,
        status="validated",
        accepted_event_id=None,
        rejected_action_id=None,
        game_status=game_status,
    )


async def _current_state(
    session_factory: async_sessionmaker[AsyncSession],
    game_id: UUID | str,
) -> GameState:
    persistence = EventPersistence(session_factory)
    async with session_factory() as session:
        async with session.begin():
            return await persistence.replay_current_state_for_update(session, _coerce_uuid(game_id))


async def _load_negotiation_for_update(
    *,
    session: AsyncSession,
    game_id: UUID,
    negotiation_id: UUID,
) -> Mapping[str, Any] | None:
    result = await session.execute(
        sa.select(negotiations)
        .where(negotiations.c.game_id == game_id, negotiations.c.id == negotiation_id)
        .with_for_update()
    )
    row = result.mappings().first()
    return None if row is None else dict(row)


async def _load_deal_for_update(
    *,
    session: AsyncSession,
    game_id: UUID,
    deal_id: UUID,
) -> Mapping[str, Any] | None:
    result = await session.execute(
        sa.select(deals)
        .where(deals.c.game_id == game_id, deals.c.id == deal_id)
        .with_for_update()
    )
    row = result.mappings().first()
    return None if row is None else dict(row)


async def _game_status_in_session(session: AsyncSession, game_id: UUID) -> str | None:
    result = await session.execute(sa.select(games.c.status).where(games.c.id == game_id))
    value = result.scalar_one_or_none()
    return None if value is None else str(value)


def _normalize_request(request: AIOutputEnforcementRequest) -> AIOutputEnforcementRequest:
    return AIOutputEnforcementRequest(
        game_id=_coerce_uuid(request.game_id),
        player_id=_coerce_uuid(request.player_id),
        decision_type=request.decision_type,
        ai_profile_id=None if request.ai_profile_id is None else _coerce_uuid(request.ai_profile_id),
        negotiation_id=None if request.negotiation_id is None else _coerce_uuid(request.negotiation_id),
        mandatory=request.mandatory,
        request_context=request.request_context,
        timeout_seconds=request.timeout_seconds,
    )


def _schema_validated_output(decision: CodexExecAIDecisionResult) -> Mapping[str, Any]:
    if decision.parsed_output is None:
        raise AIDecisionValidationError(())
    return validate_ai_decision_output(decision.parsed_output).root.model_dump(mode="json")


def _identity_errors(
    request: AIOutputEnforcementRequest,
    parsed_output: Mapping[str, Any],
) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    if str(_coerce_uuid(parsed_output["game_id"])) != str(_coerce_uuid(request.game_id)):
        errors.append(
            _issue(
                "ai_output_identity_mismatch",
                "AI output game_id must match the requested game",
                "game_id",
            )
        )
    if str(_coerce_uuid(parsed_output["player_id"])) != str(_coerce_uuid(request.player_id)):
        errors.append(
            _issue(
                "ai_output_identity_mismatch",
                "AI output player_id must match the requested AI player",
                "player_id",
            )
        )
    if parsed_output["decision_type"] != request.decision_type:
        errors.append(
            _issue(
                "ai_output_decision_type_mismatch",
                "AI output decision_type must match the requested decision type",
                "decision_type",
            )
        )
    return errors


def _game_action_from_ai_output(parsed_output: Mapping[str, Any]) -> GameAction:
    action_payload = parsed_output["action"]
    return GameAction(
        actor_id=str(parsed_output["player_id"]),
        type=str(action_payload["type"]),
        payload=action_payload["payload"],
        expected_state_hash=str(parsed_output["expected_state_hash"]),
        expected_event_sequence=int(parsed_output["expected_event_sequence"]),
    )


def _validation_errors_from_decision(
    decision: CodexExecAIDecisionResult,
) -> list[dict[str, Any]]:
    validation_result = decision.validation_result
    raw_errors = validation_result.get("validation_errors")
    if isinstance(raw_errors, Sequence) and not isinstance(raw_errors, (str, bytes, bytearray)):
        return [dict(error) for error in raw_errors if isinstance(error, Mapping)]
    reason_code = str(validation_result.get("reason_code") or decision.status or "invalid_ai_output")
    return [
        {
            "code": reason_code,
            "message": f"AI decision failed before enforcement: {reason_code}",
            "field": None,
        }
    ]


def _reason_code(validation_errors: Sequence[Mapping[str, Any]]) -> str:
    if validation_errors:
        code = validation_errors[0].get("code")
        if isinstance(code, str) and code:
            return code
    return "invalid_ai_output"


def _game_ai_blocked_issue() -> dict[str, Any]:
    return _issue(
        GAME_AI_BLOCKED_REASON_CODE,
        "AI_BLOCKED games reject AI decisions after Codex returns",
        "game_status",
    )


def _audit_action_type(decision_type: str, parsed_output: Mapping[str, Any] | None) -> str:
    if decision_type == "action_decision" and isinstance(parsed_output, Mapping):
        action = parsed_output.get("action")
        if isinstance(action, Mapping):
            action_type = action.get("type")
            if isinstance(action_type, str) and action_type:
                return action_type
    return f"AI_{decision_type.upper()}"


def _rejection_payload(
    decision: CodexExecAIDecisionResult,
    *,
    parsed_output: Mapping[str, Any] | None,
) -> dict[str, Any]:
    return {
        "ai_decision_id": str(decision.ai_decision_id),
        "ai_output": _json_safe(parsed_output),
        "raw_output": decision.raw_output,
        "orchestrator_status": decision.status,
        _AUDIT_NO_REPLACEMENT_KEY: True,
        _AUDIT_REPLACEMENT_KEY: None,
    }


def _legal_action_context(state: GameState, actor_id: str | None) -> dict[str, Any]:
    legal_actions = [] if actor_id is None else list_legal_actions(state, actor_id)
    return {
        "actor_id": actor_id,
        "current_player_id": state.turn.current_player_id,
        "phase": state.turn.phase.value,
        "phase_timing_validation": {
            "active_phase": state.turn.phase.value,
            "event_sequence": state.event_sequence,
        },
        "state_hash": state.state_hash(),
        "event_sequence": state.event_sequence,
        "legal_actions": [action.model_dump(mode="json") for action in legal_actions],
    }


def _accepted_validation_result(
    existing: Mapping[str, Any],
    message: str,
) -> dict[str, Any]:
    result = dict(existing)
    result.update(
        {
            "status": "accepted",
            "enforcement": {
                "message": message,
                "schema_validation": True,
                "legal_action_validation": True,
                "deal_validation": True,
                "phase_timing_validation": True,
            },
            _AUDIT_NO_REPLACEMENT_KEY: True,
            _AUDIT_REPLACEMENT_KEY: None,
        }
    )
    return result


def _rejected_validation_result(
    existing: Mapping[str, Any],
    *,
    reason_code: str,
    validation_errors: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    result = dict(existing)
    result.update(
        {
            "status": "rejected",
            "reason_code": reason_code,
            "validation_errors": [dict(error) for error in validation_errors],
            "rejected_ai_output_records": True,
            "schema_validation": reason_code != "malformed_ai_output",
            "legal_action_validation": reason_code
            not in {
                "codex_exec_process_error",
                "codex_exec_timeout",
                "illegal_action",
                "malformed_action",
                "malformed_ai_output",
                "mistimed_action",
                "stale_action",
                "unknown_action",
            },
            "deal_validation": reason_code != "invalid_structured_deal",
            "phase_timing_validation": reason_code not in {"mistimed_action", "stale_action"},
            _AUDIT_NO_REPLACEMENT_KEY: True,
            _AUDIT_REPLACEMENT_KEY: None,
        }
    )
    return result


def _normalized_context(row: Mapping[str, Any]) -> dict[str, Any]:
    source = row.get("context")
    context = dict(source) if isinstance(source, Mapping) else {}
    participant_ids = context.get("participant_player_ids", [])
    if not isinstance(participant_ids, Sequence) or isinstance(participant_ids, (str, bytes)):
        participant_ids = []
    context["participant_player_ids"] = [str(_coerce_uuid(player_id)) for player_id in participant_ids]
    context.setdefault("acceptances", {})
    context.setdefault("invalidated_acceptances", {})
    context.setdefault("ai_decision_attempts_by_message_id", {})
    return context


def _issue(code: str, message: str, field: str | None) -> dict[str, Any]:
    return {"code": code, "message": message, "field": field}


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, sort_keys=True, default=str, ensure_ascii=True))


def _coerce_uuid(value: UUID | str) -> UUID:
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


__all__ = [
    "AI_BLOCKED_STATUS",
    "AIOutputEnforcementRequest",
    "AIOutputEnforcementResult",
    "enforce_ai_output",
]
