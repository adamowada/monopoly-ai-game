from __future__ import annotations

import json
from collections.abc import Mapping

import pytest

from app.rules.actions import (
    ActionValidationError,
    GameAction,
    LegalAction,
    apply_action,
    execute_action,
    list_legal_actions,
    validate_action,
)
from app.rules.events import (
    ActiveAuctionSetPayload,
    BankInventorySetPayload,
    GameEvent,
    PlayerBankruptcySetPayload,
    PlayerCashDeltaPayload,
    PlayerJailCardsSetPayload,
    PlayerJailSetPayload,
    PlayerPositionSetPayload,
    PropertyImprovementsSetPayload,
    PropertyMortgageSetPayload,
    PropertyOwnerSetPayload,
)
from app.rules.phases import TurnPhase
from app.rules.reducer import apply_event
from app.rules.state import GameState, PlayerSetup, create_initial_game_state


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
        seed="actions-seed",
        players=_player_setups(count),
        game_id="actions-game",
    )


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


def _set_position(state: GameState, player_id: str, position: int) -> GameState:
    return _apply_setup_event(
        state,
        "PLAYER_POSITION_SET",
        PlayerPositionSetPayload(player_id=player_id, position=position),
    )


def _set_cash(state: GameState, player_id: str, cash: int) -> GameState:
    return _apply_setup_event(
        state,
        "PLAYER_CASH_DELTA",
        PlayerCashDeltaPayload(player_id=player_id, amount=cash - _player(state, player_id).cash),
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


def _set_bankrupt(state: GameState, player_id: str) -> GameState:
    return _apply_setup_event(
        state,
        "PLAYER_BANKRUPTCY_SET",
        PlayerBankruptcySetPayload(player_id=player_id, is_bankrupt=True),
    )


def _start_auction(state: GameState, property_id: str) -> GameState:
    return _apply_setup_event(
        state,
        "ACTIVE_AUCTION_SET",
        ActiveAuctionSetPayload(
            active=True,
            property_id=property_id,
            high_bidder_id=None,
            high_bid_amount=None,
            passed_player_ids=(),
        ),
    )


def _action(state: GameState, actor_id: str, action_type: str, payload: Mapping[str, object] | None = None) -> GameAction:
    return GameAction(
        actor_id=actor_id,
        type=action_type,
        payload={} if payload is None else dict(payload),
        expected_state_hash=state.state_hash(),
        expected_event_sequence=state.event_sequence,
    )


def _types(actions: tuple[LegalAction, ...]) -> set[str]:
    return {action.type for action in actions}


def _legal(actions: tuple[LegalAction, ...], action_type: str) -> LegalAction:
    return next(action for action in actions if action.type == action_type)


def _issue_codes(exc: ActionValidationError) -> set[str]:
    return {issue.code for issue in exc.errors}


def _assert_rejection_does_not_mutate(state: GameState, action: GameAction, expected_code: str) -> None:
    original_hash = state.state_hash()
    original_sequence = state.event_sequence
    original_event_ids = state.applied_event_ids

    with pytest.raises(ActionValidationError) as exc_info:
        apply_action(state, action, "rejected")

    assert expected_code in _issue_codes(exc_info.value)
    assert state.event_sequence == original_sequence
    assert state.applied_event_ids == original_event_ids
    assert state.state_hash() == original_hash


def test_legal_actions_are_serializable_and_include_state_guards() -> None:
    state = _initial_state()

    legal_actions = list_legal_actions(state, "player-1")

    assert legal_actions
    encoded = json.dumps([action.model_dump(mode="json") for action in legal_actions])
    decoded = json.loads(encoded)
    assert decoded[0]["expected_state_hash"] == state.state_hash()
    assert decoded[0]["expected_event_sequence"] == state.event_sequence
    assert all(action.expected_state_hash == state.state_hash() for action in legal_actions)
    assert all(action.expected_event_sequence == state.event_sequence for action in legal_actions)
    assert all(isinstance(action.payload, Mapping) for action in legal_actions)
    assert all(isinstance(action.schema, Mapping) for action in legal_actions)


def test_roll_dice_description_is_player_facing_not_rng_jargon() -> None:
    state = _initial_state()

    roll_action = next(action for action in list_legal_actions(state, "player-1") if action.type == "ROLL_DICE")

    assert roll_action.description == "Roll dice for the current turn."


def test_initial_state_exposes_roll_but_not_voluntary_bankruptcy_end_turn_or_purchase() -> None:
    state = _initial_state()

    legal_types = _types(list_legal_actions(state, "player-1"))

    assert "END_TURN" not in legal_types, "mandatory roll window must not expose END_TURN"
    assert "ROLL_DICE" in legal_types
    assert "DECLARE_BANKRUPTCY" not in legal_types
    assert "BUY_PROPERTY" not in legal_types
    assert "START_AUCTION" not in legal_types


def test_end_turn_from_start_turn_is_rejected_without_state_mutation() -> None:
    state = _initial_state(count=2)
    action = _action(state, "player-1", "END_TURN")

    assert "END_TURN" not in _types(
        list_legal_actions(state, "player-1")
    ), "mandatory roll window must not expose END_TURN"
    with pytest.raises(ActionValidationError) as exc_info:
        validate_action(state, action)
    assert _issue_codes(exc_info.value) == {"mistimed_action"}

    _assert_rejection_does_not_mutate(state, action, "mistimed_action")
    assert state.turn.current_player_id == "player-1"
    assert state.turn.turn_number == 1
    assert state.rng.dice_roll_count == 0


def test_end_turn_phase_rotates_to_next_active_player_and_advances_turn_number() -> None:
    state = _state_in_phase(_initial_state(count=3), TurnPhase.END_TURN)

    next_state = apply_action(state, _action(state, "player-1", "END_TURN"), "end-turn")

    assert next_state.turn.turn_number == state.turn.turn_number + 1
    assert next_state.turn.current_player_index == 1
    assert next_state.turn.current_player_id == "player-2"
    assert next_state.turn.phase == "START_TURN"
    assert next_state.turn.consecutive_doubles == 0
    assert next_state.event_sequence == state.event_sequence + 1
    assert next_state.applied_event_ids[-1] == "end-turn-1"


def test_end_turn_skips_bankrupt_players_and_wraps_after_full_cycle() -> None:
    state = _state_in_phase(_set_bankrupt(_initial_state(count=3), "player-2"), TurnPhase.END_TURN)

    second_turn = apply_action(state, _action(state, "player-1", "END_TURN"), "end-turn")
    third_player_end_window = _state_in_phase(second_turn, TurnPhase.END_TURN)
    full_cycle = apply_action(
        third_player_end_window,
        _action(third_player_end_window, "player-3", "END_TURN"),
        "end-turn",
    )

    assert second_turn.turn.current_player_id == "player-3"
    assert second_turn.turn.current_player_index == 2
    assert full_cycle.turn.current_player_id == "player-1"
    assert full_cycle.turn.current_player_index == 0
    assert full_cycle.turn.turn_number == state.turn.turn_number + 2


def test_end_turn_is_rejected_for_non_current_actor() -> None:
    state = _initial_state()

    _assert_rejection_does_not_mutate(state, _action(state, "player-2", "END_TURN"), "mistimed_action")


@pytest.mark.parametrize(
    "phase",
    (
        TurnPhase.PRE_ROLL_MANAGEMENT,
        TurnPhase.ROLL_REQUIRED,
        TurnPhase.MOVEMENT_RESOLUTION,
        TurnPhase.SPACE_RESOLUTION,
        TurnPhase.PURCHASE_OR_AUCTION,
        TurnPhase.PAYMENT_RESOLUTION,
        TurnPhase.JAIL_RESOLUTION,
        TurnPhase.GAME_OVER,
    ),
)
def test_end_turn_is_not_exposed_or_accepted_where_it_would_invalid_phase_transition(
    phase: TurnPhase,
) -> None:
    state = _state_in_phase(_initial_state(), phase)

    assert "END_TURN" not in _types(
        list_legal_actions(state, "player-1")
    ), "invalid phase transition guard should not be advertised"
    with pytest.raises(ActionValidationError) as exc_info:
        apply_action(state, _action(state, "player-1", "END_TURN"), "invalid-phase-transition-probe")

    assert _issue_codes(exc_info.value) == {"mistimed_action"}


@pytest.mark.parametrize(
    "phase",
    (TurnPhase.POST_ROLL_MANAGEMENT, TurnPhase.NEGOTIATION_WINDOW),
)
def test_end_turn_from_management_end_windows_commits_through_phase_graph(
    phase: TurnPhase,
) -> None:
    state = _state_in_phase(_initial_state(count=3), phase)

    assert "END_TURN" in _types(list_legal_actions(state, "player-1"))
    validate_action(state, _action(state, "player-1", "END_TURN"))

    next_state = apply_action(
        state,
        _action(state, "player-1", "END_TURN"),
        f"{phase.value.lower()}-end-turn",
    )

    assert next_state.turn.turn_number == state.turn.turn_number + 1
    assert next_state.turn.current_player_index == 1
    assert next_state.turn.current_player_id == "player-2"
    assert next_state.turn.phase == "START_TURN"
    assert next_state.turn.consecutive_doubles == 0
    assert next_state.event_sequence == state.event_sequence + 2
    assert next_state.applied_event_ids[-2:] == (
        f"{phase.value.lower()}-end-turn-1",
        f"{phase.value.lower()}-end-turn-2",
    )


def test_end_turn_from_explicit_end_turn_phase_commits_without_invalid_phase_transition() -> None:
    state = _state_in_phase(_initial_state(count=3), TurnPhase.END_TURN)

    next_state = apply_action(state, _action(state, "player-1", "END_TURN"), "end-turn-phase")

    assert next_state.turn.current_player_id == "player-2"
    assert next_state.turn.phase == "START_TURN"
    assert next_state.event_sequence == state.event_sequence + 1


def test_unowned_property_exposes_purchase_choices_and_buy_applies_events() -> None:
    state = _set_position(_initial_state(), "player-1", 1)
    legal_actions = list_legal_actions(state, "player-1")

    assert "BUY_PROPERTY" in _types(legal_actions)
    assert "START_AUCTION" in _types(legal_actions)
    buy_action = _legal(legal_actions, "BUY_PROPERTY")
    assert buy_action.payload["property_id"] == "property_mediterranean_avenue"

    next_state = apply_action(
        state,
        _action(state, "player-1", "BUY_PROPERTY", buy_action.payload),
        "buy-action",
    )

    assert _player(next_state, "player-1").cash == 1440
    assert _property(next_state, "property_mediterranean_avenue").owner_id == "player-1"
    assert next_state.event_sequence == state.event_sequence + 2
    assert next_state.applied_event_ids[-2:] == ("buy-action-2", "buy-action-3")


def test_active_auction_exposes_bid_and_pass_and_validates_bid_amounts() -> None:
    state = _start_auction(_initial_state(), "property_mediterranean_avenue")
    legal_actions = list_legal_actions(state, "player-1")

    assert {"BID_AUCTION", "PASS_AUCTION"}.issubset(_types(legal_actions))
    assert "DECLARE_BANKRUPTCY" not in _types(legal_actions)
    bid_action = _legal(legal_actions, "BID_AUCTION")
    assert bid_action.payload["property_id"] == "property_mediterranean_avenue"
    assert bid_action.payload["amount"] == 1
    assert bid_action.schema["properties"]["amount"]["minimum"] == 1  # type: ignore[index]
    assert bid_action.schema["properties"]["amount"]["maximum"] == 1500  # type: ignore[index]
    assert bid_action.description is not None
    assert "not a recommended bid" in bid_action.description
    assert "cash affordability" in bid_action.description

    bid_state = apply_action(state, _action(state, "player-1", "BID_AUCTION", {"amount": 25}), "bid")
    assert bid_state.active_auction is not None
    assert bid_state.active_auction.high_bidder_id == "player-1"
    assert bid_state.active_auction.high_bid_amount == 25

    _assert_rejection_does_not_mutate(
        bid_state,
        _action(bid_state, "player-2", "BID_AUCTION", {"amount": 25}),
        "illegal_action",
    )
    _assert_rejection_does_not_mutate(
        bid_state,
        _action(bid_state, "player-2", "BID_AUCTION", {"amount": "26"}),
        "malformed_action",
    )
    assert list_legal_actions(bid_state, "player-1") == ()
    assert {"BID_AUCTION", "PASS_AUCTION"} == _types(list_legal_actions(bid_state, "player-2"))


def test_jail_state_exposes_pay_roll_and_card_use_without_voluntary_bankruptcy() -> None:
    state = _set_jail(_initial_state(), "player-1", True)
    state = _set_jail_cards(state, "player-1", ("card_community_get_out_of_jail",))

    legal_actions = list_legal_actions(state, "player-1")

    assert {"ROLL_DICE", "PAY_JAIL_FINE", "USE_GET_OUT_OF_JAIL_CARD"}.issubset(
        _types(legal_actions)
    )
    assert "DECLARE_BANKRUPTCY" not in _types(legal_actions)
    card_action = _legal(legal_actions, "USE_GET_OUT_OF_JAIL_CARD")
    assert card_action.payload["card_id"] == "card_community_get_out_of_jail"


def test_start_turn_does_not_expose_voluntary_bankruptcy_without_debt() -> None:
    legal_actions = list_legal_actions(_initial_state(), "player-1")

    assert "ROLL_DICE" in _types(legal_actions)
    assert "DECLARE_BANKRUPTCY" not in _types(legal_actions)


def test_execute_bankruptcy_captures_bankruptcy_events_once() -> None:
    state = _own(_initial_state(), "property_mediterranean_avenue", "player-1")
    result = execute_action(
        state,
        _action(state, "player-1", "DECLARE_BANKRUPTCY", {"creditor_id": None}),
        "bankruptcy-once",
    )

    event_types = [event.type for event in result.events]
    assert event_types.count("PLAYER_CASH_DELTA") == 1
    assert event_types.count("PROPERTY_OWNER_SET") == 1
    assert event_types.count("PLAYER_BANKRUPTCY_SET") == 1
    assert _player(result.state, "player-1").cash == 0
    assert _property(result.state, "property_mediterranean_avenue").owner_id is None


def test_bankruptcy_during_active_auction_clears_auction_before_game_over() -> None:
    state = _start_auction(_initial_state(count=2), "property_mediterranean_avenue")

    result = execute_action(
        state,
        _action(state, "player-1", "DECLARE_BANKRUPTCY", {"creditor_id": None}),
        "auction-bankruptcy",
    )

    assert result.state.active_auction is None
    assert result.state.turn.phase == TurnPhase.GAME_OVER
    assert [event.type for event in result.events].count("ACTIVE_AUCTION_SET") == 1
    assert result.events[-1].type == "TURN_STATE_SET"


def test_management_state_exposes_buy_mortgage_unmortgage_and_sell_when_legal() -> None:
    state = _initial_state()
    state = _own(state, "property_mediterranean_avenue", "player-1")
    state = _own(state, "property_baltic_avenue", "player-1")
    state = _own(state, "property_oriental_avenue", "player-1")
    state = _own(state, "property_vermont_avenue", "player-1")
    state = _own(state, "property_connecticut_avenue", "player-1")
    state = _own(state, "property_reading_railroad", "player-1")
    state = _improve(state, "property_oriental_avenue", 1)
    state = _set_bank_inventory(state, houses=31, hotels=12)
    state = _mortgage(state, "property_baltic_avenue", True)

    legal_actions = list_legal_actions(state, "player-1")
    legal_types = _types(legal_actions)

    assert "BUY_HOUSE" in legal_types
    assert "SELL_HOUSE" in legal_types
    assert "MORTGAGE_PROPERTY" in legal_types
    assert "UNMORTGAGE_PROPERTY" in legal_types
    assert _legal(legal_actions, "UNMORTGAGE_PROPERTY").payload["property_id"] == "property_baltic_avenue"

    next_state = apply_action(
        state,
        _action(state, "player-1", "BUY_HOUSE", _legal(legal_actions, "BUY_HOUSE").payload),
        "build",
    )
    assert sum(
        _property(next_state, property_id).houses
        for property_id in (
            "property_oriental_avenue",
            "property_vermont_avenue",
            "property_connecticut_avenue",
        )
    ) == 2
    assert next_state.bank_inventory.houses == state.bank_inventory.houses - 1


def test_stale_unknown_malformed_mistimed_and_illegal_actions_raise_structured_errors() -> None:
    state = _initial_state()

    with pytest.raises(ActionValidationError) as stale_exc:
        validate_action(
            state,
            GameAction(
                actor_id="player-1",
                type="ROLL_DICE",
                payload={},
                expected_state_hash="stale",
                expected_event_sequence=state.event_sequence,
            ),
        )
    assert _issue_codes(stale_exc.value) == {"stale_action"}
    assert all(issue.message for issue in stale_exc.value.errors)
    assert stale_exc.value.errors[0].field in {"expected_state_hash", "expected_event_sequence"}

    with pytest.raises(ActionValidationError) as unknown_exc:
        validate_action(state, _action(state, "player-1", "DANCE"))
    assert _issue_codes(unknown_exc.value) == {"unknown_action"}

    with pytest.raises(ActionValidationError) as malformed_exc:
        validate_action(state, _action(state, "player-1", "BUY_PROPERTY", {"property_id": 123}))
    assert _issue_codes(malformed_exc.value) == {"malformed_action"}

    with pytest.raises(ActionValidationError) as mistimed_exc:
        validate_action(_start_auction(state, "property_mediterranean_avenue"), _action(state, "player-1", "ROLL_DICE"))
    assert _issue_codes(mistimed_exc.value) == {"stale_action"}

    auction_state = _start_auction(state, "property_mediterranean_avenue")
    with pytest.raises(ActionValidationError) as current_mistimed_exc:
        validate_action(auction_state, _action(auction_state, "player-1", "ROLL_DICE"))
    assert _issue_codes(current_mistimed_exc.value) == {"mistimed_action"}

    with pytest.raises(ActionValidationError) as illegal_exc:
        validate_action(state, _action(state, "player-1", "BUY_PROPERTY", {"property_id": "property_boardwalk"}))
    assert _issue_codes(illegal_exc.value) == {"illegal_action"}


def test_rejected_actions_leave_state_event_log_and_hash_unchanged() -> None:
    state = _set_position(_initial_state(), "player-1", 1)

    _assert_rejection_does_not_mutate(
        state,
        GameAction(
            actor_id="player-1",
            type="BUY_PROPERTY",
            payload={"property_id": "property_mediterranean_avenue"},
            expected_state_hash=state.state_hash(),
            expected_event_sequence=state.event_sequence + 1,
        ),
        "stale_action",
    )
    _assert_rejection_does_not_mutate(
        state,
        _action(state, "player-1", "BUY_PROPERTY", {"property_id": "property_boardwalk"}),
        "illegal_action",
    )
    bankrupt_state = _set_bankrupt(state, "player-1")
    _assert_rejection_does_not_mutate(
        bankrupt_state,
        _action(bankrupt_state, "player-1", "ROLL_DICE"),
        "illegal_action",
    )


def test_auction_and_jail_actions_apply_through_mechanics() -> None:
    auction_state = _start_auction(_initial_state(), "property_mediterranean_avenue")
    pass_state = apply_action(
        auction_state,
        _action(auction_state, "player-2", "PASS_AUCTION", {"property_id": "property_mediterranean_avenue"}),
        "pass",
    )
    assert pass_state.active_auction is not None
    assert pass_state.active_auction.passed_player_ids == ("player-2",)

    jail_state = _set_jail(_initial_state(), "player-1", True)
    fine_state = apply_action(jail_state, _action(jail_state, "player-1", "PAY_JAIL_FINE", {"amount": 50}), "fine")
    assert _player(fine_state, "player-1").cash == 1450
    assert not _player(fine_state, "player-1").in_jail

    card_state = _set_jail(_initial_state(), "player-1", True)
    card_state = _set_jail_cards(card_state, "player-1", ("card_community_get_out_of_jail",))
    used_card_state = apply_action(
        card_state,
        _action(card_state, "player-1", "USE_GET_OUT_OF_JAIL_CARD", {"card_id": "card_community_get_out_of_jail"}),
        "jail-card",
    )
    assert not _player(used_card_state, "player-1").in_jail
    assert _player(used_card_state, "player-1").get_out_of_jail_card_ids == ()


def test_roll_dice_action_uses_deterministic_rng_and_records_dice_event() -> None:
    state_a = _initial_state()
    state_b = _initial_state()

    next_state_a = apply_action(state_a, _action(state_a, "player-1", "ROLL_DICE"), "roll")
    next_state_b = apply_action(state_b, _action(state_b, "player-1", "ROLL_DICE"), "roll")

    assert next_state_a.rng.dice_roll_count == 1
    assert next_state_a.players[0].position == next_state_b.players[0].position
    assert next_state_a.turn.phase == "PURCHASE_OR_AUCTION"
    assert {"BUY_PROPERTY", "START_AUCTION"}.issubset(_types(list_legal_actions(next_state_a, "player-1")))
    assert next_state_a.applied_event_ids[0] == "roll-1"
