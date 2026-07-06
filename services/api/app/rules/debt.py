from __future__ import annotations

from dataclasses import dataclass
from typing import Final, cast

from app.rules.events import (
    ActivePaymentSetPayload,
    GameEvent,
    GameEventPayload,
    GameEventType,
    PlayerCashDeltaPayload,
)
from app.rules.reducer import apply_event
from app.rules.state import GameState, PlayerState


DEBT_LIQUIDATION_ACTION_TYPES: Final[frozenset[str]] = frozenset(
    {
        "SETTLE_DEBT",
        "SELL_HOUSE",
        "MORTGAGE_PROPERTY",
        "DECLARE_BANKRUPTCY",
    }
)
DEBT_FORBIDDEN_ACTION_TYPES: Final[frozenset[str]] = frozenset(
    {
        "BUY_HOUSE",
        "UNMORTGAGE_PROPERTY",
        "ROLL_DICE",
        "BUY_PROPERTY",
        "START_AUCTION",
        "BID_AUCTION",
        "PASS_AUCTION",
        "PAY_JAIL_FINE",
        "USE_GET_OUT_OF_JAIL_CARD",
        "END_TURN",
        "PROPOSE_DEAL",
        "COUNTER_DEAL",
        "ACCEPT_DEAL",
        "REJECT_DEAL",
    }
)


class DebtPolicyError(ValueError):
    """Raised when a debt settlement request violates debt policy."""


@dataclass(frozen=True, slots=True)
class DebtActionIssue:
    code: str
    message: str
    field: str | None = None

    def model_dump(self, *, mode: str = "python") -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "field": self.field,
        }


class _EventStream:
    def __init__(self, event_id_prefix: str) -> None:
        if not event_id_prefix:
            raise DebtPolicyError("event id prefix is required")
        self.event_id_prefix = event_id_prefix

    def apply(self, state: GameState, event_type: str, payload: GameEventPayload) -> GameState:
        next_sequence = state.event_sequence + 1
        event = GameEvent(
            event_id=f"{self.event_id_prefix}-{next_sequence}",
            sequence=next_sequence,
            type=cast(GameEventType, event_type),
            payload=payload,
        )
        return apply_event(state, event)


def outstanding_debt_amount(state: GameState) -> int:
    active_payment = state.active_payment
    if active_payment is None:
        return 0
    return max(active_payment.amount_owed - active_payment.amount_paid, 0)


def is_debt_active(state: GameState) -> bool:
    return state.active_payment is not None


def is_debt_settled(state: GameState) -> bool:
    return state.active_payment is None or outstanding_debt_amount(state) == 0


def debt_allows_negotiation(state: GameState) -> bool:
    active_payment = state.active_payment
    return False if active_payment is None else active_payment.negotiation_allowed


def debt_issue_for_action(
    state: GameState,
    action_type: str,
    actor_id: str | None = None,
) -> DebtActionIssue | None:
    active_payment = state.active_payment
    if active_payment is None:
        if action_type == "SETTLE_DEBT":
            return DebtActionIssue(
                code="mistimed_action",
                message="SETTLE_DEBT requires an active debt",
                field="type",
            )
        return None

    if actor_id is not None and actor_id != active_payment.debtor_id:
        return DebtActionIssue(
            code="mistimed_action",
            message=f"{actor_id} cannot act while {active_payment.debtor_id} has unresolved debt",
            field="actor_id",
        )

    if action_type not in DEBT_LIQUIDATION_ACTION_TYPES:
        return DebtActionIssue(
            code="mistimed_action",
            message=f"{action_type} is not legal while unresolved debt is active",
            field="type",
        )

    return None


def settle_debt_with_cash(
    state: GameState,
    debtor_id: str,
    amount: int,
    event_id_prefix: str,
) -> GameState:
    active_payment = state.active_payment
    if active_payment is None:
        raise DebtPolicyError("there is no active debt")
    if debtor_id != active_payment.debtor_id:
        raise DebtPolicyError("only the active debtor may settle debt")
    if amount <= 0:
        raise DebtPolicyError("settlement amount must be positive")

    debtor = _player_by_id(state, debtor_id)
    outstanding = outstanding_debt_amount(state)
    if amount > debtor.cash:
        raise DebtPolicyError("settlement amount exceeds debtor cash")
    if amount > outstanding:
        raise DebtPolicyError("settlement amount exceeds outstanding debt")

    stream = _EventStream(event_id_prefix)
    state = stream.apply(
        state,
        "PLAYER_CASH_DELTA",
        PlayerCashDeltaPayload(player_id=debtor_id, amount=-amount),
    )
    if active_payment.creditor_id is not None:
        _player_by_id(state, active_payment.creditor_id)
        state = stream.apply(
            state,
            "PLAYER_CASH_DELTA",
            PlayerCashDeltaPayload(player_id=active_payment.creditor_id, amount=amount),
        )

    next_amount_paid = active_payment.amount_paid + amount
    if next_amount_paid >= active_payment.amount_owed:
        return clear_active_debt(state, event_id_prefix)

    return stream.apply(
        state,
        "ACTIVE_PAYMENT_SET",
        ActivePaymentSetPayload(
            active=True,
            debtor_id=active_payment.debtor_id,
            creditor_id=active_payment.creditor_id,
            amount_owed=active_payment.amount_owed,
            amount_paid=next_amount_paid,
            reason=active_payment.reason,
            negotiation_allowed=active_payment.negotiation_allowed,
        ),
    )


def clear_active_debt(state: GameState, event_id_prefix: str) -> GameState:
    if state.active_payment is None:
        return state
    return _EventStream(event_id_prefix).apply(
        state,
        "ACTIVE_PAYMENT_SET",
        ActivePaymentSetPayload(active=False),
    )


def _player_by_id(state: GameState, player_id: str) -> PlayerState:
    for player in state.players:
        if player.id == player_id:
            return player
    raise DebtPolicyError(f"unknown player {player_id}")


__all__ = [
    "DEBT_FORBIDDEN_ACTION_TYPES",
    "DEBT_LIQUIDATION_ACTION_TYPES",
    "debt_allows_negotiation",
    "debt_issue_for_action",
    "is_debt_active",
    "is_debt_settled",
    "outstanding_debt_amount",
    "settle_debt_with_cash",
]
