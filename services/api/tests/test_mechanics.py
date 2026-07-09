from __future__ import annotations

import pytest

from app.rules.events import (
    BankInventorySetPayload,
    GameEvent,
    PlayerCashDeltaPayload,
    PlayerJailCardsSetPayload,
    PlayerJailSetPayload,
    PlayerPositionSetPayload,
    PropertyImprovementsSetPayload,
    PropertyMortgageSetPayload,
    PropertyOwnerSetPayload,
)
from app.rules.mechanics import (
    IllegalRuleActionError,
    apply_card_effect,
    apply_dice_roll,
    buy_house,
    buy_property,
    calculate_rent,
    close_auction,
    declare_bankruptcy,
    is_game_over,
    mortgage_property,
    move_player_steps,
    pass_auction,
    pay_jail_fine,
    pay_rent,
    pay_tax_for_space,
    place_auction_bid,
    sell_house,
    send_player_to_jail,
    start_auction,
    unmortgage_property,
    use_get_out_of_jail_card,
    winning_player_id,
)
from app.rules.reducer import apply_event
from app.rules.state import GameState, PlayerSetup, create_initial_game_state


def _player_setups(count: int = 3) -> tuple[PlayerSetup, ...]:
    return tuple(
        PlayerSetup(id=f"player-{index}", name=f"Player {index}", kind="human" if index == 1 else "ai")
        for index in range(1, count + 1)
    )


def _initial_state(count: int = 3) -> GameState:
    return create_initial_game_state(
        seed="mechanics-seed",
        players=_player_setups(count),
        game_id="mechanics-game",
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


def _own_brown_group(state: GameState, owner_id: str = "player-1") -> GameState:
    state = _own(state, "property_mediterranean_avenue", owner_id)
    return _own(state, "property_baltic_avenue", owner_id)


def test_initial_setup_uses_classic_start_cash_and_player_count_limits() -> None:
    state = _initial_state(5)

    assert [player.cash for player in state.players] == [1500, 1500, 1500, 1500, 1500]
    assert [player.position for player in state.players] == [0, 0, 0, 0, 0]

    with pytest.raises(ValueError, match="2 to 5 players"):
        _initial_state(1)


def test_move_player_steps_pays_go_salary_when_crossing_or_landing_beyond_board() -> None:
    state = _set_position(_initial_state(), "player-1", 39)

    state = move_player_steps(state, "player-1", 1, "move")

    assert _player(state, "player-1").position == 0
    assert _player(state, "player-1").cash == 1700
    assert len(set(state.applied_event_ids)) == len(state.applied_event_ids)


def test_apply_dice_roll_records_roll_moves_and_tracks_doubles() -> None:
    state = _initial_state()

    state = apply_dice_roll(state, "player-1", 2, 3, "roll")
    state = apply_dice_roll(state, "player-1", 1, 1, "roll")

    assert state.rng.dice_roll_count == 2
    assert _player(state, "player-1").position == 7
    assert state.turn.consecutive_doubles == 1


def test_third_consecutive_doubles_sends_player_to_jail_without_normal_movement() -> None:
    state = _initial_state()

    state = apply_dice_roll(state, "player-1", 1, 1, "double")
    state = apply_dice_roll(state, "player-1", 2, 2, "double")
    state = apply_dice_roll(state, "player-1", 3, 3, "double")

    player = _player(state, "player-1")
    assert state.rng.dice_roll_count == 3
    assert player.position == 10
    assert player.in_jail
    assert player.jail_turns == 0
    assert state.turn.consecutive_doubles == 0


def test_jail_rolls_increment_failures_pay_on_third_failure_and_doubles_leave() -> None:
    state = send_player_to_jail(_initial_state(), "player-1", "jail")

    state = apply_dice_roll(state, "player-1", 1, 2, "jail-roll")
    assert _player(state, "player-1").position == 10
    assert _player(state, "player-1").in_jail
    assert _player(state, "player-1").jail_turns == 1

    state = apply_dice_roll(state, "player-1", 3, 4, "jail-roll")
    state = apply_dice_roll(state, "player-1", 2, 3, "jail-roll")

    player = _player(state, "player-1")
    assert player.cash == 1450
    assert player.position == 15
    assert not player.in_jail
    assert player.jail_turns == 0

    doubles_state = send_player_to_jail(_initial_state(), "player-1", "jail")
    doubles_state = apply_dice_roll(doubles_state, "player-1", 2, 2, "jail-double")
    player = _player(doubles_state, "player-1")
    assert player.position == 14
    assert not player.in_jail
    assert player.cash == 1500


def test_buy_property_transfers_price_and_rejects_owned_or_insufficient_cash() -> None:
    state = buy_property(_initial_state(), "player-1", "property_mediterranean_avenue", "buy")

    assert _player(state, "player-1").cash == 1440
    assert _property(state, "property_mediterranean_avenue").owner_id == "player-1"

    with pytest.raises(IllegalRuleActionError, match="already owned"):
        buy_property(state, "player-2", "property_mediterranean_avenue", "buy")

    poor_state = _set_cash(_initial_state(), "player-1", 399)
    with pytest.raises(IllegalRuleActionError, match="insufficient cash"):
        buy_property(poor_state, "player-1", "property_boardwalk", "buy")


def test_auction_bid_pass_and_close_transfers_property_for_high_bid() -> None:
    state = start_auction(_initial_state(), "property_mediterranean_avenue", "auction")

    assert state.active_auction is not None
    assert state.active_auction.property_id == "property_mediterranean_avenue"
    assert state.active_auction.high_bid_amount is None

    state = place_auction_bid(state, "player-1", 70, "auction")
    with pytest.raises(IllegalRuleActionError, match="increase"):
        place_auction_bid(state, "player-2", 70, "auction")

    state = pass_auction(state, "player-2", "auction")
    with pytest.raises(IllegalRuleActionError, match="passed"):
        place_auction_bid(state, "player-2", 80, "auction")

    state = pass_auction(state, "player-3", "auction")
    state = close_auction(state, "auction")

    assert state.active_auction is None
    assert _player(state, "player-1").cash == 1430
    assert _property(state, "property_mediterranean_avenue").owner_id == "player-1"


def test_closing_no_bid_auction_leaves_property_unowned_and_stale_auction_calls_reject() -> None:
    state = close_auction(
        start_auction(_initial_state(), "property_oriental_avenue", "auction"),
        "auction",
    )

    assert state.active_auction is None
    assert _property(state, "property_oriental_avenue").owner_id is None

    with pytest.raises(IllegalRuleActionError, match="active auction"):
        close_auction(state, "auction")
    with pytest.raises(IllegalRuleActionError, match="active auction"):
        place_auction_bid(state, "player-1", 1, "auction")


def test_street_rent_handles_base_monopoly_improvements_and_mortgages() -> None:
    state = _own(_initial_state(), "property_mediterranean_avenue", "player-2")
    assert calculate_rent(state, "property_mediterranean_avenue") == 2

    state = _own(state, "property_baltic_avenue", "player-2")
    assert calculate_rent(state, "property_mediterranean_avenue") == 4

    state = _improve(state, "property_mediterranean_avenue", 2)
    assert calculate_rent(state, "property_mediterranean_avenue") == 30

    state = _mortgage(state, "property_mediterranean_avenue", True)
    assert calculate_rent(state, "property_mediterranean_avenue") == 0
    with pytest.raises(IllegalRuleActionError, match="no rent"):
        pay_rent(state, "player-1", "property_mediterranean_avenue", "rent")


def test_railroad_and_utility_rent_counts_owned_properties() -> None:
    state = _own(_initial_state(), "property_reading_railroad", "player-2")
    state = _own(state, "property_pennsylvania_railroad", "player-2")
    assert calculate_rent(state, "property_reading_railroad") == 50

    state = _own(state, "property_b_and_o_railroad", "player-2")
    state = _own(state, "property_short_line_railroad", "player-2")
    assert calculate_rent(state, "property_reading_railroad") == 200

    state = _own(state, "property_electric_company", "player-2")
    assert calculate_rent(state, "property_electric_company", dice_total=7) == 28

    state = _own(state, "property_water_works", "player-2")
    assert calculate_rent(state, "property_electric_company", dice_total=7) == 70

    with pytest.raises(IllegalRuleActionError, match="dice total"):
        calculate_rent(state, "property_electric_company")


def test_pay_rent_transfers_between_players() -> None:
    state = _own(_initial_state(), "property_baltic_avenue", "player-2")

    state = pay_rent(state, "player-1", "property_baltic_avenue", "rent")

    assert _player(state, "player-1").cash == 1496
    assert _player(state, "player-2").cash == 1504


def test_pay_tax_for_space_charges_static_taxes_and_rejects_non_tax_spaces() -> None:
    state = pay_tax_for_space(_initial_state(), "player-1", "space_income_tax", "tax")
    state = pay_tax_for_space(state, "player-1", "space_luxury_tax", "tax")

    assert _player(state, "player-1").cash == 1200

    with pytest.raises(IllegalRuleActionError, match="not a tax"):
        pay_tax_for_space(state, "player-1", "space_go", "tax")


def test_bank_tax_and_card_fees_create_debt_when_cash_is_short() -> None:
    tax_state = pay_tax_for_space(
        _set_cash(_initial_state(), "player-1", 25),
        "player-1",
        "space_income_tax",
        "short-tax",
    )

    assert _player(tax_state, "player-1").cash == 25
    assert tax_state.active_payment is not None
    assert tax_state.active_payment.debtor_id == "player-1"
    assert tax_state.active_payment.creditor_id is None
    assert tax_state.active_payment.amount_owed == 200
    assert tax_state.active_payment.reason == "tax:space_income_tax"
    assert not tax_state.active_payment.negotiation_allowed

    card_state = apply_card_effect(
        _set_cash(_initial_state(), "player-1", 25),
        "player-1",
        "card_community_hospital_fee",
        "short-card-fee",
    )

    assert _player(card_state, "player-1").cash == 25
    assert card_state.active_payment is not None
    assert card_state.active_payment.debtor_id == "player-1"
    assert card_state.active_payment.creditor_id is None
    assert card_state.active_payment.amount_owed == 100
    assert card_state.active_payment.reason == "card_bank:card_community_hospital_fee"
    assert not card_state.active_payment.negotiation_allowed


def test_card_player_transfers_create_debt_without_overdrawing_cash() -> None:
    pay_each_state = apply_card_effect(
        _set_cash(_initial_state(), "player-1", 25),
        "player-1",
        "card_chance_pay_each_player",
        "short-pay-each",
    )

    assert _player(pay_each_state, "player-1").cash == 25
    assert _player(pay_each_state, "player-2").cash == 1500
    assert pay_each_state.active_payment is not None
    assert pay_each_state.active_payment.debtor_id == "player-1"
    assert pay_each_state.active_payment.creditor_id == "player-2"
    assert pay_each_state.active_payment.amount_owed == 50
    assert pay_each_state.active_payment.reason == "card_player:card_chance_pay_each_player"
    assert pay_each_state.active_payment.negotiation_allowed

    collect_state = apply_card_effect(
        _set_cash(_initial_state(), "player-2", 5),
        "player-1",
        "card_community_collect_from_each_player",
        "short-collect",
    )

    assert _player(collect_state, "player-1").cash == 1500
    assert _player(collect_state, "player-2").cash == 5
    assert collect_state.active_payment is not None
    assert collect_state.active_payment.debtor_id == "player-2"
    assert collect_state.active_payment.creditor_id == "player-1"
    assert collect_state.active_payment.amount_owed == 10
    assert collect_state.active_payment.reason == "card_player:card_community_collect_from_each_player"
    assert collect_state.active_payment.negotiation_allowed


def test_card_effects_advance_nearest_relative_jail_and_bank_payments() -> None:
    state = _set_position(_initial_state(), "player-1", 39)
    state = apply_card_effect(state, "player-1", "card_chance_advance_to_illinois_avenue", "card")
    assert _player(state, "player-1").position == 24
    assert _player(state, "player-1").cash == 1700

    state = _set_position(state, "player-1", 36)
    state = apply_card_effect(state, "player-1", "card_chance_nearest_utility", "card")
    assert _player(state, "player-1").position == 12

    state = _set_position(state, "player-1", 36)
    state = apply_card_effect(state, "player-1", "card_chance_nearest_railroad_a", "card")
    assert _player(state, "player-1").position == 5

    state = _set_position(state, "player-1", 7)
    state = apply_card_effect(state, "player-1", "card_chance_move_back_three", "card")
    assert _player(state, "player-1").position == 4

    state = apply_card_effect(state, "player-1", "card_chance_bank_dividend", "card")
    state = apply_card_effect(state, "player-1", "card_chance_speeding_fine", "card")
    assert _player(state, "player-1").cash == 1735

    state = apply_card_effect(state, "player-1", "card_chance_go_to_jail", "card")
    assert _player(state, "player-1").position == 10
    assert _player(state, "player-1").in_jail


def test_card_effects_player_transfers_jail_cards_and_building_repairs() -> None:
    state = _own_brown_group(_initial_state(), "player-1")
    state = _improve(state, "property_mediterranean_avenue", 1)
    state = _improve(state, "property_baltic_avenue", 0, hotel=True)

    state = apply_card_effect(state, "player-1", "card_chance_get_out_of_jail", "card")
    assert _player(state, "player-1").get_out_of_jail_card_ids == ("card_chance_get_out_of_jail",)

    state = apply_card_effect(state, "player-1", "card_community_street_repairs", "card")
    assert _player(state, "player-1").cash == 1345

    state = apply_card_effect(state, "player-1", "card_chance_pay_each_player", "card")
    assert _player(state, "player-1").cash == 1245
    assert _player(state, "player-2").cash == 1550
    assert _player(state, "player-3").cash == 1550

    state = apply_card_effect(state, "player-1", "card_community_collect_from_each_player", "card")
    assert _player(state, "player-1").cash == 1265
    assert _player(state, "player-2").cash == 1540
    assert _player(state, "player-3").cash == 1540


def test_jail_fine_and_get_out_card_choices_reject_invalid_choices() -> None:
    state = pay_jail_fine(send_player_to_jail(_initial_state(), "player-1", "jail"), "player-1", "fine")
    assert _player(state, "player-1").cash == 1450
    assert not _player(state, "player-1").in_jail

    with pytest.raises(IllegalRuleActionError, match="not in jail"):
        pay_jail_fine(state, "player-1", "fine")

    card_state = send_player_to_jail(_initial_state(), "player-1", "jail")
    card_state = _set_jail_cards(card_state, "player-1", ("card_community_get_out_of_jail",))
    card_state = use_get_out_of_jail_card(
        card_state,
        "player-1",
        "card_community_get_out_of_jail",
        "jail-card",
    )
    assert not _player(card_state, "player-1").in_jail
    assert _player(card_state, "player-1").get_out_of_jail_card_ids == ()

    with pytest.raises(IllegalRuleActionError, match="does not hold"):
        use_get_out_of_jail_card(
            send_player_to_jail(_initial_state(), "player-1", "jail"),
            "player-1",
            "card_community_get_out_of_jail",
            "jail-card",
        )


def test_mortgage_and_unmortgage_rules() -> None:
    state = _own_brown_group(_initial_state(), "player-1")

    state = mortgage_property(state, "player-1", "property_mediterranean_avenue", "mortgage")
    assert _player(state, "player-1").cash == 1530
    assert _property(state, "property_mediterranean_avenue").mortgaged

    state = unmortgage_property(state, "player-1", "property_mediterranean_avenue", "unmortgage")
    assert _player(state, "player-1").cash == 1497
    assert not _property(state, "property_mediterranean_avenue").mortgaged

    with pytest.raises(IllegalRuleActionError, match="not mortgaged"):
        unmortgage_property(state, "player-1", "property_mediterranean_avenue", "unmortgage")

    improved_state = _improve(state, "property_baltic_avenue", 1)
    with pytest.raises(IllegalRuleActionError, match="improvements"):
        mortgage_property(improved_state, "player-1", "property_mediterranean_avenue", "mortgage")


def test_buy_house_requires_monopoly_even_building_and_inventory() -> None:
    state = _initial_state()

    with pytest.raises(IllegalRuleActionError, match="monopoly"):
        buy_house(_own(state, "property_mediterranean_avenue", "player-1"), "player-1", "property_mediterranean_avenue", "build")

    state = _own_brown_group(state, "player-1")
    state = buy_house(state, "player-1", "property_mediterranean_avenue", "build")

    assert _player(state, "player-1").cash == 1450
    assert _property(state, "property_mediterranean_avenue").houses == 1
    assert state.bank_inventory.houses == 31

    with pytest.raises(IllegalRuleActionError, match="even"):
        buy_house(state, "player-1", "property_mediterranean_avenue", "build")

    state = buy_house(state, "player-1", "property_baltic_avenue", "build")
    state = _set_bank_inventory(state, houses=0, hotels=12)
    with pytest.raises(IllegalRuleActionError, match="houses"):
        buy_house(state, "player-1", "property_mediterranean_avenue", "build")


def test_buy_house_upgrades_to_hotel_and_respects_hotel_scarcity() -> None:
    state = _own_brown_group(_initial_state(), "player-1")
    state = _improve(state, "property_mediterranean_avenue", 4)
    state = _improve(state, "property_baltic_avenue", 4)
    state = _set_bank_inventory(state, houses=24, hotels=1)

    state = buy_house(state, "player-1", "property_mediterranean_avenue", "hotel")

    assert _player(state, "player-1").cash == 1450
    assert _property(state, "property_mediterranean_avenue").hotel
    assert _property(state, "property_mediterranean_avenue").houses == 0
    assert state.bank_inventory.houses == 28
    assert state.bank_inventory.hotels == 0

    with pytest.raises(IllegalRuleActionError, match="hotels"):
        buy_house(state, "player-1", "property_baltic_avenue", "hotel")


def test_sell_house_enforces_reverse_even_rule_and_pays_half() -> None:
    state = _own_brown_group(_initial_state(), "player-1")
    state = _improve(state, "property_mediterranean_avenue", 2)
    state = _improve(state, "property_baltic_avenue", 1)
    state = _set_bank_inventory(state, houses=29, hotels=12)

    with pytest.raises(IllegalRuleActionError, match="even"):
        sell_house(state, "player-1", "property_baltic_avenue", "sell")

    state = sell_house(state, "player-1", "property_mediterranean_avenue", "sell")
    assert _player(state, "player-1").cash == 1525
    assert _property(state, "property_mediterranean_avenue").houses == 1
    assert state.bank_inventory.houses == 30


def test_sell_hotel_requires_four_bank_houses() -> None:
    state = _own_brown_group(_initial_state(), "player-1")
    state = _improve(state, "property_mediterranean_avenue", 0, hotel=True)
    state = _improve(state, "property_baltic_avenue", 4)
    state = _set_bank_inventory(state, houses=3, hotels=11)

    with pytest.raises(IllegalRuleActionError, match="four houses"):
        sell_house(state, "player-1", "property_mediterranean_avenue", "sell-hotel")

    state = _set_bank_inventory(state, houses=4, hotels=11)
    state = sell_house(state, "player-1", "property_mediterranean_avenue", "sell-hotel")

    assert _player(state, "player-1").cash == 1525
    assert _property(state, "property_mediterranean_avenue").houses == 4
    assert not _property(state, "property_mediterranean_avenue").hotel
    assert state.bank_inventory.houses == 0
    assert state.bank_inventory.hotels == 12


def test_bankruptcy_to_creditor_transfers_cash_and_properties() -> None:
    state = _own(_initial_state(), "property_mediterranean_avenue", "player-1")
    state = _mortgage(state, "property_mediterranean_avenue", True)
    state = _set_cash(state, "player-1", 120)

    state = declare_bankruptcy(state, "player-1", "player-2", "bankrupt")

    assert _player(state, "player-1").is_bankrupt
    assert _player(state, "player-1").cash == 0
    assert _player(state, "player-2").cash == 1620
    assert _property(state, "property_mediterranean_avenue").owner_id == "player-2"
    assert _property(state, "property_mediterranean_avenue").mortgaged


def test_bankruptcy_to_bank_liquidates_properties_and_returns_inventory() -> None:
    state = _own_brown_group(_initial_state(), "player-1")
    state = _improve(state, "property_mediterranean_avenue", 2)
    state = _improve(state, "property_baltic_avenue", 0, hotel=True)
    state = _mortgage(state, "property_mediterranean_avenue", True)
    state = _set_bank_inventory(state, houses=26, hotels=11)

    state = declare_bankruptcy(state, "player-1", None, "bankrupt")

    assert _player(state, "player-1").is_bankrupt
    assert _player(state, "player-1").cash == 0
    assert _property(state, "property_mediterranean_avenue").owner_id is None
    assert not _property(state, "property_mediterranean_avenue").mortgaged
    assert _property(state, "property_mediterranean_avenue").houses == 0
    assert _property(state, "property_baltic_avenue").owner_id is None
    assert not _property(state, "property_baltic_avenue").hotel
    assert state.bank_inventory.houses == 28
    assert state.bank_inventory.hotels == 12


def test_game_over_counts_non_bankrupt_players_and_reports_winner() -> None:
    state = _initial_state(2)
    assert not is_game_over(state)
    assert winning_player_id(state) is None

    state = declare_bankruptcy(state, "player-2", None, "bankrupt")
    assert is_game_over(state)
    assert winning_player_id(state) == "player-1"

    state = declare_bankruptcy(state, "player-1", None, "bankrupt")
    assert is_game_over(state)
    assert winning_player_id(state) is None


def test_deterministic_scripted_game_progresses_from_setup_to_game_over() -> None:
    def run_script() -> GameState:
        state = _initial_state(2)
        state = apply_dice_roll(state, "player-1", 1, 2, "script")
        state = buy_property(state, "player-1", "property_baltic_avenue", "script")
        state = apply_dice_roll(state, "player-2", 1, 2, "script")
        state = pay_rent(state, "player-2", "property_baltic_avenue", "script")
        state = start_auction(state, "property_reading_railroad", "script")
        state = place_auction_bid(state, "player-2", 200, "script")
        state = close_auction(state, "script")
        state = send_player_to_jail(state, "player-1", "script")
        state = pay_jail_fine(state, "player-1", "script")
        state = mortgage_property(state, "player-1", "property_baltic_avenue", "script")
        return declare_bankruptcy(state, "player-2", None, "script")

    state_a = run_script()
    state_b = run_script()

    assert state_a == state_b
    assert state_a.state_hash() == state_b.state_hash()
    assert is_game_over(state_a)
    assert winning_player_id(state_a) == "player-1"
