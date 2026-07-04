from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.rules.atomic import AtomicResolutionKind
from app.rules.phases import TurnPhase
from app.rules.static_data import load_classic_monopoly_data


PlayerKind: TypeAlias = Literal["human", "ai"]

INITIAL_PLAYER_CASH = 1500
GAME_STATE_SCHEMA_VERSION = "game-state-v1"


class StateModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")


class PlayerSetup(StateModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    kind: PlayerKind


class PlayerState(StateModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    kind: PlayerKind
    cash: int
    position: int = Field(ge=0, le=39)
    in_jail: bool
    jail_turns: int = Field(ge=0)
    get_out_of_jail_card_ids: tuple[str, ...]
    is_bankrupt: bool

    @model_validator(mode="after")
    def validate_jail_state(self) -> Self:
        if not self.in_jail and self.jail_turns != 0:
            raise ValueError("players outside jail must have zero jail turns")
        return self


class PropertyOwnershipState(StateModel):
    property_id: str = Field(min_length=1)
    owner_id: str | None = None
    mortgaged: bool = False
    houses: int = Field(default=0, ge=0, le=4)
    hotel: bool = False

    @model_validator(mode="after")
    def validate_improvement_state(self) -> Self:
        if self.hotel and self.houses != 0:
            raise ValueError("property cannot track houses and a hotel at the same time")
        return self


class DeckState(StateModel):
    draw_pile: tuple[str, ...]
    discard_pile: tuple[str, ...]

    @model_validator(mode="after")
    def validate_card_ids_are_unique_within_deck_state(self) -> Self:
        card_ids = (*self.draw_pile, *self.discard_pile)
        if len(set(card_ids)) != len(card_ids):
            raise ValueError("card ids cannot appear more than once in a deck state")
        return self


class DeckCollectionState(StateModel):
    chance: DeckState
    community_chest: DeckState


class RngState(StateModel):
    seed: str
    dice_roll_count: int = Field(ge=0)
    chance_draw_count: int = Field(ge=0)
    community_chest_draw_count: int = Field(ge=0)
    chance_shuffle_count: int = Field(ge=0)
    community_chest_shuffle_count: int = Field(ge=0)


class BankInventoryState(StateModel):
    houses: int = Field(ge=0, le=32)
    hotels: int = Field(ge=0, le=12)


class TurnState(StateModel):
    turn_number: int = Field(ge=1)
    current_player_index: int = Field(ge=0)
    current_player_id: str = Field(min_length=1)
    phase: TurnPhase
    consecutive_doubles: int = Field(ge=0)


class ActivePaymentState(StateModel):
    debtor_id: str = Field(min_length=1)
    creditor_id: str | None = Field(default=None, min_length=1)
    amount_owed: int = Field(gt=0)
    amount_paid: int = Field(ge=0)
    reason: str = Field(min_length=1)
    negotiation_allowed: bool

    @model_validator(mode="after")
    def validate_payment_state(self) -> Self:
        if self.creditor_id is not None and self.creditor_id == self.debtor_id:
            raise ValueError("active payment creditor cannot match debtor")
        if self.amount_paid > self.amount_owed:
            raise ValueError("active payment amount_paid cannot exceed amount_owed")
        return self


class ActiveAuctionState(StateModel):
    property_id: str = Field(min_length=1)
    high_bidder_id: str | None = None
    high_bid_amount: int | None = Field(default=None, gt=0)
    passed_player_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_high_bid_shape(self) -> Self:
        if (self.high_bidder_id is None) != (self.high_bid_amount is None):
            raise ValueError("active auction high bidder and high bid amount must be set together")
        if len(set(self.passed_player_ids)) != len(self.passed_player_ids):
            raise ValueError("active auction passed player ids must be unique")
        return self


class ActiveNegotiationState(StateModel):
    pass


class ActiveBankruptcyState(StateModel):
    pass


class ActiveAtomicResolutionState(StateModel):
    kind: AtomicResolutionKind
    actor_id: str | None = Field(default=None, min_length=1)


class GameState(StateModel):
    schema_version: Literal["game-state-v1"] = GAME_STATE_SCHEMA_VERSION
    game_id: str = Field(min_length=1)
    ruleset_version: str = Field(min_length=1)
    seed: str
    rng: RngState
    players: tuple[PlayerState, ...]
    property_ownership: tuple[PropertyOwnershipState, ...]
    decks: DeckCollectionState
    bank_inventory: BankInventoryState
    turn: TurnState
    active_payment: ActivePaymentState | None = None
    active_auction: ActiveAuctionState | None = None
    active_negotiation: ActiveNegotiationState | None = None
    active_bankruptcy: ActiveBankruptcyState | None = None
    active_atomic_resolution: ActiveAtomicResolutionState | None = None
    event_sequence: int = Field(default=0, ge=0)
    applied_event_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_game_state_shape(self) -> Self:
        _validate_player_count_and_ids(self.players)

        if self.rng.seed != self.seed:
            raise ValueError("rng seed must match game seed")

        if self.turn.current_player_index >= len(self.players):
            raise ValueError("current player index must reference an existing player")
        current_player = self.players[self.turn.current_player_index]
        if self.turn.current_player_id != current_player.id:
            raise ValueError("current player id must match current player index")

        property_ids = [ownership.property_id for ownership in self.property_ownership]
        if len(property_ids) != 28:
            raise ValueError("classic game state must track exactly 28 purchasable properties")
        if len(set(property_ids)) != len(property_ids):
            raise ValueError("property ownership entries must have unique property ids")

        owner_ids = {player.id for player in self.players}
        for ownership in self.property_ownership:
            if ownership.owner_id is not None and ownership.owner_id not in owner_ids:
                raise ValueError(f"{ownership.property_id} owner must reference an existing player")

        if self.active_auction is not None:
            property_ids_set = set(property_ids)
            if self.active_auction.property_id not in property_ids_set:
                raise ValueError("active auction property must reference an existing property")
            if (
                self.active_auction.high_bidder_id is not None
                and self.active_auction.high_bidder_id not in owner_ids
            ):
                raise ValueError("active auction high bidder must reference an existing player")
            unknown_passed_ids = set(self.active_auction.passed_player_ids) - owner_ids
            if unknown_passed_ids:
                raise ValueError("active auction passed player ids must reference existing players")

        if (
            self.active_atomic_resolution is not None
            and self.active_atomic_resolution.actor_id is not None
            and self.active_atomic_resolution.actor_id not in owner_ids
        ):
            raise ValueError("active atomic resolution actor must reference an existing player")

        if self.active_payment is not None:
            if self.active_payment.debtor_id not in owner_ids:
                raise ValueError("active payment debtor must reference an existing player")
            if (
                self.active_payment.creditor_id is not None
                and self.active_payment.creditor_id not in owner_ids
            ):
                raise ValueError("active payment creditor must reference an existing player")

        if len(self.applied_event_ids) != self.event_sequence:
            raise ValueError("applied event id count must match event sequence")
        if len(set(self.applied_event_ids)) != len(self.applied_event_ids):
            raise ValueError("applied event ids must be unique")
        if any(not event_id for event_id in self.applied_event_ids):
            raise ValueError("applied event ids cannot be empty")

        return self

    def canonical_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))

    def state_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


def create_initial_game_state(
    seed: str,
    players: Sequence[PlayerSetup],
    game_id: str,
) -> GameState:
    player_setups = tuple(PlayerSetup.model_validate(player) for player in players)
    _validate_player_count_and_ids(player_setups)

    data = load_classic_monopoly_data()
    player_states = tuple(
        PlayerState(
            id=player.id,
            name=player.name,
            kind=player.kind,
            cash=INITIAL_PLAYER_CASH,
            position=0,
            in_jail=False,
            jail_turns=0,
            get_out_of_jail_card_ids=(),
            is_bankrupt=False,
        )
        for player in player_setups
    )

    property_ownership = tuple(
        PropertyOwnershipState(property_id=property_data.id)
        for property_data in data.properties
    )
    decks = DeckCollectionState(
        chance=DeckState(
            draw_pile=tuple(card.id for card in data.decks.chance),
            discard_pile=(),
        ),
        community_chest=DeckState(
            draw_pile=tuple(card.id for card in data.decks.community_chest),
            discard_pile=(),
        ),
    )

    return GameState(
        schema_version=GAME_STATE_SCHEMA_VERSION,
        game_id=game_id,
        ruleset_version=data.version,
        seed=seed,
        rng=RngState(
            seed=seed,
            dice_roll_count=0,
            chance_draw_count=0,
            community_chest_draw_count=0,
            chance_shuffle_count=0,
            community_chest_shuffle_count=0,
        ),
        players=player_states,
        property_ownership=property_ownership,
        decks=decks,
        bank_inventory=BankInventoryState(
            houses=data.bank_inventory.houses,
            hotels=data.bank_inventory.hotels,
        ),
        turn=TurnState(
            turn_number=1,
            current_player_index=0,
            current_player_id=player_states[0].id,
            phase=TurnPhase.START_TURN,
            consecutive_doubles=0,
        ),
        active_payment=None,
        active_auction=None,
        active_negotiation=None,
        active_bankruptcy=None,
        active_atomic_resolution=None,
    )


def _validate_player_count_and_ids(players: Sequence[PlayerSetup | PlayerState]) -> None:
    if not 2 <= len(players) <= 5:
        raise ValueError("game state requires 2 to 5 players")

    player_ids = [player.id for player in players]
    if len(set(player_ids)) != len(player_ids):
        raise ValueError("game state cannot contain duplicate player ids")

    for player in players:
        if player.kind not in ("human", "ai"):
            raise ValueError(f"unsupported player kind {player.kind}")
