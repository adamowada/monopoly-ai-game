import { describe, expect, it } from "vitest";

import { submitAiStep } from "./gameplay";

describe("gameplay API helpers", () => {
  it("parses open_negotiation AI step responses", async () => {
    const fetcher = async (input: string, init: RequestInit): Promise<Response> => {
      expect(input).toBe("http://api.test/games/game-1/ai/step");
      expect(init.method).toBe("POST");
      expect(JSON.parse(String(init.body))).toEqual({
        player_id: "ai-player-1",
        decision_type: "open_negotiation",
        mandatory: false,
      });

      return Response.json({
        status: "done",
        game_id: "game-1",
        player_id: "ai-player-1",
        decision_type: "open_negotiation",
        negotiation_id: "negotiation-1",
        ai_decision_id: "ai-decision-1",
        accepted_events: [],
        accepted_event_id: null,
        rejected_action_id: null,
        game_status: "active",
        consumed_response_opportunity: false,
        consumed_negotiation_opportunity: null,
        outcome: {
          kind: "open_negotiation",
          status: "done",
          negotiation_id: "negotiation-1",
        },
        reason_code: null,
        validation_errors: [],
        negotiation: {
          id: "negotiation-1",
          opened_by_player_id: "ai-player-1",
          participant_player_ids: ["ai-player-1", "human-player-1"],
        },
      });
    };

    const response = await submitAiStep({
      gameId: "game-1",
      baseUrl: "http://api.test",
      fetcher,
      input: {
        player_id: "ai-player-1",
        decision_type: "open_negotiation",
        mandatory: false,
      },
    });

    expect(response.status).toBe("done");
    expect(response.decision_type).toBe("open_negotiation");
    expect(response.negotiation_id).toBe("negotiation-1");
    expect(response.outcome.negotiation_id).toBe("negotiation-1");
  });
});
