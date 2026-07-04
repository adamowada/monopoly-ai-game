from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.rules.mechanics import is_game_over, winning_player_id  # noqa: E402
from app.rules.simulation import (  # noqa: E402
    SimulationFailure,
    SimulationResult,
    run_random_legal_action_stress,
    run_scripted_game_to_completion,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run deterministic Monopoly rules simulations.")
    parser.add_argument("--mode", choices=("random", "scripted"), required=True)
    parser.add_argument("--seed", required=True)
    parser.add_argument("--players", type=int, default=3, help="Player count for random mode.")
    parser.add_argument("--actions", type=int, default=200, help="Action limit for random mode.")
    parser.add_argument("--json", action="store_true", help="Print a one-line JSON summary.")
    args = parser.parse_args()

    if args.mode == "random":
        result = run_random_legal_action_stress(
            seed=args.seed,
            player_count=args.players,
            action_limit=args.actions,
        )
    else:
        result = run_scripted_game_to_completion(seed=args.seed)

    summary = _summary(args.mode, args.seed, result)
    if args.json:
        print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    else:
        print(
            f"{args.mode} simulation seed={args.seed} "
            f"actions={result.actions_executed} invariants={result.invariant_checks} "
            f"hash={result.final_state.state_hash()}"
        )
        if result.failure is not None:
            print(json.dumps(summary["failure"], sort_keys=True, indent=2))

    return 1 if result.failure is not None else 0


def _summary(mode: str, seed: str, result: SimulationResult) -> dict[str, object]:
    summary: dict[str, object] = {
        "mode": mode,
        "seed": seed,
        "actions_executed": result.actions_executed,
        "invariant_checks": result.invariant_checks,
        "final_state_hash": result.final_state.state_hash(),
        "failure": _failure_summary(result.failure),
    }
    if mode == "scripted":
        summary["game_over"] = is_game_over(result.final_state)
        summary["winner_id"] = winning_player_id(result.final_state)
    return summary


def _failure_summary(failure: SimulationFailure | None) -> dict[str, object] | None:
    if failure is None:
        return None
    return {
        "message": str(failure),
        "reproducible_context": _jsonable(failure.reproducible_context),
    }


def _jsonable(value: object) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(inner_value) for key, inner_value in value.items()}
    if isinstance(value, tuple | list):
        return [_jsonable(item) for item in value]
    if hasattr(value, "items"):
        return {str(key): _jsonable(inner_value) for key, inner_value in value.items()}  # type: ignore[union-attr]
    return value


if __name__ == "__main__":
    raise SystemExit(main())
