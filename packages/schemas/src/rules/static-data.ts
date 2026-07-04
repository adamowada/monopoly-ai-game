import classicMonopolyData from "../../../../content/rules/classic_monopoly.json";
import type { CardId, PropertyId, SpaceId } from "../shared/ids";

export type DeckName = "chance" | "community_chest";
export type PropertyKind = "street" | "railroad" | "utility";
export type BoardSpaceType =
  | "go"
  | "street"
  | "community_chest"
  | "tax"
  | "railroad"
  | "chance"
  | "jail"
  | "utility"
  | "free_parking"
  | "go_to_jail";

export type StaticDataSpaceId = SpaceId;
export type StaticDataPropertyId = PropertyId;
export type StaticDataCardId = CardId;
export type PropertyGroupId =
  | "brown"
  | "light_blue"
  | "pink"
  | "orange"
  | "red"
  | "yellow"
  | "green"
  | "dark_blue"
  | "railroad"
  | "utility";

export interface StaticDataCurrency {
  readonly code: string;
  readonly symbol: string;
  readonly name: string;
}

export interface StaticDataBoardSpace {
  readonly id: SpaceId;
  readonly position: number;
  readonly name: string;
  readonly type: BoardSpaceType;
  readonly property_id?: PropertyId;
  readonly deck?: DeckName;
  readonly amount?: number;
}

export interface StaticDataPropertyGroup {
  readonly id: PropertyGroupId;
  readonly name: string;
  readonly kind: PropertyKind;
  readonly color: string;
  readonly property_ids: readonly PropertyId[];
  readonly house_cost?: number;
}

interface StaticDataPropertyBase {
  readonly id: PropertyId;
  readonly name: string;
  readonly group: PropertyGroupId;
  readonly price: number;
  readonly mortgage_value: number;
  readonly board_position: number;
}

export interface StaticDataStreetProperty extends StaticDataPropertyBase {
  readonly kind: "street";
  readonly house_cost: number;
  readonly hotel_cost: number;
  readonly rents: readonly [number, number, number, number, number, number];
}

export interface StaticDataRailroadProperty extends StaticDataPropertyBase {
  readonly kind: "railroad";
  readonly rent_by_owned_count: readonly [number, number, number, number];
}

export interface StaticDataUtilityProperty extends StaticDataPropertyBase {
  readonly kind: "utility";
  readonly rent_multipliers: readonly [number, number];
}

export type StaticDataProperty =
  | StaticDataStreetProperty
  | StaticDataRailroadProperty
  | StaticDataUtilityProperty;

export interface StaticDataCardEffect {
  readonly type: string;
  readonly [key: string]: string | number | boolean;
}

export interface StaticDataCard {
  readonly id: CardId;
  readonly deck: DeckName;
  readonly title: string;
  readonly description: string;
  readonly effect: StaticDataCardEffect;
}

export interface StaticDataDecks {
  readonly chance: readonly StaticDataCard[];
  readonly community_chest: readonly StaticDataCard[];
}

export interface StaticDataBankInventory {
  readonly houses: 32;
  readonly hotels: 12;
}

export interface ClassicMonopolyStaticData {
  readonly version: string;
  readonly currency: StaticDataCurrency;
  readonly board: readonly StaticDataBoardSpace[];
  readonly property_groups: readonly StaticDataPropertyGroup[];
  readonly properties: readonly StaticDataProperty[];
  readonly decks: StaticDataDecks;
  readonly bank_inventory: StaticDataBankInventory;
}

export const CLASSIC_MONOPOLY_DATA = classicMonopolyData as unknown as ClassicMonopolyStaticData;

export const BOARD_SPACES = CLASSIC_MONOPOLY_DATA.board;
export const PROPERTY_GROUPS = CLASSIC_MONOPOLY_DATA.property_groups;
export const PROPERTIES = CLASSIC_MONOPOLY_DATA.properties;
export const CHANCE_DECK = CLASSIC_MONOPOLY_DATA.decks.chance;
export const COMMUNITY_CHEST_DECK = CLASSIC_MONOPOLY_DATA.decks.community_chest;
export const BANK_INVENTORY = CLASSIC_MONOPOLY_DATA.bank_inventory;

export const PROPERTIES_BY_ID = Object.fromEntries(
  PROPERTIES.map((property) => [property.id, property]),
) as Readonly<Record<PropertyId, StaticDataProperty>>;
