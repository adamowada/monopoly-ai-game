from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from app.contracts.trigger_system import trigger_system
from app.rules.actions import (
    ActionValidationError,
    GameAction,
    execute_action,
    list_legal_actions,
    validate_action,
)
from app.rules.debt import outstanding_debt_amount
from app.rules.events import GameEvent
from app.rules.reducer import replay_events
from app.rules.state import GameState, PlayerSetup


FIXTURE_DIR = Path(__file__).with_name("fixtures")
FIXTURE_IDS = (
    "stage10_end_turn_phase_graph",
    "stage10_debt_payload_mismatch",
    "stage10_pending_immediate_obligation_settlement",
    "stage10_smoke_turn_rotation",
)


@pytest.mark.parametrize("fixture_id", FIXTURE_IDS)
def test_regression_fixture_replays_from_seed_and_event_log(fixture_id: str) -> None:
    fixture = _load_fixture(fixture_id)

    state = _replay_fixture(fixture)

    assert state.seed == fixture["seed"]
    assert state.game_id == fixture["game_id"]
    assert state.event_sequence == len(fixture["event_log"])
    assert state.applied_event_ids == tuple(event["event_id"] for event in fixture["event_log"])


@pytest.mark.parametrize("fixture_id", FIXTURE_IDS)
def test_regression_fixture_fixed_behavior(fixture_id: str) -> None:
    fixture = _load_fixture(fixture_id)
    state = _replay_fixture(fixture)

    if fixture_id == "stage10_end_turn_phase_graph":
        _assert_stage10_end_turn_phase_graph(fixture, state)
    elif fixture_id == "stage10_debt_payload_mismatch":
        _assert_stage10_debt_payload_mismatch(fixture, state)
    elif fixture_id == "stage10_pending_immediate_obligation_settlement":
        _assert_stage10_pending_immediate_obligation_settlement(fixture, state)
    elif fixture_id == "stage10_smoke_turn_rotation":
        _assert_stage10_smoke_turn_rotation(fixture, state)
    else:  # pragma: no cover - fixture IDs are static.
        raise AssertionError(f"unhandled regression fixture {fixture_id}")


def _assert_stage10_end_turn_phase_graph(fixture: Mapping[str, Any], state: GameState) -> None:
    reproduction = fixture["reproduction"]
    assertions = fixture["expectation"]["assertions"]
    assert state.turn.phase == assertions["final_replayed_phase"]

    # Mandatory-roll protection: a fresh START_TURN cannot skip directly to END_TURN.
    fresh_state = _replay_fixture(fixture, through_sequence=0)
    assert fresh_state.turn.phase == "START_TURN"
    assert "END_TURN" not in _legal_action_types(fresh_state, "player-1")
    with pytest.raises(ActionValidationError) as exc_info:
        validate_action(
            fresh_state,
            _action_from_fixture(fresh_state, reproduction["start_turn_illegal_action"]),
        )

    assert _issue_codes(exc_info.value) == {assertions["mandatory_roll_rejection_code"]}
    assert assertions["mandatory_roll_rejection_code"] == "mistimed_action"

    legal_entry_prefix_sequences = reproduction["legal_entry_prefix_sequences"]
    assert isinstance(legal_entry_prefix_sequences, Mapping)
    assert set(legal_entry_prefix_sequences) == {"POST_ROLL_MANAGEMENT", "NEGOTIATION_WINDOW"}
    for phase_name, prefix_sequence in legal_entry_prefix_sequences.items():
        legal_state = _replay_fixture(fixture, through_sequence=int(prefix_sequence))
        assert legal_state.turn.phase == phase_name
        assert "END_TURN" in _legal_action_types(legal_state, "player-1")

        action = _action_from_fixture(legal_state, reproduction["legal_action"])
        validate_action(legal_state, action)
        execution = execute_action(
            legal_state,
            action,
            f"stage10-end-turn-phase-graph-{phase_name.lower()}",
        )

        assert [event.type for event in execution.events] == ["TURN_STATE_SET", "TURN_STATE_SET"]
        assert [event.payload.phase for event in execution.events] == assertions["legal_end_turn_phases"]
        assert execution.state.turn.current_player_id == assertions["next_current_player_id"]
        assert execution.state.turn.turn_number == assertions["next_turn_number"]
        assert (
            execution.state.event_sequence
            == legal_state.event_sequence + assertions["legal_end_turn_event_count"]
        )

    illegal_state = _replay_fixture(
        fixture,
        through_sequence=reproduction["illegal_prefix_sequence"],
    )
    assert "END_TURN" not in _legal_action_types(illegal_state, "player-1")
    with pytest.raises(ActionValidationError) as exc_info:
        validate_action(
            illegal_state,
            _action_from_fixture(illegal_state, reproduction["illegal_action"]),
        )

    assert _issue_codes(exc_info.value) == {assertions["illegal_rejection_code"]}
    assert assertions["illegal_rejection_code"] == "mistimed_action"


def _assert_stage10_debt_payload_mismatch(fixture: Mapping[str, Any], state: GameState) -> None:
    assertions = fixture["expectation"]["assertions"]
    active_payment = state.active_payment
    assert active_payment is not None
    assert active_payment.debtor_id == assertions["active_debtor_id"]
    assert active_payment.creditor_id == assertions["active_creditor_id"]
    assert outstanding_debt_amount(state) == assertions["outstanding_amount"]
    assert "SETTLE_DEBT" in _legal_action_types(state, active_payment.debtor_id)

    original_hash = state.state_hash()
    original_sequence = state.event_sequence
    with pytest.raises(ActionValidationError) as exc_info:
        validate_action(
            state,
            _action_from_fixture(state, fixture["reproduction"]["forged_action"]),
        )

    assert _issue_codes(exc_info.value) == {assertions["rejection_code"]}
    assert assertions["rejection_code"] == "debt_payload_mismatch"
    assert state.state_hash() == original_hash
    assert state.event_sequence == original_sequence


def _assert_stage10_pending_immediate_obligation_settlement(
    fixture: Mapping[str, Any],
    state: GameState,
) -> None:
    obligation = fixture["reproduction"]["obligation"]
    assertions = fixture["expectation"]["assertions"]
    assert obligation["status"] == assertions["status"]
    assert obligation["due_turn"] is assertions["due_turn"]
    assert obligation["due_condition"] is assertions["due_condition"]

    match = trigger_system(
        obligation["schedule"],
        state=state,
        trigger_context=fixture["reproduction"]["trigger_context"],
    )

    assert match.matched is assertions["matched"]
    assert match.trigger_name == assertions["trigger_name"]
    assert match.trigger_name == "immediate_trigger"


def _assert_stage10_smoke_turn_rotation(fixture: Mapping[str, Any], state: GameState) -> None:
    assertions = fixture["expectation"]["assertions"]
    event_log = fixture["event_log"]
    current_player_id_rotation = [fixture["reproduction"]["initial_current_player_id"]]
    current_player_id_rotation.extend(event["payload"]["current_player_id"] for event in event_log)
    turn_number_rotation = [1]
    turn_number_rotation.extend(event["payload"]["turn_number"] for event in event_log)

    assert current_player_id_rotation == assertions["current_player_id"]
    assert turn_number_rotation == assertions["turn_number"]
    assert state.turn.current_player_id == assertions["final_current_player_id"]
    assert state.turn.turn_number == assertions["final_turn_number"]


def _load_fixture(fixture_id: str) -> dict[str, Any]:
    path = FIXTURE_DIR / f"{fixture_id}.json"
    with path.open(encoding="utf-8") as fixture_file:
        fixture = json.load(fixture_file)
    assert fixture["id"] == fixture_id
    return fixture


def _replay_fixture(fixture: Mapping[str, Any], *, through_sequence: int | None = None) -> GameState:
    players = tuple(PlayerSetup.model_validate(player) for player in fixture["players"])
    raw_events = fixture["event_log"]
    if through_sequence is not None:
        raw_events = raw_events[:through_sequence]
    events = tuple(GameEvent.model_validate(event) for event in raw_events)
    return replay_events(
        seed=fixture["seed"],
        players=players,
        game_id=fixture["game_id"],
        events=events,
    )


def _action_from_fixture(state: GameState, action_data: Mapping[str, Any]) -> GameAction:
    return GameAction(
        actor_id=str(action_data["actor_id"]),
        type=str(action_data["type"]),
        payload=_payload(action_data),
        expected_state_hash=state.state_hash(),
        expected_event_sequence=state.event_sequence,
    )


def _payload(action_data: Mapping[str, Any]) -> Mapping[str, object]:
    payload = action_data.get("payload", {})
    assert isinstance(payload, Mapping)
    return dict(payload)


def _legal_action_types(state: GameState, actor_id: str) -> set[str]:
    return {action.type for action in list_legal_actions(state, actor_id)}


def _issue_codes(exc: ActionValidationError) -> set[str]:
    return {issue.code for issue in exc.errors}
