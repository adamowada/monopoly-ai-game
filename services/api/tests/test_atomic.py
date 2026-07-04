from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest
from pydantic import ValidationError

from app.rules import (
    ATOMIC_RESOLUTION_KIND_NAMES,
    ActiveAtomicResolutionState,
    ActiveAtomicResolutionSetPayload,
    AtomicResolutionKind,
    GameEvent,
    InvalidEventError,
    build_rejected_action_audit_entry,
    is_atomic_section_active,
)
from app.rules.actions import ActionValidationError, GameAction, apply_action, list_legal_actions, validate_action
from app.rules.reducer import apply_event
from app.rules.state import GameState, PlayerSetup, create_initial_game_state


def _player_setups() -> tuple[PlayerSetup, ...]:
    return (
        PlayerSetup(id="player-1", name="Player 1", kind="human"),
        PlayerSetup(id="player-2", name="Player 2", kind="ai"),
    )


def _initial_state() -> GameState:
    return create_initial_game_state(
        seed="atomic-seed",
        game_id="atomic-game",
        players=_player_setups(),
    )


def _atomic_event(
    state: GameState,
    payload: ActiveAtomicResolutionSetPayload,
) -> GameEvent:
    return GameEvent(
        event_id=f"atomic-{state.event_sequence + 1}",
        sequence=state.event_sequence + 1,
        type="ACTIVE_ATOMIC_RESOLUTION_SET",
        payload=payload,
    )


def _begin_atomic(
    state: GameState,
    kind: AtomicResolutionKind = AtomicResolutionKind.MOVEMENT,
    actor_id: str | None = "player-1",
) -> GameState:
    return apply_event(
        state,
        _atomic_event(
            state,
            ActiveAtomicResolutionSetPayload(active=True, kind=kind, actor_id=actor_id),
        ),
    )


def _action(state: GameState, action_type: str = "ROLL_DICE") -> GameAction:
    return GameAction(
        actor_id="player-1",
        type=action_type,
        payload={},
        expected_state_hash=state.state_hash(),
        expected_event_sequence=state.event_sequence,
    )


def _issue_codes(exc: ActionValidationError) -> set[str]:
    return {issue.code for issue in exc.errors}


def test_atomic_resolution_kind_surface_is_exact() -> None:
    assert tuple(kind.value for kind in AtomicResolutionKind) == (
        "DICE_ROLL",
        "MOVEMENT",
        "CARD_DRAW",
        "CARD_EFFECT",
        "PAYMENT_CREATION",
        "FORCED_MOVEMENT",
    )
    assert ATOMIC_RESOLUTION_KIND_NAMES == tuple(kind.value for kind in AtomicResolutionKind)


def test_new_games_start_without_active_atomic_resolution() -> None:
    state = _initial_state()

    assert state.active_atomic_resolution is None
    assert not is_atomic_section_active(state)


def test_active_atomic_resolution_state_requires_one_kind() -> None:
    state = GameState.model_validate(
        {
            **_initial_state().model_dump(mode="python"),
            "active_atomic_resolution": ActiveAtomicResolutionState(
                kind=AtomicResolutionKind.CARD_EFFECT,
                actor_id="player-2",
            ),
        }
    )

    assert state.active_atomic_resolution is not None
    assert state.active_atomic_resolution.kind is AtomicResolutionKind.CARD_EFFECT
    assert state.active_atomic_resolution.actor_id == "player-2"
    assert is_atomic_section_active(state)


def test_active_atomic_resolution_rejects_unknown_actor() -> None:
    with pytest.raises(ValueError, match="active atomic resolution actor"):
        GameState.model_validate(
            {
                **_initial_state().model_dump(mode="python"),
                "active_atomic_resolution": ActiveAtomicResolutionState(
                    kind=AtomicResolutionKind.DICE_ROLL,
                    actor_id="missing-player",
                ),
            }
        )


def test_atomic_resolution_event_payload_shape() -> None:
    assert ActiveAtomicResolutionSetPayload(
        active=True,
        kind=AtomicResolutionKind.DICE_ROLL,
        actor_id="player-1",
    ).kind is AtomicResolutionKind.DICE_ROLL
    assert ActiveAtomicResolutionSetPayload(active=False).kind is None

    with pytest.raises(ValidationError, match="active atomic resolution payload must include kind"):
        ActiveAtomicResolutionSetPayload(active=True)
    with pytest.raises(ValidationError, match="inactive atomic resolution payload cannot include details"):
        ActiveAtomicResolutionSetPayload(active=False, kind=AtomicResolutionKind.MOVEMENT)
    with pytest.raises(ValidationError, match="inactive atomic resolution payload cannot include details"):
        ActiveAtomicResolutionSetPayload(active=False, actor_id="player-1")


def test_reducer_begins_and_ends_atomic_resolution() -> None:
    state = _initial_state()

    active_state = _begin_atomic(state, AtomicResolutionKind.PAYMENT_CREATION, "player-1")

    assert active_state.active_atomic_resolution == ActiveAtomicResolutionState(
        kind=AtomicResolutionKind.PAYMENT_CREATION,
        actor_id="player-1",
    )
    assert active_state.event_sequence == state.event_sequence + 1

    inactive_state = apply_event(
        active_state,
        _atomic_event(active_state, ActiveAtomicResolutionSetPayload(active=False)),
    )

    assert inactive_state.active_atomic_resolution is None
    assert inactive_state.event_sequence == active_state.event_sequence + 1


def test_reducer_rejects_invalid_atomic_resolution_lifecycle() -> None:
    state = _initial_state()

    with pytest.raises(InvalidEventError, match="no active atomic resolution"):
        apply_event(state, _atomic_event(state, ActiveAtomicResolutionSetPayload(active=False)))

    active_state = _begin_atomic(state)
    with pytest.raises(InvalidEventError, match="already active"):
        apply_event(
            active_state,
            _atomic_event(
                active_state,
                ActiveAtomicResolutionSetPayload(
                    active=True,
                    kind=AtomicResolutionKind.CARD_DRAW,
                    actor_id="player-1",
                ),
            ),
        )

    with pytest.raises(InvalidEventError, match="unknown player"):
        apply_event(
            state,
            _atomic_event(
                state,
                ActiveAtomicResolutionSetPayload(
                    active=True,
                    kind=AtomicResolutionKind.FORCED_MOVEMENT,
                    actor_id="missing-player",
                ),
            ),
        )


def test_atomic_resolution_blocks_legal_actions_and_validation() -> None:
    state = _begin_atomic(_initial_state(), AtomicResolutionKind.MOVEMENT, "player-1")

    assert list_legal_actions(state, "player-1") == ()

    with pytest.raises(ActionValidationError) as exc_info:
        validate_action(state, _action(state))
    assert _issue_codes(exc_info.value) == {"mistimed_action"}

    with pytest.raises(ActionValidationError) as apply_exc_info:
        apply_action(state, _action(state), "blocked")
    assert _issue_codes(apply_exc_info.value) == {"mistimed_action"}
    assert state.event_sequence == 1
    assert state.active_atomic_resolution is not None


def test_stale_action_rejection_happens_before_atomic_timing() -> None:
    state = _begin_atomic(_initial_state(), AtomicResolutionKind.DICE_ROLL, "player-1")

    with pytest.raises(ActionValidationError) as exc_info:
        validate_action(
            state,
            GameAction(
                actor_id="player-1",
                type="ROLL_DICE",
                payload={},
                expected_state_hash="stale",
                expected_event_sequence=state.event_sequence - 1,
            ),
        )

    assert _issue_codes(exc_info.value) == {"stale_action"}


def test_rejected_action_audit_entry_captures_atomic_context_and_errors() -> None:
    state = _begin_atomic(_initial_state(), AtomicResolutionKind.CARD_EFFECT, "player-1")
    action = _action(state)

    with pytest.raises(ActionValidationError) as exc_info:
        validate_action(state, action)

    entry = build_rejected_action_audit_entry(state, action, exc_info.value.errors)

    assert entry.game_id == state.game_id
    assert entry.state_hash == state.state_hash()
    assert entry.event_sequence == state.event_sequence
    assert entry.phase == state.turn.phase
    assert entry.active_atomic_kind == "CARD_EFFECT"
    assert entry.actor_id == "player-1"
    assert entry.action_type == "ROLL_DICE"
    assert entry.action_payload == {}
    assert entry.errors == (
        {
            "code": "mistimed_action",
            "message": "ROLL_DICE is not legal during active atomic resolution CARD_EFFECT",
            "field": "type",
        },
    )
    assert entry.model_dump(mode="json")["active_atomic_kind"] == "CARD_EFFECT"

    with pytest.raises(FrozenInstanceError):
        setattr(entry, "game_id", "changed")
