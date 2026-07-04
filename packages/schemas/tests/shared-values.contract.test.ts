import { describe, expect, it } from "vitest";

import { ACTION_TYPES, PHASE_NAMES, PLAYER_TYPES } from "../src/shared/enums";
import { ID_PREFIXES } from "../src/shared/ids";

function expectUnique(values: readonly string[]): void {
  expect(new Set(values).size).toBe(values.length);
}

describe("shared contract values", () => {
  it("exposes stable phase, action, and player values", () => {
    expect(PHASE_NAMES).toContain("START_TURN");
    expect(PHASE_NAMES).toContain("NEGOTIATION_WINDOW");
    expect(PHASE_NAMES).toContain("GAME_OVER");
    expect(ACTION_TYPES).toContain("ROLL_DICE");
    expect(ACTION_TYPES).toContain("PROPOSE_DEAL");
    expect(PLAYER_TYPES).toEqual(["human", "ai"]);

    expectUnique(PHASE_NAMES);
    expectUnique(ACTION_TYPES);
    expectUnique(PLAYER_TYPES);
  });

  it("exposes stable entity id prefixes for contract payloads", () => {
    expect(ID_PREFIXES.game).toBe("game");
    expect(ID_PREFIXES.player).toBe("player");
    expect(ID_PREFIXES.property).toBe("property");
    expect(ID_PREFIXES.action).toBe("action");
    expect(ID_PREFIXES.event).toBe("event");
  });
});
