from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator


GameEventType: TypeAlias = Literal[
    "DICE_ROLLED",
    "DECK_SHUFFLED",
    "CARD_DRAWN",
    "PLAYER_CASH_DELTA",
    "PLAYER_POSITION_SET",
    "PLAYER_JAIL_SET",
    "PLAYER_BANKRUPTCY_SET",
    "PLAYER_JAIL_CARDS_SET",
    "PROPERTY_OWNER_SET",
    "PROPERTY_MORTGAGE_SET",
    "PROPERTY_IMPROVEMENTS_SET",
    "BANK_INVENTORY_SET",
    "DECK_STATE_SET",
    "TURN_STATE_SET",
    "ACTIVE_PAYMENT_SET",
    "ACTIVE_AUCTION_SET",
    "ACTIVE_NEGOTIATION_SET",
    "ACTIVE_BANKRUPTCY_SET",
]
DeckEventName: TypeAlias = Literal["chance", "community_chest"]


class InvalidEventError(ValueError):
    """Raised when an event cannot be accepted by the deterministic reducer."""


class EventModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")


class PlayerCashDeltaPayload(EventModel):
    player_id: str = Field(min_length=1)
    amount: int


class PlayerPositionSetPayload(EventModel):
    player_id: str = Field(min_length=1)
    position: int = Field(ge=0, le=39)


class PlayerJailSetPayload(EventModel):
    player_id: str = Field(min_length=1)
    in_jail: bool
    jail_turns: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_jail_turns(self) -> Self:
        if not self.in_jail and self.jail_turns != 0:
            raise ValueError("players outside jail must have zero jail turns")
        return self


class PlayerBankruptcySetPayload(EventModel):
    player_id: str = Field(min_length=1)
    is_bankrupt: bool


class PlayerJailCardsSetPayload(EventModel):
    player_id: str = Field(min_length=1)
    card_ids: tuple[str, ...]

    @model_validator(mode="after")
    def validate_card_ids_are_unique(self) -> Self:
        if len(set(self.card_ids)) != len(self.card_ids):
            raise ValueError("card ids cannot appear more than once in player jail cards")
        return self


class PropertyOwnerSetPayload(EventModel):
    property_id: str = Field(min_length=1)
    owner_id: str | None


class PropertyMortgageSetPayload(EventModel):
    property_id: str = Field(min_length=1)
    mortgaged: bool


class PropertyImprovementsSetPayload(EventModel):
    property_id: str = Field(min_length=1)
    houses: int = Field(ge=0, le=4)
    hotel: bool

    @model_validator(mode="after")
    def validate_improvements(self) -> Self:
        if self.hotel and self.houses != 0:
            raise ValueError("property cannot track houses and a hotel at the same time")
        return self


class BankInventorySetPayload(EventModel):
    houses: int = Field(ge=0, le=32)
    hotels: int = Field(ge=0, le=12)


class DeckStateSetPayload(EventModel):
    deck: DeckEventName
    draw_pile: tuple[str, ...]
    discard_pile: tuple[str, ...]

    @model_validator(mode="after")
    def validate_card_ids_are_unique_within_deck_state(self) -> Self:
        card_ids = (*self.draw_pile, *self.discard_pile)
        if len(set(card_ids)) != len(card_ids):
            raise ValueError("card ids cannot appear more than once in a deck state")
        return self


class DiceRolledPayload(EventModel):
    player_id: str = Field(min_length=1)
    die_1: int = Field(ge=1, le=6)
    die_2: int = Field(ge=1, le=6)
    total: int = Field(ge=2, le=12)
    is_doubles: bool
    roll_counter: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_dice_outcome(self) -> Self:
        if self.total != self.die_1 + self.die_2:
            raise ValueError("dice total must equal die_1 plus die_2")
        if self.is_doubles != (self.die_1 == self.die_2):
            raise ValueError("dice doubles flag must match dice values")
        return self


class DeckShuffledPayload(EventModel):
    deck: DeckEventName
    draw_pile: tuple[str, ...]
    shuffle_counter: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_card_ids_are_unique(self) -> Self:
        if len(set(self.draw_pile)) != len(self.draw_pile):
            raise ValueError("card ids cannot appear more than once in a shuffled deck")
        return self


class CardDrawnPayload(EventModel):
    deck: DeckEventName
    card_id: str = Field(min_length=1)
    draw_counter: int = Field(ge=1)


class TurnStateSetPayload(EventModel):
    turn_number: int = Field(ge=1)
    current_player_index: int = Field(ge=0)
    current_player_id: str = Field(min_length=1)
    phase: str = Field(min_length=1)
    consecutive_doubles: int = Field(ge=0)


class ActivePaymentSetPayload(EventModel):
    active: bool


class ActiveAuctionSetPayload(EventModel):
    active: bool
    property_id: str | None = None
    high_bidder_id: str | None = None
    high_bid_amount: int | None = Field(default=None, gt=0)
    passed_player_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_auction_shape(self) -> Self:
        if not self.active:
            if (
                self.property_id is not None
                or self.high_bidder_id is not None
                or self.high_bid_amount is not None
                or self.passed_player_ids
            ):
                raise ValueError("inactive auction payload cannot include auction details")
            return self

        if self.property_id is None:
            raise ValueError("active auction payload must include property_id")
        if (self.high_bidder_id is None) != (self.high_bid_amount is None):
            raise ValueError("active auction high bidder and high bid amount must be set together")
        if len(set(self.passed_player_ids)) != len(self.passed_player_ids):
            raise ValueError("active auction passed player ids must be unique")
        return self


class ActiveNegotiationSetPayload(EventModel):
    active: bool


class ActiveBankruptcySetPayload(EventModel):
    active: bool


GameEventPayload: TypeAlias = (
    DiceRolledPayload
    | DeckShuffledPayload
    | CardDrawnPayload
    | PlayerCashDeltaPayload
    | PlayerPositionSetPayload
    | PlayerJailSetPayload
    | PlayerBankruptcySetPayload
    | PlayerJailCardsSetPayload
    | PropertyOwnerSetPayload
    | PropertyMortgageSetPayload
    | PropertyImprovementsSetPayload
    | BankInventorySetPayload
    | DeckStateSetPayload
    | TurnStateSetPayload
    | ActivePaymentSetPayload
    | ActiveAuctionSetPayload
    | ActiveNegotiationSetPayload
    | ActiveBankruptcySetPayload
)

PAYLOAD_MODEL_BY_EVENT_TYPE: dict[str, type[EventModel]] = {
    "DICE_ROLLED": DiceRolledPayload,
    "DECK_SHUFFLED": DeckShuffledPayload,
    "CARD_DRAWN": CardDrawnPayload,
    "PLAYER_CASH_DELTA": PlayerCashDeltaPayload,
    "PLAYER_POSITION_SET": PlayerPositionSetPayload,
    "PLAYER_JAIL_SET": PlayerJailSetPayload,
    "PLAYER_BANKRUPTCY_SET": PlayerBankruptcySetPayload,
    "PLAYER_JAIL_CARDS_SET": PlayerJailCardsSetPayload,
    "PROPERTY_OWNER_SET": PropertyOwnerSetPayload,
    "PROPERTY_MORTGAGE_SET": PropertyMortgageSetPayload,
    "PROPERTY_IMPROVEMENTS_SET": PropertyImprovementsSetPayload,
    "BANK_INVENTORY_SET": BankInventorySetPayload,
    "DECK_STATE_SET": DeckStateSetPayload,
    "TURN_STATE_SET": TurnStateSetPayload,
    "ACTIVE_PAYMENT_SET": ActivePaymentSetPayload,
    "ACTIVE_AUCTION_SET": ActiveAuctionSetPayload,
    "ACTIVE_NEGOTIATION_SET": ActiveNegotiationSetPayload,
    "ACTIVE_BANKRUPTCY_SET": ActiveBankruptcySetPayload,
}


class GameEvent(EventModel):
    event_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    type: GameEventType
    payload: GameEventPayload

    @model_validator(mode="before")
    @classmethod
    def validate_payload_for_event_type(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data

        event_type = data.get("type")
        if not isinstance(event_type, str):
            return data

        payload = data.get("payload")
        payload_model = PAYLOAD_MODEL_BY_EVENT_TYPE.get(event_type)
        if payload_model is None or isinstance(payload, payload_model):
            return data

        mutable_data = dict(data)
        mutable_data["payload"] = payload_model.model_validate(payload)
        return mutable_data

    @model_validator(mode="after")
    def validate_payload_type_matches_event_type(self) -> Self:
        payload_model = PAYLOAD_MODEL_BY_EVENT_TYPE.get(self.type)
        if payload_model is None or not isinstance(self.payload, payload_model):
            raise ValueError(f"{self.type} payload does not match event type")
        return self


def payload_model_for_event_type(event_type: str) -> type[EventModel] | None:
    return PAYLOAD_MODEL_BY_EVENT_TYPE.get(event_type)


__all__ = [
    "ActiveAuctionSetPayload",
    "ActiveBankruptcySetPayload",
    "ActiveNegotiationSetPayload",
    "ActivePaymentSetPayload",
    "BankInventorySetPayload",
    "CardDrawnPayload",
    "DeckEventName",
    "DeckShuffledPayload",
    "DeckStateSetPayload",
    "DiceRolledPayload",
    "EventModel",
    "GameEvent",
    "GameEventPayload",
    "GameEventType",
    "InvalidEventError",
    "PlayerBankruptcySetPayload",
    "PlayerCashDeltaPayload",
    "PlayerJailCardsSetPayload",
    "PlayerJailSetPayload",
    "PlayerPositionSetPayload",
    "PropertyImprovementsSetPayload",
    "PropertyMortgageSetPayload",
    "PropertyOwnerSetPayload",
    "TurnStateSetPayload",
    "payload_model_for_event_type",
]
