from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.db.persistence import AcceptedEventTemplate
from app.rules.state import GameState, PlayerState, PropertyOwnershipState


DEFAULT_CONTRACT_CLASSIC_RULE_POLICY: dict[str, str] = {
    "contract_obligations_affect_bankruptcy": "after_classic_debts",
    "collateral_seizure": "strict_available_collateral_only",
    "mortgaged_option_policy": "reject",
    "improved_property_option_policy": "defer",
    "rent_share_reduced_rent": "share_actual_paid",
    "rent_share_waived_rent": "settle_zero",
    "rent_share_unpaid_rent": "defer_until_paid",
    "jail_obligation_policy": "cash_obligations_continue_property_obligations_defer",
    "auction_obligation_policy": "cash_obligations_continue_property_obligations_defer",
    "bankruptcy_obligation_policy": "resolve_after_classic_debts",
    "impossible_state_prevention": "strict",
}

DecisionStatus = Literal["settle", "defer", "reject", "default"]


@dataclass(frozen=True)
class ContractRuleDecision:
    status: DecisionStatus
    decision: str
    policy_key: str
    policy_value: str
    trigger: dict[str, Any]
    classic_rule_interaction: dict[str, Any]
    resulting_state_effect: dict[str, Any]
    explanation_text: str
    cash_amount: int | None = None
    event_templates: tuple[AcceptedEventTemplate, ...] = ()
    reason_code: str | None = None
    detail: str | None = None

    def outcome_status(self) -> str:
        if self.status == "settle":
            return "settled"
        if self.status == "default":
            return "defaulted"
        return self.status

    def model_dump(self, *, mode: str = "python") -> dict[str, Any]:
        del mode
        return {
            "status": self.status,
            "decision": self.decision,
            "policy_key": self.policy_key,
            "policy_value": self.policy_value,
            "trigger": dict(self.trigger),
            "classic_rule_interaction": dict(self.classic_rule_interaction),
            "resulting_state_effect": dict(self.resulting_state_effect),
            "explanation_text": self.explanation_text,
            "cash_amount": self.cash_amount,
            "reason_code": self.reason_code,
            "detail": self.detail,
            "event_templates": [
                {"event_type": event.event_type, "payload": dict(event.payload)}
                for event in self.event_templates
            ],
        }


class BankruptcyResolutionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy: dict[str, str]
    bankrupt_player_id: str
    classic_creditor_id: str | None
    decisions: list[dict[str, Any]]
    resulting_cash_by_player_id: dict[str, int]
    resulting_property_owner_by_property_id: dict[str, str | None]


def resolve_contract_classic_rule_interaction(
    obligation_row: Mapping[str, Any],
    *,
    state: GameState,
    trigger_context: Mapping[str, Any] | None = None,
    policy: Mapping[str, str] | None = None,
) -> ContractRuleDecision:
    effective_policy = _policy(policy)
    context = dict(trigger_context or {})
    trigger = _trigger(obligation_row, context)
    terms = _terms(obligation_row)
    obligation_type = str(obligation_row.get("obligation_type", ""))

    timing_decision = _timing_sensitive_decision(
        obligation_row,
        state=state,
        trigger=trigger,
        trigger_context=context,
        policy=effective_policy,
    )
    if timing_decision is not None:
        return timing_decision

    if obligation_type == "rent_share" or terms.get("settlement_action") == "rent_share_cash_payment":
        return _rent_share_decision(
            obligation_row,
            state=state,
            trigger=trigger,
            trigger_context=context,
            policy=effective_policy,
        )

    if obligation_type == "property_option" or terms.get("settlement_action") == "record_option_expiration":
        return _property_option_decision(
            obligation_row,
            state=state,
            trigger=trigger,
            policy=effective_policy,
        )

    if obligation_type == "collateralized_loan":
        collateral = _collateral_decision(
            obligation_row,
            state=state,
            trigger=trigger,
            trigger_context=context,
            policy=effective_policy,
        )
        if collateral is not None:
            return collateral

    if _is_cash_obligation(obligation_row):
        return _cash_decision(
            obligation_row,
            state=state,
            trigger=trigger,
            policy=effective_policy,
            policy_key="impossible_state_prevention",
            decision="cash_transfer",
        )

    if _is_property_transfer(obligation_row):
        return _property_transfer_decision(
            obligation_row,
            state=state,
            trigger=trigger,
            policy=effective_policy,
        )

    return _decision(
        status="settle",
        decision="record_only",
        policy_key="impossible_state_prevention",
        trigger=trigger,
        policy=effective_policy,
        effect={"record_only": True},
        detail="record-only obligation has no cash or property mutation",
    )


def impossible_state_prevention_check(
    *,
    state: GameState,
    obligation_row: Mapping[str, Any],
    event_templates: Sequence[AcceptedEventTemplate],
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    cash_by_player = {player.id: player.cash for player in state.players}
    known_players = set(cash_by_player)
    known_properties = {ownership.property_id for ownership in state.property_ownership}
    property_assignments: set[str] = set()

    for event in event_templates:
        payload = dict(event.payload)
        if event.event_type == "PLAYER_CASH_DELTA":
            player_id = _string_or_none(payload.get("player_id"))
            amount = _int_or_none(payload.get("amount"))
            if player_id is None or player_id not in known_players:
                issues.append(
                    {
                        "code": "invalid_cash_player",
                        "message": "cash mutation must reference an existing player",
                    }
                )
                continue
            if amount is None:
                issues.append({"code": "invalid_cash_amount", "message": "cash mutation amount is invalid"})
                continue
            cash_by_player[player_id] += amount
            if cash_by_player[player_id] < 0:
                issues.append(
                    {
                        "code": "negative_cash",
                        "message": "contract cash transfer would make player cash negative",
                    }
                )

        if event.event_type == "PROPERTY_OWNER_SET":
            property_id = _string_or_none(payload.get("property_id"))
            owner_id = _string_or_none(payload.get("owner_id"))
            if property_id is None or property_id not in known_properties:
                issues.append(
                    {
                        "code": "invalid_property",
                        "message": "property mutation must reference an existing property",
                    }
                )
                continue
            if owner_id is not None and owner_id not in known_players:
                issues.append(
                    {
                        "code": "invalid_property_owner",
                        "message": "property mutation must reference an existing owner or the bank",
                    }
                )
            if property_id in property_assignments:
                issues.append(
                    {
                        "code": "duplicate_property_assignment",
                        "message": "contract cannot assign the same property more than once",
                    }
                )
            property_assignments.add(property_id)

    terms = _terms(obligation_row)
    if _is_property_transfer(obligation_row):
        property_id = _string_or_none(terms.get("property_id"))
        expected_owner = _string_or_none(terms.get("from_player_id") or obligation_row.get("owed_by_player_id"))
        ownership = _property_by_id(state, property_id)
        if ownership is None or ownership.owner_id != expected_owner:
            issues.append(
                {
                    "code": "property_owner_mismatch",
                    "message": "property transfer source owner does not match the current state",
                }
            )

    collateral_ids = _collateral_property_ids(terms)
    if len(collateral_ids) != len(set(collateral_ids)):
        issues.append(
            {
                "code": "duplicate_collateral",
                "message": "collateral property ids must be unique before seizure",
            }
        )

    return issues


def bankruptcy_resolution_plan(
    *,
    state: GameState,
    bankrupt_player_id: str,
    classic_creditor_id: str | None,
    obligations: Sequence[Mapping[str, Any]],
    policy: Mapping[str, str] | None = None,
) -> BankruptcyResolutionPlan:
    effective_policy = _policy(policy)
    bankrupt_player = _player_by_id(state, bankrupt_player_id)
    if bankrupt_player is None:
        raise ValueError("bankrupt_player_id must reference an existing player")
    creditor = _player_by_id(state, classic_creditor_id) if classic_creditor_id is not None else None
    if classic_creditor_id is not None and creditor is None:
        raise ValueError("classic_creditor_id must reference an existing player")
    cash_by_player = {player.id: player.cash for player in state.players}
    owner_by_property = {ownership.property_id: ownership.owner_id for ownership in state.property_ownership}
    decisions: list[dict[str, Any]] = []

    classic_amount = 0
    if state.active_payment is not None and state.active_payment.debtor_id == bankrupt_player_id:
        classic_amount = max(state.active_payment.amount_owed - state.active_payment.amount_paid, 0)
    amount_paid = min(cash_by_player[bankrupt_player.id], classic_amount)
    if amount_paid > 0:
        cash_by_player[bankrupt_player.id] -= amount_paid
        if creditor is not None:
            cash_by_player[creditor.id] += amount_paid
    decisions.append(
        {
            "kind": "classic_debt",
            "policy_key": "contract_obligations_affect_bankruptcy",
            "decision": "classic_debt_resolved_first",
            "debtor_id": bankrupt_player.id,
            "creditor_id": None if creditor is None else creditor.id,
            "amount_owed": classic_amount,
            "amount_paid": amount_paid,
        }
    )

    relevant_obligations = [
        obligation
        for obligation in obligations
        if _uuid_str(obligation.get("owed_by_player_id")) == bankrupt_player.id
        and str(obligation.get("status", "pending")) == "pending"
    ]
    relevant_obligations.sort(key=_bankruptcy_obligation_sort_key)

    for obligation in relevant_obligations:
        terms = _terms(obligation)
        owed_to = _uuid_str(obligation.get("owed_to_player_id"))
        amount = _cash_amount_from_terms(terms)
        if str(obligation.get("obligation_type")) == "collateralized_loan":
            collateral_ids = _collateral_property_ids(terms)
            unavailable = [
                property_id
                for property_id in collateral_ids
                if owner_by_property.get(property_id) != bankrupt_player.id
            ]
            if len(collateral_ids) == len(set(collateral_ids)) and owed_to is not None and not unavailable:
                for property_id in collateral_ids:
                    owner_by_property[property_id] = owed_to
                decisions.append(
                    {
                        "kind": "contract_obligation",
                        "policy_key": "collateral_seizure",
                        "decision": "collateral_seizure",
                        "obligation_id": str(obligation["id"]),
                        "collateral_property_ids": collateral_ids,
                        "to_player_id": owed_to,
                    }
                )
                continue

        if amount is not None and owed_to is not None and cash_by_player[bankrupt_player.id] >= amount:
            cash_by_player[bankrupt_player.id] -= amount
            cash_by_player[owed_to] += amount
            decision = "contract_obligation_paid_from_remaining_estate"
        else:
            decision = "default_after_bankruptcy_estate_exhausted"
        decisions.append(
            {
                "kind": "contract_obligation",
                "policy_key": "contract_obligations_affect_bankruptcy",
                "decision": decision,
                "obligation_id": str(obligation["id"]),
                "amount": amount,
                "to_player_id": owed_to,
            }
        )

    cash_by_player[bankrupt_player.id] = max(cash_by_player[bankrupt_player.id], 0)
    return BankruptcyResolutionPlan(
        policy=dict(effective_policy),
        bankrupt_player_id=bankrupt_player.id,
        classic_creditor_id=None if creditor is None else creditor.id,
        decisions=decisions,
        resulting_cash_by_player_id=cash_by_player,
        resulting_property_owner_by_property_id=owner_by_property,
    )


def rent_share_cash_amount(terms: Mapping[str, Any], trigger_context: Mapping[str, Any]) -> int | None:
    rent_status = str(trigger_context.get("rent_status", "")).strip().lower()
    if rent_status == "waived":
        return 0
    if rent_status == "unpaid":
        return None

    paid_amount = _positive_money(
        trigger_context.get("rent_paid_amount", trigger_context.get("amount_paid"))
    )
    if paid_amount is None:
        paid_amount = _positive_money(trigger_context.get("amount"))
    if paid_amount is None:
        return None

    share_percent = Decimal(str(terms.get("share_percent", 0)))
    if share_percent <= 0:
        return None
    amount = (Decimal(paid_amount) * share_percent / Decimal("100")).quantize(
        Decimal("1"),
        rounding=ROUND_HALF_UP,
    )
    return int(amount)


def _timing_sensitive_decision(
    obligation_row: Mapping[str, Any],
    *,
    state: GameState,
    trigger: Mapping[str, Any],
    trigger_context: Mapping[str, Any],
    policy: Mapping[str, str],
) -> ContractRuleDecision | None:
    owed_by = _uuid_str(obligation_row.get("owed_by_player_id"))
    debtor = _player_by_id(state, owed_by)
    if state.active_auction is not None and _is_property_sensitive_obligation(obligation_row):
        return _decision(
            status="defer",
            decision="defer_during_auction",
            policy_key="auction_obligation_policy",
            trigger=trigger,
            policy=policy,
            effect={"deferred": True, "active_auction_property_id": state.active_auction.property_id},
            detail="property-sensitive contract obligation deferred until the auction resolves",
        )

    if state.active_bankruptcy is not None or _context_type(trigger_context) == "bankruptcy":
        if _is_cash_obligation(obligation_row):
            return _cash_decision(
                obligation_row,
                state=state,
                trigger=trigger,
                policy=policy,
                policy_key="bankruptcy_obligation_policy",
                decision="cash_obligation_resolved_during_bankruptcy",
            )
        return _decision(
            status="defer",
            decision="defer_during_bankruptcy",
            policy_key="bankruptcy_obligation_policy",
            trigger=trigger,
            policy=policy,
            effect={"deferred": True, "active_bankruptcy": True},
            detail="non-cash contract obligation deferred during bankruptcy resolution",
        )

    if debtor is not None and debtor.in_jail:
        if _is_cash_obligation(obligation_row):
            return _cash_decision(
                obligation_row,
                state=state,
                trigger=trigger,
                policy=policy,
                policy_key="jail_obligation_policy",
                decision="cash_obligation_resolved_while_in_jail",
            )
        return _decision(
            status="defer",
            decision="defer_while_in_jail",
            policy_key="jail_obligation_policy",
            trigger=trigger,
            policy=policy,
            effect={"deferred": True, "debtor_in_jail": debtor.id},
            detail="property-sensitive contract obligation deferred while obligated player is in jail",
        )
    return None


def _rent_share_decision(
    obligation_row: Mapping[str, Any],
    *,
    state: GameState,
    trigger: Mapping[str, Any],
    trigger_context: Mapping[str, Any],
    policy: Mapping[str, str],
) -> ContractRuleDecision:
    terms = _terms(obligation_row)
    rent_status = str(trigger_context.get("rent_status", "")).strip().lower()
    paid_amount = rent_share_cash_amount(terms, trigger_context)
    if rent_status == "unpaid":
        return _decision(
            status="defer",
            decision="rent_share_deferred_until_rent_paid",
            policy_key="rent_share_unpaid_rent",
            trigger=trigger,
            policy=policy,
            effect={"deferred": True, "rent_status": "unpaid"},
            detail="rent share follows actual rent paid; unpaid rent produces no payable share yet",
        )
    if rent_status == "waived" or paid_amount == 0:
        return _decision(
            status="settle",
            decision="rent_share_waived_no_payment",
            policy_key="rent_share_waived_rent",
            trigger=trigger,
            policy=policy,
            effect={"cash_transfers": [], "rent_status": rent_status or "waived"},
            detail="rent was waived, so the rent share settles with no cash transfer",
            cash_amount=0,
        )

    policy_key = (
        "rent_share_reduced_rent"
        if rent_status == "reduced" or _is_reduced_rent(trigger_context)
        else "impossible_state_prevention"
    )
    if paid_amount is None or paid_amount <= 0:
        return _decision(
            status="default",
            decision="rent_share_missing_paid_amount",
            policy_key=policy_key,
            trigger=trigger,
            policy=policy,
            effect={"cash_transfers": []},
            detail="rent share requires actual positive rent paid",
            reason_code="invalid_amount",
            cash_amount=paid_amount,
        )

    return _cash_decision(
        {**dict(obligation_row), "terms": {**dict(terms), "amount": paid_amount}},
        state=state,
        trigger=trigger,
        policy=policy,
        policy_key=policy_key,
        decision="rent_share_cash_transfer",
        cash_amount=paid_amount,
    )


def _property_option_decision(
    obligation_row: Mapping[str, Any],
    *,
    state: GameState,
    trigger: Mapping[str, Any],
    policy: Mapping[str, str],
) -> ContractRuleDecision:
    terms = _terms(obligation_row)
    property_id = _string_or_none(terms.get("property_id"))
    ownership = _property_by_id(state, property_id)
    if ownership is None:
        return _decision(
            status="reject",
            decision="reject_option_missing_property",
            policy_key="impossible_state_prevention",
            trigger=trigger,
            policy=policy,
            effect={"rejected": True},
            detail="option references an unavailable property",
            reason_code="invalid_property",
        )
    if ownership.mortgaged:
        return _decision(
            status="reject",
            decision="reject_mortgaged_option",
            policy_key="mortgaged_option_policy",
            trigger=trigger,
            policy=policy,
            effect={"rejected": True, "property_id": ownership.property_id, "mortgaged": True},
            detail="option cannot be exercised or expired as a clean transfer while the property is mortgaged",
            reason_code="option_property_mortgaged",
        )
    if ownership.houses > 0 or ownership.hotel:
        return _decision(
            status="defer",
            decision="defer_improved_property_option",
            policy_key="improved_property_option_policy",
            trigger=trigger,
            policy=policy,
            effect={
                "deferred": True,
                "property_id": ownership.property_id,
                "houses": ownership.houses,
                "hotel": ownership.hotel,
            },
            detail="option deferred until improvements are sold or the local option policy changes",
        )
    return _decision(
        status="settle",
        decision="record_option_expiration",
        policy_key="impossible_state_prevention",
        trigger=trigger,
        policy=policy,
        effect={"record_only": True, "property_id": ownership.property_id},
        detail="option has no mortgaged or improved-property conflict",
    )


def _collateral_decision(
    obligation_row: Mapping[str, Any],
    *,
    state: GameState,
    trigger: Mapping[str, Any],
    trigger_context: Mapping[str, Any],
    policy: Mapping[str, str],
) -> ContractRuleDecision | None:
    terms = _terms(obligation_row)
    amount = _cash_amount_from_terms(terms)
    debtor_id = _uuid_str(obligation_row.get("owed_by_player_id"))
    lender_id = _uuid_str(obligation_row.get("owed_to_player_id"))
    debtor = _player_by_id(state, debtor_id)
    if _context_type(trigger_context) != "default" and debtor is not None and amount is not None and debtor.cash >= amount:
        return None

    collateral_ids = _collateral_property_ids(terms)
    duplicates = sorted({property_id for property_id in collateral_ids if collateral_ids.count(property_id) > 1})
    unavailable: list[str] = []
    for property_id in sorted(set(collateral_ids)):
        ownership = _property_by_id(state, property_id)
        if ownership is None or ownership.owner_id != debtor_id:
            unavailable.append(property_id)

    if not collateral_ids or duplicates or unavailable or lender_id is None or _player_by_id(state, lender_id) is None:
        details: list[str] = []
        if duplicates:
            details.append(f"duplicate collateral {', '.join(duplicates)}")
        if unavailable:
            details.append(f"unavailable collateral {', '.join(unavailable)}")
        if not collateral_ids:
            details.append("missing collateral")
        if lender_id is None or _player_by_id(state, lender_id) is None:
            details.append("missing collateral recipient")
        return _decision(
            status="default",
            decision="collateral_seizure_rejected",
            policy_key="collateral_seizure",
            trigger=trigger,
            policy=policy,
            effect={"collateral_property_ids": collateral_ids, "seized_property_ids": []},
            detail="; ".join(details),
            reason_code="collateral_unavailable",
        )

    event_templates = tuple(
        AcceptedEventTemplate(
            event_type="PROPERTY_OWNER_SET",
            payload={"property_id": property_id, "owner_id": lender_id},
        )
        for property_id in collateral_ids
    )
    return _decision(
        status="settle",
        decision="collateral_seizure",
        policy_key="collateral_seizure",
        trigger=trigger,
        policy=policy,
        effect={
            "collateral_property_ids": collateral_ids,
            "seized_property_ids": collateral_ids,
            "to_player_id": lender_id,
        },
        detail="collateral seized instead of creating impossible negative cash",
        event_templates=event_templates,
    )


def _cash_decision(
    obligation_row: Mapping[str, Any],
    *,
    state: GameState,
    trigger: Mapping[str, Any],
    policy: Mapping[str, str],
    policy_key: str,
    decision: str,
    cash_amount: int | None = None,
) -> ContractRuleDecision:
    terms = _terms(obligation_row)
    amount = cash_amount if cash_amount is not None else _cash_amount_from_terms(terms)
    owed_by = _uuid_str(obligation_row.get("owed_by_player_id"))
    owed_to = _uuid_str(obligation_row.get("owed_to_player_id"))
    debtor = _player_by_id(state, owed_by)
    creditor = _player_by_id(state, owed_to)
    if amount is None or amount <= 0:
        return _decision(
            status="default",
            decision="cash_transfer_invalid_amount",
            policy_key=policy_key,
            trigger=trigger,
            policy=policy,
            effect={"cash_transfers": []},
            detail="cash settlement requires a positive amount",
            reason_code="invalid_amount",
            cash_amount=amount,
        )
    if debtor is None or creditor is None:
        return _decision(
            status="default",
            decision="cash_transfer_invalid_parties",
            policy_key=policy_key,
            trigger=trigger,
            policy=policy,
            effect={"cash_transfers": []},
            detail="cash settlement requires existing owing and receiving players",
            reason_code="invalid_parties",
            cash_amount=amount,
        )
    if debtor.cash < amount:
        return _decision(
            status="default",
            decision="cash_transfer_insufficient_cash",
            policy_key=policy_key,
            trigger=trigger,
            policy=policy,
            effect={"cash_transfers": [], "debtor_cash": debtor.cash, "amount": amount},
            detail="owing player cannot pay without negative cash",
            reason_code="insufficient_cash",
            cash_amount=amount,
        )
    event_templates = (
        AcceptedEventTemplate(event_type="PLAYER_CASH_DELTA", payload={"player_id": debtor.id, "amount": -amount}),
        AcceptedEventTemplate(event_type="PLAYER_CASH_DELTA", payload={"player_id": creditor.id, "amount": amount}),
    )
    return _decision(
        status="settle",
        decision=decision,
        policy_key=policy_key,
        trigger=trigger,
        policy=policy,
        effect={
            "cash_transfers": [
                {"player_id": debtor.id, "amount": -amount},
                {"player_id": creditor.id, "amount": amount},
            ]
        },
        detail="cash obligation can be paid from current cash",
        cash_amount=amount,
        event_templates=event_templates,
    )


def _property_transfer_decision(
    obligation_row: Mapping[str, Any],
    *,
    state: GameState,
    trigger: Mapping[str, Any],
    policy: Mapping[str, str],
) -> ContractRuleDecision:
    terms = _terms(obligation_row)
    property_id = _string_or_none(terms.get("property_id"))
    expected_owner = _string_or_none(terms.get("from_player_id") or obligation_row.get("owed_by_player_id"))
    next_owner = _string_or_none(terms.get("to_player_id") or obligation_row.get("owed_to_player_id"))
    ownership = _property_by_id(state, property_id)
    if ownership is None or ownership.owner_id != expected_owner or _player_by_id(state, next_owner) is None:
        return _decision(
            status="default",
            decision="property_transfer_rejected",
            policy_key="impossible_state_prevention",
            trigger=trigger,
            policy=policy,
            effect={"property_id": property_id, "from_player_id": expected_owner, "to_player_id": next_owner},
            detail="property transfer cannot be applied to the current ownership state",
            reason_code="property_owner_mismatch",
        )
    event_templates = (
        AcceptedEventTemplate(
            event_type="PROPERTY_OWNER_SET",
            payload={"property_id": ownership.property_id, "owner_id": next_owner},
        ),
    )
    return _decision(
        status="settle",
        decision="property_transfer",
        policy_key="impossible_state_prevention",
        trigger=trigger,
        policy=policy,
        effect={"property_id": ownership.property_id, "from_player_id": expected_owner, "to_player_id": next_owner},
        detail="property transfer is possible in the current ownership state",
        event_templates=event_templates,
    )


def _decision(
    *,
    status: DecisionStatus,
    decision: str,
    policy_key: str,
    trigger: Mapping[str, Any],
    policy: Mapping[str, str],
    effect: Mapping[str, Any],
    detail: str,
    cash_amount: int | None = None,
    event_templates: Sequence[AcceptedEventTemplate] = (),
    reason_code: str | None = None,
) -> ContractRuleDecision:
    interaction = {
        "policy": dict(policy),
        "policy_key": policy_key,
        "policy_value": policy[policy_key],
        "deterministic": True,
        "detail": detail,
    }
    explanation_text = (
        "Contract outcome explanation: "
        f"trigger {dict(trigger)} used {policy_key}={policy[policy_key]}; "
        f"decision {decision}; {detail}; resulting effect {dict(effect)}."
    )
    return ContractRuleDecision(
        status=status,
        decision=decision,
        policy_key=policy_key,
        policy_value=policy[policy_key],
        trigger=dict(trigger),
        classic_rule_interaction=interaction,
        resulting_state_effect=dict(effect),
        explanation_text=explanation_text,
        cash_amount=cash_amount,
        event_templates=tuple(event_templates),
        reason_code=reason_code,
        detail=detail,
    )


def _policy(policy: Mapping[str, str] | None) -> dict[str, str]:
    merged = dict(DEFAULT_CONTRACT_CLASSIC_RULE_POLICY)
    if policy is not None:
        merged.update({str(key): str(value) for key, value in policy.items()})
    return merged


def _trigger(obligation_row: Mapping[str, Any], trigger_context: Mapping[str, Any]) -> dict[str, Any]:
    schedule = obligation_row.get("schedule")
    if isinstance(schedule, Mapping):
        trigger = schedule.get("trigger")
        if isinstance(trigger, Mapping):
            return dict(trigger)
    if trigger_context:
        return dict(trigger_context)
    return {"type": "immediate"}


def _terms(obligation_row: Mapping[str, Any]) -> Mapping[str, Any]:
    terms = obligation_row.get("terms")
    return terms if isinstance(terms, Mapping) else {}


def _context_type(trigger_context: Mapping[str, Any]) -> str:
    raw = trigger_context.get("type")
    return raw.strip().lower() if isinstance(raw, str) else ""


def _is_cash_obligation(obligation_row: Mapping[str, Any]) -> bool:
    terms = _terms(obligation_row)
    return str(terms.get("settlement_action", "record_only")) == "cash_transfer" or str(
        obligation_row.get("obligation_type", "")
    ) in {
        "cash_payment",
        "conditional_obligation",
        "default_penalty",
        "guarantee",
        "installment_loan",
        "insurance_payout",
        "interest_bearing_debt",
        "rent_share",
    }


def _is_property_transfer(obligation_row: Mapping[str, Any]) -> bool:
    terms = _terms(obligation_row)
    return (
        str(terms.get("settlement_action", "record_only")) == "property_transfer"
        or str(obligation_row.get("obligation_type", "")) == "property_transfer"
    )


def _is_property_sensitive_obligation(obligation_row: Mapping[str, Any]) -> bool:
    terms = _terms(obligation_row)
    return str(obligation_row.get("obligation_type", "")) in {
        "property_transfer",
        "property_option",
        "collateralized_loan",
    } or str(terms.get("settlement_action", "")) in {
        "property_transfer",
        "record_option_expiration",
    }


def _cash_amount_from_terms(terms: Mapping[str, Any]) -> int | None:
    return _positive_money(terms.get("amount"))


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


def _int_or_none(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _string_or_none(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _uuid_str(value: object) -> str | None:
    if value is None:
        return None
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError):
        return _string_or_none(value)


def _player_by_id(state: GameState, player_id: object) -> PlayerState | None:
    normalized = _uuid_str(player_id)
    if normalized is None:
        return None
    for player in state.players:
        if player.id == normalized:
            return player
    return None


def _property_by_id(state: GameState, property_id: object) -> PropertyOwnershipState | None:
    normalized = _string_or_none(property_id)
    if normalized is None:
        return None
    for ownership in state.property_ownership:
        if ownership.property_id == normalized:
            return ownership
    return None


def _collateral_property_ids(terms: Mapping[str, Any]) -> list[str]:
    raw_ids = terms.get("collateral_property_ids", [])
    if not isinstance(raw_ids, Sequence) or isinstance(raw_ids, (str, bytes, Mapping)):
        return []
    return [property_id for item in raw_ids if (property_id := _string_or_none(item)) is not None]


def _is_reduced_rent(trigger_context: Mapping[str, Any]) -> bool:
    paid = _positive_money(trigger_context.get("rent_paid_amount", trigger_context.get("amount_paid")))
    owed = _positive_money(trigger_context.get("rent_owed_amount", trigger_context.get("amount_owed")))
    return paid is not None and owed is not None and paid < owed


def _bankruptcy_obligation_sort_key(obligation: Mapping[str, Any]) -> tuple[int, str, str]:
    secured_rank = 0 if str(obligation.get("obligation_type")) == "collateralized_loan" else 1
    return (secured_rank, str(obligation.get("contract_id", "")), str(obligation.get("id", "")))


__all__ = [
    "DEFAULT_CONTRACT_CLASSIC_RULE_POLICY",
    "BankruptcyResolutionPlan",
    "ContractRuleDecision",
    "bankruptcy_resolution_plan",
    "impossible_state_prevention_check",
    "rent_share_cash_amount",
    "resolve_contract_classic_rule_interaction",
]
