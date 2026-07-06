from __future__ import annotations

# pyright: reportExplicitAny=false, reportImplicitRelativeImport=false
# pyright: reportMissingImports=false, reportUnknownArgumentType=false
# pyright: reportUnknownMemberType=false, reportUnknownParameterType=false
# pyright: reportUnknownVariableType=false, reportUntypedFunctionDecorator=false

import json
import string
from collections.abc import Mapping, Sequence
from typing import Any, cast

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from app.rules.atomic import AtomicResolutionKind
from app.rules.events import (
    ActiveAuctionSetPayload,
    ActiveAtomicResolutionSetPayload,
    ActiveBankruptcySetPayload,
    ActiveNegotiationSetPayload,
    ActivePaymentSetPayload,
    BankInventorySetPayload,
    CardDrawnPayload,
    DeckShuffledPayload,
    DeckStateSetPayload,
    DiceRolledPayload,
    GameEvent,
    PlayerBankruptcySetPayload,
    PlayerCashDeltaPayload,
    PlayerJailCardsSetPayload,
    PlayerJailSetPayload,
    PlayerPositionSetPayload,
    PropertyImprovementsSetPayload,
    PropertyMortgageSetPayload,
    PropertyOwnerSetPayload,
    TurnStateSetPayload,
    PAYLOAD_MODEL_BY_EVENT_TYPE,
)
from app.rules.phases import VALID_PHASE_TRANSITIONS, TurnPhase
from app.rules.reducer import apply_event, replay_events
from app.rules.simulation import (
    INVARIANT_CHECKS,
    SimulationFailure,
    check_invariants,
    run_random_legal_action_stress,
)
from app.rules.state import GameState, PlayerSetup, create_initial_game_state
from app.rules.static_data import load_classic_monopoly_data


STAGE_10_3_SETTINGS = settings(
    max_examples=18,
    derandomize=True,
    database=None,
    deadline=None,
    suppress_health_check=(HealthCheck.too_slow,),
)

SEED_ALPHABET = string.ascii_letters + string.digits + "-_"
SEEDS = st.text(alphabet=SEED_ALPHABET, min_size=1, max_size=28)

REQUIRED_INVARIANT_CHECKS = frozenset(
    {
        "cash_ledger_reconciliation",
        "ownership_uniqueness",
        "bank_inventory_non_negative",
        "house_hotel_scarcity",
        "phase_validity",
        "replay_determinism",
        "random_legal_action_simulation",
        "reproducible_failure_context",
    }
)


def _player_setups(player_count: int = 3) -> tuple[PlayerSetup, ...]:
    return tuple(
        PlayerSetup(
            id=f"player-{index}",
            name=f"Player {index}",
            kind="human" if index == 1 else "ai",
        )
        for index in range(1, player_count + 1)
    )


def _initial_state(seed: str = "stage-10-3-seed", player_count: int = 3) -> GameState:
    return create_initial_game_state(
        seed=seed,
        players=_player_setups(player_count),
        game_id=f"stage-10-3-{seed}-{player_count}",
    )


def _event(state: GameState, event_type: str, payload: object) -> GameEvent:
    return GameEvent(
        event_id=f"stage-10-3-{event_type.lower()}-{state.event_sequence + 1}",
        sequence=state.event_sequence + 1,
        type=cast(Any, event_type),
        payload=cast(Any, payload),
    )


def _apply(state: GameState, event_type: str, payload: object) -> GameState:
    return apply_event(state, _event(state, event_type, payload))


def _cash_by_player(state: GameState) -> dict[str, int]:
    return {player.id: player.cash for player in state.players}


def _payload_for_accepted_event_type(
    state: GameState,
    event_type: str,
    *,
    cash_delta: int,
    position: int,
    houses: int,
    hotels: int,
    jail_turns: int,
    use_hotel: bool,
    owner_id: str | None,
) -> object:
    data = load_classic_monopoly_data()
    first_property = data.properties[0].id
    chance_cards = tuple(card.id for card in data.decks.chance)
    community_cards = tuple(card.id for card in data.decks.community_chest)

    if event_type == "DICE_ROLLED":
        return DiceRolledPayload(
            player_id="player-1",
            die_1=2,
            die_2=5,
            total=7,
            is_doubles=False,
            roll_counter=state.rng.dice_roll_count + 1,
        )
    if event_type == "DECK_SHUFFLED":
        return DeckShuffledPayload(
            deck="chance",
            draw_pile=tuple(reversed(chance_cards)),
            shuffle_counter=state.rng.chance_shuffle_count + 1,
        )
    if event_type == "CARD_DRAWN":
        return CardDrawnPayload(
            deck="chance",
            card_id=state.decks.chance.draw_pile[0],
            draw_counter=state.rng.chance_draw_count + 1,
        )
    if event_type == "PLAYER_CASH_DELTA":
        return PlayerCashDeltaPayload(player_id="player-1", amount=cash_delta)
    if event_type == "PLAYER_POSITION_SET":
        return PlayerPositionSetPayload(player_id="player-1", position=position)
    if event_type == "PLAYER_JAIL_SET":
        return PlayerJailSetPayload(player_id="player-1", in_jail=True, jail_turns=jail_turns)
    if event_type == "PLAYER_BANKRUPTCY_SET":
        return PlayerBankruptcySetPayload(player_id="player-2", is_bankrupt=True)
    if event_type == "PLAYER_JAIL_CARDS_SET":
        return PlayerJailCardsSetPayload(player_id="player-1", card_ids=(chance_cards[0],))
    if event_type == "PROPERTY_OWNER_SET":
        return PropertyOwnerSetPayload(property_id=first_property, owner_id=owner_id)
    if event_type == "PROPERTY_MORTGAGE_SET":
        return PropertyMortgageSetPayload(property_id=first_property, mortgaged=True)
    if event_type == "PROPERTY_IMPROVEMENTS_SET":
        return PropertyImprovementsSetPayload(
            property_id=first_property,
            houses=0 if use_hotel else min(houses, 4),
            hotel=use_hotel,
        )
    if event_type == "BANK_INVENTORY_SET":
        return BankInventorySetPayload(houses=houses, hotels=hotels)
    if event_type == "DECK_STATE_SET":
        return DeckStateSetPayload(
            deck="community_chest",
            draw_pile=community_cards[:8],
            discard_pile=community_cards[8:],
        )
    if event_type == "TURN_STATE_SET":
        return TurnStateSetPayload(
            turn_number=state.turn.turn_number,
            current_player_index=state.turn.current_player_index,
            current_player_id=state.turn.current_player_id,
            phase=TurnPhase.START_TURN.value,
            consecutive_doubles=0,
        )
    if event_type == "ACTIVE_PAYMENT_SET":
        return ActivePaymentSetPayload(
            active=True,
            debtor_id="player-1",
            creditor_id="player-2",
            amount_owed=125,
            amount_paid=0,
            reason="stage 10.3 generated ledger",
            negotiation_allowed=True,
        )
    if event_type == "ACTIVE_AUCTION_SET":
        return ActiveAuctionSetPayload(active=True, property_id=first_property)
    if event_type == "ACTIVE_NEGOTIATION_SET":
        return ActiveNegotiationSetPayload(active=True)
    if event_type == "ACTIVE_BANKRUPTCY_SET":
        return ActiveBankruptcySetPayload(active=True)
    if event_type == "ACTIVE_ATOMIC_RESOLUTION_SET":
        return ActiveAtomicResolutionSetPayload(
            active=True,
            kind=AtomicResolutionKind.MOVEMENT,
            actor_id="player-1",
        )
    raise AssertionError(f"missing generated payload for {event_type}")


def _cash_ledger_delta(event_type: str, payload: object) -> Mapping[str, int]:
    if event_type != "PLAYER_CASH_DELTA":
        return {}
    cash_payload = cast(PlayerCashDeltaPayload, payload)
    return {cash_payload.player_id: cash_payload.amount}


def _apply_owner_assignments(
    state: GameState,
    owner_slots: Sequence[int],
) -> GameState:
    player_ids = tuple(player.id for player in state.players)
    for property_data, owner_slot in zip(load_classic_monopoly_data().properties, owner_slots, strict=True):
        owner_id = None if owner_slot < 0 else player_ids[owner_slot]
        state = _apply(
            state,
            "PROPERTY_OWNER_SET",
            PropertyOwnerSetPayload(property_id=property_data.id, owner_id=owner_id),
        )
    return state


def _draw_street_improvement_levels(data: st.DataObject) -> tuple[int, ...]:
    street_count = sum(1 for property_data in load_classic_monopoly_data().properties if property_data.kind == "street")
    remaining_houses = 32
    remaining_hotels = 12
    levels: list[int] = []

    for index in range(street_count):
        can_hotel = remaining_hotels > 0
        hotel = data.draw(st.booleans(), label=f"street_{index}_hotel") if can_hotel else False
        if hotel:
            levels.append(5)
            remaining_hotels -= 1
            continue

        houses = data.draw(
            st.integers(min_value=0, max_value=min(4, remaining_houses)),
            label=f"street_{index}_houses",
        )
        levels.append(houses)
        remaining_houses -= houses

    return tuple(levels)


def _street_improvement_levels_for_bank_inventory(houses: int, hotels: int) -> tuple[int, ...]:
    street_count = sum(1 for property_data in load_classic_monopoly_data().properties if property_data.kind == "street")
    houses_to_place = 32 - houses
    hotels_to_place = 12 - hotels
    levels: list[int] = []

    for _ in range(street_count):
        if hotels_to_place > 0:
            levels.append(5)
            hotels_to_place -= 1
            continue

        placed_houses = min(4, houses_to_place)
        levels.append(placed_houses)
        houses_to_place -= placed_houses

    assert houses_to_place == 0
    assert hotels_to_place == 0
    return tuple(levels)


def _apply_street_improvements_with_matching_bank_inventory(
    state: GameState,
    levels: Sequence[int],
) -> GameState:
    data = load_classic_monopoly_data()
    street_properties = tuple(property_data for property_data in data.properties if property_data.kind == "street")
    houses_used = sum(level for level in levels if level < 5)
    hotels_used = sum(1 for level in levels if level == 5)

    for property_data, level in zip(street_properties, levels, strict=True):
        state = _apply(
            state,
            "PROPERTY_OWNER_SET",
            PropertyOwnerSetPayload(property_id=property_data.id, owner_id="player-1"),
        )
        state = _apply(
            state,
            "PROPERTY_IMPROVEMENTS_SET",
            PropertyImprovementsSetPayload(
                property_id=property_data.id,
                houses=0 if level == 5 else level,
                hotel=level == 5,
            ),
        )

    return _apply(
        state,
        "BANK_INVENTORY_SET",
        BankInventorySetPayload(houses=32 - houses_used, hotels=12 - hotels_used),
    )


def _apply_generated_phase_walk(state: GameState, data: st.DataObject) -> GameState:
    step_count = data.draw(st.integers(min_value=1, max_value=8), label="phase_step_count")
    for step_index in range(step_count):
        current_phase = TurnPhase(state.turn.phase)
        next_phases = (current_phase, *VALID_PHASE_TRANSITIONS[current_phase])
        next_phase = data.draw(st.sampled_from(next_phases), label=f"phase_step_{step_index}")
        state = _apply(
            state,
            "TURN_STATE_SET",
            TurnStateSetPayload(
                turn_number=state.turn.turn_number,
                current_player_index=state.turn.current_player_index,
                current_player_id=state.turn.current_player_id,
                phase=next_phase.value,
                consecutive_doubles=state.turn.consecutive_doubles,
            ),
        )
    return state


def _failure_context(failure: SimulationFailure | None) -> str:
    if failure is None:
        return ""
    return json.dumps(dict(failure.reproducible_context), sort_keys=True, default=str)


@STAGE_10_3_SETTINGS
@given(
    cash_delta=st.integers(min_value=-500, max_value=500),
    position=st.integers(min_value=0, max_value=39),
    houses=st.integers(min_value=0, max_value=32),
    hotels=st.integers(min_value=0, max_value=12),
    jail_turns=st.integers(min_value=0, max_value=3),
    use_hotel=st.booleans(),
    owner_slot=st.integers(min_value=-1, max_value=2),
)
def test_stage_10_3_generated_cash_ledger_reconciles_every_accepted_event_type(
    cash_delta: int,
    position: int,
    houses: int,
    hotels: int,
    jail_turns: int,
    use_hotel: bool,
    owner_slot: int,
) -> None:
    accepted_event_types = tuple(PAYLOAD_MODEL_BY_EVENT_TYPE)
    assert "PLAYER_CASH_DELTA" in accepted_event_types

    for event_type in accepted_event_types:
        state = _initial_state(seed=f"stage-10-3-ledger-{event_type}")
        owner_id = None if owner_slot < 0 else state.players[owner_slot].id
        before_cash = _cash_by_player(state)
        payload = _payload_for_accepted_event_type(
            state,
            event_type,
            cash_delta=cash_delta,
            position=position,
            houses=houses,
            hotels=hotels,
            jail_turns=jail_turns,
            use_hotel=use_hotel,
            owner_id=owner_id,
        )

        after = _apply(state, event_type, payload)

        expected_cash = dict(before_cash)
        for player_id, delta in _cash_ledger_delta(event_type, payload).items():
            expected_cash[player_id] += delta
        assert _cash_by_player(after) == expected_cash, event_type

    assert set(accepted_event_types) == set(PAYLOAD_MODEL_BY_EVENT_TYPE)


@STAGE_10_3_SETTINGS
@given(
    data=st.data(),
    seed=SEEDS,
    player_count=st.integers(min_value=2, max_value=5),
    houses=st.integers(min_value=0, max_value=32),
    hotels=st.integers(min_value=0, max_value=12),
)
def test_stage_10_3_generated_ownership_is_unique_and_bank_inventory_never_negative(
    data: st.DataObject,
    seed: str,
    player_count: int,
    houses: int,
    hotels: int,
) -> None:
    state = _initial_state(seed=seed, player_count=player_count)
    owner_slots = data.draw(
        st.lists(
            st.integers(min_value=-1, max_value=player_count - 1),
            min_size=len(state.property_ownership),
            max_size=len(state.property_ownership),
        ),
        label="owner_slots",
    )

    state = _apply_owner_assignments(state, owner_slots)
    state = _apply_street_improvements_with_matching_bank_inventory(
        state,
        _street_improvement_levels_for_bank_inventory(houses, hotels),
    )

    check_invariants(state)
    property_ids = tuple(ownership.property_id for ownership in state.property_ownership)
    assert len(property_ids) == len(set(property_ids))
    assert all(ownership.owner_id in {None, *(player.id for player in state.players)} for ownership in state.property_ownership)
    assert state.bank_inventory.houses >= 0
    assert state.bank_inventory.hotels >= 0


@STAGE_10_3_SETTINGS
@given(data=st.data(), seed=SEEDS)
def test_stage_10_3_generated_house_hotel_scarcity_and_phase_validity_hold(
    data: st.DataObject,
    seed: str,
) -> None:
    state = _initial_state(seed=seed)
    improvement_levels = _draw_street_improvement_levels(data)
    state = _apply_street_improvements_with_matching_bank_inventory(state, improvement_levels)
    state = _apply_generated_phase_walk(state, data)

    check_invariants(state)
    houses_on_board = sum(ownership.houses for ownership in state.property_ownership)
    hotels_on_board = sum(1 for ownership in state.property_ownership if ownership.hotel)
    assert state.bank_inventory.houses + houses_on_board == 32
    assert state.bank_inventory.hotels + hotels_on_board == 12
    assert TurnPhase(state.turn.phase) in TurnPhase


@STAGE_10_3_SETTINGS
@given(seed=SEEDS, cash_delta=st.integers(min_value=-300, max_value=300), owner_slot=st.integers(min_value=-1, max_value=2))
def test_stage_10_3_replay_determinism_reports_reproducible_seed_on_failure(
    seed: str,
    cash_delta: int,
    owner_slot: int,
) -> None:
    players = _player_setups(3)
    owner_id = None if owner_slot < 0 else players[owner_slot].id
    property_id = load_classic_monopoly_data().properties[0].id
    events = (
        GameEvent(
            event_id="stage-10-3-replay-1",
            sequence=1,
            type="PLAYER_CASH_DELTA",
            payload=PlayerCashDeltaPayload(player_id="player-1", amount=cash_delta),
        ),
        GameEvent(
            event_id="stage-10-3-replay-2",
            sequence=2,
            type="PROPERTY_OWNER_SET",
            payload=PropertyOwnerSetPayload(property_id=property_id, owner_id=owner_id),
        ),
    )

    first = replay_events(seed=seed, players=players, game_id=f"stage-10-3-replay-{seed}", events=events)
    second = replay_events(seed=seed, players=players, game_id=f"stage-10-3-replay-{seed}", events=events)

    assert first == second
    assert first.state_hash() == second.state_hash()

    broken_state = first.model_copy(update={"property_ownership": first.property_ownership[:-1]})
    with pytest.raises(SimulationFailure) as exc_info:
        check_invariants(broken_state, action={"actor_id": "player-1", "type": "ROLL_DICE"})

    context = exc_info.value.reproducible_context
    assert context["seed"] == seed
    assert context["state_hash"] == broken_state.state_hash()
    assert context["action_type"] == "ROLL_DICE"
    assert context["invariant_violations"]


@STAGE_10_3_SETTINGS
@given(
    seed=SEEDS,
    player_count=st.integers(min_value=2, max_value=5),
    action_limit=st.integers(min_value=90, max_value=140),
)
def test_stage_10_3_random_legal_action_sequences_survive_long_runs(
    seed: str,
    player_count: int,
    action_limit: int,
) -> None:
    first = run_random_legal_action_stress(
        seed=seed,
        player_count=player_count,
        action_limit=action_limit,
    )
    second = run_random_legal_action_stress(
        seed=seed,
        player_count=player_count,
        action_limit=action_limit,
    )

    assert first.failure is None, _failure_context(first.failure)
    assert first.actions_executed == action_limit
    assert first.invariant_checks == action_limit + 1
    assert first.final_state.state_hash() == second.final_state.state_hash()
    assert [entry.model_dump() for entry in first.action_log] == [
        entry.model_dump() for entry in second.action_log
    ]


def test_stage_10_3_rules_engine_invariants_manifest_lists_all_required_checks() -> None:
    assert REQUIRED_INVARIANT_CHECKS.issubset(set(INVARIANT_CHECKS))
    for check_name in REQUIRED_INVARIANT_CHECKS:
        description = INVARIANT_CHECKS[check_name]
        assert isinstance(description, str)
        assert description
