from __future__ import annotations

import pytest

from app.rules.events import CardDrawnPayload, DeckShuffledPayload, DiceRolledPayload, GameEvent
from app.rules.reducer import InvalidEventError, apply_event, replay_events
from app.rules.rng import (
    generate_card_draw_event,
    generate_deck_shuffle_event,
    generate_dice_roll_event,
)
from app.rules.state import GameState, PlayerSetup, create_initial_game_state


def _player_setups() -> tuple[PlayerSetup, ...]:
    return (
        PlayerSetup(id="player-1", name="Player 1", kind="human"),
        PlayerSetup(id="player-2", name="Player 2", kind="ai"),
    )


def _initial_state(seed: str = "seed-1") -> GameState:
    return create_initial_game_state(seed=seed, players=_player_setups(), game_id="game-1")


def _dice_sequence(seed: str, roll_count: int = 8) -> tuple[tuple[int, int], ...]:
    state = _initial_state(seed)
    rolls: list[tuple[int, int]] = []

    for index in range(roll_count):
        event = generate_dice_roll_event(state, f"roll-{index + 1}", "player-1")
        payload = event.payload
        assert isinstance(payload, DiceRolledPayload)
        rolls.append((payload.die_1, payload.die_2))
        state = apply_event(state, event)

    return tuple(rolls)


def test_same_seed_produces_identical_dice_event_payloads_for_same_generation_path() -> None:
    state_a = _initial_state("shared-seed")
    state_b = _initial_state("shared-seed")

    for index in range(4):
        event_a = generate_dice_roll_event(state_a, f"a-roll-{index + 1}", "player-1")
        event_b = generate_dice_roll_event(state_b, f"b-roll-{index + 1}", "player-1")

        assert event_a.payload == event_b.payload

        state_a = apply_event(state_a, event_a)
        state_b = apply_event(state_b, event_b)


def test_different_seeds_produce_divergent_dice_sequences() -> None:
    assert _dice_sequence("seed-alpha") != _dice_sequence("seed-beta")


def test_deck_shuffle_is_deterministic_and_preserves_membership() -> None:
    state_a = _initial_state("shuffle-seed")
    state_b = _initial_state("shuffle-seed")
    original_draw_pile = state_a.decks.chance.draw_pile

    event_a = generate_deck_shuffle_event(state_a, "shuffle-a", "chance")
    event_b = generate_deck_shuffle_event(state_b, "shuffle-b", "chance")
    payload_a = event_a.payload
    payload_b = event_b.payload
    assert isinstance(payload_a, DeckShuffledPayload)
    assert isinstance(payload_b, DeckShuffledPayload)

    assert payload_a == payload_b
    assert payload_a.draw_pile != original_draw_pile
    assert set(payload_a.draw_pile) == set(original_draw_pile)
    assert len(payload_a.draw_pile) == len(original_draw_pile)

    shuffled_state = apply_event(state_a, event_a)

    assert shuffled_state.decks.chance.draw_pile == payload_a.draw_pile
    assert shuffled_state.decks.chance.discard_pile == ()
    assert shuffled_state.rng.chance_shuffle_count == 1
    assert shuffled_state.rng.community_chest_shuffle_count == 0


def test_card_draw_event_stores_drawn_card_and_reducer_moves_card_to_discard() -> None:
    state = _initial_state()
    top_card_id = state.decks.community_chest.draw_pile[0]

    event = generate_card_draw_event(state, "draw-1", "community_chest")
    payload = event.payload
    assert isinstance(payload, CardDrawnPayload)

    assert payload.card_id == top_card_id
    assert payload.draw_counter == 1

    next_state = apply_event(state, event)

    assert next_state.decks.community_chest.draw_pile == state.decks.community_chest.draw_pile[1:]
    assert next_state.decks.community_chest.discard_pile == (top_card_id,)
    assert next_state.rng.community_chest_draw_count == 1
    assert next_state.rng.chance_draw_count == 0


def test_dice_shuffle_and_card_events_replay_to_same_final_state_hash() -> None:
    players = _player_setups()
    state = create_initial_game_state(seed="replay-seed", players=players, game_id="game-1")
    events: list[GameEvent] = []

    event = generate_dice_roll_event(state, "roll-1", "player-1")
    events.append(event)
    state = apply_event(state, event)

    event = generate_deck_shuffle_event(state, "chance-shuffle-1", "chance")
    events.append(event)
    state = apply_event(state, event)

    event = generate_card_draw_event(state, "chance-draw-1", "chance")
    events.append(event)
    state = apply_event(state, event)

    event = generate_dice_roll_event(state, "roll-2", "player-2")
    events.append(event)
    state = apply_event(state, event)

    event = generate_deck_shuffle_event(state, "community-shuffle-1", "community_chest")
    events.append(event)
    state = apply_event(state, event)

    event = generate_card_draw_event(state, "community-draw-1", "community_chest")
    events.append(event)
    state = apply_event(state, event)

    replayed_state = replay_events(seed="replay-seed", players=players, game_id="game-1", events=events)

    assert replayed_state == state
    assert replayed_state.state_hash() == state.state_hash()


def test_rng_counters_live_in_state_and_advance_only_through_accepted_events() -> None:
    state = _initial_state()

    assert state.rng.seed == "seed-1"
    assert state.rng.dice_roll_count == 0
    assert state.rng.chance_draw_count == 0
    assert state.rng.community_chest_draw_count == 0
    assert state.rng.chance_shuffle_count == 0
    assert state.rng.community_chest_shuffle_count == 0
    assert '"rng":' in state.canonical_json()

    dice_event = generate_dice_roll_event(state, "roll-1", "player-1")
    assert state.rng.dice_roll_count == 0
    state = apply_event(state, dice_event)
    assert state.rng.dice_roll_count == 1

    shuffle_event = generate_deck_shuffle_event(state, "shuffle-1", "chance")
    assert state.rng.chance_shuffle_count == 0
    state = apply_event(state, shuffle_event)
    assert state.rng.chance_shuffle_count == 1

    draw_event = generate_card_draw_event(state, "draw-1", "chance")
    assert state.rng.chance_draw_count == 0
    state = apply_event(state, draw_event)
    assert state.rng.chance_draw_count == 1

    invalid_event = GameEvent(
        event_id="invalid-roll-counter",
        sequence=state.event_sequence + 1,
        type="DICE_ROLLED",
        payload=DiceRolledPayload(
            player_id="player-1",
            die_1=3,
            die_2=4,
            total=7,
            is_doubles=False,
            roll_counter=state.rng.dice_roll_count,
        ),
    )

    with pytest.raises(InvalidEventError, match="roll counter"):
        apply_event(state, invalid_event)

    assert state.rng.dice_roll_count == 1
