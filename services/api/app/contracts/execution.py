from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.metadata import contracts, deals, negotiation_messages, obligations
from app.db.persistence import EventPersistence
from app.rules.financial_instruments import create_instrument, settle_instrument


AUDIT_CONTRACT_CREATED = "CONTRACT_CREATED"
AUDIT_OBLIGATION_SCHEDULED = "OBLIGATION_SCHEDULED"
STRUCTURED_DEAL_KIND = "structured_deal"
DEAL_STATUS_ACCEPTED = "accepted"


class ContractExecutionError(RuntimeError):
    def __init__(self, reason_code: str, message: str, *, field: str | None = None) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.message = message
        self.field = field


@dataclass(frozen=True)
class ObligationDraft:
    owed_by_player_id: UUID | None
    owed_to_player_id: UUID | None
    obligation_type: str
    schedule: dict[str, Any]
    terms: dict[str, Any]
    due_at: Any | None = None


@dataclass(frozen=True)
class ContractCreationResult:
    contract: dict[str, Any]
    obligations: list[dict[str, Any]]
    created: bool


async def create_contract_from_accepted_deal(
    *,
    session: AsyncSession,
    session_factory: async_sessionmaker,
    game_id: UUID,
    deal_id: UUID,
) -> ContractCreationResult:
    await EventPersistence(session_factory).replay_current_state_for_update(session, game_id)
    deal_row = await _load_deal_for_update(session=session, game_id=game_id, deal_id=deal_id)
    if deal_row is None:
        raise ContractExecutionError("deal_not_found", "deal does not belong to game", field="deal_id")
    _validate_contract_eligible_deal(deal_row)

    existing_contract = await _load_contract_for_deal(session=session, game_id=game_id, deal_id=deal_id)
    if existing_contract is not None:
        return ContractCreationResult(
            contract=existing_contract,
            obligations=await _load_obligations_for_contract(session, existing_contract["id"]),
            created=False,
        )

    contract_terms = _contract_terms_for_deal(deal_row)
    contract_result = await session.execute(
        contracts.insert()
        .values(
            game_id=game_id,
            deal_id=deal_id,
            effective_event_id=None,
            status="active",
            terms=contract_terms,
            executed_at=sa.func.now(),
        )
        .returning(contracts)
    )
    contract_row = dict(contract_result.mappings().one())

    obligation_rows: list[dict[str, Any]] = []
    for draft in generate_obligation_schedule(
        game_id=game_id,
        contract_id=contract_row["id"],
        deal_row=deal_row,
    ):
        result = await session.execute(
            obligations.insert()
            .values(
                game_id=game_id,
                contract_id=contract_row["id"],
                owed_by_player_id=draft.owed_by_player_id,
                owed_to_player_id=draft.owed_to_player_id,
                settled_event_id=None,
                status="pending",
                obligation_type=draft.obligation_type,
                schedule=draft.schedule,
                terms=draft.terms,
                due_at=draft.due_at,
            )
            .returning(obligations)
        )
        obligation_row = dict(result.mappings().one())
        obligation_rows.append(obligation_row)
        await _insert_contract_audit(
            session=session,
            deal_row=deal_row,
            message_type=AUDIT_OBLIGATION_SCHEDULED,
            payload={
                "contract_id": str(contract_row["id"]),
                "obligation_id": str(obligation_row["id"]),
                "source_deal_id": str(deal_id),
                "obligation_type": obligation_row["obligation_type"],
                "schedule": obligation_row["schedule"],
            },
        )

    await _insert_contract_audit(
        session=session,
        deal_row=deal_row,
        message_type=AUDIT_CONTRACT_CREATED,
        payload={
            "contract_id": str(contract_row["id"]),
            "source_deal_id": str(deal_id),
            "obligation_ids": [str(row["id"]) for row in obligation_rows],
            "eligible_for_contract": True,
        },
    )
    return ContractCreationResult(contract=contract_row, obligations=obligation_rows, created=True)


def generate_obligation_schedule(
    *,
    game_id: UUID,
    contract_id: UUID,
    deal_row: Mapping[str, Any],
) -> list[ObligationDraft]:
    del game_id, contract_id
    terms = _terms_mapping(deal_row)
    participants = [str(player_id) for player_id in terms.get("participants", [])]
    instrument_ids = _instrument_ids(terms.get("terms", []))
    drafts: list[ObligationDraft] = []

    for index, raw_term in enumerate(_term_mappings(terms.get("terms", []))):
        instrument = create_instrument(raw_term)
        settlement = settle_instrument(
            instrument,
            player_ids=participants,
            instrument_ids=instrument_ids,
        )
        if settlement.status != "planned":
            raise ContractExecutionError(
                "invalid_settlement_spec",
                settlement.failure_reason or "accepted deal produced an invalid settlement spec",
                field=f"terms.{index}",
            )
        drafts.extend(
            _drafts_from_settlement(
                settlement.model_dump(mode="json"),
                source_term=instrument.payload,
                source_term_index=index,
            )
        )
    return drafts


def _drafts_from_settlement(
    settlement: Mapping[str, Any],
    *,
    source_term: Mapping[str, Any],
    source_term_index: int,
) -> list[ObligationDraft]:
    settlement_type = str(settlement["settlement_type"])
    spec = settlement["spec"] if isinstance(settlement.get("spec"), Mapping) else {}
    instrument_id = _instrument_id(source_term, source_term_index)
    base_terms = {
        "instrument_id": instrument_id,
        "instrument_kind": settlement["kind"],
        "settlement_type": settlement_type,
        "source_term_index": source_term_index,
    }

    if settlement_type == "immediate_transfer":
        return [
            _transfer_obligation(
                transfer,
                base_terms=base_terms,
                trigger={"type": "immediate"},
            )
            for transfer in _mapping_items(spec.get("transfers"))
        ]

    if settlement_type == "future_obligation":
        obligation = _mapping(spec.get("obligation"))
        obligation_type = str(obligation.get("type", "cash_payment"))
        if obligation_type == "cash_payment":
            return [
                _cash_obligation(
                    from_player_id=obligation.get("from_player_id"),
                    to_player_id=obligation.get("to_player_id"),
                    amount=obligation.get("amount"),
                    obligation_type="cash_payment",
                    schedule={
                        "trigger": {
                            "type": "round",
                            "round": obligation.get("due_turn"),
                            "due_turn": obligation.get("due_turn"),
                        }
                    },
                    base_terms=base_terms,
                )
            ]
        if obligation_type == "installment_loan":
            return [
                _cash_obligation(
                    from_player_id=obligation.get("borrower_player_id"),
                    to_player_id=obligation.get("lender_player_id"),
                    amount=payment.get("amount"),
                    obligation_type="installment_loan",
                    schedule={
                        "trigger": {
                            "type": "round",
                            "round": payment.get("due_turn"),
                            "due_turn": payment.get("due_turn"),
                        },
                        "installment_index": payment_index,
                    },
                    base_terms={
                        **base_terms,
                        "principal_amount": obligation.get("principal_amount"),
                    },
                )
                for payment_index, payment in enumerate(_mapping_items(obligation.get("schedule")), start=1)
            ]
        if obligation_type == "interest_bearing_debt":
            return [
                _cash_obligation(
                    from_player_id=obligation.get("borrower_player_id"),
                    to_player_id=obligation.get("lender_player_id"),
                    amount=_interest_bearing_amount(obligation),
                    obligation_type="interest_bearing_debt",
                    schedule={
                        "trigger": {
                            "type": "round",
                            "round": obligation.get("due_turn"),
                            "due_turn": obligation.get("due_turn"),
                        }
                    },
                    base_terms={
                        **base_terms,
                        "principal_amount": obligation.get("principal_amount"),
                        "interest_rate_percent": obligation.get("interest_rate_percent"),
                    },
                )
            ]

    if settlement_type == "collateral_claim":
        obligation = _mapping(spec.get("obligation"))
        collateral_claim = _mapping(spec.get("collateral_claim"))
        return [
            _cash_obligation(
                from_player_id=obligation.get("borrower_player_id"),
                to_player_id=obligation.get("lender_player_id"),
                amount=obligation.get("principal_amount"),
                obligation_type="collateralized_loan",
                schedule={
                    "trigger": {
                        "type": "round",
                        "round": obligation.get("due_turn"),
                        "due_turn": obligation.get("due_turn"),
                    }
                },
                base_terms={
                    **base_terms,
                    "collateral_property_ids": list(collateral_claim.get("property_ids", [])),
                },
            )
        ]

    if settlement_type == "option":
        option = _mapping(spec.get("option"))
        return [
            ObligationDraft(
                owed_by_player_id=_uuid_or_none(option.get("grantor_player_id")),
                owed_to_player_id=_uuid_or_none(option.get("holder_player_id")),
                obligation_type="property_option",
                schedule={
                    "trigger": {
                        "type": "round",
                        "round": option.get("expiration_turn"),
                        "due_turn": option.get("expiration_turn"),
                    }
                },
                terms={
                    **base_terms,
                    "property_id": option.get("property_id"),
                    "strike_price": option.get("strike_price"),
                    "expiration_turn": option.get("expiration_turn"),
                    "settlement_action": "record_option_expiration",
                },
            )
        ]

    if settlement_type == "trigger":
        trigger = _mapping(spec.get("trigger"))
        if "rent_share" in spec:
            rent_share = _mapping(spec.get("rent_share"))
            return [
                _cash_obligation(
                    from_player_id=rent_share.get("from_player_id"),
                    to_player_id=rent_share.get("to_player_id"),
                    amount=None,
                    obligation_type="rent_share",
                    schedule={"trigger": _normalized_trigger(trigger)},
                    base_terms={
                        **base_terms,
                        "property_id": trigger.get("property_id"),
                        "share_percent": rent_share.get("share_percent"),
                        "duration_turns": rent_share.get("duration_turns"),
                        "settlement_action": "rent_share_cash_payment",
                    },
                )
            ]
        payout = _mapping(spec.get("payout"))
        return [
            _cash_obligation(
                from_player_id=payout.get("from_player_id"),
                to_player_id=payout.get("to_player_id"),
                amount=payout.get("amount"),
                obligation_type="insurance_payout",
                schedule={"trigger": _normalized_trigger(trigger)},
                base_terms=base_terms,
            )
        ]

    if settlement_type == "conditional_obligation":
        trigger = _mapping(spec.get("trigger"))
        obligation = _mapping(spec.get("obligation"))
        return [
            _cash_obligation(
                from_player_id=obligation.get("from_player_id"),
                to_player_id=obligation.get("to_player_id"),
                amount=obligation.get("amount"),
                obligation_type="conditional_obligation",
                schedule={"trigger": _normalized_trigger(trigger)},
                base_terms=base_terms,
            )
        ]

    if settlement_type == "guarantee_exposure":
        exposure = _mapping(spec.get("guarantee_exposure"))
        return [
            _cash_obligation(
                from_player_id=exposure.get("guarantor_player_id"),
                to_player_id=exposure.get("beneficiary_player_id"),
                amount=exposure.get("amount"),
                obligation_type="guarantee",
                schedule={
                    "trigger": {
                        "type": "default",
                        "instrument_id": exposure.get("target_instrument_id"),
                    }
                },
                base_terms={
                    **base_terms,
                    "guaranteed_player_id": exposure.get("guaranteed_player_id"),
                    "target_instrument_id": exposure.get("target_instrument_id"),
                },
            )
        ]

    if settlement_type == "default_penalty":
        penalty = _mapping(spec.get("default_penalty"))
        return [
            _cash_obligation(
                from_player_id=penalty.get("liable_player_id"),
                to_player_id=penalty.get("beneficiary_player_id"),
                amount=penalty.get("amount"),
                obligation_type="default_penalty",
                schedule={
                    "trigger": {
                        "type": "default",
                        "instrument_id": penalty.get("target_instrument_id"),
                    }
                },
                base_terms={
                    **base_terms,
                    "target_instrument_id": penalty.get("target_instrument_id"),
                },
            )
        ]

    return [
        ObligationDraft(
            owed_by_player_id=None,
            owed_to_player_id=None,
            obligation_type=settlement_type,
            schedule={"trigger": {"type": "immediate"}},
            terms={**base_terms, "settlement_action": "record_only"},
        )
    ]


def _transfer_obligation(
    transfer: Mapping[str, Any],
    *,
    base_terms: Mapping[str, Any],
    trigger: Mapping[str, Any],
) -> ObligationDraft:
    transfer_type = str(transfer.get("type", ""))
    if transfer_type == "cash":
        return _cash_obligation(
            from_player_id=transfer.get("from_player_id"),
            to_player_id=transfer.get("to_player_id"),
            amount=transfer.get("amount"),
            obligation_type="cash_payment",
            schedule={"trigger": dict(trigger)},
            base_terms={**base_terms, "settlement_action": "cash_transfer"},
        )
    return ObligationDraft(
        owed_by_player_id=_uuid_or_none(transfer.get("from_player_id")),
        owed_to_player_id=_uuid_or_none(transfer.get("to_player_id")),
        obligation_type="property_transfer",
        schedule={"trigger": dict(trigger)},
        terms={
            **base_terms,
            "settlement_action": "property_transfer",
            "property_id": transfer.get("property_id"),
            "from_player_id": transfer.get("from_player_id"),
            "to_player_id": transfer.get("to_player_id"),
        },
    )


def _cash_obligation(
    *,
    from_player_id: object,
    to_player_id: object,
    amount: object,
    obligation_type: str,
    schedule: Mapping[str, Any],
    base_terms: Mapping[str, Any],
) -> ObligationDraft:
    terms = {
        **base_terms,
        "settlement_action": base_terms.get("settlement_action", "cash_transfer"),
        "from_player_id": from_player_id,
        "to_player_id": to_player_id,
    }
    if amount is not None:
        terms["amount"] = amount
    return ObligationDraft(
        owed_by_player_id=_uuid_or_none(from_player_id),
        owed_to_player_id=_uuid_or_none(to_player_id),
        obligation_type=obligation_type,
        schedule=dict(schedule),
        terms=terms,
    )


def _normalized_trigger(trigger: Mapping[str, Any]) -> dict[str, Any]:
    trigger_type = str(trigger.get("type", ""))
    if trigger_type == "rent_collected":
        return {"type": "rent_collected", "property_id": trigger.get("property_id")}
    if trigger_type in {"turn_start", "turn_end"}:
        return {"type": trigger_type, "turn": trigger.get("turn")}
    if trigger_type == "property_landed":
        return {"type": "property_landed", "property_id": trigger.get("property_id")}
    if trigger_type == "bankruptcy":
        normalized: dict[str, Any] = {"type": "bankruptcy"}
        if trigger.get("player_id") is not None:
            normalized["player_id"] = trigger.get("player_id")
        return normalized
    if trigger_type == "default":
        return {"type": "default", "instrument_id": trigger.get("instrument_id")}
    if trigger_type == "time":
        return {"type": "time", "due_at": trigger.get("due_at")}
    return dict(trigger)


async def _load_deal_for_update(
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


async def _load_contract_for_deal(
    *,
    session: AsyncSession,
    game_id: UUID,
    deal_id: UUID,
) -> dict[str, Any] | None:
    result = await session.execute(
        sa.select(contracts)
        .where(contracts.c.game_id == game_id, contracts.c.deal_id == deal_id)
        .order_by(contracts.c.created_at, contracts.c.id)
        .limit(1)
        .with_for_update()
    )
    row = result.mappings().first()
    return None if row is None else dict(row)


async def _load_obligations_for_contract(session: AsyncSession, contract_id: UUID) -> list[dict[str, Any]]:
    result = await session.execute(
        sa.select(obligations)
        .where(obligations.c.contract_id == contract_id)
        .order_by(obligations.c.created_at, obligations.c.id)
    )
    return [dict(row) for row in result.mappings().all()]


def _validate_contract_eligible_deal(deal_row: Mapping[str, Any]) -> None:
    terms = _terms_mapping(deal_row)
    if deal_row["status"] != DEAL_STATUS_ACCEPTED or not _is_structured_deal_terms(terms):
        raise ContractExecutionError(
            "deal_not_contract_eligible",
            "only eligible_for_contract accepted structured deals can create contracts",
            field="deal_id",
        )


def _contract_terms_for_deal(deal_row: Mapping[str, Any]) -> dict[str, Any]:
    terms = dict(_terms_mapping(deal_row))
    specs: list[dict[str, Any]] = []
    participants = [str(player_id) for player_id in terms.get("participants", [])]
    instrument_ids = _instrument_ids(terms.get("terms", []))
    for raw_term in _term_mappings(terms.get("terms", [])):
        settlement = settle_instrument(
            create_instrument(raw_term),
            player_ids=participants,
            instrument_ids=instrument_ids,
        )
        specs.append(settlement.model_dump(mode="json"))
    return {
        **terms,
        "contract_schema_version": 1,
        "source_deal_id": str(deal_row["id"]),
        "source_negotiation_id": None
        if deal_row["negotiation_id"] is None
        else str(deal_row["negotiation_id"]),
        "settlement_specs": specs,
    }


async def _insert_contract_audit(
    *,
    session: AsyncSession,
    deal_row: Mapping[str, Any],
    message_type: str,
    payload: Mapping[str, Any],
) -> None:
    negotiation_id = deal_row["negotiation_id"]
    if negotiation_id is None:
        return
    await session.execute(
        negotiation_messages.insert().values(
            game_id=deal_row["game_id"],
            negotiation_id=negotiation_id,
            sender_player_id=None,
            recipient_player_id=None,
            message_type=message_type,
            body=None,
            payload=dict(payload),
        )
    )


def _is_structured_deal_terms(terms: Mapping[str, Any]) -> bool:
    return terms.get("kind") == STRUCTURED_DEAL_KIND and isinstance(terms.get("terms_hash"), str)


def _terms_mapping(deal_row: Mapping[str, Any]) -> Mapping[str, Any]:
    terms = deal_row.get("terms")
    return terms if isinstance(terms, Mapping) else {}


def _term_mappings(value: object) -> list[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, Mapping)):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _mapping_items(value: object) -> list[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, Mapping)):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _instrument_ids(value: object) -> set[str]:
    instrument_ids: set[str] = set()
    for term in _term_mappings(value):
        instrument_id = term.get("instrument_id")
        if isinstance(instrument_id, str) and instrument_id.strip():
            instrument_ids.add(instrument_id.strip())
    return instrument_ids


def _instrument_id(term: Mapping[str, Any], index: int) -> str:
    raw = term.get("instrument_id")
    return raw.strip() if isinstance(raw, str) and raw.strip() else f"term-{index + 1}"


def _uuid_or_none(value: object) -> UUID | None:
    if value is None:
        return None
    return UUID(str(value))


def _interest_bearing_amount(obligation: Mapping[str, Any]) -> int:
    principal = Decimal(str(obligation.get("principal_amount", 0)))
    rate = Decimal(str(obligation.get("interest_rate_percent", 0)))
    interest = (principal * rate / Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(principal + interest)


__all__ = [
    "AUDIT_CONTRACT_CREATED",
    "AUDIT_OBLIGATION_SCHEDULED",
    "ContractCreationResult",
    "ContractExecutionError",
    "ObligationDraft",
    "create_contract_from_accepted_deal",
    "generate_obligation_schedule",
]
