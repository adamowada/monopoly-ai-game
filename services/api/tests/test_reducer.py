from __future__ import annotations

from collections.abc import Sequence

import pytest

from app.rules.events import (
    DeckStateSetPayload,
    GameEvent,
    PlayerCashDeltaPayload,
    PlayerPositionSetPayload,
    PropertyOwnerSetPayload,
    TurnStateSetPayload,
)
from app.rules.reducer import InvalidEventError, apply_event, replay_events
from app.rules.state import GameState, PlayerSetup, create_initial_game_state


def _player_setups() -> tuple[PlayerSetup, ...]:
    return (
        PlayerSetup(id="player-1", name="Player 1", kind="human"),
        PlayerSetup(id="player-2", name="Player 2", kind="ai"),
    )


def _initial_state() -> GameState:
    return create_initial_game_state(seed="seed-1", players=_player_setups(), game_id="game-1")


def _apply_events(state: GameState, events: Sequence[GameEvent]) -> GameState:
    next_state = state
    for event in events:
        next_state = apply_event(next_state, event)
    return next_state


def test_valid_event_sequence_changes_core_state() -> None:
    state = _initial_state()
    events = (
        GameEvent(
            event_id="event-1",
            sequence=1,
            type="PLAYER_CASH_DELTA",
            payload=PlayerCashDeltaPayload(player_id="player-1", amount=-100),
        ),
        GameEvent(
            event_id="event-2",
            sequence=2,
            type="PLAYER_POSITION_SET",
            payload=PlayerPositionSetPayload(player_id="player-1", position=7),
        ),
        GameEvent(
            event_id="event-3",
            sequence=3,
            type="PROPERTY_OWNER_SET",
            payload=PropertyOwnerSetPayload(
                property_id="property_mediterranean_avenue",
                owner_id="player-2",
            ),
        ),
        GameEvent(
            event_id="event-4",
            sequence=4,
            type="TURN_STATE_SET",
            payload=TurnStateSetPayload(
                turn_number=2,
                current_player_index=1,
                current_player_id="player-2",
                phase="START_TURN",
                consecutive_doubles=0,
            ),
        ),
    )

    next_state = _apply_events(state, events)

    assert next_state.players[0].cash == 1400
    assert next_state.players[0].position == 7
    assert next_state.property_ownership[0].owner_id == "player-2"
    assert next_state.turn.turn_number == 2
    assert next_state.turn.current_player_index == 1
    assert next_state.turn.current_player_id == "player-2"
    assert next_state.event_sequence == 4
    assert next_state.applied_event_ids == ("event-1", "event-2", "event-3", "event-4")


def test_apply_event_is_pure_and_keeps_original_state_unchanged() -> None:
    state = _initial_state()
    original_hash = state.state_hash()

    next_state = apply_event(
        state,
        GameEvent(
            event_id="event-1",
            sequence=1,
            type="PLAYER_CASH_DELTA",
            payload=PlayerCashDeltaPayload(player_id="player-1", amount=-50),
        ),
    )

    assert state.players[0].cash == 1500
    assert state.event_sequence == 0
    assert state.applied_event_ids == ()
    assert state.state_hash() == original_hash
    assert next_state.players[0].cash == 1450
    assert next_state.state_hash() != original_hash


def test_replay_events_recreates_final_state_and_hash() -> None:
    players = _player_setups()
    events = (
        GameEvent(
            event_id="event-1",
            sequence=1,
            type="PLAYER_CASH_DELTA",
            payload=PlayerCashDeltaPayload(player_id="player-1", amount=75),
        ),
        GameEvent(
            event_id="event-2",
            sequence=2,
            type="PLAYER_POSITION_SET",
            payload=PlayerPositionSetPayload(player_id="player-2", position=12),
        ),
        GameEvent(
            event_id="event-3",
            sequence=3,
            type="PROPERTY_OWNER_SET",
            payload=PropertyOwnerSetPayload(
                property_id="property_electric_company",
                owner_id="player-1",
            ),
        ),
    )
    applied_state = _apply_events(
        create_initial_game_state(seed="seed-1", players=players, game_id="game-1"),
        events,
    )

    replayed_state = replay_events(seed="seed-1", players=players, game_id="game-1", events=events)

    assert replayed_state == applied_state
    assert replayed_state.state_hash() == applied_state.state_hash()


def test_out_of_order_events_are_rejected() -> None:
    with pytest.raises(InvalidEventError, match="sequence"):
        apply_event(
            _initial_state(),
            GameEvent(
                event_id="event-2",
                sequence=2,
                type="PLAYER_CASH_DELTA",
                payload=PlayerCashDeltaPayload(player_id="player-1", amount=-10),
            ),
        )


def test_duplicate_event_ids_are_rejected() -> None:
    state = apply_event(
        _initial_state(),
        GameEvent(
            event_id="event-1",
            sequence=1,
            type="PLAYER_CASH_DELTA",
            payload=PlayerCashDeltaPayload(player_id="player-1", amount=-10),
        ),
    )

    with pytest.raises(InvalidEventError, match="duplicate"):
        apply_event(
            state,
            GameEvent(
                event_id="event-1",
                sequence=2,
                type="PLAYER_CASH_DELTA",
                payload=PlayerCashDeltaPayload(player_id="player-2", amount=10),
            ),
        )


def test_unknown_players_are_rejected() -> None:
    with pytest.raises(InvalidEventError, match="unknown player"):
        apply_event(
            _initial_state(),
            GameEvent(
                event_id="event-1",
                sequence=1,
                type="PLAYER_CASH_DELTA",
                payload=PlayerCashDeltaPayload(player_id="missing-player", amount=-10),
            ),
        )


def test_unknown_property_ids_are_rejected() -> None:
    with pytest.raises(InvalidEventError, match="unknown property"):
        apply_event(
            _initial_state(),
            GameEvent(
                event_id="event-1",
                sequence=1,
                type="PROPERTY_OWNER_SET",
                payload=PropertyOwnerSetPayload(property_id="missing-property", owner_id=None),
            ),
        )


def test_unknown_property_owner_references_are_rejected() -> None:
    with pytest.raises(InvalidEventError, match="unknown owner"):
        apply_event(
            _initial_state(),
            GameEvent(
                event_id="event-1",
                sequence=1,
                type="PROPERTY_OWNER_SET",
                payload=PropertyOwnerSetPayload(
                    property_id="property_mediterranean_avenue",
                    owner_id="missing-player",
                ),
            ),
        )


def test_unknown_card_ids_are_rejected() -> None:
    with pytest.raises(InvalidEventError, match="unknown card"):
        apply_event(
            _initial_state(),
            GameEvent(
                event_id="event-1",
                sequence=1,
                type="DECK_STATE_SET",
                payload=DeckStateSetPayload(
                    deck="chance",
                    draw_pile=("missing-card",),
                    discard_pile=(),
                ),
            ),
        )


def test_unknown_decks_are_rejected() -> None:
    payload = DeckStateSetPayload.model_construct(
        deck="treasure",
        draw_pile=(),
        discard_pile=(),
    )
    event = GameEvent.model_construct(
        event_id="event-1",
        sequence=1,
        type="DECK_STATE_SET",
        payload=payload,
    )

    with pytest.raises(InvalidEventError, match="unknown deck"):
        apply_event(_initial_state(), event)


def test_malformed_payloads_are_rejected() -> None:
    payload = PlayerPositionSetPayload.model_construct(player_id="player-1", position=40)
    event = GameEvent.model_construct(
        event_id="event-1",
        sequence=1,
        type="PLAYER_POSITION_SET",
        payload=payload,
    )

    with pytest.raises(InvalidEventError, match="payload"):
        apply_event(_initial_state(), event)


def test_mismatched_event_type_and_payload_are_rejected() -> None:
    event = GameEvent.model_construct(
        event_id="event-1",
        sequence=1,
        type="PLAYER_CASH_DELTA",
        payload=PlayerPositionSetPayload(player_id="player-1", position=3),
    )

    with pytest.raises(InvalidEventError, match="payload"):
        apply_event(_initial_state(), event)
