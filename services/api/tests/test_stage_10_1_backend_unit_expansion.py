from __future__ import annotations

import importlib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

import pytest

from app.rules.actions import (
    ActionValidationError,
    GameAction,
    LegalAction,
    list_legal_actions,
    validate_action,
)
from app.rules.debt import outstanding_debt_amount, settle_debt_with_cash
from app.rules.events import (
    ActivePaymentSetPayload,
    BankInventorySetPayload,
    GameEvent,
    PlayerCashDeltaPayload,
    PlayerJailCardsSetPayload,
    PlayerJailSetPayload,
    PlayerPositionSetPayload,
    PropertyImprovementsSetPayload,
    PropertyMortgageSetPayload,
    PropertyOwnerSetPayload,
    TurnStateSetPayload,
)
from app.rules.financial_instruments import (
    INSTRUMENT_PRIMITIVE_KINDS,
    combination_deal,
    create_instrument,
    failure_reason,
    settle_instrument,
    validate_instrument,
)
from app.rules.mechanics import (
    IllegalRuleActionError,
    apply_card_effect,
    apply_dice_roll,
    buy_house,
    calculate_rent,
    close_auction,
    declare_bankruptcy,
    pass_auction,
    pay_jail_fine,
    pay_rent,
    pay_tax_for_space,
    place_auction_bid,
    sell_house,
    send_player_to_jail,
    start_auction,
    use_get_out_of_jail_card,
)
from app.rules.phases import TurnPhase, assert_valid_phase_transition, can_transition_phase
from app.rules.reducer import InvalidEventError, apply_event, replay_events
from app.rules.state import (
    GameState,
    PlayerSetup,
    PlayerState,
    PropertyOwnershipState,
    create_initial_game_state,
)
from app.rules.timing import is_action_allowed_now, timing_issue_for_action


CORE_RULE_MODULES = (
    "actions",
    "atomic",
    "debt",
    "event_capture",
    "events",
    "financial_instruments",
    "mechanics",
    "phases",
    "reducer",
    "rng",
    "simulation",
    "state",
    "static_data",
    "timing",
)

PLAYER_IDS = (
    "11111111-1111-4111-8111-111111111111",
    "22222222-2222-4222-8222-222222222222",
    "33333333-3333-4333-8333-333333333333",
)
PROPERTY_IDS = (
    "property_mediterranean_avenue",
    "property_baltic_avenue",
    "property_reading_railroad",
)

VALID_INSTRUMENT_PAYLOADS: dict[str, dict[str, Any]] = {
    "immediate_cash_transfer": {
        "kind": "immediate_cash_transfer",
        "instrument_id": "cash-now",
        "from_player_id": PLAYER_IDS[0],
        "to_player_id": PLAYER_IDS[1],
        "amount": 50,
    },
    "immediate_property_transfer": {
        "kind": "immediate_property_transfer",
        "instrument_id": "property-now",
        "from_player_id": PLAYER_IDS[1],
        "to_player_id": PLAYER_IDS[0],
        "property_id": PROPERTY_IDS[0],
    },
    "deferred_cash_payment": {
        "kind": "deferred_cash_payment",
        "instrument_id": "cash-later",
        "from_player_id": PLAYER_IDS[0],
        "to_player_id": PLAYER_IDS[1],
        "amount": 80,
        "due_turn": 4,
    },
    "installment_loan": {
        "kind": "installment_loan",
        "instrument_id": "installment-loan",
        "lender_player_id": PLAYER_IDS[1],
        "borrower_player_id": PLAYER_IDS[0],
        "principal_amount": 120,
        "schedule": (
            {"due_turn": 2, "amount": 40},
            {"due_turn": 4, "amount": 40},
            {"due_turn": 6, "amount": 40},
        ),
    },
    "interest_bearing_debt": {
        "kind": "interest_bearing_debt",
        "instrument_id": "interest-debt",
        "lender_player_id": PLAYER_IDS[1],
        "borrower_player_id": PLAYER_IDS[0],
        "principal_amount": 200,
        "interest_rate_percent": 10,
        "due_turn": 8,
    },
    "collateralized_loan": {
        "kind": "collateralized_loan",
        "instrument_id": "collateral-loan",
        "lender_player_id": PLAYER_IDS[1],
        "borrower_player_id": PLAYER_IDS[0],
        "principal_amount": 150,
        "due_turn": 7,
        "collateral_property_ids": (PROPERTY_IDS[1],),
    },
    "property_purchase_option": {
        "kind": "property_purchase_option",
        "instrument_id": "purchase-option",
        "grantor_player_id": PLAYER_IDS[1],
        "holder_player_id": PLAYER_IDS[0],
        "property_id": PROPERTY_IDS[1],
        "strike_price": 220,
        "expiration_turn": 10,
    },
    "rent_share": {
        "kind": "rent_share",
        "instrument_id": "rent-share",
        "from_player_id": PLAYER_IDS[1],
        "to_player_id": PLAYER_IDS[0],
        "property_id": PROPERTY_IDS[0],
        "share_percent": 25,
        "duration_turns": 5,
    },
    "insurance_payout": {
        "kind": "insurance_payout",
        "instrument_id": "insurance",
        "insurer_player_id": PLAYER_IDS[1],
        "insured_player_id": PLAYER_IDS[0],
        "amount": 100,
        "trigger": {"type": "property_landed", "property_id": PROPERTY_IDS[0]},
    },
    "conditional_obligation": {
        "kind": "conditional_obligation",
        "instrument_id": "conditional",
        "obligor_player_id": PLAYER_IDS[0],
        "obligee_player_id": PLAYER_IDS[1],
        "amount": 60,
        "trigger": {"type": "turn_start", "turn": 3},
    },
    "guarantee": {
        "kind": "guarantee",
        "instrument_id": "guarantee",
        "guarantor_player_id": PLAYER_IDS[2],
        "guaranteed_player_id": PLAYER_IDS[0],
        "beneficiary_player_id": PLAYER_IDS[1],
        "amount": 75,
        "target_instrument_id": "interest-debt",
    },
    "default_penalty": {
        "kind": "default_penalty",
        "instrument_id": "default-penalty",
        "liable_player_id": PLAYER_IDS[0],
        "beneficiary_player_id": PLAYER_IDS[1],
        "amount": 30,
        "target_instrument_id": "interest-debt",
    },
}


def _player_setups(count: int = 3) -> tuple[PlayerSetup, ...]:
    return tuple(
        PlayerSetup(
            id=f"player-{index}",
            name=f"Player {index}",
            kind="human" if index == 1 else "ai",
        )
        for index in range(1, count + 1)
    )


def _initial_state(count: int = 3) -> GameState:
    return create_initial_game_state(
        seed="stage-10-1-seed",
        players=_player_setups(count),
        game_id="stage-10-1-game",
    )


def _player(state: GameState, player_id: str) -> PlayerState:
    return next(player for player in state.players if player.id == player_id)


def _property(state: GameState, property_id: str) -> PropertyOwnershipState:
    return next(
        ownership
        for ownership in state.property_ownership
        if ownership.property_id == property_id
    )


def _cash_total(state: GameState) -> int:
    return sum(player.cash for player in state.players)


def _state_in_phase(state: GameState, phase: TurnPhase) -> GameState:
    return GameState.model_validate(
        {
            **state.model_dump(mode="python"),
            "turn": {**state.turn.model_dump(mode="python"), "phase": phase},
        }
    )


def _apply_setup_event(state: GameState, event_type: str, payload: object) -> GameState:
    return apply_event(
        state,
        GameEvent(
            event_id=f"setup-{state.event_sequence + 1}",
            sequence=state.event_sequence + 1,
            type=cast(Any, event_type),
            payload=cast(Any, payload),
        ),
    )


def _set_cash(state: GameState, player_id: str, cash: int) -> GameState:
    return _apply_setup_event(
        state,
        "PLAYER_CASH_DELTA",
        PlayerCashDeltaPayload(player_id=player_id, amount=cash - _player(state, player_id).cash),
    )


def _set_position(state: GameState, player_id: str, position: int) -> GameState:
    return _apply_setup_event(
        state,
        "PLAYER_POSITION_SET",
        PlayerPositionSetPayload(player_id=player_id, position=position),
    )


def _set_jail(state: GameState, player_id: str, in_jail: bool, jail_turns: int = 0) -> GameState:
    return _apply_setup_event(
        state,
        "PLAYER_JAIL_SET",
        PlayerJailSetPayload(player_id=player_id, in_jail=in_jail, jail_turns=jail_turns),
    )


def _set_jail_cards(state: GameState, player_id: str, card_ids: tuple[str, ...]) -> GameState:
    return _apply_setup_event(
        state,
        "PLAYER_JAIL_CARDS_SET",
        PlayerJailCardsSetPayload(player_id=player_id, card_ids=card_ids),
    )


def _own(state: GameState, property_id: str, owner_id: str | None) -> GameState:
    return _apply_setup_event(
        state,
        "PROPERTY_OWNER_SET",
        PropertyOwnerSetPayload(property_id=property_id, owner_id=owner_id),
    )


def _mortgage(state: GameState, property_id: str, mortgaged: bool) -> GameState:
    return _apply_setup_event(
        state,
        "PROPERTY_MORTGAGE_SET",
        PropertyMortgageSetPayload(property_id=property_id, mortgaged=mortgaged),
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
            reason="stage 10.1 payment edge",
            negotiation_allowed=True,
        ),
    )


def _turn_event(state: GameState, phase: TurnPhase | str) -> GameEvent:
    return GameEvent(
        event_id=f"phase-{state.event_sequence + 1}",
        sequence=state.event_sequence + 1,
        type="TURN_STATE_SET",
        payload=TurnStateSetPayload(
            turn_number=state.turn.turn_number,
            current_player_index=state.turn.current_player_index,
            current_player_id=state.turn.current_player_id,
            phase=str(phase),
            consecutive_doubles=state.turn.consecutive_doubles,
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


def _own_brown_group(state: GameState, owner_id: str = "player-1") -> GameState:
    state = _own(state, "property_mediterranean_avenue", owner_id)
    return _own(state, "property_baltic_avenue", owner_id)


def _own_light_blue_group(state: GameState, owner_id: str = "player-1") -> GameState:
    state = _own(state, "property_oriental_avenue", owner_id)
    state = _own(state, "property_vermont_avenue", owner_id)
    return _own(state, "property_connecticut_avenue", owner_id)


def _instrument_ids() -> set[str]:
    return {
        str(payload["instrument_id"])
        for payload in VALID_INSTRUMENT_PAYLOADS.values()
        if "instrument_id" in payload
    }


def _valid_instruments_payloads() -> tuple[dict[str, Any], ...]:
    return tuple(VALID_INSTRUMENT_PAYLOADS[kind] for kind in INSTRUMENT_PRIMITIVE_KINDS)


def test_stage_10_1_reducer_replay_preserves_state_hash_and_event_sequence() -> None:
    players = _player_setups(2)
    initial = create_initial_game_state(
        seed="stage-10-1-replay",
        players=players,
        game_id="stage-10-1-replay-game",
    )
    events = (
        GameEvent(
            event_id="replay-1",
            sequence=1,
            type="PLAYER_CASH_DELTA",
            payload=PlayerCashDeltaPayload(player_id="player-1", amount=-125),
        ),
        GameEvent(
            event_id="replay-2",
            sequence=2,
            type="PLAYER_POSITION_SET",
            payload=PlayerPositionSetPayload(player_id="player-1", position=5),
        ),
        GameEvent(
            event_id="replay-3",
            sequence=3,
            type="PROPERTY_OWNER_SET",
            payload=PropertyOwnerSetPayload(
                property_id="property_reading_railroad",
                owner_id="player-2",
            ),
        ),
        GameEvent(
            event_id="replay-4",
            sequence=4,
            type="TURN_STATE_SET",
            payload=TurnStateSetPayload(
                turn_number=1,
                current_player_index=0,
                current_player_id="player-1",
                phase=TurnPhase.PRE_ROLL_MANAGEMENT.value,
                consecutive_doubles=0,
            ),
        ),
    )

    applied = initial
    for event in events:
        applied = apply_event(applied, event)

    replayed = replay_events(
        seed="stage-10-1-replay",
        players=players,
        game_id="stage-10-1-replay-game",
        events=events,
    )

    assert initial.event_sequence == 0
    assert initial.applied_event_ids == ()
    assert replayed == applied
    assert replayed.state_hash() == applied.state_hash()
    assert replayed.event_sequence == len(events)
    assert replayed.applied_event_ids == tuple(event.event_id for event in events)


def test_stage_10_1_legal_actions_include_expected_timing_guards_for_core_phases() -> None:
    start_state = _initial_state()
    purchase_state = _state_in_phase(
        _set_position(_initial_state(), "player-1", 1),
        TurnPhase.PURCHASE_OR_AUCTION,
    )
    payment_state = _set_active_payment(
        _state_in_phase(_initial_state(), TurnPhase.PAYMENT_RESOLUTION)
    )
    jail_state = _state_in_phase(
        _set_jail_cards(
            _set_jail(_initial_state(), "player-1", True),
            "player-1",
            ("card_community_get_out_of_jail",),
        ),
        TurnPhase.JAIL_RESOLUTION,
    )

    phase_actions = {
        TurnPhase.START_TURN: (start_state, {"ROLL_DICE", "DECLARE_BANKRUPTCY"}),
        TurnPhase.PURCHASE_OR_AUCTION: (
            purchase_state,
            {"BUY_PROPERTY", "START_AUCTION", "DECLARE_BANKRUPTCY"},
        ),
        TurnPhase.PAYMENT_RESOLUTION: (
            payment_state,
            {"SETTLE_DEBT", "DECLARE_BANKRUPTCY"},
        ),
        TurnPhase.JAIL_RESOLUTION: (
            jail_state,
            {"ROLL_DICE", "PAY_JAIL_FINE", "USE_GET_OUT_OF_JAIL_CARD"},
        ),
    }

    for phase, (state, expected_types) in phase_actions.items():
        legal_actions = list_legal_actions(state, "player-1")
        legal_types = _types(legal_actions)
        assert expected_types.issubset(legal_types), phase
        assert all(action.expected_state_hash == state.state_hash() for action in legal_actions)
        assert all(action.expected_event_sequence == state.event_sequence for action in legal_actions)
        assert all(action.schema["type"] == "object" for action in legal_actions)

    pre_roll_state = _state_in_phase(start_state, TurnPhase.PRE_ROLL_MANAGEMENT)
    assert not is_action_allowed_now(pre_roll_state, "ROLL_DICE", actor_id="player-1")
    assert timing_issue_for_action(pre_roll_state, "ROLL_DICE", actor_id="player-1") is not None

    space_state = _state_in_phase(purchase_state, TurnPhase.SPACE_RESOLUTION)
    assert "BUY_PROPERTY" not in _types(list_legal_actions(space_state, "player-1"))
    with pytest.raises(ActionValidationError) as exc_info:
        validate_action(
            space_state,
            _action(
                space_state,
                "player-1",
                "BUY_PROPERTY",
                {"property_id": "property_mediterranean_avenue"},
            ),
        )
    assert _issue_codes(exc_info.value) == {"mistimed_action"}


def test_stage_10_1_phase_transitions_reject_skipped_mandatory_windows() -> None:
    state = _initial_state(2)

    assert can_transition_phase(TurnPhase.START_TURN, TurnPhase.PRE_ROLL_MANAGEMENT)
    assert not can_transition_phase(TurnPhase.START_TURN, TurnPhase.ROLL_REQUIRED)
    with pytest.raises(ValueError, match="invalid phase transition"):
        assert_valid_phase_transition(TurnPhase.START_TURN, TurnPhase.ROLL_REQUIRED)
    with pytest.raises(InvalidEventError, match="invalid phase transition"):
        apply_event(state, _turn_event(state, TurnPhase.ROLL_REQUIRED))

    roll_state = _state_in_phase(state, TurnPhase.ROLL_REQUIRED)
    with pytest.raises(InvalidEventError, match="invalid phase transition"):
        apply_event(roll_state, _turn_event(roll_state, TurnPhase.SPACE_RESOLUTION))

    purchase_state = _state_in_phase(state, TurnPhase.PURCHASE_OR_AUCTION)
    with pytest.raises(InvalidEventError, match="invalid phase transition"):
        apply_event(purchase_state, _turn_event(purchase_state, TurnPhase.END_TURN))

    payment_state = _set_active_payment(_state_in_phase(state, TurnPhase.POST_ROLL_MANAGEMENT))
    with pytest.raises(InvalidEventError, match="active payment"):
        apply_event(payment_state, _turn_event(payment_state, TurnPhase.END_TURN))


def test_stage_10_1_rent_and_payment_edges_keep_cash_conserved() -> None:
    state = _own(_initial_state(), "property_reading_railroad", "player-2")
    state = _own(state, "property_pennsylvania_railroad", "player-2")
    before_cash = _cash_total(state)

    rent_state = pay_rent(state, "player-1", "property_reading_railroad", "rail-rent")

    assert calculate_rent(state, "property_reading_railroad") == 50
    assert _cash_total(rent_state) == before_cash
    assert _player(rent_state, "player-1").cash == _player(state, "player-1").cash - 50
    assert _player(rent_state, "player-2").cash == _player(state, "player-2").cash + 50

    utility_state = _own(rent_state, "property_electric_company", "player-2")
    assert calculate_rent(utility_state, "property_electric_company", dice_total=8) == 32
    with pytest.raises(IllegalRuleActionError, match="dice total"):
        calculate_rent(utility_state, "property_electric_company")
    with pytest.raises(IllegalRuleActionError, match="themselves"):
        pay_rent(utility_state, "player-2", "property_electric_company", "self-rent", dice_total=8)

    debt_state = _set_active_payment(
        _state_in_phase(utility_state, TurnPhase.PAYMENT_RESOLUTION),
        amount_owed=120,
        amount_paid=20,
    )
    before_debt_cash = _cash_total(debt_state)
    settled = settle_debt_with_cash(debt_state, "player-1", 100, "debt")

    assert _cash_total(settled) == before_debt_cash
    assert settled.active_payment is None
    assert outstanding_debt_amount(settled) == 0

    tax_state = pay_tax_for_space(settled, "player-1", "space_luxury_tax", "tax")
    assert _player(tax_state, "player-1").cash == _player(settled, "player-1").cash - 100
    with pytest.raises(IllegalRuleActionError, match="not a tax"):
        pay_tax_for_space(tax_state, "player-1", "space_go", "not-tax")


def test_stage_10_1_card_effects_cover_tax_repairs_nearest_and_jail_cards() -> None:
    state = _own_brown_group(_initial_state(), "player-1")
    state = _own(state, "property_reading_railroad", "player-2")
    state = _improve(state, "property_mediterranean_avenue", 2)
    state = _improve(state, "property_baltic_avenue", 0, hotel=True)
    state = _set_position(state, "player-1", 36)

    nearest_utility = apply_card_effect(
        state,
        "player-1",
        "card_chance_nearest_utility",
        "nearest-utility",
    )
    assert _player(nearest_utility, "player-1").position == 12

    nearest_railroad = apply_card_effect(
        state,
        "player-1",
        "card_chance_nearest_railroad_a",
        "nearest-rail",
    )
    assert _player(nearest_railroad, "player-1").position == 5
    assert _player(nearest_railroad, "player-1").cash == 1450
    assert _player(nearest_railroad, "player-2").cash == 1550

    repaired = apply_card_effect(
        state,
        "player-1",
        "card_community_street_repairs",
        "repairs",
    )
    assert _player(repaired, "player-1").cash == 1305

    taxed = apply_card_effect(repaired, "player-1", "card_community_hospital_fee", "fee")
    assert _player(taxed, "player-1").cash == 1205

    jail_card = apply_card_effect(taxed, "player-1", "card_chance_get_out_of_jail", "jail-card")
    assert _player(jail_card, "player-1").get_out_of_jail_card_ids == (
        "card_chance_get_out_of_jail",
    )
    jailed = apply_card_effect(jail_card, "player-1", "card_community_go_to_jail", "go-jail")
    assert _player(jailed, "player-1").position == 10
    assert _player(jailed, "player-1").in_jail


def test_stage_10_1_house_hotel_inventory_and_even_building_edges() -> None:
    state = _own_brown_group(_initial_state(), "player-1")
    state = buy_house(state, "player-1", "property_mediterranean_avenue", "build")
    assert _property(state, "property_mediterranean_avenue").houses == 1
    assert state.bank_inventory.houses == 31

    with pytest.raises(IllegalRuleActionError, match="even"):
        buy_house(state, "player-1", "property_mediterranean_avenue", "build-again")

    state = _improve(state, "property_mediterranean_avenue", 4)
    state = _improve(state, "property_baltic_avenue", 4)
    state = _set_bank_inventory(state, houses=24, hotels=1)
    hotel_state = buy_house(state, "player-1", "property_baltic_avenue", "hotel")

    assert _property(hotel_state, "property_baltic_avenue").hotel
    assert _property(hotel_state, "property_baltic_avenue").houses == 0
    assert hotel_state.bank_inventory.houses == 28
    assert hotel_state.bank_inventory.hotels == 0

    with pytest.raises(IllegalRuleActionError, match="hotels"):
        buy_house(hotel_state, "player-1", "property_mediterranean_avenue", "no-hotels")

    with pytest.raises(IllegalRuleActionError, match="four houses"):
        sell_house(
            _set_bank_inventory(hotel_state, houses=3, hotels=11),
            "player-1",
            "property_baltic_avenue",
            "sell-hotel",
        )

    sold_hotel = sell_house(
        _set_bank_inventory(hotel_state, houses=4, hotels=11),
        "player-1",
        "property_baltic_avenue",
        "sell-hotel",
    )
    assert _property(sold_hotel, "property_baltic_avenue").houses == 4
    assert not _property(sold_hotel, "property_baltic_avenue").hotel
    assert sold_hotel.bank_inventory.houses == 0
    assert sold_hotel.bank_inventory.hotels == 12


def test_stage_10_1_auction_edges_reject_self_overbid_and_close_no_bid() -> None:
    state = start_auction(_initial_state(), "property_oriental_avenue", "auction")
    state = place_auction_bid(state, "player-1", 90, "auction")

    with pytest.raises(IllegalRuleActionError, match="own high bid"):
        place_auction_bid(state, "player-1", 91, "self-overbid")

    with pytest.raises(ActionValidationError) as exc_info:
        validate_action(state, _action(state, "player-1", "BID_AUCTION", {"amount": 91}))
    assert _issue_codes(exc_info.value) == {"illegal_action"}
    assert "BID_AUCTION" not in _types(list_legal_actions(state, "player-1"))

    passed = pass_auction(state, "player-2", "pass")
    resolved = pass_auction(passed, "player-3", "pass")
    closed = close_auction(resolved, "close")

    assert closed.active_auction is None
    assert _property(closed, "property_oriental_avenue").owner_id == "player-1"
    assert _player(closed, "player-1").cash == 1410

    no_bid_closed = close_auction(
        start_auction(_initial_state(), "property_vermont_avenue", "no-bid"),
        "no-bid-close",
    )
    assert no_bid_closed.active_auction is None
    assert _property(no_bid_closed, "property_vermont_avenue").owner_id is None


def test_stage_10_1_jail_edges_cover_third_failed_roll_card_and_fine() -> None:
    state = send_player_to_jail(_initial_state(), "player-1", "jail")
    state = apply_dice_roll(state, "player-1", 1, 2, "failed-roll")
    state = apply_dice_roll(state, "player-1", 3, 4, "failed-roll")

    assert _player(state, "player-1").in_jail
    assert _player(state, "player-1").jail_turns == 2

    released_by_third_failure = apply_dice_roll(state, "player-1", 2, 3, "third-failure")
    assert _player(released_by_third_failure, "player-1").cash == 1450
    assert _player(released_by_third_failure, "player-1").position == 15
    assert not _player(released_by_third_failure, "player-1").in_jail

    fine_state = pay_jail_fine(
        send_player_to_jail(_initial_state(), "player-1", "jail-fine"),
        "player-1",
        "fine",
    )
    assert _player(fine_state, "player-1").cash == 1450
    assert not _player(fine_state, "player-1").in_jail

    card_state = _set_jail_cards(
        send_player_to_jail(_initial_state(), "player-1", "jail-card"),
        "player-1",
        ("card_community_get_out_of_jail",),
    )
    card_state = use_get_out_of_jail_card(
        card_state,
        "player-1",
        "card_community_get_out_of_jail",
        "use-card",
    )
    assert _player(card_state, "player-1").get_out_of_jail_card_ids == ()
    assert not _player(card_state, "player-1").in_jail


def test_stage_10_1_bankruptcy_edges_transfer_or_liquidate_assets() -> None:
    creditor_state = _own_brown_group(_initial_state(), "player-1")
    creditor_state = _mortgage(creditor_state, "property_mediterranean_avenue", True)
    creditor_state = _set_jail_cards(creditor_state, "player-1", ("card_chance_get_out_of_jail",))
    creditor_state = _set_cash(creditor_state, "player-1", 140)

    transferred = declare_bankruptcy(creditor_state, "player-1", "player-2", "bankrupt")

    assert _player(transferred, "player-1").is_bankrupt
    assert _player(transferred, "player-1").cash == 0
    assert _player(transferred, "player-2").cash == 1640
    assert _player(transferred, "player-1").get_out_of_jail_card_ids == ()
    assert _property(transferred, "property_mediterranean_avenue").owner_id == "player-2"
    assert _property(transferred, "property_mediterranean_avenue").mortgaged

    bank_state = _own_brown_group(_initial_state(), "player-1")
    bank_state = _improve(bank_state, "property_mediterranean_avenue", 2)
    bank_state = _improve(bank_state, "property_baltic_avenue", 0, hotel=True)
    bank_state = _mortgage(bank_state, "property_mediterranean_avenue", True)
    bank_state = _set_bank_inventory(bank_state, houses=26, hotels=11)

    liquidated = declare_bankruptcy(bank_state, "player-1", None, "bank-bankrupt")

    assert _player(liquidated, "player-1").is_bankrupt
    assert _player(liquidated, "player-1").cash == 0
    assert _property(liquidated, "property_mediterranean_avenue").owner_id is None
    assert not _property(liquidated, "property_mediterranean_avenue").mortgaged
    assert _property(liquidated, "property_mediterranean_avenue").houses == 0
    assert _property(liquidated, "property_baltic_avenue").owner_id is None
    assert not _property(liquidated, "property_baltic_avenue").hotel
    assert liquidated.bank_inventory.houses == 28
    assert liquidated.bank_inventory.hotels == 12


def test_stage_10_1_contract_primitives_cover_required_validation_edges() -> None:
    assert tuple(VALID_INSTRUMENT_PAYLOADS) == INSTRUMENT_PRIMITIVE_KINDS

    instruments, errors = combination_deal(
        _valid_instruments_payloads(),
        player_ids=PLAYER_IDS,
        property_ids=PROPERTY_IDS,
        field="terms",
    )

    assert errors == []
    assert [instrument.kind for instrument in instruments] == list(INSTRUMENT_PRIMITIVE_KINDS)
    for instrument in instruments:
        validation_errors = validate_instrument(
            instrument,
            player_ids=PLAYER_IDS,
            property_ids=PROPERTY_IDS,
            instrument_ids=_instrument_ids(),
        )
        assert validation_errors == []
        settlement = settle_instrument(
            instrument,
            player_ids=PLAYER_IDS,
            property_ids=PROPERTY_IDS,
            instrument_ids=_instrument_ids(),
        )
        assert settlement.status == "planned"
        assert settlement.failure_reason is None

    invalid_payloads: Sequence[Mapping[str, Any]] = (
        {**VALID_INSTRUMENT_PAYLOADS["immediate_cash_transfer"], "amount": 0},
        {
            **VALID_INSTRUMENT_PAYLOADS["installment_loan"],
            "schedule": ({"due_turn": 4, "amount": 40}, {"due_turn": 2, "amount": 40}),
        },
        {**VALID_INSTRUMENT_PAYLOADS["rent_share"], "share_percent": 101},
        {
            **VALID_INSTRUMENT_PAYLOADS["guarantee"],
            "instrument_id": "self-reference",
            "target_instrument_id": "self-reference",
        },
        {
            **VALID_INSTRUMENT_PAYLOADS["collateralized_loan"],
            "collateral_property_ids": (PROPERTY_IDS[1], PROPERTY_IDS[1]),
        },
    )
    invalid_instruments, invalid_errors = combination_deal(
        invalid_payloads,
        player_ids=PLAYER_IDS,
        property_ids=PROPERTY_IDS,
        field="terms",
    )

    assert len(invalid_instruments) == len(invalid_payloads)
    assert {error.code for error in invalid_errors} == {"invalid_instrument"}
    assert {
        "terms.0.amount",
        "terms.1.schedule.1.due_turn",
        "terms.2.share_percent",
        "terms.3.target_instrument_id",
        "terms.4.collateral_property_ids.1",
    }.issubset({error.field for error in invalid_errors})
    assert failure_reason(invalid_errors)

    unknown = create_instrument({"kind": "not_real", "instrument_id": "unknown"})
    unknown_errors = validate_instrument(
        unknown,
        player_ids=PLAYER_IDS,
        property_ids=PROPERTY_IDS,
        instrument_ids=_instrument_ids(),
    )
    assert unknown_errors
    assert settle_instrument(
        unknown,
        player_ids=PLAYER_IDS,
        property_ids=PROPERTY_IDS,
        instrument_ids=_instrument_ids(),
    ).status == "failed"


def test_stage_10_1_core_rule_coverage_manifest_lists_all_modules() -> None:
    rules_dir = Path(__file__).resolve().parents[1] / "app" / "rules"
    discovered_modules = tuple(
        sorted(path.stem for path in rules_dir.glob("*.py") if path.name != "__init__.py")
    )

    assert discovered_modules == CORE_RULE_MODULES
    for module_name in CORE_RULE_MODULES:
        module = importlib.import_module(f"app.rules.{module_name}")
        assert module.__name__ == f"app.rules.{module_name}"
