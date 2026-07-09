from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from app.rules.actions import GameAction, LegalAction, apply_action, list_legal_actions
from app.rules.mechanics import is_game_over, winning_player_id
from app.rules.phases import TurnPhase
from app.rules.static_data import load_classic_monopoly_data
from app.rules.state import GameState, PlayerSetup, PlayerState, create_initial_game_state


INVARIANT_CHECKS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "cash_ledger_reconciliation": (
            "Accepted event streams must reconcile player cash from PLAYER_CASH_DELTA events."
        ),
        "ownership_uniqueness": "Each tracked purchasable property must have one ownership slot.",
        "bank_inventory_non_negative": "Bank house and hotel inventory must stay within bank bounds.",
        "house_hotel_scarcity": "Bank inventory plus board improvements must equal the classic supply.",
        "phase_validity": "The turn phase must be one of the declared rules-engine phases.",
        "replay_determinism": "Replaying the same seed and events must reproduce the same state.",
        "random_legal_action_simulation": "Generated legal-action simulations must preserve invariants.",
        "reproducible_failure_context": "Invariant failures must include enough context to reproduce.",
    }
)


@dataclass(frozen=True, slots=True)
class InvariantViolation:
    code: str
    message: str
    path: str | None = None

    def model_dump(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "path": self.path,
        }


class SimulationFailure(RuntimeError):
    reproducible_context: Mapping[str, object]

    def __init__(self, message: str, reproducible_context: Mapping[str, object]) -> None:
        self.reproducible_context = _freeze_mapping(reproducible_context)
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ActionLogEntry:
    index: int
    actor_id: str
    action: Mapping[str, object]
    state_hash_before: str
    state_hash_after: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "action", _freeze_mapping(self.action))

    def model_dump(self) -> dict[str, object]:
        return {
            "index": self.index,
            "actor_id": self.actor_id,
            "action": _thaw_value(self.action),
            "state_hash_before": self.state_hash_before,
            "state_hash_after": self.state_hash_after,
        }


@dataclass(frozen=True, slots=True)
class SimulationResult:
    final_state: GameState
    actions_executed: int
    invariant_checks: int
    action_log: tuple[ActionLogEntry, ...] = ()
    failure: SimulationFailure | None = None


class ScriptedPlayer:
    def __init__(self, actions: Sequence[GameAction] | Sequence[Mapping[str, object]]) -> None:
        self._actions: tuple[GameAction | Mapping[str, object], ...] = tuple(actions)

    def choose_action(self, state: GameState, action_index: int) -> GameAction | None:
        if action_index >= len(self._actions):
            return None
        return _coerce_scripted_action(self._actions[action_index], state)


@dataclass(frozen=True, slots=True)
class RandomLegalActionPlayer:
    seed: str

    def choose_action(self, state: GameState, action_index: int) -> GameAction | None:
        legal_actions = _legal_actions_for_state(state)
        if not legal_actions:
            return None

        preferred_actions = tuple(
            legal_action for legal_action in legal_actions if legal_action.type != "DECLARE_BANKRUPTCY"
        )
        candidates = preferred_actions or legal_actions
        choice = min(
            candidates,
            key=lambda legal_action: _stable_action_score(
                seed=self.seed,
                state=state,
                action_index=action_index,
                action=legal_action,
            ),
        )
        return _game_action_from_legal(choice)


def check_invariants(state: GameState, action: object | None = None) -> None:
    _check_invariants(state, action=action, action_index=None)


def run_random_legal_action_stress(
    seed: str,
    player_count: int,
    action_limit: int,
) -> SimulationResult:
    if action_limit < 0:
        raise ValueError("action_limit must be non-negative")

    state = create_initial_game_state(
        seed=seed,
        players=_player_setups(player_count),
        game_id=f"simulation-random-{_short_hash(seed, str(player_count), str(action_limit))}",
    )
    player = RandomLegalActionPlayer(seed=seed)
    return _run_player_actions(
        state=state,
        choose_action=player.choose_action,
        action_limit=action_limit,
        event_id_prefix="simulation-random",
    )


def run_scripted_game_to_completion(seed: str) -> SimulationResult:
    state = create_initial_game_state(
        seed=seed,
        players=_player_setups(2),
        game_id=f"simulation-scripted-{_short_hash(seed)}",
    )
    player = ScriptedPlayer(
        (
            {"actor_id": "player-1", "type": "ROLL_DICE"},
            {"actor_id": "player-2", "type": "DECLARE_BANKRUPTCY", "payload": {"creditor_id": None}},
        )
    )
    result = _run_player_actions(
        state=state,
        choose_action=player.choose_action,
        action_limit=2,
        event_id_prefix="simulation-scripted",
    )
    if result.failure is not None or is_game_over(result.final_state):
        return result

    failure = _build_failure(
        "scripted simulation did not reach game over",
        result.final_state,
        action=None,
        action_index=result.actions_executed,
        violations=(
            InvariantViolation(
                code="scripted_game_not_over",
                message="scripted game exhausted actions before reaching game over",
            ),
        ),
    )
    return SimulationResult(
        final_state=result.final_state,
        actions_executed=result.actions_executed,
        invariant_checks=result.invariant_checks,
        action_log=result.action_log,
        failure=failure,
    )


def _run_player_actions(
    *,
    state: GameState,
    choose_action: object,
    action_limit: int,
    event_id_prefix: str,
) -> SimulationResult:
    action_log: list[ActionLogEntry] = []
    invariant_checks = 0
    actions_executed = 0

    try:
        _check_invariants(state, action=None, action_index=None)
        invariant_checks += 1

        for action_index in range(action_limit):
            action = _call_choose_action(choose_action, state, action_index)
            if action is None:
                raise _build_failure(
                    "simulation has no legal action to apply",
                    state,
                    action=None,
                    action_index=action_index,
                    violations=(
                        InvariantViolation(
                            code="no_legal_actions",
                            message="no scripted or legal random action was available",
                        ),
                    ),
                )

            state_hash_before = state.state_hash()
            state = apply_action(state, action, f"{event_id_prefix}-{action_index}")
            state_hash_after = state.state_hash()
            action_log.append(
                ActionLogEntry(
                    index=action_index,
                    actor_id=action.actor_id,
                    action=action.model_dump(mode="json"),
                    state_hash_before=state_hash_before,
                    state_hash_after=state_hash_after,
                )
            )
            actions_executed += 1
            _check_invariants(state, action=action, action_index=action_index)
            invariant_checks += 1
    except SimulationFailure as exc:
        return SimulationResult(
            final_state=state,
            actions_executed=actions_executed,
            invariant_checks=invariant_checks,
            action_log=tuple(action_log),
            failure=exc,
        )
    except Exception as exc:
        action = locals().get("action")
        failure = _build_failure(
            f"simulation action failed: {exc}",
            state,
            action=action,
            action_index=locals().get("action_index"),
            violations=(
                InvariantViolation(
                    code="action_application_failed",
                    message=str(exc),
                    path=type(exc).__name__,
                ),
            ),
        )
        return SimulationResult(
            final_state=state,
            actions_executed=actions_executed,
            invariant_checks=invariant_checks,
            action_log=tuple(action_log),
            failure=failure,
        )

    return SimulationResult(
        final_state=state,
        actions_executed=actions_executed,
        invariant_checks=invariant_checks,
        action_log=tuple(action_log),
        failure=None,
    )


def _call_choose_action(choose_action: object, state: GameState, action_index: int) -> GameAction | None:
    if not callable(choose_action):
        raise TypeError("choose_action must be callable")
    action = choose_action(state, action_index)
    if action is None or isinstance(action, GameAction):
        return action
    raise TypeError("choose_action must return GameAction or None")


def _check_invariants(
    state: GameState,
    *,
    action: object | None,
    action_index: int | None,
) -> None:
    violations = _collect_invariant_violations(state)
    if violations:
        raise _build_failure(
            "simulation invariant violation",
            state,
            action=action,
            action_index=action_index,
            violations=violations,
        )


def _collect_invariant_violations(state: GameState) -> tuple[InvariantViolation, ...]:
    violations: list[InvariantViolation] = []
    data = load_classic_monopoly_data()
    expected_property_ids = {property_data.id for property_data in data.properties}

    def add(code: str, message: str, path: str | None = None) -> None:
        violations.append(InvariantViolation(code=code, message=message, path=path))

    if not 2 <= len(state.players) <= 5:
        add("invalid_player_count", "game must contain 2 to 5 players", "players")

    player_ids = [player.id for player in state.players]
    if len(set(player_ids)) != len(player_ids):
        add("duplicate_player_id", "player ids must be unique", "players")
    player_id_set = set(player_ids)
    active_player_ids = {player.id for player in state.players if not player.is_bankrupt}

    property_ids = [ownership.property_id for ownership in state.property_ownership]
    if len(property_ids) != 28:
        add(
            "invalid_property_ownership_count",
            "classic state must track exactly 28 property ownership entries",
            "property_ownership",
        )
    if len(set(property_ids)) != len(property_ids):
        add("duplicate_property_id", "property ownership ids must be unique", "property_ownership")
    unknown_property_ids = set(property_ids) - expected_property_ids
    if unknown_property_ids:
        add(
            "unknown_property_id",
            f"unknown property id {sorted(unknown_property_ids)[0]}",
            "property_ownership",
        )

    if not 0 <= state.bank_inventory.houses <= 32:
        add("invalid_bank_houses", "bank house inventory must be within 0 to 32", "bank_inventory.houses")
    if not 0 <= state.bank_inventory.hotels <= 12:
        add("invalid_bank_hotels", "bank hotel inventory must be within 0 to 12", "bank_inventory.hotels")

    houses_on_board = sum(ownership.houses for ownership in state.property_ownership)
    hotels_on_board = sum(1 for ownership in state.property_ownership if ownership.hotel)
    if state.bank_inventory.houses + houses_on_board != data.bank_inventory.houses:
        add(
            "house_scarcity_mismatch",
            "bank houses plus board houses must equal the classic house supply",
            "bank_inventory.houses",
        )
    if state.bank_inventory.hotels + hotels_on_board != data.bank_inventory.hotels:
        add(
            "hotel_scarcity_mismatch",
            "bank hotels plus board hotels must equal the classic hotel supply",
            "bank_inventory.hotels",
        )

    try:
        TurnPhase(state.turn.phase)
    except ValueError:
        add("invalid_turn_phase", f"unknown turn phase {state.turn.phase}", "turn.phase")

    for ownership in state.property_ownership:
        if ownership.owner_id is not None and ownership.owner_id not in active_player_ids:
            add(
                "invalid_property_owner",
                f"{ownership.property_id} owner must reference an active player or None",
                f"property_ownership.{ownership.property_id}.owner_id",
            )
        if ownership.hotel and ownership.houses:
            add(
                "property_has_houses_and_hotel",
                f"{ownership.property_id} cannot have both houses and a hotel",
                f"property_ownership.{ownership.property_id}",
            )

    chance_card_ids = {card.id for card in data.decks.chance}
    community_chest_card_ids = {card.id for card in data.decks.community_chest}
    _collect_deck_invariant_violations(
        violations,
        "chance",
        (
            *state.decks.chance.draw_pile,
            *state.decks.chance.discard_pile,
            *_held_card_ids_for_deck(state, chance_card_ids),
        ),
        chance_card_ids,
    )
    _collect_deck_invariant_violations(
        violations,
        "community_chest",
        (
            *state.decks.community_chest.draw_pile,
            *state.decks.community_chest.discard_pile,
            *_held_card_ids_for_deck(state, community_chest_card_ids),
        ),
        community_chest_card_ids,
    )

    auction = state.active_auction
    if auction is not None:
        property_by_id = {ownership.property_id: ownership for ownership in state.property_ownership}
        auction_property = property_by_id.get(auction.property_id)
        if auction_property is None:
            add(
                "active_auction_unknown_property",
                "active auction must reference an existing property",
                "active_auction.property_id",
            )
        elif auction_property.owner_id is not None:
            add(
                "active_auction_owned_property",
                "active auction property must be unowned",
                "active_auction.property_id",
            )

        if auction.high_bidder_id is not None and auction.high_bidder_id not in active_player_ids:
            add(
                "active_auction_invalid_high_bidder",
                "active auction high bidder must reference an active player",
                "active_auction.high_bidder_id",
            )
        if auction.high_bidder_id is not None and auction.high_bid_amount is None:
            add(
                "active_auction_missing_high_bid",
                "active auction high bidder requires a high bid amount",
                "active_auction.high_bid_amount",
            )
        if auction.high_bidder_id is None and auction.high_bid_amount is not None:
            add(
                "active_auction_missing_high_bidder",
                "active auction high bid amount requires a high bidder",
                "active_auction.high_bidder_id",
            )
        if auction.high_bid_amount is not None and auction.high_bid_amount <= 0:
            add(
                "active_auction_invalid_high_bid",
                "active auction high bid must be positive",
                "active_auction.high_bid_amount",
            )

        high_bidder = _player_by_id(state, auction.high_bidder_id)
        if (
            high_bidder is not None
            and auction.high_bid_amount is not None
            and auction.high_bid_amount > high_bidder.cash
        ):
            add(
                "active_auction_high_bid_exceeds_cash",
                "active auction high bidder must be able to cover the high bid",
                "active_auction.high_bid_amount",
            )

        if len(set(auction.passed_player_ids)) != len(auction.passed_player_ids):
            add(
                "active_auction_duplicate_pass",
                "active auction passed player ids must be unique",
                "active_auction.passed_player_ids",
            )
        invalid_passed_ids = set(auction.passed_player_ids) - active_player_ids
        if invalid_passed_ids:
            unknown_id = sorted(invalid_passed_ids)[0]
            if unknown_id in player_id_set:
                message = f"passed auction player {unknown_id} must be active"
            else:
                message = f"passed auction player {unknown_id} must reference an existing player"
            add("active_auction_invalid_passed_player", message, "active_auction.passed_player_ids")

    return tuple(violations)


def _collect_deck_invariant_violations(
    violations: list[InvariantViolation],
    deck_name: str,
    card_ids: tuple[str, ...],
    expected_card_ids: set[str],
) -> None:
    if len(set(card_ids)) != len(card_ids):
        violations.append(
            InvariantViolation(
                code="duplicate_deck_card",
                message=f"{deck_name} deck cannot contain duplicate card ids",
                path=f"decks.{deck_name}",
            )
        )

    card_id_set = set(card_ids)
    unknown_card_ids = card_id_set - expected_card_ids
    if unknown_card_ids:
        violations.append(
            InvariantViolation(
                code="unknown_deck_card",
                message=f"{deck_name} deck contains unknown card {sorted(unknown_card_ids)[0]}",
                path=f"decks.{deck_name}",
            )
        )
    missing_card_ids = expected_card_ids - card_id_set
    if missing_card_ids:
        violations.append(
            InvariantViolation(
                code="missing_deck_card",
                message=f"{deck_name} deck is missing card {sorted(missing_card_ids)[0]}",
                path=f"decks.{deck_name}",
            )
        )


def _held_card_ids_for_deck(state: GameState, expected_card_ids: set[str]) -> tuple[str, ...]:
    return tuple(
        card_id
        for player in state.players
        for card_id in player.get_out_of_jail_card_ids
        if card_id in expected_card_ids
    )


def _build_failure(
    message: str,
    state: GameState,
    *,
    action: object | None,
    action_index: object,
    violations: Sequence[InvariantViolation],
) -> SimulationFailure:
    action_data = _action_to_mapping(action)
    context = {
        "seed": state.seed,
        "state_hash": _safe_state_hash(state),
        "event_sequence": state.event_sequence,
        "action_index": action_index if isinstance(action_index, int) else None,
        "actor_id": action_data.get("actor_id"),
        "action_type": action_data.get("type"),
        "action": action_data,
        "state_summary": _state_summary(state),
        "invariant_violations": tuple(violation.model_dump() for violation in violations),
    }
    return SimulationFailure(message, context)


def _state_summary(state: GameState) -> Mapping[str, object]:
    active_player_ids = tuple(player.id for player in state.players if not player.is_bankrupt)
    bankrupt_player_ids = tuple(player.id for player in state.players if player.is_bankrupt)
    owned_properties = sum(1 for ownership in state.property_ownership if ownership.owner_id is not None)
    auction: Mapping[str, object] | None = None
    if state.active_auction is not None:
        auction = {
            "property_id": state.active_auction.property_id,
            "high_bidder_id": state.active_auction.high_bidder_id,
            "high_bid_amount": state.active_auction.high_bid_amount,
            "passed_player_ids": state.active_auction.passed_player_ids,
        }
    return {
        "game_id": state.game_id,
        "player_count": len(state.players),
        "active_player_ids": active_player_ids,
        "bankrupt_player_ids": bankrupt_player_ids,
        "cash_by_player": {player.id: player.cash for player in state.players},
        "owned_property_count": owned_properties,
        "bank_inventory": {
            "houses": state.bank_inventory.houses,
            "hotels": state.bank_inventory.hotels,
        },
        "turn": {
            "turn_number": state.turn.turn_number,
            "current_player_id": state.turn.current_player_id,
            "phase": state.turn.phase,
        },
        "active_auction": auction,
        "game_over": is_game_over(state),
        "winner_id": winning_player_id(state),
    }


def _legal_actions_for_state(state: GameState) -> tuple[LegalAction, ...]:
    actions: list[LegalAction] = []
    for player in state.players:
        actions.extend(list_legal_actions(state, player.id))
    return tuple(actions)


def _stable_action_score(
    *,
    seed: str,
    state: GameState,
    action_index: int,
    action: LegalAction,
) -> str:
    payload = {
        "seed": seed,
        "state_hash": state.state_hash(),
        "action_index": action_index,
        "action": action.model_dump(mode="json"),
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _game_action_from_legal(action: LegalAction) -> GameAction:
    return GameAction(
        actor_id=action.actor_id,
        type=action.type,
        payload=action.payload,
        expected_state_hash=action.expected_state_hash,
        expected_event_sequence=action.expected_event_sequence,
    )


def _coerce_scripted_action(action: GameAction | Mapping[str, object], state: GameState) -> GameAction:
    if isinstance(action, GameAction):
        return GameAction(
            actor_id=action.actor_id,
            type=action.type,
            payload=action.payload,
            expected_state_hash=state.state_hash(),
            expected_event_sequence=state.event_sequence,
        )

    actor_id = action.get("actor_id")
    action_type = action.get("type")
    if not isinstance(actor_id, str) or not actor_id:
        raise ValueError("scripted action actor_id is required")
    if not isinstance(action_type, str) or not action_type:
        raise ValueError("scripted action type is required")
    payload = action.get("payload", {})
    if not isinstance(payload, Mapping):
        raise ValueError("scripted action payload must be an object")
    return GameAction(
        actor_id=actor_id,
        type=action_type,
        payload=payload,
        expected_state_hash=state.state_hash(),
        expected_event_sequence=state.event_sequence,
    )


def _action_to_mapping(action: object | None) -> Mapping[str, object]:
    if action is None:
        return {}
    if isinstance(action, GameAction):
        return action.model_dump(mode="json")
    if isinstance(action, LegalAction):
        return action.model_dump(mode="json")
    if isinstance(action, Mapping):
        return {str(key): _thaw_value(value) for key, value in action.items()}
    return {"repr": repr(action)}


def _player_setups(player_count: int) -> tuple[PlayerSetup, ...]:
    return tuple(
        PlayerSetup(
            id=f"player-{index}",
            name=f"Player {index}",
            kind="human" if index == 1 else "ai",
        )
        for index in range(1, player_count + 1)
    )


def _player_by_id(state: GameState, player_id: str | None) -> PlayerState | None:
    if player_id is None:
        return None
    for player in state.players:
        if player.id == player_id:
            return player
    return None


def _safe_state_hash(state: GameState) -> str:
    try:
        return state.state_hash()
    except Exception as exc:
        return f"unavailable:{type(exc).__name__}:{exc}"


def _short_hash(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()[:12]


def _freeze_mapping(mapping: Mapping[str, object]) -> Mapping[str, object]:
    return MappingProxyType({str(key): _freeze_value(value) for key, value in mapping.items()})


def _freeze_value(value: object) -> object:
    if isinstance(value, Mapping):
        return _freeze_mapping(value)
    if isinstance(value, tuple):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, list):
        return tuple(_freeze_value(item) for item in value)
    return value


def _thaw_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _thaw_value(inner_value) for key, inner_value in value.items()}
    if isinstance(value, tuple):
        return [_thaw_value(item) for item in value]
    return value


__all__ = [
    "ActionLogEntry",
    "INVARIANT_CHECKS",
    "InvariantViolation",
    "RandomLegalActionPlayer",
    "ScriptedPlayer",
    "SimulationFailure",
    "SimulationResult",
    "check_invariants",
    "run_random_legal_action_stress",
    "run_scripted_game_to_completion",
]
