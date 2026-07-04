from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, TypeVar, cast

from pydantic import ValidationError

from app.rules.events import (
    ActiveAuctionSetPayload,
    ActiveBankruptcySetPayload,
    ActiveNegotiationSetPayload,
    ActivePaymentSetPayload,
    BankInventorySetPayload,
    CardDrawnPayload,
    DeckShuffledPayload,
    DeckStateSetPayload,
    DiceRolledPayload,
    EventModel,
    GameEvent,
    InvalidEventError,
    PlayerBankruptcySetPayload,
    PlayerCashDeltaPayload,
    PlayerJailCardsSetPayload,
    PlayerJailSetPayload,
    PlayerPositionSetPayload,
    PropertyImprovementsSetPayload,
    PropertyMortgageSetPayload,
    PropertyOwnerSetPayload,
    TurnStateSetPayload,
    payload_model_for_event_type,
)
from app.rules.static_data import load_classic_monopoly_data
from app.rules.state import (
    ActiveAuctionState,
    ActiveBankruptcyState,
    ActiveNegotiationState,
    ActivePaymentState,
    BankInventoryState,
    DeckCollectionState,
    DeckState,
    GameState,
    PlayerSetup,
    PlayerState,
    PropertyOwnershipState,
    RngState,
    TurnPhase,
    TurnState,
    create_initial_game_state,
)


PayloadT = TypeVar("PayloadT", bound=EventModel)


def apply_event(state: GameState, event: GameEvent) -> GameState:
    accepted_event = _validate_event(event)
    _validate_sequence(state, accepted_event)
    _validate_duplicate_event_id(state, accepted_event)

    try:
        updates = _updates_for_event(state, accepted_event)
        updates["event_sequence"] = accepted_event.sequence
        updates["applied_event_ids"] = (*state.applied_event_ids, accepted_event.event_id)
        return _build_game_state(state, updates)
    except InvalidEventError:
        raise
    except (TypeError, ValueError, ValidationError) as exc:
        raise InvalidEventError(f"invalid event payload: {exc}") from exc


def replay_events(
    seed: str,
    players: Sequence[PlayerSetup],
    game_id: str,
    events: Sequence[GameEvent],
) -> GameState:
    state = create_initial_game_state(seed=seed, players=players, game_id=game_id)
    for event in events:
        state = apply_event(state, event)
    return state


def _validate_event(event: GameEvent) -> GameEvent:
    event_type = getattr(event, "type", None)
    if not isinstance(event_type, str):
        raise InvalidEventError("unknown event type")

    payload_model = payload_model_for_event_type(event_type)
    if payload_model is None:
        raise InvalidEventError(f"unknown event type {event_type}")

    payload = getattr(event, "payload", None)
    if event_type in {"DECK_STATE_SET", "DECK_SHUFFLED", "CARD_DRAWN"}:
        deck_name = _raw_payload_value(payload, "deck")
        if deck_name not in ("chance", "community_chest"):
            raise InvalidEventError(f"unknown deck {deck_name}")

    if isinstance(event, GameEvent) and not isinstance(payload, payload_model):
        raise InvalidEventError("event payload does not match event type")

    try:
        return GameEvent.model_validate(
            {
                "event_id": getattr(event, "event_id", None),
                "sequence": getattr(event, "sequence", None),
                "type": event_type,
                "payload": _payload_data(payload),
            }
        )
    except (TypeError, ValueError, ValidationError) as exc:
        raise InvalidEventError(f"invalid event payload: {exc}") from exc


def _raw_payload_value(payload: object, field_name: str) -> object:
    if isinstance(payload, Mapping):
        return payload.get(field_name)
    return getattr(payload, field_name, None)


def _payload_data(payload: object) -> object:
    if isinstance(payload, EventModel):
        return payload.model_dump(mode="python")
    return payload


def _validate_sequence(state: GameState, event: GameEvent) -> None:
    expected_sequence = state.event_sequence + 1
    if event.sequence != expected_sequence:
        raise InvalidEventError(
            f"event sequence {event.sequence} does not match expected sequence {expected_sequence}"
        )


def _validate_duplicate_event_id(state: GameState, event: GameEvent) -> None:
    if event.event_id in state.applied_event_ids:
        raise InvalidEventError(f"duplicate event id {event.event_id}")


def _updates_for_event(state: GameState, event: GameEvent) -> dict[str, Any]:
    if event.type == "PLAYER_CASH_DELTA":
        payload = _expect_payload(event, PlayerCashDeltaPayload)
        player = _player_by_id(state, payload.player_id)
        return {
            "players": _replace_player(
                state,
                payload.player_id,
                {"cash": player.cash + payload.amount},
            )
        }

    if event.type == "PLAYER_POSITION_SET":
        payload = _expect_payload(event, PlayerPositionSetPayload)
        return {"players": _replace_player(state, payload.player_id, {"position": payload.position})}

    if event.type == "PLAYER_JAIL_SET":
        payload = _expect_payload(event, PlayerJailSetPayload)
        return {
            "players": _replace_player(
                state,
                payload.player_id,
                {"in_jail": payload.in_jail, "jail_turns": payload.jail_turns},
            )
        }

    if event.type == "PLAYER_BANKRUPTCY_SET":
        payload = _expect_payload(event, PlayerBankruptcySetPayload)
        return {
            "players": _replace_player(
                state,
                payload.player_id,
                {"is_bankrupt": payload.is_bankrupt},
            )
        }

    if event.type == "PLAYER_JAIL_CARDS_SET":
        payload = _expect_payload(event, PlayerJailCardsSetPayload)
        _validate_player_card_ids(payload.card_ids)
        return {
            "players": _replace_player(
                state,
                payload.player_id,
                {"get_out_of_jail_card_ids": payload.card_ids},
            )
        }

    if event.type == "PROPERTY_OWNER_SET":
        payload = _expect_payload(event, PropertyOwnerSetPayload)
        _validate_property_owner_reference(state, payload.owner_id)
        return {
            "property_ownership": _replace_property(
                state,
                payload.property_id,
                {"owner_id": payload.owner_id},
            )
        }

    if event.type == "PROPERTY_MORTGAGE_SET":
        payload = _expect_payload(event, PropertyMortgageSetPayload)
        return {
            "property_ownership": _replace_property(
                state,
                payload.property_id,
                {"mortgaged": payload.mortgaged},
            )
        }

    if event.type == "PROPERTY_IMPROVEMENTS_SET":
        payload = _expect_payload(event, PropertyImprovementsSetPayload)
        return {
            "property_ownership": _replace_property(
                state,
                payload.property_id,
                {"houses": payload.houses, "hotel": payload.hotel},
            )
        }

    if event.type == "BANK_INVENTORY_SET":
        payload = _expect_payload(event, BankInventorySetPayload)
        return {"bank_inventory": BankInventoryState(houses=payload.houses, hotels=payload.hotels)}

    if event.type == "DICE_ROLLED":
        payload = _expect_payload(event, DiceRolledPayload)
        _validate_dice_rolled_payload(state, payload)
        return {"rng": _replace_rng_state(state, {"dice_roll_count": payload.roll_counter})}

    if event.type == "DECK_STATE_SET":
        payload = _expect_payload(event, DeckStateSetPayload)
        _validate_deck_state_payload(payload)
        deck_updates = state.decks.model_dump(mode="python")
        deck_updates[payload.deck] = DeckState(
            draw_pile=payload.draw_pile,
            discard_pile=payload.discard_pile,
        )
        return {"decks": DeckCollectionState.model_validate(deck_updates)}

    if event.type == "DECK_SHUFFLED":
        payload = _expect_payload(event, DeckShuffledPayload)
        _validate_deck_shuffled_payload(state, payload)
        deck_updates = state.decks.model_dump(mode="python")
        deck_updates[payload.deck] = DeckState(draw_pile=payload.draw_pile, discard_pile=())
        return {
            "decks": DeckCollectionState.model_validate(deck_updates),
            "rng": _replace_rng_state(
                state,
                {_shuffle_counter_field(payload.deck): payload.shuffle_counter},
            ),
        }

    if event.type == "CARD_DRAWN":
        payload = _expect_payload(event, CardDrawnPayload)
        deck_state = _validate_card_drawn_payload(state, payload)
        deck_updates = state.decks.model_dump(mode="python")
        deck_updates[payload.deck] = DeckState(
            draw_pile=deck_state.draw_pile[1:],
            discard_pile=(*deck_state.discard_pile, payload.card_id),
        )
        return {
            "decks": DeckCollectionState.model_validate(deck_updates),
            "rng": _replace_rng_state(
                state,
                {_draw_counter_field(payload.deck): payload.draw_counter},
            ),
        }

    if event.type == "TURN_STATE_SET":
        payload = _expect_payload(event, TurnStateSetPayload)
        _validate_turn_payload(state, payload)
        return {
            "turn": TurnState(
                turn_number=payload.turn_number,
                current_player_index=payload.current_player_index,
                current_player_id=payload.current_player_id,
                phase=cast(TurnPhase, payload.phase),
                consecutive_doubles=payload.consecutive_doubles,
            )
        }

    if event.type == "ACTIVE_PAYMENT_SET":
        payload = _expect_payload(event, ActivePaymentSetPayload)
        return {"active_payment": ActivePaymentState() if payload.active else None}

    if event.type == "ACTIVE_AUCTION_SET":
        payload = _expect_payload(event, ActiveAuctionSetPayload)
        return {"active_auction": ActiveAuctionState() if payload.active else None}

    if event.type == "ACTIVE_NEGOTIATION_SET":
        payload = _expect_payload(event, ActiveNegotiationSetPayload)
        return {"active_negotiation": ActiveNegotiationState() if payload.active else None}

    if event.type == "ACTIVE_BANKRUPTCY_SET":
        payload = _expect_payload(event, ActiveBankruptcySetPayload)
        return {"active_bankruptcy": ActiveBankruptcyState() if payload.active else None}

    raise InvalidEventError(f"unknown event type {event.type}")


def _expect_payload(event: GameEvent, payload_type: type[PayloadT]) -> PayloadT:
    if not isinstance(event.payload, payload_type):
        raise InvalidEventError("event payload does not match event type")
    return event.payload


def _player_by_id(state: GameState, player_id: str) -> PlayerState:
    for player in state.players:
        if player.id == player_id:
            return player
    raise InvalidEventError(f"unknown player {player_id}")


def _replace_player(state: GameState, player_id: str, updates: Mapping[str, object]) -> tuple[PlayerState, ...]:
    player = _player_by_id(state, player_id)
    updated_player = PlayerState.model_validate({**player.model_dump(mode="python"), **updates})
    return tuple(updated_player if current.id == player_id else current for current in state.players)


def _property_by_id(state: GameState, property_id: str) -> PropertyOwnershipState:
    for ownership in state.property_ownership:
        if ownership.property_id == property_id:
            return ownership
    raise InvalidEventError(f"unknown property {property_id}")


def _replace_property(
    state: GameState,
    property_id: str,
    updates: Mapping[str, object],
) -> tuple[PropertyOwnershipState, ...]:
    ownership = _property_by_id(state, property_id)
    updated_ownership = PropertyOwnershipState.model_validate(
        {**ownership.model_dump(mode="python"), **updates}
    )
    return tuple(
        updated_ownership if current.property_id == property_id else current
        for current in state.property_ownership
    )


def _validate_property_owner_reference(state: GameState, owner_id: str | None) -> None:
    if owner_id is None:
        return
    if owner_id not in {player.id for player in state.players}:
        raise InvalidEventError(f"unknown owner {owner_id}")


def _validate_deck_state_payload(payload: DeckStateSetPayload) -> None:
    _validate_full_deck_membership(payload.deck, (*payload.draw_pile, *payload.discard_pile))


def _validate_dice_rolled_payload(state: GameState, payload: DiceRolledPayload) -> None:
    _player_by_id(state, payload.player_id)
    expected_counter = state.rng.dice_roll_count + 1
    if payload.roll_counter != expected_counter:
        raise InvalidEventError(
            f"dice roll counter {payload.roll_counter} does not match expected "
            f"roll counter {expected_counter}"
        )
    if not 1 <= payload.die_1 <= 6 or not 1 <= payload.die_2 <= 6:
        raise InvalidEventError("dice values must be between 1 and 6")
    if payload.total != payload.die_1 + payload.die_2:
        raise InvalidEventError("dice total must equal die_1 plus die_2")
    if payload.is_doubles != (payload.die_1 == payload.die_2):
        raise InvalidEventError("dice doubles flag must match dice values")


def _validate_deck_shuffled_payload(state: GameState, payload: DeckShuffledPayload) -> None:
    expected_counter = _shuffle_counter(state, payload.deck) + 1
    if payload.shuffle_counter != expected_counter:
        raise InvalidEventError(
            f"{payload.deck} shuffle counter {payload.shuffle_counter} does not match expected "
            f"shuffle counter {expected_counter}"
        )
    _validate_full_deck_membership(payload.deck, payload.draw_pile)


def _validate_card_drawn_payload(state: GameState, payload: CardDrawnPayload) -> DeckState:
    expected_counter = _draw_counter(state, payload.deck) + 1
    if payload.draw_counter != expected_counter:
        raise InvalidEventError(
            f"{payload.deck} draw counter {payload.draw_counter} does not match expected "
            f"draw counter {expected_counter}"
        )

    deck_state = _deck_state_for_name(state, payload.deck)
    if not deck_state.draw_pile:
        raise InvalidEventError(f"{payload.deck} draw pile is empty")
    expected_card_id = deck_state.draw_pile[0]
    if payload.card_id != expected_card_id:
        raise InvalidEventError(
            f"card draw {payload.card_id} does not match current top card {expected_card_id}"
        )
    return deck_state


def _validate_full_deck_membership(deck_name: str, card_ids: tuple[str, ...]) -> None:
    expected_card_ids = _card_ids_for_deck(deck_name)
    event_card_ids = set(card_ids)
    unknown_card_ids = event_card_ids - expected_card_ids
    if unknown_card_ids:
        unknown_card_id = sorted(unknown_card_ids)[0]
        raise InvalidEventError(f"unknown card {unknown_card_id}")
    if len(card_ids) != len(set(card_ids)):
        raise InvalidEventError(f"{deck_name} deck cannot contain duplicate cards")
    if event_card_ids != expected_card_ids:
        raise InvalidEventError(f"{deck_name} deck state must contain every deck card exactly once")


def _validate_player_card_ids(card_ids: tuple[str, ...]) -> None:
    known_card_ids = _all_card_ids()
    unknown_card_ids = set(card_ids) - known_card_ids
    if unknown_card_ids:
        unknown_card_id = sorted(unknown_card_ids)[0]
        raise InvalidEventError(f"unknown card {unknown_card_id}")


def _validate_turn_payload(state: GameState, payload: TurnStateSetPayload) -> None:
    if payload.current_player_index >= len(state.players):
        raise InvalidEventError(f"unknown player index {payload.current_player_index}")
    if payload.current_player_id not in {player.id for player in state.players}:
        raise InvalidEventError(f"unknown player {payload.current_player_id}")
    if state.players[payload.current_player_index].id != payload.current_player_id:
        raise InvalidEventError("current player id must match current player index")
    if payload.phase != "START_TURN":
        raise InvalidEventError(f"unsupported turn phase {payload.phase}")


def _card_ids_for_deck(deck_name: str) -> set[str]:
    data = load_classic_monopoly_data()
    if deck_name == "chance":
        return {card.id for card in data.decks.chance}
    if deck_name == "community_chest":
        return {card.id for card in data.decks.community_chest}
    raise InvalidEventError(f"unknown deck {deck_name}")


def _all_card_ids() -> set[str]:
    data = load_classic_monopoly_data()
    return {card.id for card in (*data.decks.chance, *data.decks.community_chest)}


def _deck_state_for_name(state: GameState, deck_name: str) -> DeckState:
    if deck_name == "chance":
        return state.decks.chance
    if deck_name == "community_chest":
        return state.decks.community_chest
    raise InvalidEventError(f"unknown deck {deck_name}")


def _draw_counter(state: GameState, deck_name: str) -> int:
    if deck_name == "chance":
        return state.rng.chance_draw_count
    if deck_name == "community_chest":
        return state.rng.community_chest_draw_count
    raise InvalidEventError(f"unknown deck {deck_name}")


def _shuffle_counter(state: GameState, deck_name: str) -> int:
    if deck_name == "chance":
        return state.rng.chance_shuffle_count
    if deck_name == "community_chest":
        return state.rng.community_chest_shuffle_count
    raise InvalidEventError(f"unknown deck {deck_name}")


def _draw_counter_field(deck_name: str) -> str:
    if deck_name == "chance":
        return "chance_draw_count"
    if deck_name == "community_chest":
        return "community_chest_draw_count"
    raise InvalidEventError(f"unknown deck {deck_name}")


def _shuffle_counter_field(deck_name: str) -> str:
    if deck_name == "chance":
        return "chance_shuffle_count"
    if deck_name == "community_chest":
        return "community_chest_shuffle_count"
    raise InvalidEventError(f"unknown deck {deck_name}")


def _replace_rng_state(state: GameState, updates: Mapping[str, object]) -> RngState:
    return RngState.model_validate({**state.rng.model_dump(mode="python"), **updates})


def _build_game_state(state: GameState, updates: Mapping[str, object]) -> GameState:
    return GameState.model_validate({**state.model_dump(mode="python"), **updates})


__all__ = ["InvalidEventError", "apply_event", "replay_events"]
