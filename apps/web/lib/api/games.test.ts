import { describe, expect, it } from "vitest";

import { createGame, endGame, readGame } from "./games";

describe("game API helpers", () => {
  it("creates a game through the backend game API", async () => {
    const fetcher = async (input: string, init: RequestInit): Promise<Response> => {
      expect(input).toBe("http://api.test/games");
      expect(init.method).toBe("POST");
      expect(init.body).toBe(
        JSON.stringify({
          seed: "frontend-test",
          players: [
            { name: "Ada", kind: "human" },
            { name: "Grace", kind: "ai" },
          ],
        }),
      );

      return Response.json({
        id: "11111111-1111-1111-1111-111111111111",
        status: "active",
        ruleset_version: "classic-v1",
        seed: "frontend-test",
        current_phase: "START_TURN",
        settings: {},
        created_at: "2026-07-04T00:00:00.000Z",
        updated_at: "2026-07-04T00:00:00.000Z",
        players: [
          {
            id: "22222222-2222-2222-2222-222222222222",
            game_id: "11111111-1111-1111-1111-111111111111",
            seat_order: 0,
            name: "Ada",
            controller_type: "human",
            status: "active",
            state: {},
            created_at: "2026-07-04T00:00:00.000Z",
            updated_at: "2026-07-04T00:00:00.000Z",
          },
        ],
      });
    };

    const snapshot = await createGame({
      baseUrl: "http://api.test",
      fetcher,
      seed: "frontend-test",
      players: [
        { name: "Ada", kind: "human" },
        { name: "Grace", kind: "ai" },
      ],
    });

    expect(snapshot.state).toBe("loaded");
    if (snapshot.state === "loaded") {
      expect(snapshot.game.id).toBe("11111111-1111-1111-1111-111111111111");
      expect(snapshot.game.players[0].name).toBe("Ada");
    }
  });

  it("keeps setup-only player colors and negotiation cutoffs in game settings", async () => {
    const fetcher = async (input: string, init: RequestInit): Promise<Response> => {
      expect(input).toBe("http://api.test/games");
      expect(init.method).toBe("POST");
      expect(init.body).toBe(
        JSON.stringify({
          seed: "setup-seed",
          players: [
            { name: "Ada", kind: "human" },
            { name: "Grace", kind: "ai" },
          ],
          settings: {
            player_colors: [
              { seat_order: 0, color: "#0f766e" },
              { seat_order: 1, color: "#7c3aed" },
            ],
            player_icons: [
              { seat_order: 0, icon: "🚗" },
              { seat_order: 1, icon: "🎩" },
            ],
            negotiation_cutoffs: {
              max_rounds: 4,
              max_proposals_per_player: 3,
            },
          },
        }),
      );

      return Response.json(
        {
          id: "11111111-1111-1111-1111-111111111111",
          status: "active",
          ruleset_version: "classic-v1",
          seed: "setup-seed",
          current_phase: "START_TURN",
          settings: {
            player_colors: [
              { seat_order: 0, color: "#0f766e" },
              { seat_order: 1, color: "#7c3aed" },
            ],
            player_icons: [
              { seat_order: 0, icon: "🚗" },
              { seat_order: 1, icon: "🎩" },
            ],
            negotiation_cutoffs: {
              max_rounds: 4,
              max_proposals_per_player: 3,
            },
          },
          created_at: "2026-07-04T00:00:00.000Z",
          updated_at: "2026-07-04T00:00:00.000Z",
          players: [],
        },
        { status: 201 },
      );
    };

    const snapshot = await createGame({
      baseUrl: "http://api.test",
      fetcher,
      seed: "setup-seed",
      players: [
        { name: "Ada", kind: "human" },
        { name: "Grace", kind: "ai" },
      ],
      settings: {
        player_colors: [
          { seat_order: 0, color: "#0f766e" },
          { seat_order: 1, color: "#7c3aed" },
        ],
        player_icons: [
          { seat_order: 0, icon: "🚗" },
          { seat_order: 1, icon: "🎩" },
        ],
        negotiation_cutoffs: {
          max_rounds: 4,
          max_proposals_per_player: 3,
        },
      },
    });

    expect(snapshot.state).toBe("loaded");
    if (snapshot.state === "loaded") {
      expect(snapshot.game.settings).toEqual({
        player_colors: [
          { seat_order: 0, color: "#0f766e" },
          { seat_order: 1, color: "#7c3aed" },
        ],
        player_icons: [
          { seat_order: 0, icon: "🚗" },
          { seat_order: 1, icon: "🎩" },
        ],
        negotiation_cutoffs: {
          max_rounds: 4,
          max_proposals_per_player: 3,
        },
      });
    }
  });

  it("loads a game by id and returns errors as snapshots", async () => {
    const loaded = await readGame({
      baseUrl: "http://api.test/",
      gameId: "11111111-1111-1111-1111-111111111111",
      fetcher: async (input: string, init: RequestInit): Promise<Response> => {
        expect(input).toBe("http://api.test/games/11111111-1111-1111-1111-111111111111");
        expect(init.method).toBeUndefined();
        return Response.json({
          id: "11111111-1111-1111-1111-111111111111",
          status: "active",
          ruleset_version: "classic-v1",
          seed: "frontend-test",
          current_phase: "START_TURN",
          settings: {},
          created_at: "2026-07-04T00:00:00.000Z",
          updated_at: "2026-07-04T00:00:00.000Z",
          players: [],
        });
      },
    });
    const error = await readGame({
      baseUrl: "http://api.test",
      gameId: "missing",
      fetcher: async (): Promise<Response> => new Response("missing", { status: 404 }),
    });

    expect(loaded.state).toBe("loaded");
    expect(error.state).toBe("error");
    if (error.state === "error") {
      expect(error.error).toContain("HTTP 404");
    }
  });

  it("marks a game ended through the backend game API", async () => {
    const snapshot = await endGame({
      baseUrl: "http://api.test/",
      gameId: "11111111-1111-1111-1111-111111111111",
      fetcher: async (input: string, init: RequestInit): Promise<Response> => {
        expect(input).toBe("http://api.test/games/11111111-1111-1111-1111-111111111111/end");
        expect(init.method).toBe("POST");
        return Response.json({
          id: "11111111-1111-1111-1111-111111111111",
          status: "ended",
          ruleset_version: "classic-v1",
          seed: "frontend-test",
          current_phase: "ENDED",
          settings: {},
          created_at: "2026-07-04T00:00:00.000Z",
          updated_at: "2026-07-04T00:05:00.000Z",
          players: [],
        });
      },
    });

    expect(snapshot.state).toBe("loaded");
    if (snapshot.state === "loaded") {
      expect(snapshot.game.status).toBe("ended");
      expect(snapshot.game.current_phase).toBe("ENDED");
    }
  });
});
