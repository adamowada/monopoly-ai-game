from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.rules.simulation import (
    SimulationFailure,
    check_invariants,
    run_random_legal_action_stress,
    run_scripted_game_to_completion,
)
from app.rules.state import PlayerSetup, create_initial_game_state


API_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = API_ROOT / "scripts" / "run_simulation.py"


def _player_setups(count: int = 3) -> tuple[PlayerSetup, ...]:
    return tuple(
        PlayerSetup(
            id=f"player-{index}",
            name=f"Player {index}",
            kind="human" if index == 1 else "ai",
        )
        for index in range(1, count + 1)
    )


def _initial_state(count: int = 3):
    return create_initial_game_state(
        seed="simulation-test-seed",
        players=_player_setups(count),
        game_id="simulation-test-game",
    )


def test_random_stress_runs_hundreds_of_actions_and_is_deterministic() -> None:
    first = run_random_legal_action_stress(seed="stress-seed", player_count=3, action_limit=210)
    second = run_random_legal_action_stress(seed="stress-seed", player_count=3, action_limit=210)

    assert first.failure is None
    assert first.actions_executed == 210
    assert first.invariant_checks == first.actions_executed + 1
    assert first.final_state.state_hash() == second.final_state.state_hash()
    assert [
        entry.action for entry in first.action_log[:25]
    ] == [
        entry.action for entry in second.action_log[:25]
    ]


def test_scripted_game_completes_to_game_over_with_winner() -> None:
    result = run_scripted_game_to_completion(seed="scripted-seed")

    assert result.failure is None
    assert result.actions_executed >= 1
    assert result.invariant_checks == result.actions_executed + 1
    assert sum(not player.is_bankrupt for player in result.final_state.players) == 1
    assert result.final_state.players[0].id == "player-1"
    assert not result.final_state.players[0].is_bankrupt


def test_invariant_failures_include_reproducible_context() -> None:
    state = _initial_state()
    broken_state = state.model_copy(update={"property_ownership": state.property_ownership[:-1]})

    with pytest.raises(SimulationFailure) as exc_info:
        check_invariants(broken_state, action={"actor_id": "player-1", "type": "ROLL_DICE"})

    context = exc_info.value.reproducible_context
    assert context["state_hash"] == broken_state.state_hash()
    assert context["event_sequence"] == broken_state.event_sequence
    assert context["action_index"] is None
    assert context["actor_id"] == "player-1"
    assert context["action_type"] == "ROLL_DICE"
    assert "state_summary" in context
    assert "invariant_violations" in context


def test_cli_random_mode_outputs_json_summary() -> None:
    summary = _run_cli_json(
        "--mode",
        "random",
        "--seed",
        "cli-random",
        "--players",
        "3",
        "--actions",
        "40",
        "--json",
    )

    assert summary["mode"] == "random"
    assert summary["seed"] == "cli-random"
    assert summary["actions_executed"] == 40
    assert summary["invariant_checks"] == 41
    assert isinstance(summary["final_state_hash"], str)
    assert summary["failure"] is None


def test_cli_scripted_mode_outputs_json_summary() -> None:
    summary = _run_cli_json("--mode", "scripted", "--seed", "cli-scripted", "--json")
    actions_executed = summary["actions_executed"]
    invariant_checks = summary["invariant_checks"]

    assert summary["mode"] == "scripted"
    assert summary["seed"] == "cli-scripted"
    assert isinstance(actions_executed, int)
    assert isinstance(invariant_checks, int)
    assert actions_executed >= 1
    assert invariant_checks == actions_executed + 1
    assert isinstance(summary["final_state_hash"], str)
    assert summary["failure"] is None
    assert summary["game_over"] is True
    assert summary["winner_id"] == "player-1"


def _run_cli_json(*args: str) -> dict[str, object]:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *args],
        cwd=API_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout.strip().splitlines()[-1])
