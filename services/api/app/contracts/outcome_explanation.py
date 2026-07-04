from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.classic_rules import DEFAULT_CONTRACT_CLASSIC_RULE_POLICY, ContractRuleDecision
from app.db.metadata import contracts, obligations


CONTRACT_OUTCOME_EXPLANATION_KEY = "contract_outcome_explanation"


class ContractOutcomeExplanation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    game_id: UUID
    source_deal_id: UUID | None
    contract_id: UUID
    obligation_id: UUID | None
    obligation_type: str
    trigger: dict[str, Any]
    classic_rule_interaction: dict[str, Any]
    decision: dict[str, Any]
    resulting_state_effect: dict[str, Any]
    explanation_text: str


async def load_contract_outcome_explanations(
    *,
    session: AsyncSession,
    game_id: UUID,
    contract_id: UUID | None = None,
) -> list[ContractOutcomeExplanation]:
    statement = sa.select(contracts).where(contracts.c.game_id == game_id).order_by(contracts.c.created_at, contracts.c.id)
    if contract_id is not None:
        statement = statement.where(contracts.c.id == contract_id)
    contract_result = await session.execute(statement)
    contract_rows = [dict(row) for row in contract_result.mappings().all()]

    explanations: list[ContractOutcomeExplanation] = []
    for contract_row in contract_rows:
        obligation_result = await session.execute(
            sa.select(obligations)
            .where(obligations.c.contract_id == contract_row["id"])
            .order_by(obligations.c.created_at, obligations.c.id)
        )
        obligation_rows = [dict(row) for row in obligation_result.mappings().all()]
        if not obligation_rows:
            explanations.append(contract_outcome_explanation(contract_row=contract_row, obligation_row=None))
            continue
        for obligation_row in obligation_rows:
            explanations.append(contract_outcome_explanation(contract_row=contract_row, obligation_row=obligation_row))
    return explanations


def contract_outcome_explanation(
    *,
    contract_row: Mapping[str, Any],
    obligation_row: Mapping[str, Any] | None,
) -> ContractOutcomeExplanation:
    if obligation_row is None:
        return _contract_only_explanation(contract_row)

    terms = _terms(obligation_row)
    stored = terms.get(CONTRACT_OUTCOME_EXPLANATION_KEY)
    if isinstance(stored, Mapping):
        return ContractOutcomeExplanation.model_validate(stored)

    source_deal_id = contract_row.get("deal_id")
    obligation_id = obligation_row.get("id")
    trigger = _trigger(obligation_row)
    status = str(obligation_row.get("status", "pending"))
    interaction = {
        "policy": dict(DEFAULT_CONTRACT_CLASSIC_RULE_POLICY),
        "policy_key": "impossible_state_prevention",
        "policy_value": DEFAULT_CONTRACT_CLASSIC_RULE_POLICY["impossible_state_prevention"],
        "deterministic": True,
        "detail": "pending obligation has no committed outcome yet",
    }
    decision = {"status": status, "decision": "pending_contract_obligation"}
    effect = {"pending": status == "pending"}
    text = (
        "Contract outcome explanation: "
        f"source deal {source_deal_id} contract {contract_row['id']} obligation {obligation_id} "
        f"has trigger {trigger}; decision pending_contract_obligation; resulting effect {effect}."
    )
    return ContractOutcomeExplanation(
        id=f"{contract_row['id']}:{obligation_id}",
        game_id=contract_row["game_id"],
        source_deal_id=source_deal_id,
        contract_id=contract_row["id"],
        obligation_id=obligation_id,
        obligation_type=str(obligation_row.get("obligation_type", "contract")),
        trigger=trigger,
        classic_rule_interaction=interaction,
        decision=decision,
        resulting_state_effect=effect,
        explanation_text=text,
    )


def contract_outcome_explanation_payload(
    *,
    contract_row: Mapping[str, Any],
    obligation_row: Mapping[str, Any],
    decision: ContractRuleDecision,
    accepted_event_ids: Sequence[str] = (),
) -> dict[str, Any]:
    decision_payload = {
        "status": decision.outcome_status(),
        "decision": decision.decision,
        "reason_code": decision.reason_code,
        "detail": decision.detail,
        "accepted_event_ids": list(accepted_event_ids),
    }
    source_deal_id = contract_row.get("deal_id")
    obligation_id = obligation_row.get("id")
    payload = ContractOutcomeExplanation(
        id=f"{contract_row['id']}:{obligation_id}",
        game_id=contract_row["game_id"],
        source_deal_id=source_deal_id,
        contract_id=contract_row["id"],
        obligation_id=obligation_id,
        obligation_type=str(obligation_row.get("obligation_type", "contract")),
        trigger=dict(decision.trigger),
        classic_rule_interaction=dict(decision.classic_rule_interaction),
        decision=decision_payload,
        resulting_state_effect=dict(decision.resulting_state_effect),
        explanation_text=_explanation_text(
            contract_row=contract_row,
            obligation_row=obligation_row,
            decision=decision,
        ),
    )
    return payload.model_dump(mode="json")


def _contract_only_explanation(contract_row: Mapping[str, Any]) -> ContractOutcomeExplanation:
    effect = {"contract_status": contract_row.get("status")}
    text = (
        "Contract outcome explanation: "
        f"source deal {contract_row.get('deal_id')} contract {contract_row['id']} has no obligations; "
        f"decision contract_record_only; resulting effect {effect}."
    )
    return ContractOutcomeExplanation(
        id=f"{contract_row['id']}:contract",
        game_id=contract_row["game_id"],
        source_deal_id=contract_row.get("deal_id"),
        contract_id=contract_row["id"],
        obligation_id=None,
        obligation_type="contract",
        trigger={"type": "contract_record"},
        classic_rule_interaction={
            "policy": dict(DEFAULT_CONTRACT_CLASSIC_RULE_POLICY),
            "policy_key": "impossible_state_prevention",
            "policy_value": DEFAULT_CONTRACT_CLASSIC_RULE_POLICY["impossible_state_prevention"],
            "deterministic": True,
            "detail": "contract has no obligations to settle",
        },
        decision={"status": contract_row.get("status"), "decision": "contract_record_only"},
        resulting_state_effect=effect,
        explanation_text=text,
    )


def _explanation_text(
    *,
    contract_row: Mapping[str, Any],
    obligation_row: Mapping[str, Any],
    decision: ContractRuleDecision,
) -> str:
    return (
        "Contract outcome explanation: "
        f"source deal {contract_row.get('deal_id')} produced contract {contract_row['id']} "
        f"and obligation {obligation_row['id']} with trigger {decision.trigger}; "
        f"classic-rule interaction {decision.policy_key}={decision.policy_value}; "
        f"decision {decision.decision}; resulting state/effect {decision.resulting_state_effect}."
    )


def _terms(obligation_row: Mapping[str, Any]) -> Mapping[str, Any]:
    terms = obligation_row.get("terms")
    return terms if isinstance(terms, Mapping) else {}


def _trigger(obligation_row: Mapping[str, Any]) -> dict[str, Any]:
    schedule = obligation_row.get("schedule")
    if isinstance(schedule, Mapping):
        trigger = schedule.get("trigger")
        if isinstance(trigger, Mapping):
            return dict(trigger)
    return {"type": "immediate"}


__all__ = [
    "CONTRACT_OUTCOME_EXPLANATION_KEY",
    "ContractOutcomeExplanation",
    "contract_outcome_explanation",
    "contract_outcome_explanation_payload",
    "load_contract_outcome_explanations",
]
