from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.metadata import contracts, deals, negotiation_messages, obligations


AUDIT_CONTRACT_DEFAULTED = "CONTRACT_DEFAULTED"


@dataclass(frozen=True)
class ContractDefault:
    contract_id: UUID
    obligation_id: UUID
    source_deal_id: UUID | None
    reason_code: str
    detail: str


async def default_handling(
    *,
    session: AsyncSession,
    contract_row: Mapping[str, Any],
    obligation_row: Mapping[str, Any],
    reason_code: str,
    detail: str,
) -> ContractDefault:
    contract_id = contract_row["id"]
    obligation_id = obligation_row["id"]
    source_deal_id = contract_row["deal_id"]
    schedule = obligation_row["schedule"] if isinstance(obligation_row["schedule"], Mapping) else {}
    terms = obligation_row["terms"] if isinstance(obligation_row["terms"], Mapping) else {}
    default_payload = {
        "defaulted_at": "defaulted",
        "reason_code": reason_code,
        "detail": detail,
    }
    await session.execute(
        obligations.update()
        .where(obligations.c.id == obligation_id)
        .values(
            status="defaulted",
            schedule={**dict(schedule), **default_payload},
            terms={**dict(terms), "default": default_payload},
            updated_at=sa.func.now(),
        )
    )
    await session.execute(
        contracts.update()
        .where(contracts.c.id == contract_id)
        .values(status="defaulted", updated_at=sa.func.now())
    )
    await _insert_default_audit(
        session=session,
        contract_row=contract_row,
        obligation_row=obligation_row,
        reason_code=reason_code,
        detail=detail,
    )
    return ContractDefault(
        contract_id=contract_id,
        obligation_id=obligation_id,
        source_deal_id=source_deal_id,
        reason_code=reason_code,
        detail=detail,
    )


async def _insert_default_audit(
    *,
    session: AsyncSession,
    contract_row: Mapping[str, Any],
    obligation_row: Mapping[str, Any],
    reason_code: str,
    detail: str,
) -> None:
    deal_id = contract_row["deal_id"]
    negotiation_id: UUID | None = None
    if deal_id is not None:
        result = await session.execute(sa.select(deals.c.negotiation_id).where(deals.c.id == deal_id))
        negotiation_id = result.scalar_one_or_none()
    if negotiation_id is None:
        return
    await session.execute(
        negotiation_messages.insert().values(
            game_id=contract_row["game_id"],
            negotiation_id=negotiation_id,
            sender_player_id=None,
            recipient_player_id=None,
            message_type=AUDIT_CONTRACT_DEFAULTED,
            body=None,
            payload={
                "contract_id": str(contract_row["id"]),
                "obligation_id": str(obligation_row["id"]),
                "source_deal_id": None if deal_id is None else str(deal_id),
                "reason_code": reason_code,
                "detail": detail,
                "deterministic": True,
                "no_substitute_action": True,
            },
        )
    )


__all__ = [
    "AUDIT_CONTRACT_DEFAULTED",
    "ContractDefault",
    "default_handling",
]
