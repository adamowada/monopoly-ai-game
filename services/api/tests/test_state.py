from __future__ import annotations

import re

import pytest
from pydantic import ValidationError

from app.rules.state import (
    GameState,
    PlayerSetup,
    PlayerState,
    create_initial_game_state,
)


def _player_setups(count: int) -> tuple[PlayerSetup, ...]:
    return tuple(
        PlayerSetup(id=f"player-{index}", name=f"Player {index}", kind="human" if index == 1 else "ai")
        for index in range(1, count + 1)
    )


@pytest.mark.parametrize("player_count", [2, 3, 5])
def test_create_initial_game_state_accepts_supported_player_counts(player_count: int) -> None:
    players = _player_setups(player_count)

    state = create_initial_game_state(seed="seed-1", players=players, game_id="game-1")

    assert state.schema_version == "game-state-v1"
    assert state.game_id == "game-1"
    assert state.ruleset_version == "classic-monopoly-v1"
    assert state.seed == "seed-1"
    assert [player.id for player in state.players] == [player.id for player in players]
    assert state.turn.current_player_index == 0
    assert state.turn.current_player_id == players[0].id
    assert state.turn.phase == "START_TURN"


@pytest.mark.parametrize("player_count", [1, 6])
def test_create_initial_game_state_rejects_unsupported_player_counts(player_count: int) -> None:
    with pytest.raises(ValueError, match="2 to 5 players"):
        create_initial_game_state(seed="seed-1", players=_player_setups(player_count), game_id="game-1")


def test_create_initial_game_state_rejects_duplicate_player_ids() -> None:
    players = (
        PlayerSetup(id="duplicate", name="Player 1", kind="human"),
        PlayerSetup(id="duplicate", name="Player 2", kind="ai"),
    )

    with pytest.raises(ValueError, match="duplicate player ids"):
        create_initial_game_state(seed="seed-1", players=players, game_id="game-1")


def test_player_setup_rejects_unsupported_player_kind() -> None:
    with pytest.raises(ValidationError):
        PlayerSetup.model_validate({"id": "player-1", "name": "Player 1", "kind": "bot"})


def test_create_initial_game_state_rejects_unsupported_player_kind() -> None:
    invalid_player = PlayerSetup.model_construct(id="player-2", name="Player 2", kind="bot")

    with pytest.raises(ValidationError):
        create_initial_game_state(
            seed="seed-1",
            players=(PlayerSetup(id="player-1", name="Player 1", kind="human"), invalid_player),
            game_id="game-1",
        )


def test_initial_game_state_contains_core_state_slots() -> None:
    state = create_initial_game_state(seed="seed-1", players=_player_setups(3), game_id="game-1")

    assert state.players == (
        PlayerState(
            id="player-1",
            name="Player 1",
            kind="human",
            cash=1500,
            position=0,
            in_jail=False,
            jail_turns=0,
            get_out_of_jail_card_ids=(),
            is_bankrupt=False,
        ),
        PlayerState(
            id="player-2",
            name="Player 2",
            kind="ai",
            cash=1500,
            position=0,
            in_jail=False,
            jail_turns=0,
            get_out_of_jail_card_ids=(),
            is_bankrupt=False,
        ),
        PlayerState(
            id="player-3",
            name="Player 3",
            kind="ai",
            cash=1500,
            position=0,
            in_jail=False,
            jail_turns=0,
            get_out_of_jail_card_ids=(),
            is_bankrupt=False,
        ),
    )

    assert len(state.property_ownership) == 28
    assert state.property_ownership[0].property_id == "property_mediterranean_avenue"
    assert all(ownership.owner_id is None for ownership in state.property_ownership)
    assert all(not ownership.mortgaged for ownership in state.property_ownership)
    assert all(ownership.houses == 0 for ownership in state.property_ownership)
    assert all(not ownership.hotel for ownership in state.property_ownership)

    assert len(state.decks.chance.draw_pile) == 16
    assert state.decks.chance.draw_pile[0] == "card_chance_advance_to_go"
    assert state.decks.chance.discard_pile == ()
    assert len(state.decks.community_chest.draw_pile) == 16
    assert state.decks.community_chest.draw_pile[0] == "card_community_advance_to_go"
    assert state.decks.community_chest.discard_pile == ()

    assert state.bank_inventory.houses == 32
    assert state.bank_inventory.hotels == 12
    assert state.turn.turn_number == 1
    assert state.turn.current_player_index == 0
    assert state.turn.current_player_id == "player-1"
    assert state.turn.phase == "START_TURN"
    assert state.turn.consecutive_doubles == 0

    assert state.active_payment is None
    assert state.active_auction is None
    assert state.active_negotiation is None
    assert state.active_bankruptcy is None


def test_game_state_serialization_round_trip_preserves_equality() -> None:
    state = create_initial_game_state(seed="seed-1", players=_player_setups(2), game_id="game-1")

    round_tripped = GameState.model_validate_json(state.model_dump_json())

    assert round_tripped == state
    assert round_tripped.canonical_json() == state.canonical_json()
    assert round_tripped.state_hash() == state.state_hash()


def test_state_hash_is_stable_and_changes_for_meaningful_state_changes() -> None:
    players = _player_setups(2)
    state = create_initial_game_state(seed="seed-1", players=players, game_id="game-1")
    identical_state = create_initial_game_state(seed="seed-1", players=players, game_id="game-1")
    changed_state = state.model_copy(
        update={
            "players": (
                state.players[0].model_copy(update={"cash": 1499}),
                state.players[1],
            )
        }
    )

    assert state.canonical_json() == identical_state.canonical_json()
    assert state.state_hash() == identical_state.state_hash()
    assert state.state_hash() != changed_state.state_hash()
    assert re.fullmatch(r"[0-9a-f]{64}", state.state_hash()) is not None
