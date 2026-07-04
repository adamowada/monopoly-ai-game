import { describe, expect, it, vi } from "vitest";

import { readRejectedActions } from "./rejected-actions";

const rejectionRecord = {
  id: "11111111-1111-1111-1111-111111111111",
  game_id: "22222222-2222-2222-2222-222222222222",
  actor_player_id: "33333333-3333-3333-3333-333333333333",
  action_type: "BUY_PROPERTY",
  payload: { property_id: "property_boardwalk" },
  reason_code: "illegal_action",
  validation_errors: [
    {
      code: "illegal_action",
      message: "player is not on property_boardwalk",
      field: "payload.property_id",
    },
  ],
  legal_action_context: {
    phase: "START_TURN",
    legal_actions: ["ROLL_DICE", "DECLARE_BANKRUPTCY"],
  },
  phase: "START_TURN",
  state_hash: "abc123",
  created_at: "2026-07-04T12:00:00.000Z",
};

describe("readRejectedActions", () => {
  it("reads and validates rejected action records with optional actor filtering", async () => {
    const fetcher = vi.fn(async () =>
      Response.json({
        rejected_actions: [rejectionRecord],
      }),
    );

    const snapshot = await readRejectedActions({
      baseUrl: "http://api.test/",
      gameId: rejectionRecord.game_id,
      actorPlayerId: rejectionRecord.actor_player_id,
      fetcher,
      checkedAt: () => "2026-07-04T12:01:00.000Z",
    });

    expect(fetcher).toHaveBeenCalledWith(
      "http://api.test/games/22222222-2222-2222-2222-222222222222/rejected-actions?actor_player_id=33333333-3333-3333-3333-333333333333",
      {
        cache: "no-store",
        headers: { accept: "application/json" },
      },
    );
    expect(snapshot).toEqual({
      state: "loaded",
      checkedAt: "2026-07-04T12:01:00.000Z",
      rejectedActions: [rejectionRecord],
    });
  });

  it("returns an error snapshot when the audit response shape drifts", async () => {
    const fetcher = vi.fn(async () => Response.json({ rejected_actions: [{ id: "missing-fields" }] }));

    const snapshot = await readRejectedActions({
      baseUrl: "http://api.test",
      gameId: rejectionRecord.game_id,
      fetcher,
      checkedAt: () => "2026-07-04T12:01:00.000Z",
    });

    expect(snapshot.state).toBe("error");
    expect(snapshot.checkedAt).toBe("2026-07-04T12:01:00.000Z");
    if (snapshot.state === "error") {
      expect(snapshot.error).toContain("Invalid rejected actions response");
    }
  });
});
