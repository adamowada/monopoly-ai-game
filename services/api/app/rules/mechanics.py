from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from app.rules.events import (
    ActivePaymentSetPayload,
    ActiveAuctionSetPayload,
    BankInventorySetPayload,
    DeckStateSetPayload,
    DiceRolledPayload,
    GameEvent,
    GameEventPayload,
    GameEventType,
    PlayerBankruptcySetPayload,
    PlayerCashDeltaPayload,
    PlayerJailCardsSetPayload,
    PlayerJailSetPayload,
    PlayerPositionSetPayload,
    PropertyImprovementsSetPayload,
    PropertyMortgageSetPayload,
    PropertyOwnerSetPayload,
    TurnStateSetPayload,
)
from app.rules.event_capture import record_rule_event
from app.rules.reducer import apply_event
from app.rules.phases import TurnPhase
from app.rules.static_data import (
    BoardSpace,
    CardData,
    ClassicMonopolyData,
    PropertyData,
    PropertyGroup,
    load_classic_monopoly_data,
)
from app.rules.state import GameState, PlayerState, PropertyOwnershipState


GO_POSITION = 0
GO_SALARY = 200
JAIL_POSITION = 10
JAIL_FINE = 50
BOARD_SPACE_COUNT = 40
END_TURN_ENTRY_PHASES = frozenset(
    {
        TurnPhase.POST_ROLL_MANAGEMENT,
        TurnPhase.NEGOTIATION_WINDOW,
    }
)
END_TURN_COMMIT_PHASES = frozenset(
    {
        TurnPhase.END_TURN,
        *END_TURN_ENTRY_PHASES,
    }
)


class IllegalRuleActionError(ValueError):
    """Raised when a requested high-level rule mechanic is not legal."""


class _EventStream:
    def __init__(self, event_id_prefix: str) -> None:
        if not event_id_prefix:
            raise IllegalRuleActionError("event id prefix is required")
        self.event_id_prefix = event_id_prefix

    def apply(self, state: GameState, event_type: str, payload: GameEventPayload) -> GameState:
        next_sequence = state.event_sequence + 1
        event = GameEvent(
            event_id=f"{self.event_id_prefix}-{next_sequence}",
            sequence=next_sequence,
            type=cast(GameEventType, event_type),
            payload=payload,
        )
        record_rule_event(event)
        return apply_event(state, event)


def move_player_steps(
    state: GameState,
    player_id: str,
    steps: int,
    event_id_prefix: str,
) -> GameState:
    player = _active_player_by_id(state, player_id)
    stream = _EventStream(event_id_prefix)
    destination = (player.position + steps) % BOARD_SPACE_COUNT
    go_salary_count = (player.position + steps) // BOARD_SPACE_COUNT if steps > 0 else 0

    state = _set_player_position(stream, state, player_id, destination)
    if go_salary_count > 0:
        state = _adjust_cash(stream, state, player_id, GO_SALARY * go_salary_count)
    return state


def apply_dice_roll(
    state: GameState,
    player_id: str,
    die_1: int,
    die_2: int,
    event_id_prefix: str,
) -> GameState:
    player = _active_player_by_id(state, player_id)
    _validate_die(die_1)
    _validate_die(die_2)

    stream = _EventStream(event_id_prefix)
    is_doubles = die_1 == die_2
    dice_total = die_1 + die_2
    state = stream.apply(
        state,
        "DICE_ROLLED",
        DiceRolledPayload(
            player_id=player_id,
            die_1=die_1,
            die_2=die_2,
            total=dice_total,
            is_doubles=is_doubles,
            roll_counter=state.rng.dice_roll_count + 1,
        ),
    )

    if player.in_jail:
        if is_doubles:
            state = _set_jail(stream, state, player_id, in_jail=False, jail_turns=0)
            state = _set_consecutive_doubles(stream, state, 0)
            return move_player_steps(state, player_id, dice_total, event_id_prefix)

        if player.jail_turns >= 2:
            state = _adjust_cash(stream, state, player_id, -JAIL_FINE)
            state = _set_jail(stream, state, player_id, in_jail=False, jail_turns=0)
            state = _set_consecutive_doubles(stream, state, 0)
            return move_player_steps(state, player_id, dice_total, event_id_prefix)

        state = _set_jail(stream, state, player_id, in_jail=True, jail_turns=player.jail_turns + 1)
        return _set_consecutive_doubles(stream, state, 0)

    next_doubles_count = state.turn.consecutive_doubles + 1 if is_doubles else 0
    if next_doubles_count >= 3:
        return send_player_to_jail(state, player_id, event_id_prefix)

    state = _set_consecutive_doubles(stream, state, next_doubles_count)
    return move_player_steps(state, player_id, dice_total, event_id_prefix)


def end_turn(state: GameState, player_id: str, event_id_prefix: str) -> GameState:
    player = _active_player_by_id(state, player_id)
    if player.id != state.turn.current_player_id:
        raise IllegalRuleActionError(f"{player_id} is not the current turn player")
    if state.active_auction is not None:
        raise IllegalRuleActionError("players cannot end a turn during an active auction")
    if state.active_payment is not None:
        raise IllegalRuleActionError("players cannot end a turn with unresolved debt")
    current_phase = TurnPhase(state.turn.phase)
    if current_phase not in END_TURN_COMMIT_PHASES:
        raise IllegalRuleActionError(f"players cannot end a turn during {current_phase.value}")
    if state.turn.consecutive_doubles > 0:
        raise IllegalRuleActionError("players cannot end a turn while a doubles roll is pending")

    stream = _EventStream(event_id_prefix)
    if current_phase in END_TURN_ENTRY_PHASES:
        state = stream.apply(
            state,
            "TURN_STATE_SET",
            TurnStateSetPayload(
                turn_number=state.turn.turn_number,
                current_player_index=state.turn.current_player_index,
                current_player_id=state.turn.current_player_id,
                phase=TurnPhase.END_TURN.value,
                consecutive_doubles=state.turn.consecutive_doubles,
            ),
        )
    next_player_index = _next_active_player_index(state)
    next_player = state.players[next_player_index]
    return stream.apply(
        state,
        "TURN_STATE_SET",
        TurnStateSetPayload(
            turn_number=state.turn.turn_number + 1,
            current_player_index=next_player_index,
            current_player_id=next_player.id,
            phase=TurnPhase.START_TURN.value,
            consecutive_doubles=0,
        ),
    )


def buy_property(
    state: GameState,
    player_id: str,
    property_id: str,
    event_id_prefix: str,
) -> GameState:
    player = _active_player_by_id(state, player_id)
    property_data = _property_data(property_id)
    ownership = _property_ownership(state, property_id)
    if ownership.owner_id is not None:
        raise IllegalRuleActionError(f"{property_id} is already owned")
    if player.cash < property_data.price:
        raise IllegalRuleActionError("insufficient cash to buy property")

    stream = _EventStream(event_id_prefix)
    state = _adjust_cash(stream, state, player_id, -property_data.price)
    return _set_property_owner(stream, state, property_id, player_id)


def calculate_rent(
    state: GameState,
    property_id: str,
    dice_total: int | None = None,
) -> int:
    property_data = _property_data(property_id)
    ownership = _property_ownership(state, property_id)
    if ownership.owner_id is None or ownership.mortgaged:
        return 0

    if property_data.kind == "street":
        rents = _street_rents(property_data)
        if ownership.hotel:
            return rents[5]
        if ownership.houses > 0:
            return rents[ownership.houses]

        group = _property_group(property_data.group)
        group_ownerships = [_property_ownership(state, group_property_id) for group_property_id in group.property_ids]
        owns_group = all(group_ownership.owner_id == ownership.owner_id for group_ownership in group_ownerships)
        group_unmortgaged = all(not group_ownership.mortgaged for group_ownership in group_ownerships)
        group_unimproved = all(
            group_ownership.houses == 0 and not group_ownership.hotel
            for group_ownership in group_ownerships
        )
        return rents[0] * 2 if owns_group and group_unmortgaged and group_unimproved else rents[0]

    if property_data.kind == "railroad":
        rent_tiers = property_data.rent_by_owned_count
        if rent_tiers is None:
            raise IllegalRuleActionError(f"{property_id} has no railroad rent table")
        count = _owned_property_count_in_group(state, property_data.group, ownership.owner_id)
        return rent_tiers[count - 1] if count > 0 else 0

    if dice_total is None:
        raise IllegalRuleActionError("dice total is required for utility rent")
    if dice_total <= 0:
        raise IllegalRuleActionError("dice total must be positive for utility rent")
    multipliers = property_data.rent_multipliers
    if multipliers is None:
        raise IllegalRuleActionError(f"{property_id} has no utility rent multiplier")
    count = _owned_property_count_in_group(state, property_data.group, ownership.owner_id)
    return dice_total * multipliers[count - 1] if count > 0 else 0


def pay_rent(
    state: GameState,
    payer_id: str,
    property_id: str,
    event_id_prefix: str,
    dice_total: int | None = None,
) -> GameState:
    _active_player_by_id(state, payer_id)
    ownership = _property_ownership(state, property_id)
    if ownership.owner_id is None:
        raise IllegalRuleActionError(f"{property_id} has no owner")
    if ownership.owner_id == payer_id:
        raise IllegalRuleActionError("player cannot pay rent to themselves")

    rent = calculate_rent(state, property_id, dice_total=dice_total)
    if rent <= 0:
        raise IllegalRuleActionError(f"{property_id} has no rent due")

    stream = _EventStream(event_id_prefix)
    state = _adjust_cash(stream, state, payer_id, -rent)
    return _adjust_cash(stream, state, ownership.owner_id, rent)


def pay_tax_for_space(
    state: GameState,
    player_id: str,
    space_id: str,
    event_id_prefix: str,
) -> GameState:
    _active_player_by_id(state, player_id)
    space = _space_by_id(space_id)
    if space.type != "tax" or space.amount is None:
        raise IllegalRuleActionError(f"{space_id} is not a tax space")
    return _adjust_cash(_EventStream(event_id_prefix), state, player_id, -space.amount)


def apply_card_effect(
    state: GameState,
    player_id: str,
    card_id: str,
    event_id_prefix: str,
    *,
    dice_total: int | None = None,
    apply_card_rent: bool = True,
) -> GameState:
    _active_player_by_id(state, player_id)
    card = _card_data(card_id)
    effect = card.effect
    effect_type = _effect_str(effect, "type")
    stream = _EventStream(event_id_prefix)

    if effect_type == "advance_to":
        target = _space_by_id(_effect_str(effect, "target"))
        return _move_player_to_position(
            stream,
            state,
            player_id,
            target.position,
            collect_go_salary=_effect_bool(effect, "collect_go_salary", default=False),
        )

    if effect_type == "advance_to_nearest_utility":
        target = _nearest_space_of_type(state, player_id, "utility")
        state = _move_player_to_position(stream, state, player_id, target.position, collect_go_salary=False)
        if target.property_id is None:
            return state
        ownership = _property_ownership(state, target.property_id)
        multiplier = _effect_int(effect, "rent_multiplier_if_owned", default=1)
        if (
            apply_card_rent
            and ownership.owner_id is not None
            and ownership.owner_id != player_id
            and dice_total is not None
        ):
            if dice_total <= 0:
                raise IllegalRuleActionError("dice total must be positive for utility card rent")
            rent = dice_total * multiplier
            state = _pay_card_rent_or_create_debt(
                stream,
                state,
                debtor_id=player_id,
                creditor_id=ownership.owner_id,
                amount=rent,
                reason=f"card_rent:{target.property_id}",
            )
        return state

    if effect_type == "advance_to_nearest_railroad":
        target = _nearest_space_of_type(state, player_id, "railroad")
        state = _move_player_to_position(stream, state, player_id, target.position, collect_go_salary=False)
        if target.property_id is None:
            return state
        ownership = _property_ownership(state, target.property_id)
        multiplier = _effect_int(effect, "rent_multiplier_if_owned", default=1)
        if apply_card_rent and ownership.owner_id is not None and ownership.owner_id != player_id:
            rent = calculate_rent(state, target.property_id) * multiplier
            if rent > 0:
                state = _pay_card_rent_or_create_debt(
                    stream,
                    state,
                    debtor_id=player_id,
                    creditor_id=ownership.owner_id,
                    amount=rent,
                    reason=f"card_rent:{target.property_id}",
                )
        return state

    if effect_type == "collect_from_bank":
        return _adjust_cash(stream, state, player_id, _effect_int(effect, "amount"))

    if effect_type == "pay_bank":
        return _adjust_cash(stream, state, player_id, -_effect_int(effect, "amount"))

    if effect_type == "get_out_of_jail":
        player = _player_by_id(state, player_id)
        if card_id in player.get_out_of_jail_card_ids:
            raise IllegalRuleActionError(f"{player_id} already holds {card_id}")
        state = _set_jail_cards(stream, state, player_id, (*player.get_out_of_jail_card_ids, card_id))
        return _remove_card_from_deck_discard(stream, state, card_id)

    if effect_type == "move_relative":
        player = _player_by_id(state, player_id)
        destination = (player.position + _effect_int(effect, "spaces")) % BOARD_SPACE_COUNT
        return _set_player_position(stream, state, player_id, destination)

    if effect_type == "go_to_jail":
        return send_player_to_jail(state, player_id, event_id_prefix)

    if effect_type == "building_repairs":
        house_count, hotel_count = _owned_improvement_counts(state, player_id)
        amount = (
            house_count * _effect_int(effect, "per_house")
            + hotel_count * _effect_int(effect, "per_hotel")
        )
        return _adjust_cash(stream, state, player_id, -amount)

    if effect_type == "pay_each_other_player":
        amount = _effect_int(effect, "amount")
        for other_player in _other_active_players(state, player_id):
            state = _adjust_cash(stream, state, player_id, -amount)
            state = _adjust_cash(stream, state, other_player.id, amount)
        return state

    if effect_type == "collect_from_each_player":
        amount = _effect_int(effect, "amount")
        for other_player in _other_active_players(state, player_id):
            state = _adjust_cash(stream, state, other_player.id, -amount)
            state = _adjust_cash(stream, state, player_id, amount)
        return state

    raise IllegalRuleActionError(f"unsupported card effect {effect_type}")


def send_player_to_jail(
    state: GameState,
    player_id: str,
    event_id_prefix: str,
) -> GameState:
    _active_player_by_id(state, player_id)
    stream = _EventStream(event_id_prefix)
    state = _set_player_position(stream, state, player_id, JAIL_POSITION)
    state = _set_jail(stream, state, player_id, in_jail=True, jail_turns=0)
    return _set_consecutive_doubles(stream, state, 0)


def pay_jail_fine(
    state: GameState,
    player_id: str,
    event_id_prefix: str,
) -> GameState:
    player = _active_player_by_id(state, player_id)
    if not player.in_jail:
        raise IllegalRuleActionError(f"{player_id} is not in jail")
    if player.cash < JAIL_FINE:
        raise IllegalRuleActionError("insufficient cash to pay jail fine")

    stream = _EventStream(event_id_prefix)
    state = _adjust_cash(stream, state, player_id, -JAIL_FINE)
    state = _set_jail(stream, state, player_id, in_jail=False, jail_turns=0)
    return _set_consecutive_doubles(stream, state, 0)


def use_get_out_of_jail_card(
    state: GameState,
    player_id: str,
    card_id: str,
    event_id_prefix: str,
) -> GameState:
    player = _active_player_by_id(state, player_id)
    if not player.in_jail:
        raise IllegalRuleActionError(f"{player_id} is not in jail")
    if card_id not in player.get_out_of_jail_card_ids:
        raise IllegalRuleActionError(f"{player_id} does not hold {card_id}")
    card = _card_data(card_id)
    if _effect_str(card.effect, "type") != "get_out_of_jail":
        raise IllegalRuleActionError(f"{card_id} is not a get-out-of-jail card")

    remaining_cards = tuple(held_card_id for held_card_id in player.get_out_of_jail_card_ids if held_card_id != card_id)
    stream = _EventStream(event_id_prefix)
    state = _set_jail_cards(stream, state, player_id, remaining_cards)
    state = _set_jail(stream, state, player_id, in_jail=False, jail_turns=0)
    state = _set_consecutive_doubles(stream, state, 0)
    return _return_card_to_deck_discard(stream, state, card_id)


def mortgage_property(
    state: GameState,
    player_id: str,
    property_id: str,
    event_id_prefix: str,
) -> GameState:
    _active_player_by_id(state, player_id)
    property_data = _property_data(property_id)
    ownership = _property_ownership(state, property_id)
    _validate_property_owned_by(ownership, player_id)
    if ownership.mortgaged:
        raise IllegalRuleActionError(f"{property_id} is already mortgaged")
    if property_data.kind == "street" and _group_has_improvements(state, property_data.group):
        raise IllegalRuleActionError("cannot mortgage while the color group has improvements")

    stream = _EventStream(event_id_prefix)
    state = _adjust_cash(stream, state, player_id, property_data.mortgage_value)
    return _set_property_mortgage(stream, state, property_id, True)


def unmortgage_property(
    state: GameState,
    player_id: str,
    property_id: str,
    event_id_prefix: str,
) -> GameState:
    player = _active_player_by_id(state, player_id)
    property_data = _property_data(property_id)
    ownership = _property_ownership(state, property_id)
    _validate_property_owned_by(ownership, player_id)
    if not ownership.mortgaged:
        raise IllegalRuleActionError(f"{property_id} is not mortgaged")

    cost = property_data.mortgage_value + _mortgage_interest(property_data.mortgage_value)
    if player.cash < cost:
        raise IllegalRuleActionError("insufficient cash to unmortgage property")

    stream = _EventStream(event_id_prefix)
    state = _adjust_cash(stream, state, player_id, -cost)
    return _set_property_mortgage(stream, state, property_id, False)


def buy_house(
    state: GameState,
    player_id: str,
    property_id: str,
    event_id_prefix: str,
) -> GameState:
    player = _active_player_by_id(state, player_id)
    property_data = _street_property_data(property_id)
    ownership = _property_ownership(state, property_id)
    _validate_property_owned_by(ownership, player_id)
    _validate_buildable_group(state, player_id, property_data.group)
    if ownership.hotel:
        raise IllegalRuleActionError(f"{property_id} already has a hotel")

    group = _property_group(property_data.group)
    levels = _group_improvement_levels(state, group)
    current_level = levels[property_id]
    if current_level != min(levels.values()):
        raise IllegalRuleActionError("building must follow the even building rule")

    cost = _house_cost(property_data)
    if player.cash < cost:
        raise IllegalRuleActionError("insufficient cash to buy improvement")

    stream = _EventStream(event_id_prefix)
    if ownership.houses < 4:
        updated_levels = {**levels, property_id: current_level + 1}
        if max(updated_levels.values()) - min(updated_levels.values()) > 1:
            raise IllegalRuleActionError("building must follow the even building rule")
        if state.bank_inventory.houses < 1:
            raise IllegalRuleActionError("bank has no houses available")

        state = _adjust_cash(stream, state, player_id, -cost)
        state = _set_bank_inventory(
            stream,
            state,
            houses=state.bank_inventory.houses - 1,
            hotels=state.bank_inventory.hotels,
        )
        return _set_property_improvements(stream, state, property_id, ownership.houses + 1, False)

    updated_levels = {**levels, property_id: 5}
    if max(updated_levels.values()) - min(updated_levels.values()) > 1:
        raise IllegalRuleActionError("building must follow the even building rule")
    if state.bank_inventory.hotels < 1:
        raise IllegalRuleActionError("bank has no hotels available")
    if state.bank_inventory.houses > 28:
        raise IllegalRuleActionError("bank cannot accept four returned houses")

    state = _adjust_cash(stream, state, player_id, -cost)
    state = _set_bank_inventory(
        stream,
        state,
        houses=state.bank_inventory.houses + 4,
        hotels=state.bank_inventory.hotels - 1,
    )
    return _set_property_improvements(stream, state, property_id, 0, True)


def sell_house(
    state: GameState,
    player_id: str,
    property_id: str,
    event_id_prefix: str,
) -> GameState:
    _active_player_by_id(state, player_id)
    property_data = _street_property_data(property_id)
    ownership = _property_ownership(state, property_id)
    _validate_property_owned_by(ownership, player_id)

    group = _property_group(property_data.group)
    levels = _group_improvement_levels(state, group)
    current_level = levels[property_id]
    if current_level <= 0:
        raise IllegalRuleActionError(f"{property_id} has no houses or hotel to sell")
    if current_level != max(levels.values()):
        raise IllegalRuleActionError("selling must follow the even building rule")

    proceeds = _house_cost(property_data) // 2
    stream = _EventStream(event_id_prefix)
    if ownership.hotel:
        if state.bank_inventory.houses < 4:
            raise IllegalRuleActionError("bank must have four houses to sell a hotel")
        if state.bank_inventory.hotels >= 12:
            raise IllegalRuleActionError("bank cannot accept another hotel")

        state = _adjust_cash(stream, state, player_id, proceeds)
        state = _set_bank_inventory(
            stream,
            state,
            houses=state.bank_inventory.houses - 4,
            hotels=state.bank_inventory.hotels + 1,
        )
        return _set_property_improvements(stream, state, property_id, 4, False)

    if state.bank_inventory.houses >= 32:
        raise IllegalRuleActionError("bank cannot accept another house")
    state = _adjust_cash(stream, state, player_id, proceeds)
    state = _set_bank_inventory(
        stream,
        state,
        houses=state.bank_inventory.houses + 1,
        hotels=state.bank_inventory.hotels,
    )
    return _set_property_improvements(stream, state, property_id, ownership.houses - 1, False)


def start_auction(
    state: GameState,
    property_id: str,
    event_id_prefix: str,
) -> GameState:
    _property_data(property_id)
    ownership = _property_ownership(state, property_id)
    if state.active_auction is not None:
        raise IllegalRuleActionError("there is already an active auction")
    if ownership.owner_id is not None:
        raise IllegalRuleActionError(f"{property_id} is already owned")

    return _set_active_auction(
        _EventStream(event_id_prefix),
        state,
        property_id=property_id,
        high_bidder_id=None,
        high_bid_amount=None,
        passed_player_ids=(),
    )


def place_auction_bid(
    state: GameState,
    player_id: str,
    amount: int,
    event_id_prefix: str,
) -> GameState:
    player = _active_player_by_id(state, player_id)
    auction = state.active_auction
    if auction is None:
        raise IllegalRuleActionError("there is no active auction")
    if player_id in auction.passed_player_ids:
        raise IllegalRuleActionError(f"{player_id} has already passed")
    if player_id == auction.high_bidder_id:
        raise IllegalRuleActionError("player cannot increase their own high bid")
    if amount <= 0:
        raise IllegalRuleActionError("auction bid must be positive")
    if auction.high_bid_amount is not None and amount <= auction.high_bid_amount:
        raise IllegalRuleActionError("auction bid must increase the current high bid")
    if player.cash < amount:
        raise IllegalRuleActionError("insufficient cash for auction bid")

    return _set_active_auction(
        _EventStream(event_id_prefix),
        state,
        property_id=auction.property_id,
        high_bidder_id=player_id,
        high_bid_amount=amount,
        passed_player_ids=auction.passed_player_ids,
    )


def pass_auction(
    state: GameState,
    player_id: str,
    event_id_prefix: str,
) -> GameState:
    _active_player_by_id(state, player_id)
    auction = state.active_auction
    if auction is None:
        raise IllegalRuleActionError("there is no active auction")
    if player_id in auction.passed_player_ids:
        raise IllegalRuleActionError(f"{player_id} has already passed")
    if player_id == auction.high_bidder_id:
        raise IllegalRuleActionError("player cannot pass while holding the high bid")

    return _set_active_auction(
        _EventStream(event_id_prefix),
        state,
        property_id=auction.property_id,
        high_bidder_id=auction.high_bidder_id,
        high_bid_amount=auction.high_bid_amount,
        passed_player_ids=(*auction.passed_player_ids, player_id),
    )


def close_auction(state: GameState, event_id_prefix: str) -> GameState:
    auction = state.active_auction
    if auction is None:
        raise IllegalRuleActionError("there is no active auction")

    stream = _EventStream(event_id_prefix)
    if auction.high_bidder_id is not None and auction.high_bid_amount is not None:
        high_bidder = _active_player_by_id(state, auction.high_bidder_id)
        if high_bidder.cash < auction.high_bid_amount:
            raise IllegalRuleActionError("high bidder no longer has enough cash")
        state = _adjust_cash(stream, state, auction.high_bidder_id, -auction.high_bid_amount)
        state = _set_property_owner(stream, state, auction.property_id, auction.high_bidder_id)

    return stream.apply(state, "ACTIVE_AUCTION_SET", ActiveAuctionSetPayload(active=False))


def declare_bankruptcy(
    state: GameState,
    player_id: str,
    creditor_id: str | None,
    event_id_prefix: str,
) -> GameState:
    player = _active_player_by_id(state, player_id)
    if creditor_id == player_id:
        raise IllegalRuleActionError("bankrupt player cannot be their own creditor")
    if creditor_id is not None:
        _active_player_by_id(state, creditor_id)

    stream = _EventStream(event_id_prefix)
    owned_properties = [
        ownership for ownership in state.property_ownership if ownership.owner_id == player_id
    ]

    if creditor_id is None:
        returned_houses = sum(ownership.houses for ownership in owned_properties)
        returned_hotels = sum(1 for ownership in owned_properties if ownership.hotel)
        if state.bank_inventory.houses + returned_houses > 32:
            raise IllegalRuleActionError("bank cannot accept returned houses")
        if state.bank_inventory.hotels + returned_hotels > 12:
            raise IllegalRuleActionError("bank cannot accept returned hotels")

        if player.cash != 0:
            state = _adjust_cash(stream, state, player_id, -player.cash)
        if returned_houses or returned_hotels:
            state = _set_bank_inventory(
                stream,
                state,
                houses=state.bank_inventory.houses + returned_houses,
                hotels=state.bank_inventory.hotels + returned_hotels,
            )
        for ownership in owned_properties:
            if ownership.hotel or ownership.houses:
                state = _set_property_improvements(stream, state, ownership.property_id, 0, False)
            if ownership.mortgaged:
                state = _set_property_mortgage(stream, state, ownership.property_id, False)
            state = _set_property_owner(stream, state, ownership.property_id, None)
    else:
        if player.cash != 0:
            state = _adjust_cash(stream, state, player_id, -player.cash)
            state = _adjust_cash(stream, state, creditor_id, player.cash)
        for ownership in owned_properties:
            state = _set_property_owner(stream, state, ownership.property_id, creditor_id)

    held_jail_cards = player.get_out_of_jail_card_ids
    if held_jail_cards:
        state = _set_jail_cards(stream, state, player_id, ())
        for card_id in held_jail_cards:
            state = _return_card_to_deck_discard(stream, state, card_id)
    if player.in_jail:
        state = _set_jail(stream, state, player_id, in_jail=False, jail_turns=0)
    return stream.apply(
        state,
        "PLAYER_BANKRUPTCY_SET",
        PlayerBankruptcySetPayload(player_id=player_id, is_bankrupt=True),
    )


def is_game_over(state: GameState) -> bool:
    return len(_active_players(state)) <= 1


def winning_player_id(state: GameState) -> str | None:
    active_players = _active_players(state)
    return active_players[0].id if len(active_players) == 1 else None


def _validate_die(value: int) -> None:
    if value < 1 or value > 6:
        raise IllegalRuleActionError("dice values must be between 1 and 6")


def _adjust_cash(stream: _EventStream, state: GameState, player_id: str, amount: int) -> GameState:
    if amount == 0:
        return state
    return stream.apply(
        state,
        "PLAYER_CASH_DELTA",
        PlayerCashDeltaPayload(player_id=player_id, amount=amount),
    )


def _set_player_position(
    stream: _EventStream,
    state: GameState,
    player_id: str,
    position: int,
) -> GameState:
    return stream.apply(
        state,
        "PLAYER_POSITION_SET",
        PlayerPositionSetPayload(player_id=player_id, position=position),
    )


def _set_jail(
    stream: _EventStream,
    state: GameState,
    player_id: str,
    *,
    in_jail: bool,
    jail_turns: int,
) -> GameState:
    return stream.apply(
        state,
        "PLAYER_JAIL_SET",
        PlayerJailSetPayload(player_id=player_id, in_jail=in_jail, jail_turns=jail_turns),
    )


def _set_jail_cards(
    stream: _EventStream,
    state: GameState,
    player_id: str,
    card_ids: tuple[str, ...],
) -> GameState:
    return stream.apply(
        state,
        "PLAYER_JAIL_CARDS_SET",
        PlayerJailCardsSetPayload(player_id=player_id, card_ids=card_ids),
    )


def _set_consecutive_doubles(stream: _EventStream, state: GameState, count: int) -> GameState:
    if state.turn.consecutive_doubles == count:
        return state
    return stream.apply(
        state,
        "TURN_STATE_SET",
        TurnStateSetPayload(
            turn_number=state.turn.turn_number,
            current_player_index=state.turn.current_player_index,
            current_player_id=state.turn.current_player_id,
            phase=state.turn.phase,
            consecutive_doubles=count,
        ),
    )


def _pay_card_rent_or_create_debt(
    stream: _EventStream,
    state: GameState,
    *,
    debtor_id: str,
    creditor_id: str,
    amount: int,
    reason: str,
) -> GameState:
    debtor = _player_by_id(state, debtor_id)
    if debtor.cash < amount:
        return stream.apply(
            state,
            "ACTIVE_PAYMENT_SET",
            ActivePaymentSetPayload(
                active=True,
                debtor_id=debtor_id,
                creditor_id=creditor_id,
                amount_owed=amount,
                amount_paid=0,
                reason=reason,
                negotiation_allowed=True,
            ),
        )

    state = _adjust_cash(stream, state, debtor_id, -amount)
    return _adjust_cash(stream, state, creditor_id, amount)


def _set_property_owner(
    stream: _EventStream,
    state: GameState,
    property_id: str,
    owner_id: str | None,
) -> GameState:
    return stream.apply(
        state,
        "PROPERTY_OWNER_SET",
        PropertyOwnerSetPayload(property_id=property_id, owner_id=owner_id),
    )


def _set_property_mortgage(
    stream: _EventStream,
    state: GameState,
    property_id: str,
    mortgaged: bool,
) -> GameState:
    return stream.apply(
        state,
        "PROPERTY_MORTGAGE_SET",
        PropertyMortgageSetPayload(property_id=property_id, mortgaged=mortgaged),
    )


def _set_property_improvements(
    stream: _EventStream,
    state: GameState,
    property_id: str,
    houses: int,
    hotel: bool,
) -> GameState:
    return stream.apply(
        state,
        "PROPERTY_IMPROVEMENTS_SET",
        PropertyImprovementsSetPayload(property_id=property_id, houses=houses, hotel=hotel),
    )


def _set_bank_inventory(
    stream: _EventStream,
    state: GameState,
    *,
    houses: int,
    hotels: int,
) -> GameState:
    return stream.apply(
        state,
        "BANK_INVENTORY_SET",
        BankInventorySetPayload(houses=houses, hotels=hotels),
    )


def _set_active_auction(
    stream: _EventStream,
    state: GameState,
    *,
    property_id: str,
    high_bidder_id: str | None,
    high_bid_amount: int | None,
    passed_player_ids: tuple[str, ...],
) -> GameState:
    return stream.apply(
        state,
        "ACTIVE_AUCTION_SET",
        ActiveAuctionSetPayload(
            active=True,
            property_id=property_id,
            high_bidder_id=high_bidder_id,
            high_bid_amount=high_bid_amount,
            passed_player_ids=passed_player_ids,
        ),
    )


def _set_deck_state(
    stream: _EventStream,
    state: GameState,
    *,
    deck: str,
    draw_pile: tuple[str, ...],
    discard_pile: tuple[str, ...],
) -> GameState:
    return stream.apply(
        state,
        "DECK_STATE_SET",
        DeckStateSetPayload(
            deck=cast(Any, deck),
            draw_pile=draw_pile,
            discard_pile=discard_pile,
        ),
    )


def _move_player_to_position(
    stream: _EventStream,
    state: GameState,
    player_id: str,
    position: int,
    *,
    collect_go_salary: bool,
) -> GameState:
    player = _player_by_id(state, player_id)
    should_collect_go = collect_go_salary and position <= player.position
    state = _set_player_position(stream, state, player_id, position)
    if should_collect_go:
        state = _adjust_cash(stream, state, player_id, GO_SALARY)
    return state


def _remove_card_from_deck_discard(stream: _EventStream, state: GameState, card_id: str) -> GameState:
    card = _card_data(card_id)
    deck_state = _deck_state_for_card(state, card)
    if card_id not in deck_state.draw_pile and card_id not in deck_state.discard_pile:
        return state
    return _set_deck_state(
        stream,
        state,
        deck=card.deck,
        draw_pile=tuple(current_card_id for current_card_id in deck_state.draw_pile if current_card_id != card_id),
        discard_pile=tuple(current_card_id for current_card_id in deck_state.discard_pile if current_card_id != card_id),
    )


def _return_card_to_deck_discard(stream: _EventStream, state: GameState, card_id: str) -> GameState:
    card = _card_data(card_id)
    deck_state = _deck_state_for_card(state, card)
    if card_id in deck_state.draw_pile or card_id in deck_state.discard_pile:
        return state
    return _set_deck_state(
        stream,
        state,
        deck=card.deck,
        draw_pile=(*deck_state.draw_pile, card_id),
        discard_pile=deck_state.discard_pile,
    )


def _deck_state_for_card(state: GameState, card: CardData):
    if card.deck == "chance":
        return state.decks.chance
    return state.decks.community_chest


def _active_player_by_id(state: GameState, player_id: str) -> PlayerState:
    player = _player_by_id(state, player_id)
    if player.is_bankrupt:
        raise IllegalRuleActionError(f"{player_id} is bankrupt")
    return player


def _player_by_id(state: GameState, player_id: str) -> PlayerState:
    for player in state.players:
        if player.id == player_id:
            return player
    raise IllegalRuleActionError(f"unknown player {player_id}")


def _active_players(state: GameState) -> tuple[PlayerState, ...]:
    return tuple(player for player in state.players if not player.is_bankrupt)


def _other_active_players(state: GameState, player_id: str) -> tuple[PlayerState, ...]:
    return tuple(player for player in state.players if player.id != player_id and not player.is_bankrupt)


def _property_ownership(state: GameState, property_id: str) -> PropertyOwnershipState:
    for ownership in state.property_ownership:
        if ownership.property_id == property_id:
            return ownership
    raise IllegalRuleActionError(f"unknown property {property_id}")


def _validate_property_owned_by(ownership: PropertyOwnershipState, player_id: str) -> None:
    if ownership.owner_id != player_id:
        raise IllegalRuleActionError(f"{ownership.property_id} is not owned by {player_id}")


def _owned_property_count_in_group(state: GameState, group_id: str, owner_id: str | None) -> int:
    if owner_id is None:
        return 0
    group = _property_group(group_id)
    return sum(
        1
        for property_id in group.property_ids
        if _property_ownership(state, property_id).owner_id == owner_id
    )


def _validate_buildable_group(state: GameState, player_id: str, group_id: str) -> None:
    group = _property_group(group_id)
    ownerships = [_property_ownership(state, property_id) for property_id in group.property_ids]
    if not all(ownership.owner_id == player_id for ownership in ownerships):
        raise IllegalRuleActionError("player must own the full color group monopoly")
    if any(ownership.mortgaged for ownership in ownerships):
        raise IllegalRuleActionError("cannot build while a group property is mortgaged")


def _group_has_improvements(state: GameState, group_id: str) -> bool:
    group = _property_group(group_id)
    return any(
        ownership.houses > 0 or ownership.hotel
        for ownership in (_property_ownership(state, property_id) for property_id in group.property_ids)
    )


def _group_improvement_levels(state: GameState, group: PropertyGroup) -> dict[str, int]:
    levels: dict[str, int] = {}
    for property_id in group.property_ids:
        ownership = _property_ownership(state, property_id)
        levels[property_id] = 5 if ownership.hotel else ownership.houses
    return levels


def _owned_improvement_counts(state: GameState, player_id: str) -> tuple[int, int]:
    house_count = 0
    hotel_count = 0
    for ownership in state.property_ownership:
        if ownership.owner_id != player_id:
            continue
        house_count += ownership.houses
        if ownership.hotel:
            hotel_count += 1
    return house_count, hotel_count


def _mortgage_interest(mortgage_value: int) -> int:
    return (mortgage_value + 9) // 10


def _street_rents(property_data: PropertyData) -> tuple[int, int, int, int, int, int]:
    if property_data.rents is None:
        raise IllegalRuleActionError(f"{property_data.id} does not define street rent")
    return property_data.rents


def _house_cost(property_data: PropertyData) -> int:
    if property_data.house_cost is None:
        raise IllegalRuleActionError(f"{property_data.id} does not define house cost")
    return property_data.house_cost


def _street_property_data(property_id: str) -> PropertyData:
    property_data = _property_data(property_id)
    if property_data.kind != "street":
        raise IllegalRuleActionError(f"{property_id} is not a street property")
    return property_data


def _property_data(property_id: str) -> PropertyData:
    for property_data in _classic_data().properties:
        if property_data.id == property_id:
            return property_data
    raise IllegalRuleActionError(f"unknown property {property_id}")


def _property_group(group_id: str) -> PropertyGroup:
    for group in _classic_data().property_groups:
        if group.id == group_id:
            return group
    raise IllegalRuleActionError(f"unknown property group {group_id}")


def _space_by_id(space_id: str) -> BoardSpace:
    for space in _classic_data().board:
        if space.id == space_id:
            return space
    raise IllegalRuleActionError(f"unknown board space {space_id}")


def _nearest_space_of_type(state: GameState, player_id: str, space_type: str) -> BoardSpace:
    player = _player_by_id(state, player_id)
    matching_spaces = [space for space in _classic_data().board if space.type == space_type]
    for offset in range(1, BOARD_SPACE_COUNT + 1):
        position = (player.position + offset) % BOARD_SPACE_COUNT
        for space in matching_spaces:
            if space.position == position:
                return space
    raise IllegalRuleActionError(f"no {space_type} space exists")


def _card_data(card_id: str) -> CardData:
    for card in (*_classic_data().decks.chance, *_classic_data().decks.community_chest):
        if card.id == card_id:
            return card
    raise IllegalRuleActionError(f"unknown card {card_id}")


def _effect_str(effect: Mapping[str, Any], key: str) -> str:
    value = effect.get(key)
    if not isinstance(value, str):
        raise IllegalRuleActionError(f"card effect field {key} must be a string")
    return value


def _effect_int(effect: Mapping[str, Any], key: str, default: int | None = None) -> int:
    value = effect.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool):
        raise IllegalRuleActionError(f"card effect field {key} must be an integer")
    return value


def _effect_bool(effect: Mapping[str, Any], key: str, *, default: bool) -> bool:
    value = effect.get(key, default)
    if not isinstance(value, bool):
        raise IllegalRuleActionError(f"card effect field {key} must be a boolean")
    return value


def _classic_data() -> ClassicMonopolyData:
    return load_classic_monopoly_data()


def _next_active_player_index(state: GameState) -> int:
    current_index = state.turn.current_player_index
    for offset in range(1, len(state.players) + 1):
        candidate_index = (current_index + offset) % len(state.players)
        if not state.players[candidate_index].is_bankrupt:
            return candidate_index
    raise IllegalRuleActionError("no active player is available for the next turn")


__all__ = [
    "IllegalRuleActionError",
    "apply_card_effect",
    "apply_dice_roll",
    "buy_house",
    "buy_property",
    "calculate_rent",
    "close_auction",
    "declare_bankruptcy",
    "end_turn",
    "is_game_over",
    "mortgage_property",
    "move_player_steps",
    "pass_auction",
    "pay_jail_fine",
    "pay_rent",
    "pay_tax_for_space",
    "place_auction_bid",
    "sell_house",
    "send_player_to_jail",
    "start_auction",
    "unmortgage_property",
    "use_get_out_of_jail_card",
    "winning_player_id",
]
