from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.contracts.default_handling import ContractDefault, default_handling
from app.contracts.trigger_system import trigger_system
from app.db.metadata import contracts, deals, negotiation_messages, obligations
from app.db.persistence import AcceptedEventRecord, AcceptedEventTemplate, EventPersistence
from app.rules.state import GameState


AUDIT_OBLIGATION_SETTLED = "OBLIGATION_SETTLED"
AUDIT_CONTRACT_SETTLEMENT_EVENT = "CONTRACT_SETTLEMENT_EVENT"


class SettlementEngineError(RuntimeError):
    def __init__(self, reason_code: str, message: str, *, field: str | None = None) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.message = message
        self.field = field


@dataclass(frozen=True)
class SettlementEngineResult:
    settled_obligation_ids: list[UUID]
    defaulted_obligation_ids: list[UUID]
    accepted_events: list[AcceptedEventRecord]
    defaults: list[ContractDefault]
    state: GameState


async def settlement_engine(
    *,
    session: AsyncSession,
    session_factory: async_sessionmaker,
    game_id: UUID,
    contract_id: UUID | None = None,
    obligation_id: UUID | None = None,
    trigger_context: Mapping[str, Any] | None = None,
) -> SettlementEngineResult:
    persistence = EventPersistence(session_factory)
    state = await persistence.replay_current_state_for_update(session, game_id)
    contract_rows = await _load_contract_rows(
        session=session,
        game_id=game_id,
        contract_id=contract_id,
    )
    if contract_id is not None and not contract_rows:
        raise SettlementEngineError("contract_not_found", "contract does not belong to game", field="contract_id")

    settled_ids: list[UUID] = []
    defaulted_ids: list[UUID] = []
    accepted_events: list[AcceptedEventRecord] = []
    defaults: list[ContractDefault] = []

    for contract_row in contract_rows:
        obligation_rows = await _load_pending_obligation_rows(
            session=session,
            game_id=game_id,
            contract_id=contract_row["id"],
            obligation_id=obligation_id,
        )
        if obligation_id is not None and contract_id is not None and not obligation_rows:
            continue
        for obligation_row in obligation_rows:
            if obligation_id is None and not _obligation_due(obligation_row, state, trigger_context):
                continue
            outcome = await _settle_one_obligation(
                session=session,
                persistence=persistence,
                game_id=game_id,
                contract_row=contract_row,
                obligation_row=obligation_row,
                state=state,
                trigger_context=trigger_context or {},
            )
            state = outcome.state
            if outcome.default is not None:
                defaults.append(outcome.default)
                defaulted_ids.append(obligation_row["id"])
            else:
                settled_ids.append(obligation_row["id"])
                accepted_events.extend(outcome.accepted_events)
        await _close_contract_if_complete(session=session, contract_id=contract_row["id"])

    if obligation_id is not None and contract_id is None and not settled_ids and not defaulted_ids:
        # Contract-specific settlement endpoints use contract_id, but keep this guard for service callers.
        raise SettlementEngineError("obligation_not_found", "obligation does not belong to game", field="obligation_id")

    return SettlementEngineResult(
        settled_obligation_ids=settled_ids,
        defaulted_obligation_ids=defaulted_ids,
        accepted_events=accepted_events,
        defaults=defaults,
        state=state,
    )


async def settle_contract(
    *,
    session: AsyncSession,
    session_factory: async_sessionmaker,
    game_id: UUID,
    contract_id: UUID,
    obligation_id: UUID | None = None,
    trigger_context: Mapping[str, Any] | None = None,
) -> SettlementEngineResult:
    return await settlement_engine(
        session=session,
        session_factory=session_factory,
        game_id=game_id,
        contract_id=contract_id,
        obligation_id=obligation_id,
        trigger_context=trigger_context,
    )


async def enforce_contracts(
    *,
    session: AsyncSession,
    session_factory: async_sessionmaker,
    game_id: UUID,
    trigger_context: Mapping[str, Any] | None = None,
) -> SettlementEngineResult:
    return await settlement_engine(
        session=session,
        session_factory=session_factory,
        game_id=game_id,
        trigger_context=trigger_context,
    )


@dataclass(frozen=True)
class _SingleSettlementOutcome:
    accepted_events: list[AcceptedEventRecord]
    default: ContractDefault | None
    state: GameState


async def _settle_one_obligation(
    *,
    session: AsyncSession,
    persistence: EventPersistence,
    game_id: UUID,
    contract_row: Mapping[str, Any],
    obligation_row: Mapping[str, Any],
    state: GameState,
    trigger_context: Mapping[str, Any],
) -> _SingleSettlementOutcome:
    reason = _default_reason_for_obligation(obligation_row, state, trigger_context)
    if reason is not None:
        default = await default_handling(
            session=session,
            contract_row=contract_row,
            obligation_row=obligation_row,
            reason_code=reason[0],
            detail=reason[1],
        )
        return _SingleSettlementOutcome(accepted_events=[], default=default, state=state)

    templates = _event_templates_for_obligation(obligation_row, trigger_context)
    records: list[AcceptedEventRecord] = []
    next_state = state
    if templates:
        result = await persistence.append_accepted_events_to_locked_state(
            session=session,
            game_id=game_id,
            state=state,
            actor_player_id=obligation_row["owed_by_player_id"],
            event_templates=templates,
            expected_base_sequence=state.event_sequence,
            expected_base_state_hash=state.state_hash(),
        )
        records = list(result.events)
        next_state = result.state

    settled_event_id = records[-1].id if records else None
    schedule = obligation_row["schedule"] if isinstance(obligation_row["schedule"], Mapping) else {}
    terms = obligation_row["terms"] if isinstance(obligation_row["terms"], Mapping) else {}
    await session.execute(
        obligations.update()
        .where(obligations.c.id == obligation_row["id"])
        .values(
            status="settled",
            settled_event_id=settled_event_id,
            schedule={**dict(schedule), "settled_at": "settled"},
            terms={**dict(terms), "settled_at": "settled"},
            updated_at=sa.func.now(),
        )
    )
    await _insert_settlement_audits(
        session=session,
        contract_row=contract_row,
        obligation_row=obligation_row,
        accepted_events=records,
        settled_event_id=settled_event_id,
    )
    return _SingleSettlementOutcome(accepted_events=records, default=None, state=next_state)


def _default_reason_for_obligation(
    obligation_row: Mapping[str, Any],
    state: GameState,
    trigger_context: Mapping[str, Any],
) -> tuple[str, str] | None:
    terms = _terms(obligation_row)
    action = str(terms.get("settlement_action", "record_only"))
    if action == "cash_transfer" or obligation_row["obligation_type"] in {
        "cash_payment",
        "conditional_obligation",
        "default_penalty",
        "guarantee",
        "installment_loan",
        "insurance_payout",
        "interest_bearing_debt",
        "rent_share",
    }:
        amount = _cash_amount(terms, trigger_context)
        owed_by = obligation_row["owed_by_player_id"]
        owed_to = obligation_row["owed_to_player_id"]
        if owed_by is None or owed_to is None:
            return ("invalid_parties", "cash settlement requires owing and receiving players")
        if amount is None or amount <= 0:
            return ("invalid_amount", "cash settlement requires a positive amount")
        debtor = _player_by_id(state, str(owed_by))
        if debtor is None:
            return ("invalid_parties", "owing player is not in replayed game state")
        if _player_by_id(state, str(owed_to)) is None:
            return ("invalid_parties", "receiving player is not in replayed game state")
        if debtor.cash < amount:
            return ("insufficient_cash", "owing player cannot pay the scheduled obligation")
        return None

    if action == "property_transfer" or obligation_row["obligation_type"] == "property_transfer":
        property_id = terms.get("property_id")
        owner = _property_owner(state, property_id)
        expected_owner = terms.get("from_player_id") or (
            None if obligation_row["owed_by_player_id"] is None else str(obligation_row["owed_by_player_id"])
        )
        next_owner = terms.get("to_player_id") or (
            None if obligation_row["owed_to_player_id"] is None else str(obligation_row["owed_to_player_id"])
        )
        if not isinstance(property_id, str) or not property_id:
            return ("invalid_property", "property transfer requires a property_id")
        if next_owner is None or _player_by_id(state, str(next_owner)) is None:
            return ("invalid_parties", "property settlement requires a receiving player")
        if owner != expected_owner:
            return ("property_owner_mismatch", "property is not owned by the obligated transferor")
    return None


def _event_templates_for_obligation(
    obligation_row: Mapping[str, Any],
    trigger_context: Mapping[str, Any],
) -> list[AcceptedEventTemplate]:
    terms = _terms(obligation_row)
    action = str(terms.get("settlement_action", "record_only"))
    if action == "cash_transfer" or obligation_row["obligation_type"] in {
        "cash_payment",
        "conditional_obligation",
        "default_penalty",
        "guarantee",
        "installment_loan",
        "insurance_payout",
        "interest_bearing_debt",
        "rent_share",
    }:
        amount = _cash_amount(terms, trigger_context)
        if amount is None:
            return []
        return [
            AcceptedEventTemplate(
                event_type="PLAYER_CASH_DELTA",
                payload={"player_id": str(obligation_row["owed_by_player_id"]), "amount": -amount},
            ),
            AcceptedEventTemplate(
                event_type="PLAYER_CASH_DELTA",
                payload={"player_id": str(obligation_row["owed_to_player_id"]), "amount": amount},
            ),
        ]
    if action == "property_transfer" or obligation_row["obligation_type"] == "property_transfer":
        return [
            AcceptedEventTemplate(
                event_type="PROPERTY_OWNER_SET",
                payload={
                    "property_id": str(terms["property_id"]),
                    "owner_id": str(terms.get("to_player_id") or obligation_row["owed_to_player_id"]),
                },
            )
        ]
    return []


async def _load_contract_rows(
    *,
    session: AsyncSession,
    game_id: UUID,
    contract_id: UUID | None,
) -> list[dict[str, Any]]:
    statement = sa.select(contracts).where(contracts.c.game_id == game_id)
    if contract_id is None:
        statement = statement.where(contracts.c.status == "active")
    else:
        statement = statement.where(contracts.c.id == contract_id)
    result = await session.execute(statement.order_by(contracts.c.created_at, contracts.c.id).with_for_update())
    return [dict(row) for row in result.mappings().all()]


async def _load_pending_obligation_rows(
    *,
    session: AsyncSession,
    game_id: UUID,
    contract_id: UUID,
    obligation_id: UUID | None,
) -> list[dict[str, Any]]:
    statement = sa.select(obligations).where(
        obligations.c.game_id == game_id,
        obligations.c.contract_id == contract_id,
        obligations.c.status == "pending",
    )
    if obligation_id is not None:
        statement = statement.where(obligations.c.id == obligation_id)
    result = await session.execute(statement.order_by(obligations.c.created_at, obligations.c.id).with_for_update())
    return [dict(row) for row in result.mappings().all()]


def _obligation_due(
    obligation_row: Mapping[str, Any],
    state: GameState,
    trigger_context: Mapping[str, Any] | None,
) -> bool:
    schedule = obligation_row["schedule"] if isinstance(obligation_row["schedule"], Mapping) else {}
    return trigger_system(schedule, state=state, trigger_context=trigger_context).matched


async def _close_contract_if_complete(*, session: AsyncSession, contract_id: UUID) -> None:
    result = await session.execute(
        sa.select(sa.func.count())
        .select_from(obligations)
        .where(obligations.c.contract_id == contract_id, obligations.c.status == "pending")
    )
    pending_count = int(result.scalar_one())
    if pending_count == 0:
        await session.execute(
            contracts.update()
            .where(contracts.c.id == contract_id, contracts.c.status == "active")
            .values(status="closed", closed_at=sa.func.now(), updated_at=sa.func.now())
        )


async def _insert_settlement_audits(
    *,
    session: AsyncSession,
    contract_row: Mapping[str, Any],
    obligation_row: Mapping[str, Any],
    accepted_events: list[AcceptedEventRecord],
    settled_event_id: UUID | None,
) -> None:
    negotiation_id = await _negotiation_id_for_contract(session=session, contract_row=contract_row)
    if negotiation_id is None:
        return
    payload = {
        "contract_id": str(contract_row["id"]),
        "obligation_id": str(obligation_row["id"]),
        "source_deal_id": None if contract_row["deal_id"] is None else str(contract_row["deal_id"]),
        "accepted_event_ids": [str(record.id) for record in accepted_events],
        "settled_event_id": None if settled_event_id is None else str(settled_event_id),
    }
    for message_type in (AUDIT_OBLIGATION_SETTLED, AUDIT_CONTRACT_SETTLEMENT_EVENT):
        await session.execute(
            negotiation_messages.insert().values(
                game_id=contract_row["game_id"],
                negotiation_id=negotiation_id,
                sender_player_id=None,
                recipient_player_id=None,
                message_type=message_type,
                body=None,
                payload=payload,
            )
        )


async def _negotiation_id_for_contract(
    *,
    session: AsyncSession,
    contract_row: Mapping[str, Any],
) -> UUID | None:
    deal_id = contract_row["deal_id"]
    if deal_id is None:
        return None
    result = await session.execute(sa.select(deals.c.negotiation_id).where(deals.c.id == deal_id))
    return result.scalar_one_or_none()


def _cash_amount(terms: Mapping[str, Any], trigger_context: Mapping[str, Any]) -> int | None:
    raw_amount = terms.get("amount")
    if raw_amount is not None:
        return _positive_money(raw_amount)
    if terms.get("settlement_action") in {"rent_share", "rent_share_cash_payment"}:
        rent_amount = _positive_money(trigger_context.get("amount"))
        share_percent = Decimal(str(terms.get("share_percent", 0)))
        if rent_amount is None or share_percent <= 0:
            return None
        amount = (Decimal(rent_amount) * share_percent / Decimal("100")).quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP,
        )
        return int(amount)
    return None


def _positive_money(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str):
        try:
            parsed = int(value)
        except ValueError:
            return None
        return parsed if parsed > 0 else None
    return None


def _player_by_id(state: GameState, player_id: str) -> Any | None:
    for player in state.players:
        if player.id == player_id:
            return player
    return None


def _property_owner(state: GameState, property_id: object) -> str | None:
    if not isinstance(property_id, str):
        return None
    for ownership in state.property_ownership:
        if ownership.property_id == property_id:
            return ownership.owner_id
    return None


def _terms(obligation_row: Mapping[str, Any]) -> Mapping[str, Any]:
    terms = obligation_row["terms"]
    return terms if isinstance(terms, Mapping) else {}


__all__ = [
    "AUDIT_CONTRACT_SETTLEMENT_EVENT",
    "AUDIT_OBLIGATION_SETTLED",
    "SettlementEngineError",
    "SettlementEngineResult",
    "enforce_contracts",
    "settle_contract",
    "settlement_engine",
]
