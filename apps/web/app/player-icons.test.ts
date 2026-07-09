import { describe, expect, it } from "vitest";

import { PLAYER_ICON_OPTIONS, defaultPlayerIcon, isPlayerIconOption, playerIconLabel } from "./player-icons";

describe("player icon options", () => {
  it("uses a small unique set of real emoji token glyphs", () => {
    expect(PLAYER_ICON_OPTIONS).toHaveLength(6);
    expect(new Set(PLAYER_ICON_OPTIONS.map((option) => option.icon)).size).toBe(PLAYER_ICON_OPTIONS.length);

    for (const option of PLAYER_ICON_OPTIONS) {
      expect(option.icon).not.toMatch(/[ðŸ]/u);
      expect(option.icon).toMatch(/\p{Extended_Pictographic}/u);
    }
  });

  it("keeps token option helpers aligned with the configured choices", () => {
    for (const [index, option] of PLAYER_ICON_OPTIONS.entries()) {
      expect(defaultPlayerIcon(index)).toBe(option.icon);
      expect(isPlayerIconOption(option.icon)).toBe(true);
      expect(playerIconLabel(option.icon)).toBe(option.label);
    }
  });
});
