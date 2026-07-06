from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.paths import resolve_content_rules_dir


DeckName: TypeAlias = Literal["chance", "community_chest"]
PropertyKind: TypeAlias = Literal["street", "railroad", "utility"]
BoardSpaceType: TypeAlias = Literal[
    "go",
    "street",
    "community_chest",
    "tax",
    "railroad",
    "chance",
    "jail",
    "utility",
    "free_parking",
    "go_to_jail",
]
EffectValue: TypeAlias = str | int | bool

RULES_DATA_PATH = resolve_content_rules_dir(Path(__file__)) / "classic_monopoly.json"


class StaticDataModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Currency(StaticDataModel):
    code: str
    symbol: str
    name: str


class BoardSpace(StaticDataModel):
    id: str
    position: int = Field(ge=0, le=39)
    name: str
    type: BoardSpaceType
    property_id: str | None = None
    deck: DeckName | None = None
    amount: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_space_shape(self) -> Self:
        purchasable_types = {"street", "railroad", "utility"}
        if self.type in purchasable_types:
            if self.property_id is None:
                raise ValueError(f"{self.id} must reference a purchasable property")
        elif self.property_id is not None:
            raise ValueError(f"{self.id} cannot reference a property")

        if self.type in {"chance", "community_chest"}:
            if self.deck != self.type:
                raise ValueError(f"{self.id} must reference the {self.type} deck")
        elif self.deck is not None:
            raise ValueError(f"{self.id} cannot reference a deck")

        if self.type == "tax":
            if self.amount is None:
                raise ValueError(f"{self.id} must define a tax amount")
        elif self.amount is not None:
            raise ValueError(f"{self.id} cannot define an amount")

        return self


class PropertyGroup(StaticDataModel):
    id: str
    name: str
    kind: PropertyKind
    color: str
    property_ids: tuple[str, ...]
    house_cost: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_group_shape(self) -> Self:
        if not self.property_ids:
            raise ValueError(f"{self.id} must contain at least one property")
        if self.kind == "street" and self.house_cost is None:
            raise ValueError(f"{self.id} must define a house cost")
        if self.kind != "street" and self.house_cost is not None:
            raise ValueError(f"{self.id} cannot define a house cost")
        return self


class PropertyData(StaticDataModel):
    id: str
    name: str
    kind: PropertyKind
    group: str
    price: int = Field(gt=0)
    mortgage_value: int = Field(gt=0)
    board_position: int = Field(ge=0, le=39)
    house_cost: int | None = Field(default=None, gt=0)
    hotel_cost: int | None = Field(default=None, gt=0)
    rents: tuple[int, int, int, int, int, int] | None = None
    rent_by_owned_count: tuple[int, int, int, int] | None = None
    rent_multipliers: tuple[int, int] | None = None

    @model_validator(mode="after")
    def validate_property_shape(self) -> Self:
        if not self.id.startswith("property_"):
            raise ValueError(f"{self.id} must use the property_ prefix")

        if self.kind == "street":
            if self.house_cost is None or self.hotel_cost is None or self.rents is None:
                raise ValueError(f"{self.id} must define street improvement economics")
            if self.rent_by_owned_count is not None or self.rent_multipliers is not None:
                raise ValueError(f"{self.id} cannot define railroad or utility rent fields")
            return self

        if self.house_cost is not None or self.hotel_cost is not None or self.rents is not None:
            raise ValueError(f"{self.id} cannot define street rent fields")

        if self.kind == "railroad":
            if self.rent_by_owned_count is None:
                raise ValueError(f"{self.id} must define railroad rent tiers")
            if self.rent_multipliers is not None:
                raise ValueError(f"{self.id} cannot define utility rent multipliers")
            return self

        if self.rent_multipliers is None:
            raise ValueError(f"{self.id} must define utility rent multipliers")
        if self.rent_by_owned_count is not None:
            raise ValueError(f"{self.id} cannot define railroad rent tiers")
        return self


class CardData(StaticDataModel):
    id: str
    deck: DeckName
    title: str
    description: str
    effect: Mapping[str, EffectValue]

    @model_validator(mode="after")
    def validate_card_shape(self) -> Self:
        if not self.id.startswith("card_"):
            raise ValueError(f"{self.id} must use the card_ prefix")
        effect_type = self.effect.get("type")
        if not isinstance(effect_type, str) or not effect_type:
            raise ValueError(f"{self.id} must define an effect type")
        return self


class Decks(StaticDataModel):
    chance: tuple[CardData, ...]
    community_chest: tuple[CardData, ...]


class BankInventory(StaticDataModel):
    houses: int
    hotels: int

    @model_validator(mode="after")
    def validate_classic_inventory(self) -> Self:
        if self.houses != 32 or self.hotels != 12:
            raise ValueError("classic bank inventory must contain exactly 32 houses and 12 hotels")
        return self


class ClassicMonopolyData(StaticDataModel):
    version: str
    currency: Currency
    board: tuple[BoardSpace, ...]
    property_groups: tuple[PropertyGroup, ...]
    properties: tuple[PropertyData, ...]
    decks: Decks
    bank_inventory: BankInventory

    @model_validator(mode="after")
    def validate_static_data(self) -> Self:
        self._validate_board()
        self._validate_property_groups()
        self._validate_decks()
        return self

    def _validate_board(self) -> None:
        if len(self.board) != 40:
            raise ValueError("classic board must contain exactly 40 spaces")
        if [space.position for space in self.board] != list(range(40)):
            raise ValueError("classic board positions must be ordered from 0 through 39")
        if len({space.id for space in self.board}) != len(self.board):
            raise ValueError("board space ids must be unique")
        for space in self.board:
            if not space.id.startswith("space_"):
                raise ValueError(f"{space.id} must use the space_ prefix")

        property_by_id = {property_data.id: property_data for property_data in self.properties}
        property_space_ids: set[str] = set()
        for space in self.board:
            if space.property_id is None:
                continue
            property_data = property_by_id.get(space.property_id)
            if property_data is None:
                raise ValueError(f"{space.id} references missing property {space.property_id}")
            if property_data.board_position != space.position:
                raise ValueError(f"{property_data.id} board_position does not match board space")
            if property_data.kind != space.type:
                raise ValueError(f"{property_data.id} kind does not match board space type")
            property_space_ids.add(space.property_id)

        if property_space_ids != set(property_by_id):
            raise ValueError("every property must be represented by one board space")

    def _validate_property_groups(self) -> None:
        property_by_id = {property_data.id: property_data for property_data in self.properties}
        if len(property_by_id) != len(self.properties):
            raise ValueError("property ids must be unique")

        group_ids: set[str] = set()
        grouped_property_ids: list[str] = []
        for group in self.property_groups:
            if group.id in group_ids:
                raise ValueError(f"duplicate property group id {group.id}")
            group_ids.add(group.id)
            if len(set(group.property_ids)) != len(group.property_ids):
                raise ValueError(f"{group.id} contains duplicate property ids")

            for property_id in group.property_ids:
                property_data = property_by_id.get(property_id)
                if property_data is None:
                    raise ValueError(f"{group.id} references missing property {property_id}")
                if property_data.group != group.id:
                    raise ValueError(f"{property_data.id} does not point back to group {group.id}")
                if property_data.kind != group.kind:
                    raise ValueError(f"{property_data.id} kind does not match group {group.id}")
                if property_data.kind == "street":
                    if property_data.house_cost != group.house_cost:
                        raise ValueError(f"{property_data.id} house cost does not match group")
                    if property_data.hotel_cost != group.house_cost:
                        raise ValueError(f"{property_data.id} hotel cost does not match group")
                grouped_property_ids.append(property_id)

        if len(grouped_property_ids) != len(set(grouped_property_ids)):
            raise ValueError("property ids cannot appear in more than one group")
        if set(grouped_property_ids) != set(property_by_id):
            raise ValueError("property groups must contain every property exactly once")

    def _validate_decks(self) -> None:
        self._validate_deck("chance", self.decks.chance)
        self._validate_deck("community_chest", self.decks.community_chest)

    @staticmethod
    def _validate_deck(deck_name: DeckName, cards: tuple[CardData, ...]) -> None:
        if len(cards) != 16:
            raise ValueError(f"{deck_name} deck must contain exactly 16 cards")
        card_ids = [card.id for card in cards]
        if len(set(card_ids)) != len(card_ids):
            raise ValueError(f"{deck_name} card ids must be unique")
        for card in cards:
            if card.deck != deck_name:
                raise ValueError(f"{card.id} must declare deck {deck_name}")


@lru_cache(maxsize=1)
def load_classic_monopoly_data() -> ClassicMonopolyData:
    return ClassicMonopolyData.model_validate_json(
        RULES_DATA_PATH.read_text(encoding="utf-8")
    )
