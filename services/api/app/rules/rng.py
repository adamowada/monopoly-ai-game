from __future__ import annotations

from typing import Literal, TypeAlias

from app.rules.deterministic import bounded_int, deterministic_shuffle
from app.rules.events import (
    CardDrawnPayload,
    DeckShuffledPayload,
    DiceRolledPayload,
    GameEvent,
    InvalidEventError,
)
from app.rules.state import DeckState, GameState


DeckName: TypeAlias = Literal["chance", "community_chest"]


def generate_dice_roll_event(state: GameState, event_id: str, player_id: str) -> GameEvent:
    roll_counter = state.rng.dice_roll_count + 1
    die_1 = bounded_int(state.rng.seed, 1, 6, "dice", roll_counter, "die_1")
    die_2 = bounded_int(state.rng.seed, 1, 6, "dice", roll_counter, "die_2")

    return GameEvent(
        event_id=event_id,
        sequence=state.event_sequence + 1,
        type="DICE_ROLLED",
        payload=DiceRolledPayload(
            player_id=player_id,
            die_1=die_1,
            die_2=die_2,
            total=die_1 + die_2,
            is_doubles=die_1 == die_2,
            roll_counter=roll_counter,
        ),
    )


def generate_deck_shuffle_event(state: GameState, event_id: str, deck: DeckName) -> GameEvent:
    deck_state = _deck_state_for_name(state, deck)
    shuffle_counter = _shuffle_counter(state, deck) + 1
    draw_pile = deterministic_shuffle(
        seed=state.rng.seed,
        deck=deck,
        shuffle_counter=shuffle_counter,
        card_ids=deck_state.draw_pile,
    )

    return GameEvent(
        event_id=event_id,
        sequence=state.event_sequence + 1,
        type="DECK_SHUFFLED",
        payload=DeckShuffledPayload(
            deck=deck,
            draw_pile=draw_pile,
            shuffle_counter=shuffle_counter,
        ),
    )


def generate_card_draw_event(state: GameState, event_id: str, deck: DeckName) -> GameEvent:
    deck_state = _deck_state_for_name(state, deck)
    if not deck_state.draw_pile:
        raise InvalidEventError(f"{deck} draw pile is empty")

    draw_counter = _draw_counter(state, deck) + 1
    return GameEvent(
        event_id=event_id,
        sequence=state.event_sequence + 1,
        type="CARD_DRAWN",
        payload=CardDrawnPayload(
            deck=deck,
            card_id=deck_state.draw_pile[0],
            draw_counter=draw_counter,
        ),
    )


def _deck_state_for_name(state: GameState, deck: DeckName) -> DeckState:
    if deck == "chance":
        return state.decks.chance
    if deck == "community_chest":
        return state.decks.community_chest
    raise InvalidEventError(f"unknown deck {deck}")


def _draw_counter(state: GameState, deck: DeckName) -> int:
    if deck == "chance":
        return state.rng.chance_draw_count
    if deck == "community_chest":
        return state.rng.community_chest_draw_count
    raise InvalidEventError(f"unknown deck {deck}")


def _shuffle_counter(state: GameState, deck: DeckName) -> int:
    if deck == "chance":
        return state.rng.chance_shuffle_count
    if deck == "community_chest":
        return state.rng.community_chest_shuffle_count
    raise InvalidEventError(f"unknown deck {deck}")


__all__ = [
    "generate_card_draw_event",
    "generate_deck_shuffle_event",
    "generate_dice_roll_event",
]
