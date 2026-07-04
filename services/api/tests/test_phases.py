from __future__ import annotations

import pytest

from app.rules.actions import ActionValidationError, GameAction, validate_action
from app.rules.events import GameEvent, TurnStateSetPayload
from app.rules.phases import (
    PHASE_NAMES,
    VALID_PHASE_TRANSITIONS,
    TurnPhase,
    assert_valid_phase_transition,
    can_transition_phase,
)
from app.rules.reducer import InvalidEventError, apply_event
from app.rules.state import GameState, PlayerSetup, TurnState, create_initial_game_state


REQUIRED_PHASES = (
    "START_TURN",
    "PRE_ROLL_MANAGEMENT",
    "ROLL_REQUIRED",
    "MOVEMENT_RESOLUTION",
    "SPACE_RESOLUTION",
    "PURCHASE_OR_AUCTION",
    "PAYMENT_RESOLUTION",
    "JAIL_RESOLUTION",
    "POST_ROLL_MANAGEMENT",
    "NEGOTIATION_WINDOW",
    "END_TURN",
    "BANKRUPTCY_RESOLUTION",
    "GAME_OVER",
)

EXPECTED_TRANSITIONS = {
    "START_TURN": ("PRE_ROLL_MANAGEMENT", "BANKRUPTCY_RESOLUTION"),
    "PRE_ROLL_MANAGEMENT": ("ROLL_REQUIRED", "JAIL_RESOLUTION", "BANKRUPTCY_RESOLUTION"),
    "ROLL_REQUIRED": ("MOVEMENT_RESOLUTION", "BANKRUPTCY_RESOLUTION"),
    "MOVEMENT_RESOLUTION": ("SPACE_RESOLUTION", "BANKRUPTCY_RESOLUTION"),
    "SPACE_RESOLUTION": (
        "PURCHASE_OR_AUCTION",
        "PAYMENT_RESOLUTION",
        "POST_ROLL_MANAGEMENT",
        "BANKRUPTCY_RESOLUTION",
    ),
    "PURCHASE_OR_AUCTION": ("PAYMENT_RESOLUTION", "POST_ROLL_MANAGEMENT", "BANKRUPTCY_RESOLUTION"),
    "PAYMENT_RESOLUTION": ("POST_ROLL_MANAGEMENT", "BANKRUPTCY_RESOLUTION"),
    "JAIL_RESOLUTION": ("ROLL_REQUIRED", "MOVEMENT_RESOLUTION", "BANKRUPTCY_RESOLUTION"),
    "POST_ROLL_MANAGEMENT": ("NEGOTIATION_WINDOW", "END_TURN", "BANKRUPTCY_RESOLUTION"),
    "NEGOTIATION_WINDOW": ("POST_ROLL_MANAGEMENT", "END_TURN", "BANKRUPTCY_RESOLUTION"),
    "END_TURN": ("START_TURN", "BANKRUPTCY_RESOLUTION"),
    "BANKRUPTCY_RESOLUTION": ("POST_ROLL_MANAGEMENT", "END_TURN", "GAME_OVER"),
    "GAME_OVER": (),
}


def _initial_state() -> GameState:
    return create_initial_game_state(
        seed="phase-seed",
        game_id="phase-game",
        players=(
            PlayerSetup(id="player-1", name="Player 1", kind="human"),
            PlayerSetup(id="player-2", name="Player 2", kind="ai"),
        ),
    )


def _turn_state_event(state: GameState, phase: str, event_id: str) -> GameEvent:
    return GameEvent(
        event_id=event_id,
        sequence=state.event_sequence + 1,
        type="TURN_STATE_SET",
        payload=TurnStateSetPayload(
            turn_number=state.turn.turn_number,
            current_player_index=state.turn.current_player_index,
            current_player_id=state.turn.current_player_id,
            phase=phase,
            consecutive_doubles=state.turn.consecutive_doubles,
        ),
    )


def _apply_phase(state: GameState, phase: str) -> GameState:
    return apply_event(state, _turn_state_event(state, phase, f"phase-{state.event_sequence + 1}"))


def _issue_codes(exc: ActionValidationError) -> set[str]:
    return {issue.code for issue in exc.errors}


def test_phase_names_are_authoritative_and_ordered() -> None:
    assert PHASE_NAMES == REQUIRED_PHASES
    assert tuple(phase.value for phase in TurnPhase) == REQUIRED_PHASES
    assert len(set(PHASE_NAMES)) == len(PHASE_NAMES)


def test_transition_table_is_explicit_and_deterministic() -> None:
    assert {
        phase.value: tuple(next_phase.value for next_phase in next_phases)
        for phase, next_phases in VALID_PHASE_TRANSITIONS.items()
    } == EXPECTED_TRANSITIONS
    assert tuple(VALID_PHASE_TRANSITIONS) == REQUIRED_PHASES
    assert set(VALID_PHASE_TRANSITIONS) == set(PHASE_NAMES)
    assert all(isinstance(next_phases, tuple) for next_phases in VALID_PHASE_TRANSITIONS.values())
    assert all(
        set(next_phases).issubset(set(PHASE_NAMES))
        for next_phases in VALID_PHASE_TRANSITIONS.values()
    )

    for phase in PHASE_NAMES:
        if phase != "GAME_OVER":
            assert can_transition_phase(phase, "BANKRUPTCY_RESOLUTION")
    assert not can_transition_phase("START_TURN", "GAME_OVER")

    assert_valid_phase_transition("START_TURN", "PRE_ROLL_MANAGEMENT")
    with pytest.raises(ValueError, match="invalid phase transition"):
        assert_valid_phase_transition("START_TURN", "GAME_OVER")


def test_reducer_accepts_preserving_phase_transitions() -> None:
    state = _initial_state()

    preserved_start = _apply_phase(state, "START_TURN")
    assert preserved_start.turn.phase == "START_TURN"

    pre_roll = _apply_phase(preserved_start, "PRE_ROLL_MANAGEMENT")
    preserved_pre_roll = _apply_phase(pre_roll, "PRE_ROLL_MANAGEMENT")
    assert preserved_pre_roll.turn.phase == "PRE_ROLL_MANAGEMENT"


def test_reducer_accepts_valid_next_phase_transitions() -> None:
    state = _initial_state()
    for phase in (
        "PRE_ROLL_MANAGEMENT",
        "ROLL_REQUIRED",
        "MOVEMENT_RESOLUTION",
        "SPACE_RESOLUTION",
        "PAYMENT_RESOLUTION",
        "BANKRUPTCY_RESOLUTION",
        "GAME_OVER",
    ):
        state = _apply_phase(state, phase)

    assert state.turn.phase == "GAME_OVER"


def test_reducer_rejects_unknown_phases_and_invalid_phase_jumps() -> None:
    state = _initial_state()

    with pytest.raises(InvalidEventError, match="unknown turn phase"):
        apply_event(state, _turn_state_event(state, "NOT_A_PHASE", "unknown-phase"))

    with pytest.raises(InvalidEventError, match="invalid phase transition"):
        apply_event(state, _turn_state_event(state, "GAME_OVER", "invalid-start-game-over"))


def test_turn_state_rejects_unknown_phase_names() -> None:
    with pytest.raises(ValueError):
        TurnState.model_validate(
            {
                "turn_number": 1,
                "current_player_index": 0,
                "current_player_id": "player-1",
                "phase": "NOT_A_PHASE",
                "consecutive_doubles": 0,
            }
        )


def test_start_turn_action_rejects_mandatory_non_start_phase() -> None:
    state = _apply_phase(_initial_state(), "PRE_ROLL_MANAGEMENT")
    action = GameAction(
        actor_id="player-1",
        type="ROLL_DICE",
        payload={},
        expected_state_hash=state.state_hash(),
        expected_event_sequence=state.event_sequence,
    )

    with pytest.raises(ActionValidationError) as exc_info:
        validate_action(state, action)

    assert _issue_codes(exc_info.value) == {"mistimed_action"}
