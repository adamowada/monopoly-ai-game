"""Stage 11.1 regression coverage for jail, auction, mortgage, bankruptcy, card, and house scarcity."""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from app.rules.actions import GameAction, apply_action, list_legal_actions
from app.rules.events import (
    BankInventorySetPayload,
    DeckStateSetPayload,
    GameEvent,
    PlayerCashDeltaPayload,
    PlayerJailSetPayload,
    PlayerPositionSetPayload,
    PropertyImprovementsSetPayload,
    PropertyMortgageSetPayload,
    PropertyOwnerSetPayload,
)
from app.rules.mechanics import IllegalRuleActionError, mortgage_property
from app.rules.phases import TurnPhase
from app.rules.reducer import apply_event
from app.rules.static_data import load_classic_monopoly_data
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
        game_id="stage-11-1-rules",
    )


def _player(state: GameState, player_id: str):
    return next(player for player in state.players if player.id == player_id)


def _property(state: GameState, property_id: str):
    return next(ownership for ownership in state.property_ownership if ownership.property_id == property_id)


def _apply_setup_event(state: GameState, event_type: str, payload: object) -> GameState:
    return apply_event(
        state,
        GameEvent(
            event_id=f"stage-11-1-setup-{state.event_sequence + 1}",
            sequence=state.event_sequence + 1,
            type=event_type,  # type: ignore[arg-type]
            payload=payload,  # type: ignore[arg-type]
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


def _put_card_on_top(state: GameState, deck: str, card_id: str) -> GameState:
    data = load_classic_monopoly_data()
    cards = data.decks.chance if deck == "chance" else data.decks.community_chest
    draw_pile = (card_id, *(card.id for card in cards if card.id != card_id))
    return _apply_setup_event(
        state,
        "DECK_STATE_SET",
        DeckStateSetPayload(
            deck=deck,  # type: ignore[arg-type]
            draw_pile=draw_pile,
            discard_pile=(),
        ),
    )


def _legal_types(state: GameState, actor_id: str) -> set[str]:
    return {action.type for action in list_legal_actions(state, actor_id)}


def test_stage_11_1_regression_roll_to_unowned_property_enters_auction_purchase_window() -> None:
    state = _initial_state()

    rolled = apply_action(state, _action(state, "player-1", "ROLL_DICE"), "stage-11-1-roll")

    assert _player(rolled, "player-1").position == 9
    assert rolled.turn.phase == TurnPhase.PURCHASE_OR_AUCTION
    assert {"BUY_PROPERTY", "START_AUCTION"}.issubset(_legal_types(rolled, "player-1"))

    bought = apply_action(
        rolled,
        _action(rolled, "player-1", "BUY_PROPERTY", {"property_id": "property_connecticut_avenue"}),
        "stage-11-1-buy",
    )
    assert bought.turn.phase == TurnPhase.POST_ROLL_MANAGEMENT
    assert "END_TURN" in _legal_types(bought, "player-1")


def test_stage_11_1_regression_roll_to_owned_property_creates_rent_debt_and_bankruptcy_path() -> None:
    state = _own(_initial_state(), "property_connecticut_avenue", "player-2")

    rolled = apply_action(state, _action(state, "player-1", "ROLL_DICE"), "stage-11-1-rent")

    assert rolled.turn.phase == TurnPhase.PAYMENT_RESOLUTION
    assert rolled.active_payment is not None
    assert rolled.active_payment.debtor_id == "player-1"
    assert rolled.active_payment.creditor_id == "player-2"
    assert rolled.active_payment.amount_owed == 8
    assert {"SETTLE_DEBT", "DECLARE_BANKRUPTCY"}.issubset(_legal_types(rolled, "player-1"))

    bankrupt = apply_action(
        rolled,
        _action(rolled, "player-1", "DECLARE_BANKRUPTCY"),
        "stage-11-1-bankruptcy",
    )
    assert _player(bankrupt, "player-1").is_bankrupt
    assert bankrupt.active_payment is None


def test_stage_11_1_regression_roll_to_go_to_jail_space_sets_jail_state() -> None:
    state = _set_position(_initial_state(), "player-1", 21)

    rolled = apply_action(state, _action(state, "player-1", "ROLL_DICE"), "stage-11-1-jail")

    player = _player(rolled, "player-1")
    assert player.position == 10
    assert player.in_jail
    assert player.jail_turns == 0
    assert rolled.turn.phase == TurnPhase.POST_ROLL_MANAGEMENT


def test_stage_11_1_regression_card_draw_applies_effect_and_preserves_jail_card_accounting() -> None:
    state = _put_card_on_top(_set_position(_initial_state(), "player-1", 38), "chance", "card_chance_get_out_of_jail")

    drawn = apply_action(state, _action(state, "player-1", "ROLL_DICE"), "stage-11-1-card")

    assert _player(drawn, "player-1").position == 7
    assert _player(drawn, "player-1").get_out_of_jail_card_ids == ("card_chance_get_out_of_jail",)
    assert "card_chance_get_out_of_jail" not in drawn.decks.chance.discard_pile
    assert drawn.rng.chance_draw_count == 1

    jailed = _state_in_phase(_set_jail(drawn, "player-1", True), TurnPhase.JAIL_RESOLUTION)
    used = apply_action(
        jailed,
        _action(
            jailed,
            "player-1",
            "USE_GET_OUT_OF_JAIL_CARD",
            {"card_id": "card_chance_get_out_of_jail"},
        ),
        "stage-11-1-card-use",
    )

    assert not _player(used, "player-1").in_jail
    assert _player(used, "player-1").get_out_of_jail_card_ids == ()
    assert used.decks.chance.draw_pile[-1] == "card_chance_get_out_of_jail"
    assert "card_chance_get_out_of_jail" not in used.decks.chance.discard_pile


def test_stage_11_1_regression_chance_nearest_utility_uses_fresh_roll_and_active_debt() -> None:
    # nearest utility card rent must use an extra roll and an active_payment debt window.
    state = _set_position(_initial_state(), "player-1", 13)
    state = _set_cash(state, "player-1", 20)
    state = _own(state, "property_water_works", "player-2")
    state = _put_card_on_top(state, "chance", "card_chance_nearest_utility")

    rolled = apply_action(state, _action(state, "player-1", "ROLL_DICE"), "stage-11-1-utility-card")

    assert _player(rolled, "player-1").position == 28
    assert rolled.rng.dice_roll_count == 2
    assert rolled.turn.phase == TurnPhase.PAYMENT_RESOLUTION
    assert rolled.active_payment is not None
    assert rolled.active_payment.debtor_id == "player-1"
    assert rolled.active_payment.creditor_id == "player-2"
    assert rolled.active_payment.amount_owed == 60
    assert rolled.active_payment.reason == "card_rent:property_water_works"
    assert _player(rolled, "player-1").cash == 20
    assert _player(rolled, "player-2").cash == 1500
    assert {"SETTLE_DEBT", "DECLARE_BANKRUPTCY"}.issubset(_legal_types(rolled, "player-1"))


def test_stage_11_1_regression_chance_nearest_railroad_uses_active_debt() -> None:
    state = _set_position(_initial_state(), "player-1", 13)
    state = _set_cash(state, "player-1", 20)
    state = _own(state, "property_b_and_o_railroad", "player-2")
    state = _put_card_on_top(state, "chance", "card_chance_nearest_railroad_a")

    rolled = apply_action(state, _action(state, "player-1", "ROLL_DICE"), "stage-11-1-railroad-card")

    assert _player(rolled, "player-1").position == 25
    assert rolled.turn.phase == TurnPhase.PAYMENT_RESOLUTION
    assert rolled.active_payment is not None
    assert rolled.active_payment.debtor_id == "player-1"
    assert rolled.active_payment.creditor_id == "player-2"
    assert rolled.active_payment.amount_owed == 50
    assert rolled.active_payment.reason == "card_rent:property_b_and_o_railroad"
    assert _player(rolled, "player-1").cash == 20
    assert _player(rolled, "player-2").cash == 1500
    assert {"SETTLE_DEBT", "DECLARE_BANKRUPTCY"}.issubset(_legal_types(rolled, "player-1"))


def test_stage_11_1_regression_public_doubles_flow_allows_same_player_to_roll_again() -> None:
    state = _set_position(
        create_initial_game_state(
            seed="doubles-seed-0",
            players=_player_setups(2),
            game_id="stage-11-1-doubles",
        ),
        "player-1",
        18,
    )

    rolled = apply_action(state, _action(state, "player-1", "ROLL_DICE"), "stage-11-1-doubles-roll")

    assert _player(rolled, "player-1").position == 20
    assert rolled.turn.phase == TurnPhase.POST_ROLL_MANAGEMENT
    assert rolled.turn.current_player_id == "player-1"
    assert rolled.turn.consecutive_doubles == 1
    assert "ROLL_DICE" in _legal_types(rolled, "player-1")
    assert "END_TURN" not in _legal_types(rolled, "player-1")

    second_roll = apply_action(
        rolled,
        _action(rolled, "player-1", "ROLL_DICE"),
        "stage-11-1-doubles-second-roll",
    )

    assert second_roll.turn.current_player_id == "player-1"
    assert second_roll.rng.dice_roll_count == 2


def test_stage_11_1_regression_mortgage_and_house_scarcity_rules_stay_enforced() -> None:
    state = _own(_initial_state(), "property_mediterranean_avenue", "player-1")
    state = _own(state, "property_baltic_avenue", "player-1")
    state = _improve(state, "property_baltic_avenue", 1)

    with pytest.raises(IllegalRuleActionError, match="improvements"):
        mortgage_property(state, "player-1", "property_mediterranean_avenue", "stage-11-1-mortgage")

    scarcity_state = _set_bank_inventory(state, houses=0, hotels=12)
    assert "BUY_HOUSE" not in _legal_types(scarcity_state, "player-1")


def test_stage_11_1_regression_auction_closes_when_last_competitor_passes() -> None:
    state = _state_in_phase(_set_position(_initial_state(), "player-1", 9), TurnPhase.PURCHASE_OR_AUCTION)
    auction_state = apply_action(
        state,
        _action(state, "player-1", "START_AUCTION", {"property_id": "property_connecticut_avenue"}),
        "stage-11-1-auction-start",
    )
    bid_state = apply_action(
        auction_state,
        _action(
            auction_state,
            "player-2",
            "BID_AUCTION",
            {"property_id": "property_connecticut_avenue", "amount": 25},
        ),
        "stage-11-1-auction-bid",
    )
    pass_state = apply_action(
        bid_state,
        _action(
            bid_state,
            "player-1",
            "PASS_AUCTION",
            {"property_id": "property_connecticut_avenue"},
        ),
        "stage-11-1-auction-pass-1",
    )
    closed = apply_action(
        pass_state,
        _action(
            pass_state,
            "player-3",
            "PASS_AUCTION",
            {"property_id": "property_connecticut_avenue"},
        ),
        "stage-11-1-auction-pass-2",
    )

    assert closed.active_auction is None
    assert closed.turn.phase == TurnPhase.POST_ROLL_MANAGEMENT
    assert "END_TURN" in _legal_types(closed, "player-1")
    assert _property(closed, "property_connecticut_avenue").owner_id == "player-2"
    assert _player(closed, "player-2").cash == 1475
