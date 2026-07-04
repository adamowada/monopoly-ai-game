import { describe, expect, it } from "vitest";

import {
  BANK_INVENTORY,
  BOARD_SPACES,
  CHANCE_DECK,
  CLASSIC_MONOPOLY_DATA,
  COMMUNITY_CHEST_DECK,
  PROPERTIES,
  PROPERTIES_BY_ID,
  PROPERTY_GROUPS,
  type StaticDataCard,
  type StaticDataProperty,
} from "../src/rules/static-data";

function expectUnique(values: readonly string[]): void {
  expect(new Set(values).size).toBe(values.length);
}

describe("classic Monopoly static data contract", () => {
  it("exports frontend-serializable canonical data", () => {
    expect(CLASSIC_MONOPOLY_DATA.version).toBe("classic-monopoly-v1");
    expect(CLASSIC_MONOPOLY_DATA.currency.code).toBe("M");
    expect(BANK_INVENTORY).toEqual({ houses: 32, hotels: 12 });

    const serialized = JSON.parse(JSON.stringify(CLASSIC_MONOPOLY_DATA)) as typeof CLASSIC_MONOPOLY_DATA;
    expect(serialized.board[0]?.name).toBe("GO");
    expect(serialized.properties[0]?.id.startsWith("property_")).toBe(true);
    expect(serialized.decks.chance[0]?.id.startsWith("card_")).toBe(true);
  });

  it("keeps the board in exact classic position order", () => {
    expect(BOARD_SPACES).toHaveLength(40);
    expect(BOARD_SPACES.map((space) => space.position)).toEqual(
      Array.from({ length: 40 }, (_, position) => position),
    );
    expectUnique(BOARD_SPACES.map((space) => space.id));

    expect(BOARD_SPACES[0]).toMatchObject({ name: "GO", type: "go" });
    expect(BOARD_SPACES[1]).toMatchObject({
      name: "Mediterranean Avenue",
      property_id: "property_mediterranean_avenue",
    });
    expect(BOARD_SPACES[2]).toMatchObject({ type: "community_chest", deck: "community_chest" });
    expect(BOARD_SPACES[4]).toMatchObject({ type: "tax", amount: 200 });
    expect(BOARD_SPACES[10]).toMatchObject({ type: "jail" });
    expect(BOARD_SPACES[20]).toMatchObject({ type: "free_parking" });
    expect(BOARD_SPACES[30]).toMatchObject({ type: "go_to_jail" });
    expect(BOARD_SPACES[38]).toMatchObject({ type: "tax", amount: 100 });
    expect(BOARD_SPACES[39]).toMatchObject({
      name: "Boardwalk",
      property_id: "property_boardwalk",
    });
  });

  it("exports complete property economics and group membership", () => {
    expect(PROPERTIES).toHaveLength(28);
    expectUnique(PROPERTIES.map((property) => property.id));

    const streets = PROPERTIES.filter(
      (property): property is StaticDataProperty & { kind: "street" } => property.kind === "street",
    );
    const railroads = PROPERTIES.filter((property) => property.kind === "railroad");
    const utilities = PROPERTIES.filter((property) => property.kind === "utility");

    expect(streets).toHaveLength(22);
    expect(railroads).toHaveLength(4);
    expect(utilities).toHaveLength(2);
    expect(PROPERTIES_BY_ID.property_boardwalk).toMatchObject({
      price: 400,
      mortgage_value: 200,
      rents: [50, 200, 600, 1400, 1700, 2000],
      house_cost: 200,
      hotel_cost: 200,
    });
    expect(PROPERTIES_BY_ID.property_reading_railroad).toMatchObject({
      price: 200,
      mortgage_value: 100,
      rent_by_owned_count: [25, 50, 100, 200],
    });
    expect(PROPERTIES_BY_ID.property_water_works).toMatchObject({
      price: 150,
      mortgage_value: 75,
      rent_multipliers: [4, 10],
    });

    const expectedGroupIds = [
      "brown",
      "light_blue",
      "pink",
      "orange",
      "red",
      "yellow",
      "green",
      "dark_blue",
      "railroad",
      "utility",
    ];
    expect(PROPERTY_GROUPS.map((group) => group.id)).toEqual(expectedGroupIds);

    const groupedPropertyIds = new Set(PROPERTY_GROUPS.flatMap((group) => group.property_ids));
    expect(groupedPropertyIds).toEqual(new Set(PROPERTIES.map((property) => property.id)));
  });

  it("exports complete Chance and Community Chest static card descriptors", () => {
    expect(CHANCE_DECK).toHaveLength(16);
    expect(COMMUNITY_CHEST_DECK).toHaveLength(16);

    for (const [deck, cards] of [
      ["chance", CHANCE_DECK],
      ["community_chest", COMMUNITY_CHEST_DECK],
    ] as const satisfies readonly [StaticDataCard["deck"], readonly StaticDataCard[]][]) {
      expectUnique(cards.map((card) => card.id));
      for (const card of cards) {
        expect(card.id.startsWith("card_")).toBe(true);
        expect(card.deck).toBe(deck);
        expect(card.title.length).toBeGreaterThan(0);
        expect(card.description.length).toBeGreaterThan(0);
        expect(card.effect.type.length).toBeGreaterThan(0);
      }
    }

    expect(CHANCE_DECK.filter((card) => card.effect.type === "advance_to_nearest_railroad")).toHaveLength(
      2,
    );
    expect(CHANCE_DECK.some((card) => card.effect.type === "advance_to_nearest_utility")).toBe(true);
    expect(CHANCE_DECK.some((card) => card.effect.type === "building_repairs")).toBe(true);
    expect(COMMUNITY_CHEST_DECK.some((card) => card.effect.type === "collect_from_each_player")).toBe(
      true,
    );
    expect(COMMUNITY_CHEST_DECK.some((card) => card.effect.type === "building_repairs")).toBe(true);
  });
});
