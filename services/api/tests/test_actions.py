from __future__ import annotations

import json
from collections.abc import Mapping

import pytest

from app.rules.actions import (
    ActionValidationError,
    GameAction,
    LegalAction,
    apply_action,
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


def test_initial_state_exposes_roll_and_bankruptcy_but_not_purchase() -> None:
    state = _initial_state()

    legal_types = _types(list_legal_actions(state, "player-1"))

    assert "ROLL_DICE" in legal_types
    assert "DECLARE_BANKRUPTCY" in legal_types
    assert "BUY_PROPERTY" not in legal_types
    assert "START_AUCTION" not in legal_types


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
    bid_action = _legal(legal_actions, "BID_AUCTION")
    assert bid_action.payload["property_id"] == "property_mediterranean_avenue"
    assert bid_action.payload["amount"] == 1
    assert bid_action.schema["properties"]["amount"]["minimum"] == 1  # type: ignore[index]

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


def test_jail_state_exposes_pay_roll_card_use_and_bankruptcy() -> None:
    state = _set_jail(_initial_state(), "player-1", True)
    state = _set_jail_cards(state, "player-1", ("card_community_get_out_of_jail",))

    legal_actions = list_legal_actions(state, "player-1")

    assert {"ROLL_DICE", "PAY_JAIL_FINE", "USE_GET_OUT_OF_JAIL_CARD", "DECLARE_BANKRUPTCY"}.issubset(
        _types(legal_actions)
    )
    card_action = _legal(legal_actions, "USE_GET_OUT_OF_JAIL_CARD")
    assert card_action.payload["card_id"] == "card_community_get_out_of_jail"


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
    assert next_state_a.applied_event_ids[0] == "roll-1"
