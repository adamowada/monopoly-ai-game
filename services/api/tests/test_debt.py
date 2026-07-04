from __future__ import annotations

from collections.abc import Mapping

import pytest
from pydantic import ValidationError

from app.rules.actions import (
    ActionValidationError,
    GameAction,
    LegalAction,
    apply_action,
    list_legal_actions,
    validate_action,
)
from app.rules.debt import (
    DEBT_FORBIDDEN_ACTION_TYPES,
    DEBT_LIQUIDATION_ACTION_TYPES,
    debt_allows_negotiation,
    debt_issue_for_action,
    is_debt_active,
    is_debt_settled,
    outstanding_debt_amount,
)
from app.rules.events import (
    ActivePaymentSetPayload,
    BankInventorySetPayload,
    GameEvent,
    PlayerCashDeltaPayload,
    PropertyImprovementsSetPayload,
    PropertyOwnerSetPayload,
    TurnStateSetPayload,
)
from app.rules.phases import TurnPhase
from app.rules.reducer import InvalidEventError, apply_event
from app.rules.state import GameState, PlayerSetup, create_initial_game_state


def _player_setups() -> tuple[PlayerSetup, ...]:
    return (
        PlayerSetup(id="player-1", name="Player 1", kind="human"),
        PlayerSetup(id="player-2", name="Player 2", kind="ai"),
        PlayerSetup(id="player-3", name="Player 3", kind="ai"),
    )


def _initial_state() -> GameState:
    return create_initial_game_state(seed="debt-seed", players=_player_setups(), game_id="debt-game")


def _player(state: GameState, player_id: str):
    return next(player for player in state.players if player.id == player_id)


def _property(state: GameState, property_id: str):
    return next(ownership for ownership in state.property_ownership if ownership.property_id == property_id)


def _apply_setup_event(state: GameState, event_type: str, payload: object) -> GameState:
    return apply_event(
        state,
        GameEvent(
            event_id=f"setup-{state.event_sequence + 1}",
            sequence=state.event_sequence + 1,
            type=event_type,  # type: ignore[arg-type]
            payload=payload,  # type: ignore[arg-type]
        ),
    )


def _state_in_phase(state: GameState, phase: TurnPhase) -> GameState:
    return GameState.model_validate(
        {
            **state.model_dump(mode="python"),
            "turn": {
                **state.turn.model_dump(mode="python"),
                "phase": phase,
            },
        }
    )


def _set_cash(state: GameState, player_id: str, cash: int) -> GameState:
    return _apply_setup_event(
        state,
        "PLAYER_CASH_DELTA",
        PlayerCashDeltaPayload(player_id=player_id, amount=cash - _player(state, player_id).cash),
    )


def _own(state: GameState, property_id: str, owner_id: str | None) -> GameState:
    return _apply_setup_event(
        state,
        "PROPERTY_OWNER_SET",
        PropertyOwnerSetPayload(property_id=property_id, owner_id=owner_id),
    )


def _improve(state: GameState, property_id: str, houses: int, hotel: bool = False) -> GameState:
    return _apply_setup_event(
        state,
        "PROPERTY_IMPROVEMENTS_SET",
        PropertyImprovementsSetPayload(property_id=property_id, houses=houses, hotel=hotel),
    )


def _set_bank_inventory(state: GameState, houses: int, hotels: int) -> GameState:
    return _apply_setup_event(
        state,
        "BANK_INVENTORY_SET",
        BankInventorySetPayload(houses=houses, hotels=hotels),
    )


def _set_active_payment(
    state: GameState,
    *,
    debtor_id: str = "player-1",
    creditor_id: str | None = "player-2",
    amount_owed: int = 100,
    amount_paid: int = 20,
    reason: str = "rent",
    negotiation_allowed: bool = True,
) -> GameState:
    return _apply_setup_event(
        state,
        "ACTIVE_PAYMENT_SET",
        ActivePaymentSetPayload(
            active=True,
            debtor_id=debtor_id,
            creditor_id=creditor_id,
            amount_owed=amount_owed,
            amount_paid=amount_paid,
            reason=reason,
            negotiation_allowed=negotiation_allowed,
        ),
    )


def _action(
    state: GameState,
    actor_id: str,
    action_type: str,
    payload: Mapping[str, object] | None = None,
) -> GameAction:
    return GameAction(
        actor_id=actor_id,
        type=action_type,
        payload={} if payload is None else dict(payload),
        expected_state_hash=state.state_hash(),
        expected_event_sequence=state.event_sequence,
    )


def _types(actions: tuple[LegalAction, ...]) -> set[str]:
    return {action.type for action in actions}


def _issue_codes(exc: ActionValidationError) -> set[str]:
    return {issue.code for issue in exc.errors}


def _debt_management_state() -> GameState:
    state = _initial_state()
    state = _own(state, "property_oriental_avenue", "player-1")
    state = _own(state, "property_vermont_avenue", "player-1")
    state = _own(state, "property_connecticut_avenue", "player-1")
    state = _own(state, "property_reading_railroad", "player-1")
    state = _improve(state, "property_oriental_avenue", 1)
    state = _set_bank_inventory(state, houses=31, hotels=12)
    state = _state_in_phase(state, TurnPhase.PAYMENT_RESOLUTION)
    return _set_active_payment(state)


def test_active_payment_payload_requires_details_only_when_active() -> None:
    payload = ActivePaymentSetPayload(
        active=True,
        debtor_id="player-1",
        creditor_id=None,
        amount_owed=100,
        amount_paid=0,
        reason="tax",
        negotiation_allowed=False,
    )

    assert payload.debtor_id == "player-1"
    assert payload.creditor_id is None
    assert payload.amount_owed == 100
    assert payload.amount_paid == 0

    with pytest.raises(ValidationError, match="active payment payload must include"):
        ActivePaymentSetPayload(active=True, debtor_id="player-1", amount_owed=100, reason="tax")

    with pytest.raises(ValidationError, match="inactive payment payload cannot include debt details"):
        ActivePaymentSetPayload(active=False, debtor_id="player-1")

    with pytest.raises(ValidationError, match="creditor cannot match debtor"):
        ActivePaymentSetPayload(
            active=True,
            debtor_id="player-1",
            creditor_id="player-1",
            amount_owed=100,
            amount_paid=0,
            reason="rent",
            negotiation_allowed=True,
        )

    with pytest.raises(ValidationError, match="amount_paid cannot exceed amount_owed"):
        ActivePaymentSetPayload(
            active=True,
            debtor_id="player-1",
            creditor_id=None,
            amount_owed=100,
            amount_paid=101,
            reason="tax",
            negotiation_allowed=False,
        )


def test_reducer_creates_clears_and_validates_active_payment_state() -> None:
    state = _set_active_payment(_initial_state(), creditor_id=None, amount_owed=200, amount_paid=50)

    assert state.active_payment is not None
    assert state.active_payment.debtor_id == "player-1"
    assert state.active_payment.creditor_id is None
    assert state.active_payment.amount_owed == 200
    assert state.active_payment.amount_paid == 50
    assert outstanding_debt_amount(state) == 150
    assert is_debt_active(state)
    assert not is_debt_settled(state)
    assert debt_allows_negotiation(state)

    cleared = _apply_setup_event(state, "ACTIVE_PAYMENT_SET", ActivePaymentSetPayload(active=False))

    assert cleared.active_payment is None
    assert outstanding_debt_amount(cleared) == 0
    assert not is_debt_active(cleared)
    assert is_debt_settled(cleared)
    assert not debt_allows_negotiation(cleared)

    with pytest.raises(InvalidEventError, match="unknown debtor"):
        _set_active_payment(_initial_state(), debtor_id="missing-player")

    with pytest.raises(InvalidEventError, match="unknown creditor"):
        _set_active_payment(_initial_state(), creditor_id="missing-player")


def test_reducer_rejects_turn_end_or_start_transition_while_debt_is_unresolved() -> None:
    state = _set_active_payment(_state_in_phase(_initial_state(), TurnPhase.POST_ROLL_MANAGEMENT))

    with pytest.raises(InvalidEventError, match="active payment"):
        _apply_setup_event(
            state,
            "TURN_STATE_SET",
            TurnStateSetPayload(
                turn_number=state.turn.turn_number,
                current_player_index=state.turn.current_player_index,
                current_player_id=state.turn.current_player_id,
                phase="END_TURN",
                consecutive_doubles=state.turn.consecutive_doubles,
            ),
        )

    end_state = _state_in_phase(state, TurnPhase.END_TURN)
    with pytest.raises(InvalidEventError, match="active payment"):
        _apply_setup_event(
            end_state,
            "TURN_STATE_SET",
            TurnStateSetPayload(
                turn_number=end_state.turn.turn_number + 1,
                current_player_index=1,
                current_player_id="player-2",
                phase="START_TURN",
                consecutive_doubles=0,
            ),
        )


def test_debt_policy_limits_legal_actions_to_debtor_liquidation_options() -> None:
    state = _debt_management_state()

    assert DEBT_LIQUIDATION_ACTION_TYPES == frozenset(
        {"SETTLE_DEBT", "SELL_HOUSE", "MORTGAGE_PROPERTY", "DECLARE_BANKRUPTCY"}
    )
    assert {
        "BUY_HOUSE",
        "UNMORTGAGE_PROPERTY",
        "ROLL_DICE",
        "BUY_PROPERTY",
        "START_AUCTION",
        "BID_AUCTION",
        "PASS_AUCTION",
        "PAY_JAIL_FINE",
        "USE_GET_OUT_OF_JAIL_CARD",
        "PROPOSE_DEAL",
        "COUNTER_DEAL",
        "ACCEPT_DEAL",
        "REJECT_DEAL",
    }.issubset(DEBT_FORBIDDEN_ACTION_TYPES)

    debtor_types = _types(list_legal_actions(state, "player-1"))
    assert {"SETTLE_DEBT", "SELL_HOUSE", "MORTGAGE_PROPERTY", "DECLARE_BANKRUPTCY"}.issubset(debtor_types)
    assert "BUY_HOUSE" not in debtor_types
    assert "UNMORTGAGE_PROPERTY" not in debtor_types
    assert list_legal_actions(state, "player-2") == ()

    buy_issue = debt_issue_for_action(state, "BUY_HOUSE", "player-1")
    assert buy_issue is not None
    assert buy_issue.code == "mistimed_action"
    assert buy_issue.field == "type"

    with pytest.raises(ActionValidationError) as buy_exc:
        validate_action(
            state,
            _action(state, "player-1", "BUY_HOUSE", {"property_id": "property_vermont_avenue"}),
        )
    assert _issue_codes(buy_exc.value) == {"mistimed_action"}

    with pytest.raises(ActionValidationError) as deal_exc:
        validate_action(state, _action(state, "player-1", "PROPOSE_DEAL"))
    assert _issue_codes(deal_exc.value) == {"mistimed_action"}

    with pytest.raises(ActionValidationError) as non_debtor_exc:
        validate_action(state, _action(state, "player-2", "ROLL_DICE"))
    assert _issue_codes(non_debtor_exc.value) == {"mistimed_action"}


def test_settle_debt_transfers_cash_to_creditor_and_clears_when_paid() -> None:
    state = _set_active_payment(_state_in_phase(_initial_state(), TurnPhase.PAYMENT_RESOLUTION))

    partial = apply_action(state, _action(state, "player-1", "SETTLE_DEBT", {"amount": 50}), "settle")

    assert _player(partial, "player-1").cash == 1450
    assert _player(partial, "player-2").cash == 1550
    assert partial.active_payment is not None
    assert partial.active_payment.amount_paid == 70
    assert outstanding_debt_amount(partial) == 30

    settled = apply_action(partial, _action(partial, "player-1", "SETTLE_DEBT", {"amount": 30}), "settle")

    assert _player(settled, "player-1").cash == 1420
    assert _player(settled, "player-2").cash == 1580
    assert settled.active_payment is None
    assert is_debt_settled(settled)


def test_settle_bank_debt_only_reduces_debtor_cash() -> None:
    state = _set_active_payment(
        _state_in_phase(_initial_state(), TurnPhase.PAYMENT_RESOLUTION),
        creditor_id=None,
        amount_owed=60,
        amount_paid=0,
        negotiation_allowed=False,
    )

    settled = apply_action(state, _action(state, "player-1", "SETTLE_DEBT", {"amount": 60}), "bank-debt")

    assert _player(settled, "player-1").cash == 1440
    assert _player(settled, "player-2").cash == 1500
    assert settled.active_payment is None


def test_settle_debt_rejects_non_debtor_and_overpayment() -> None:
    state = _set_active_payment(_state_in_phase(_initial_state(), TurnPhase.PAYMENT_RESOLUTION))
    low_cash_state = _set_cash(state, "player-1", 10)

    with pytest.raises(ActionValidationError) as non_debtor_exc:
        validate_action(state, _action(state, "player-2", "SETTLE_DEBT", {"amount": 10}))
    assert _issue_codes(non_debtor_exc.value) == {"mistimed_action"}

    with pytest.raises(ActionValidationError) as cash_exc:
        validate_action(low_cash_state, _action(low_cash_state, "player-1", "SETTLE_DEBT", {"amount": 20}))
    assert _issue_codes(cash_exc.value) == {"illegal_action"}

    with pytest.raises(ActionValidationError) as debt_exc:
        validate_action(state, _action(state, "player-1", "SETTLE_DEBT", {"amount": 81}))
    assert _issue_codes(debt_exc.value) == {"illegal_action"}

    with pytest.raises(ActionValidationError) as malformed_exc:
        validate_action(state, _action(state, "player-1", "SETTLE_DEBT", {"amount": 0}))
    assert _issue_codes(malformed_exc.value) == {"malformed_action"}


def test_bankruptcy_uses_active_debt_creditor_when_payload_omits_creditor_and_clears_debt() -> None:
    state = _initial_state()
    state = _own(state, "property_reading_railroad", "player-1")
    state = _set_cash(state, "player-1", 100)
    state = _state_in_phase(state, TurnPhase.PAYMENT_RESOLUTION)
    state = _set_active_payment(state, creditor_id="player-2", amount_owed=200, amount_paid=0)

    bankrupt = apply_action(state, _action(state, "player-1", "DECLARE_BANKRUPTCY"), "bankruptcy")

    assert _player(bankrupt, "player-1").is_bankrupt
    assert _player(bankrupt, "player-1").cash == 0
    assert _player(bankrupt, "player-2").cash == 1600
    assert _property(bankrupt, "property_reading_railroad").owner_id == "player-2"
    assert bankrupt.active_payment is None
