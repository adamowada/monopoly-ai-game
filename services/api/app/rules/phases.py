from __future__ import annotations

from enum import StrEnum
from typing import Final


class TurnPhase(StrEnum):
    START_TURN = "START_TURN"
    PRE_ROLL_MANAGEMENT = "PRE_ROLL_MANAGEMENT"
    ROLL_REQUIRED = "ROLL_REQUIRED"
    MOVEMENT_RESOLUTION = "MOVEMENT_RESOLUTION"
    SPACE_RESOLUTION = "SPACE_RESOLUTION"
    PURCHASE_OR_AUCTION = "PURCHASE_OR_AUCTION"
    PAYMENT_RESOLUTION = "PAYMENT_RESOLUTION"
    JAIL_RESOLUTION = "JAIL_RESOLUTION"
    POST_ROLL_MANAGEMENT = "POST_ROLL_MANAGEMENT"
    NEGOTIATION_WINDOW = "NEGOTIATION_WINDOW"
    END_TURN = "END_TURN"
    BANKRUPTCY_RESOLUTION = "BANKRUPTCY_RESOLUTION"
    GAME_OVER = "GAME_OVER"


PHASE_NAMES: Final = tuple(phase.value for phase in TurnPhase)

VALID_PHASE_TRANSITIONS: Final[dict[TurnPhase, tuple[TurnPhase, ...]]] = {
    TurnPhase.START_TURN: (TurnPhase.PRE_ROLL_MANAGEMENT, TurnPhase.BANKRUPTCY_RESOLUTION),
    TurnPhase.PRE_ROLL_MANAGEMENT: (
        TurnPhase.ROLL_REQUIRED,
        TurnPhase.JAIL_RESOLUTION,
        TurnPhase.BANKRUPTCY_RESOLUTION,
    ),
    TurnPhase.ROLL_REQUIRED: (TurnPhase.MOVEMENT_RESOLUTION, TurnPhase.BANKRUPTCY_RESOLUTION),
    TurnPhase.MOVEMENT_RESOLUTION: (TurnPhase.SPACE_RESOLUTION, TurnPhase.BANKRUPTCY_RESOLUTION),
    TurnPhase.SPACE_RESOLUTION: (
        TurnPhase.PURCHASE_OR_AUCTION,
        TurnPhase.PAYMENT_RESOLUTION,
        TurnPhase.POST_ROLL_MANAGEMENT,
        TurnPhase.BANKRUPTCY_RESOLUTION,
    ),
    TurnPhase.PURCHASE_OR_AUCTION: (
        TurnPhase.PAYMENT_RESOLUTION,
        TurnPhase.POST_ROLL_MANAGEMENT,
        TurnPhase.BANKRUPTCY_RESOLUTION,
    ),
    TurnPhase.PAYMENT_RESOLUTION: (
        TurnPhase.POST_ROLL_MANAGEMENT,
        TurnPhase.BANKRUPTCY_RESOLUTION,
    ),
    TurnPhase.JAIL_RESOLUTION: (
        TurnPhase.ROLL_REQUIRED,
        TurnPhase.MOVEMENT_RESOLUTION,
        TurnPhase.BANKRUPTCY_RESOLUTION,
    ),
    TurnPhase.POST_ROLL_MANAGEMENT: (
        TurnPhase.ROLL_REQUIRED,
        TurnPhase.NEGOTIATION_WINDOW,
        TurnPhase.END_TURN,
        TurnPhase.BANKRUPTCY_RESOLUTION,
    ),
    TurnPhase.NEGOTIATION_WINDOW: (
        TurnPhase.POST_ROLL_MANAGEMENT,
        TurnPhase.END_TURN,
        TurnPhase.BANKRUPTCY_RESOLUTION,
    ),
    TurnPhase.END_TURN: (TurnPhase.START_TURN, TurnPhase.BANKRUPTCY_RESOLUTION),
    TurnPhase.BANKRUPTCY_RESOLUTION: (
        TurnPhase.POST_ROLL_MANAGEMENT,
        TurnPhase.END_TURN,
        TurnPhase.GAME_OVER,
    ),
    TurnPhase.GAME_OVER: (),
}

def can_transition_phase(current_phase: str, next_phase: str) -> bool:
    current = _parse_phase(current_phase)
    next_ = _parse_phase(next_phase)
    if current is None or next_ is None:
        return False
    if current == next_:
        return True
    return next_ in VALID_PHASE_TRANSITIONS[current]


def assert_valid_phase_transition(current_phase: str, next_phase: str) -> None:
    current = _parse_phase(current_phase)
    next_ = _parse_phase(next_phase)
    if current is None:
        raise ValueError(f"unknown current turn phase {current_phase}")
    if next_ is None:
        raise ValueError(f"unknown turn phase {next_phase}")
    if not can_transition_phase(current_phase, next_phase):
        raise ValueError(f"invalid phase transition {current_phase} -> {next_phase}")


def _parse_phase(phase: str) -> TurnPhase | None:
    try:
        return TurnPhase(phase)
    except ValueError:
        return None


__all__ = [
    "PHASE_NAMES",
    "VALID_PHASE_TRANSITIONS",
    "TurnPhase",
    "assert_valid_phase_transition",
    "can_transition_phase",
]
