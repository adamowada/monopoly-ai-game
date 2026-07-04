from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from app.rules.atomic import is_atomic_section_active
from app.rules.debt import debt_issue_for_action
from app.rules.phases import TurnPhase
from app.rules.state import GameState


DEAL_PROPOSAL_ACTION_TYPES: Final[frozenset[str]] = frozenset(
    {
        "PROPOSE_DEAL",
        "COUNTER_DEAL",
    }
)
TRADE_RESPONSE_ACTION_TYPES: Final[frozenset[str]] = frozenset(
    {
        "ACCEPT_DEAL",
        "REJECT_DEAL",
    }
)

MANAGEMENT_PHASES: Final[frozenset[TurnPhase]] = frozenset(
    {
        TurnPhase.START_TURN,
        TurnPhase.PRE_ROLL_MANAGEMENT,
        TurnPhase.POST_ROLL_MANAGEMENT,
        TurnPhase.NEGOTIATION_WINDOW,
    }
)
LIQUIDATION_PHASES: Final[frozenset[TurnPhase]] = frozenset(
    {
        TurnPhase.PAYMENT_RESOLUTION,
        TurnPhase.BANKRUPTCY_RESOLUTION,
    }
)

_ROLL_PHASES: Final = frozenset(
    {
        TurnPhase.START_TURN,
        TurnPhase.ROLL_REQUIRED,
        TurnPhase.JAIL_RESOLUTION,
    }
)
_PURCHASE_OR_AUCTION_PHASES: Final = frozenset(
    {
        TurnPhase.START_TURN,
        TurnPhase.PURCHASE_OR_AUCTION,
    }
)
_JAIL_ACTION_PHASES: Final = frozenset(
    {
        TurnPhase.START_TURN,
        TurnPhase.JAIL_RESOLUTION,
    }
)
_BANKRUPTCY_ACTION_PHASES: Final = frozenset(
    phase for phase in TurnPhase if phase is not TurnPhase.GAME_OVER
)
_DEBT_ACTION_PHASES: Final = frozenset(
    phase for phase in TurnPhase if phase is not TurnPhase.GAME_OVER
)

_ACTION_TIMING_WINDOWS: Final[dict[str, frozenset[TurnPhase]]] = {
    "ROLL_DICE": _ROLL_PHASES,
    "BUY_PROPERTY": _PURCHASE_OR_AUCTION_PHASES,
    "START_AUCTION": _PURCHASE_OR_AUCTION_PHASES,
    "BID_AUCTION": _PURCHASE_OR_AUCTION_PHASES,
    "PASS_AUCTION": _PURCHASE_OR_AUCTION_PHASES,
    "PAY_JAIL_FINE": _JAIL_ACTION_PHASES,
    "USE_GET_OUT_OF_JAIL_CARD": _JAIL_ACTION_PHASES,
    "BUY_HOUSE": MANAGEMENT_PHASES | LIQUIDATION_PHASES,
    "SELL_HOUSE": MANAGEMENT_PHASES | LIQUIDATION_PHASES,
    "MORTGAGE_PROPERTY": MANAGEMENT_PHASES | LIQUIDATION_PHASES,
    "UNMORTGAGE_PROPERTY": MANAGEMENT_PHASES | LIQUIDATION_PHASES,
    "DECLARE_BANKRUPTCY": _BANKRUPTCY_ACTION_PHASES,
    "SETTLE_DEBT": _DEBT_ACTION_PHASES,
    **{
        action_type: MANAGEMENT_PHASES
        for action_type in DEAL_PROPOSAL_ACTION_TYPES | TRADE_RESPONSE_ACTION_TYPES
    },
}
ACTION_TIMING_WINDOWS: Final[Mapping[str, frozenset[TurnPhase]]] = MappingProxyType(
    _ACTION_TIMING_WINDOWS
)

_ACTIVE_AUCTION_ACTION_TYPES: Final = frozenset({"BID_AUCTION", "PASS_AUCTION"})
_ACTIVE_AUCTION_COMPATIBLE_ACTION_TYPES: Final = _ACTIVE_AUCTION_ACTION_TYPES | frozenset(
    {"DECLARE_BANKRUPTCY"}
)


@dataclass(frozen=True, slots=True)
class ActionTimingIssue:
    code: str
    message: str
    field: str | None = None

    def model_dump(self, *, mode: str = "python") -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "field": self.field,
        }


def is_action_type_allowed_in_phase(action_type: str, phase: TurnPhase | str) -> bool:
    parsed_phase = _parse_phase(phase)
    if parsed_phase is None:
        return False
    timing_window = ACTION_TIMING_WINDOWS.get(action_type)
    if timing_window is None:
        return False
    return parsed_phase in timing_window


def is_action_allowed_now(
    state: GameState,
    action_type: str,
    actor_id: str | None = None,
) -> bool:
    return timing_issue_for_action(state, action_type, actor_id=actor_id) is None


def timing_issue_for_action(
    state: GameState,
    action_type: str,
    actor_id: str | None = None,
) -> ActionTimingIssue | None:
    if is_atomic_section_active(state):
        active_atomic = state.active_atomic_resolution
        atomic_kind = "UNKNOWN" if active_atomic is None else active_atomic.kind.value
        return ActionTimingIssue(
            code="mistimed_action",
            message=f"{action_type} is not legal during active atomic resolution {atomic_kind}",
            field="type",
        )

    phase = _parse_phase(state.turn.phase)
    if phase is None:
        return ActionTimingIssue(
            code="mistimed_action",
            message=f"unknown turn phase {state.turn.phase}",
            field="type",
        )

    if not is_action_type_allowed_in_phase(action_type, phase):
        return ActionTimingIssue(
            code="mistimed_action",
            message=f"{action_type} is not legal during {phase.value}",
            field="type",
        )

    debt_issue = debt_issue_for_action(state, action_type, actor_id)
    if debt_issue is not None:
        return ActionTimingIssue(
            code=debt_issue.code,
            message=debt_issue.message,
            field=debt_issue.field,
        )

    if state.active_auction is not None and action_type not in _ACTIVE_AUCTION_COMPATIBLE_ACTION_TYPES:
        return ActionTimingIssue(
            code="mistimed_action",
            message=f"{action_type} is not legal during an active auction",
            field="type",
        )

    if action_type in _ACTIVE_AUCTION_ACTION_TYPES and state.active_auction is None:
        return ActionTimingIssue(
            code="mistimed_action",
            message=f"{action_type} requires an active auction",
            field="type",
        )

    return None


def _parse_phase(phase: TurnPhase | str) -> TurnPhase | None:
    try:
        return TurnPhase(phase)
    except ValueError:
        return None


__all__ = [
    "ACTION_TIMING_WINDOWS",
    "DEAL_PROPOSAL_ACTION_TYPES",
    "LIQUIDATION_PHASES",
    "MANAGEMENT_PHASES",
    "TRADE_RESPONSE_ACTION_TYPES",
    "ActionTimingIssue",
    "is_action_allowed_now",
    "is_action_type_allowed_in_phase",
    "timing_issue_for_action",
]
