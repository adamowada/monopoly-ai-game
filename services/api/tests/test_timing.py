from __future__ import annotations

from collections.abc import Mapping

import pytest

from app.rules.actions import ActionValidationError, GameAction, LegalAction, list_legal_actions, validate_action
from app.rules.events import (
    ActiveAuctionSetPayload,
    GameEvent,
    PlayerJailCardsSetPayload,
    PlayerJailSetPayload,
    PlayerPositionSetPayload,
    PropertyImprovementsSetPayload,
    PropertyMortgageSetPayload,
    PropertyOwnerSetPayload,
)
from app.rules.phases import TurnPhase
from app.rules.reducer import apply_event
from app.rules.state import GameState, PlayerSetup, create_initial_game_state
from app.rules.timing import (
    ACTION_TIMING_WINDOWS,
    DEAL_PROPOSAL_ACTION_TYPES,
    LIQUIDATION_PHASES,
    MANAGEMENT_PHASES,
    TRADE_RESPONSE_ACTION_TYPES,
    is_action_allowed_now,
    is_action_type_allowed_in_phase,
    timing_issue_for_action,
)


def _player_setups() -> tuple[PlayerSetup, ...]:
    return (
        PlayerSetup(id="player-1", name="Player 1", kind="human"),
        PlayerSetup(id="player-2", name="Player 2", kind="ai"),
        PlayerSetup(id="player-3", name="Player 3", kind="ai"),
    )


def _initial_state() -> GameState:
    return create_initial_game_state(
        seed="timing-seed",
        game_id="timing-game",
        players=_player_setups(),
    )


def _apply_setup_event(state: GameState, event_type: str, payload: object) -> GameState:
    return apply_event(
        state,
        GameEvent(
            event_id=f"setup-{state.event_sequence + 1}",
            sequence=state.event_sequence + 1,
            type=event_type,  # type: ignore[arg-type]
            payload=payload,  # type: ignore[arg-type]
        ),
    )


def _state_in_phase(state: GameState, phase: TurnPhase) -> GameState:
    return GameState.model_validate(
        {
            **state.model_dump(mode="python"),
            "turn": {
                **state.turn.model_dump(mode="python"),
                "phase": phase,
            },
        }
    )


def _set_position(state: GameState, player_id: str, position: int) -> GameState:
    return _apply_setup_event(
        state,
        "PLAYER_POSITION_SET",
        PlayerPositionSetPayload(player_id=player_id, position=position),
    )


def _set_jail(state: GameState, player_id: str, in_jail: bool, jail_turns: int = 0) -> GameState:
    return _apply_setup_event(
        state,
        "PLAYER_JAIL_SET",
        PlayerJailSetPayload(player_id=player_id, in_jail=in_jail, jail_turns=jail_turns),
    )


def _set_jail_cards(state: GameState, player_id: str, card_ids: tuple[str, ...]) -> GameState:
    return _apply_setup_event(
        state,
        "PLAYER_JAIL_CARDS_SET",
        PlayerJailCardsSetPayload(player_id=player_id, card_ids=card_ids),
    )


def _own(state: GameState, property_id: str, owner_id: str | None) -> GameState:
    return _apply_setup_event(
        state,
        "PROPERTY_OWNER_SET",
        PropertyOwnerSetPayload(property_id=property_id, owner_id=owner_id),
    )


def _mortgage(state: GameState, property_id: str, mortgaged: bool) -> GameState:
    return _apply_setup_event(
        state,
        "PROPERTY_MORTGAGE_SET",
        PropertyMortgageSetPayload(property_id=property_id, mortgaged=mortgaged),
    )


def _improve(state: GameState, property_id: str, houses: int, hotel: bool = False) -> GameState:
    return _apply_setup_event(
        state,
        "PROPERTY_IMPROVEMENTS_SET",
        PropertyImprovementsSetPayload(property_id=property_id, houses=houses, hotel=hotel),
    )


def _start_auction(state: GameState, property_id: str) -> GameState:
    return _apply_setup_event(
        state,
        "ACTIVE_AUCTION_SET",
        ActiveAuctionSetPayload(
            active=True,
            property_id=property_id,
            high_bidder_id=None,
            high_bid_amount=None,
            passed_player_ids=(),
        ),
    )


def _management_state(phase: TurnPhase = TurnPhase.START_TURN) -> GameState:
    state = _initial_state()
    state = _own(state, "property_oriental_avenue", "player-1")
    state = _own(state, "property_vermont_avenue", "player-1")
    state = _own(state, "property_connecticut_avenue", "player-1")
    state = _own(state, "property_baltic_avenue", "player-1")
    state = _own(state, "property_reading_railroad", "player-1")
    state = _mortgage(state, "property_baltic_avenue", True)
    state = _improve(state, "property_oriental_avenue", 1)
    return _state_in_phase(state, phase)


def _action(
    state: GameState,
    actor_id: str,
    action_type: str,
    payload: Mapping[str, object] | None = None,
) -> GameAction:
    return GameAction(
        actor_id=actor_id,
        type=action_type,
        payload={} if payload is None else dict(payload),
        expected_state_hash=state.state_hash(),
        expected_event_sequence=state.event_sequence,
    )


def _types(actions: tuple[LegalAction, ...]) -> set[str]:
    return {action.type for action in actions}


def _issue_codes(exc: ActionValidationError) -> set[str]:
    return {issue.code for issue in exc.errors}


def test_timing_constants_define_stage_3_2_windows() -> None:
    assert DEAL_PROPOSAL_ACTION_TYPES == frozenset({"PROPOSE_DEAL", "COUNTER_DEAL"})
    assert TRADE_RESPONSE_ACTION_TYPES == frozenset({"ACCEPT_DEAL", "REJECT_DEAL"})
    assert TurnPhase.START_TURN in MANAGEMENT_PHASES
    assert TurnPhase.PRE_ROLL_MANAGEMENT in MANAGEMENT_PHASES
    assert TurnPhase.POST_ROLL_MANAGEMENT in MANAGEMENT_PHASES
    assert TurnPhase.NEGOTIATION_WINDOW in MANAGEMENT_PHASES
    assert {TurnPhase.PAYMENT_RESOLUTION, TurnPhase.BANKRUPTCY_RESOLUTION}.issubset(
        LIQUIDATION_PHASES
    )
    assert ACTION_TIMING_WINDOWS["BUY_HOUSE"] == MANAGEMENT_PHASES | LIQUIDATION_PHASES
    assert ACTION_TIMING_WINDOWS["START_AUCTION"] == frozenset(
        {TurnPhase.START_TURN, TurnPhase.PURCHASE_OR_AUCTION}
    )


def test_placeholder_deal_and_trade_actions_are_only_management_or_negotiation_timed() -> None:
    action_types = DEAL_PROPOSAL_ACTION_TYPES | TRADE_RESPONSE_ACTION_TYPES
    legal_phases = {
        TurnPhase.START_TURN,
        TurnPhase.PRE_ROLL_MANAGEMENT,
        TurnPhase.POST_ROLL_MANAGEMENT,
        TurnPhase.NEGOTIATION_WINDOW,
    }
    illegal_phases = {
        TurnPhase.MOVEMENT_RESOLUTION,
        TurnPhase.SPACE_RESOLUTION,
        TurnPhase.PURCHASE_OR_AUCTION,
        TurnPhase.PAYMENT_RESOLUTION,
        TurnPhase.JAIL_RESOLUTION,
        TurnPhase.END_TURN,
        TurnPhase.BANKRUPTCY_RESOLUTION,
        TurnPhase.GAME_OVER,
    }

    for action_type in action_types:
        for phase in legal_phases:
            assert is_action_type_allowed_in_phase(action_type, phase)
        for phase in illegal_phases:
            assert not is_action_type_allowed_in_phase(action_type, phase)


def test_auction_timing_requires_purchase_window_and_active_auction_for_bids() -> None:
    state = _state_in_phase(_set_position(_initial_state(), "player-1", 1), TurnPhase.START_TURN)
    purchase_state = _state_in_phase(state, TurnPhase.PURCHASE_OR_AUCTION)
    space_state = _state_in_phase(state, TurnPhase.SPACE_RESOLUTION)

    assert is_action_allowed_now(state, "START_AUCTION")
    assert is_action_allowed_now(purchase_state, "START_AUCTION")
    assert not is_action_allowed_now(space_state, "START_AUCTION")

    assert not is_action_allowed_now(state, "BID_AUCTION")
    assert timing_issue_for_action(state, "BID_AUCTION") is not None

    active_auction_state = _start_auction(state, "property_mediterranean_avenue")
    assert is_action_allowed_now(active_auction_state, "BID_AUCTION")
    assert is_action_allowed_now(active_auction_state, "PASS_AUCTION")


def test_management_actions_are_filtered_outside_management_and_liquidation_windows() -> None:
    management_state = _management_state(TurnPhase.POST_ROLL_MANAGEMENT)
    space_state = _management_state(TurnPhase.SPACE_RESOLUTION)
    payment_state = _management_state(TurnPhase.PAYMENT_RESOLUTION)

    assert "BUY_HOUSE" in _types(list_legal_actions(management_state, "player-1"))
    assert "BUY_HOUSE" not in _types(list_legal_actions(space_state, "player-1"))
    assert "MORTGAGE_PROPERTY" in _types(list_legal_actions(payment_state, "player-1"))

    with pytest.raises(ActionValidationError) as exc_info:
        validate_action(
            space_state,
            _action(space_state, "player-1", "BUY_HOUSE", {"property_id": "property_vermont_avenue"}),
        )
    assert _issue_codes(exc_info.value) == {"mistimed_action"}


def test_jail_actions_are_limited_to_jail_timing_with_start_turn_compatibility() -> None:
    state = _set_jail(_initial_state(), "player-1", True)
    state = _set_jail_cards(state, "player-1", ("card_community_get_out_of_jail",))
    start_state = _state_in_phase(state, TurnPhase.START_TURN)
    jail_state = _state_in_phase(state, TurnPhase.JAIL_RESOLUTION)
    post_roll_state = _state_in_phase(state, TurnPhase.POST_ROLL_MANAGEMENT)

    assert {"PAY_JAIL_FINE", "USE_GET_OUT_OF_JAIL_CARD"}.issubset(
        _types(list_legal_actions(start_state, "player-1"))
    )
    assert {"PAY_JAIL_FINE", "USE_GET_OUT_OF_JAIL_CARD"}.issubset(
        _types(list_legal_actions(jail_state, "player-1"))
    )
    assert "PAY_JAIL_FINE" not in _types(list_legal_actions(post_roll_state, "player-1"))

    with pytest.raises(ActionValidationError) as exc_info:
        validate_action(post_roll_state, _action(post_roll_state, "player-1", "PAY_JAIL_FINE"))
    assert _issue_codes(exc_info.value) == {"mistimed_action"}


def test_bankruptcy_is_broadly_timed_but_rejected_at_game_over() -> None:
    state = _initial_state()

    for phase in TurnPhase:
        phase_state = _state_in_phase(state, phase)
        allowed = phase is not TurnPhase.GAME_OVER
        assert is_action_allowed_now(phase_state, "DECLARE_BANKRUPTCY") is allowed

    game_over_state = _state_in_phase(state, TurnPhase.GAME_OVER)
    assert "DECLARE_BANKRUPTCY" not in _types(list_legal_actions(game_over_state, "player-1"))
    with pytest.raises(ActionValidationError) as exc_info:
        validate_action(game_over_state, _action(game_over_state, "player-1", "DECLARE_BANKRUPTCY"))
    assert _issue_codes(exc_info.value) == {"mistimed_action"}


def test_validate_action_rejects_supported_purchase_action_outside_timing_window() -> None:
    state = _state_in_phase(_set_position(_initial_state(), "player-1", 1), TurnPhase.SPACE_RESOLUTION)

    assert "BUY_PROPERTY" not in _types(list_legal_actions(state, "player-1"))
    with pytest.raises(ActionValidationError) as exc_info:
        validate_action(
            state,
            _action(state, "player-1", "BUY_PROPERTY", {"property_id": "property_mediterranean_avenue"}),
        )
    assert _issue_codes(exc_info.value) == {"mistimed_action"}
