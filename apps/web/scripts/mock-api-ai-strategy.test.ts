import { createServer } from "node:net";
import { spawn, type ChildProcess } from "node:child_process";
import { once } from "node:events";
import { resolve } from "node:path";

import { afterEach, describe, expect, it } from "vitest";

type MockGame = {
  current_phase?: string;
  id: string;
  players: Array<{ id: string; name: string; state?: { cash?: number }; status?: string }>;
  status?: string;
};

type AiStepPayload = {
  accepted_events: Array<{
    event_type: string;
    payload: Record<string, unknown>;
  }>;
  deal?: {
    id?: string;
    parent_deal_id?: string | null;
    status?: string;
    terms?: Array<Record<string, unknown>>;
  } | null;
  negotiation?: {
    context?: string;
    id?: string;
    participant_player_ids?: string[];
    topic?: string;
  } | null;
  outcome?: Record<string, unknown>;
  status: string;
};

type ActionAcceptedPayload = {
  accepted_events: Array<{
    event_type: string;
    payload: Record<string, unknown>;
  }>;
  event_sequence: number;
  state_hash: string;
  status: string;
};

type LegalActionsPayload = {
  legal_actions: Array<{
    type: string;
    payload: Record<string, unknown>;
  }>;
};

type CreateGameOptions = {
  players?: Array<{ kind: "ai" | "human"; name: string }>;
  seed?: string;
  settings?: Record<string, unknown>;
};

let mockApiProcess: ChildProcess | null = null;
let mockApiOutput = "";

async function allocatePort(): Promise<number> {
  const server = createServer();
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const address = server.address();
  if (address === null || typeof address === "string") {
    throw new Error("mock API test could not allocate a local TCP port");
  }
  const port = address.port;
  server.close();
  await once(server, "close");
  return port;
}

async function waitForMockApi(baseUrl: string) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < 10_000) {
    try {
      const response = await fetch(`${baseUrl}/health`);
      if (response.ok) {
        return;
      }
    } catch {
      // The child process may still be binding the port.
    }
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error(`mock API did not become healthy:\n${mockApiOutput}`);
}

async function startMockApi(): Promise<string> {
  const port = await allocatePort();
  const scriptPath = resolve(process.cwd(), "scripts/mock-api.mjs");
  mockApiOutput = "";
  const child = spawn(process.execPath, [scriptPath], {
    env: { ...process.env, MOCK_API_PORT: String(port) },
    stdio: ["ignore", "pipe", "pipe"],
  });
  mockApiProcess = child;
  child.stdout?.on("data", (chunk) => {
    mockApiOutput += String(chunk);
  });
  child.stderr?.on("data", (chunk) => {
    mockApiOutput += String(chunk);
  });
  const baseUrl = `http://127.0.0.1:${port}`;
  await waitForMockApi(baseUrl);
  return baseUrl;
}

async function stopMockApi() {
  const child = mockApiProcess;
  mockApiProcess = null;
  if (!child || child.exitCode !== null) {
    return;
  }
  child.kill("SIGTERM");
  const exited = await Promise.race([
    once(child, "exit").then(() => true),
    new Promise<boolean>((resolve) => setTimeout(() => resolve(false), 2_000)),
  ]);
  if (!exited && child.exitCode === null) {
    child.kill("SIGKILL");
    await once(child, "exit");
  }
}

async function postJson<T>(baseUrl: string, path: string, payload: unknown): Promise<T> {
  const response = await fetch(`${baseUrl}${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  const body = (await response.json()) as T;
  expect(response.ok, JSON.stringify(body)).toBe(true);
  return body;
}

async function postJsonRejected<T>(baseUrl: string, path: string, payload: unknown, expectedStatus: number): Promise<T> {
  const response = await fetch(`${baseUrl}${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  const body = (await response.json()) as T;
  expect(response.status, JSON.stringify(body)).toBe(expectedStatus);
  return body;
}

async function getJson<T>(baseUrl: string, path: string): Promise<T> {
  const response = await fetch(`${baseUrl}${path}`);
  const body = (await response.json()) as T;
  expect(response.ok, JSON.stringify(body)).toBe(true);
  return body;
}

function createGame(baseUrl: string, options: CreateGameOptions = {}): Promise<MockGame> {
  const players = options.players ?? [
    { name: "Ada", kind: "ai" },
    { name: "Grace", kind: "ai" },
  ];
  return postJson<MockGame>(baseUrl, "/games", {
    seed: options.seed ?? "stage-10-5-two-human-full-round-ai-development",
    players,
    settings: options.settings ?? {
      player_colors: players.map((_, index) => ({
        seat_order: index,
        color: ["#0f766e", "#7c3aed", "#2563eb", "#dc2626", "#ca8a04"][index] ?? "#525252",
      })),
      negotiation_cutoffs: {
        max_rounds: 8,
        max_proposals_per_player: 12,
      },
    },
  });
}

function stepAi(baseUrl: string, gameId: string, playerId: string): Promise<AiStepPayload> {
  return postJson<AiStepPayload>(baseUrl, `/games/${gameId}/ai/step`, {
    player_id: playerId,
    decision_type: "action_decision",
    mandatory: true,
    mode: "auto",
  });
}

async function submitGameAction(
  baseUrl: string,
  gameId: string,
  action: { actor_id: string; payload: Record<string, unknown>; type: string },
): Promise<ActionAcceptedPayload> {
  const state = await getJson<{ event_sequence: number; state_hash: string }>(baseUrl, `/games/${gameId}/state`);
  const response = await fetch(`${baseUrl}/games/${gameId}/actions`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "Idempotency-Key": `${action.type}-${state.event_sequence}-${Math.random().toString(36).slice(2)}`,
    },
    body: JSON.stringify({
      ...action,
      expected_event_sequence: state.event_sequence,
      expected_state_hash: state.state_hash,
    }),
  });
  const body = (await response.json()) as ActionAcceptedPayload;
  expect(response.ok, JSON.stringify(body)).toBe(true);
  return body;
}

async function playAiTurnsUntilTerminal(baseUrl: string, gameId: string, stepLimit: number): Promise<{
  game: MockGame;
  steps: AiStepPayload[];
}> {
  const steps: AiStepPayload[] = [];
  for (let index = 0; index < stepLimit; index += 1) {
    const game = await getJson<MockGame>(baseUrl, `/games/${gameId}`);
    if (game.status === "ended") {
      return { game, steps };
    }
    const state = await getJson<{ state: { turn: { current_player_id: string | null } } }>(
      baseUrl,
      `/games/${gameId}/state`,
    );
    const activePlayerId = state.state.turn.current_player_id;
    if (!activePlayerId) {
      throw new Error("mock full-game loop had no active player");
    }
    steps.push(await stepAi(baseUrl, gameId, activePlayerId));
  }
  return { game: await getJson<MockGame>(baseUrl, `/games/${gameId}`), steps };
}

function openNegotiationAi(
  baseUrl: string,
  gameId: string,
  playerId: string,
  tradeOpportunity: Record<string, unknown>,
): Promise<AiStepPayload> {
  return postJson<AiStepPayload>(baseUrl, `/games/${gameId}/ai/step`, {
    player_id: playerId,
    decision_type: "open_negotiation",
    mandatory: false,
    request_context: {
      mode: "auto_negotiation",
      selected_deal_id: null,
      trade_opportunity: tradeOpportunity,
    },
  });
}

function proposeDealAi(baseUrl: string, gameId: string, playerId: string, negotiationId: string): Promise<AiStepPayload> {
  return postJson<AiStepPayload>(baseUrl, `/games/${gameId}/ai/step`, {
    player_id: playerId,
    decision_type: "deal_proposal",
    negotiation_id: negotiationId,
    mandatory: false,
    request_context: {
      mode: "auto_negotiation",
      selected_deal_id: null,
    },
  });
}

function counterofferAi(
  baseUrl: string,
  gameId: string,
  playerId: string,
  negotiationId: string,
  selectedDealId: string,
): Promise<AiStepPayload> {
  return postJson<AiStepPayload>(baseUrl, `/games/${gameId}/ai/step`, {
    player_id: playerId,
    decision_type: "counteroffer",
    negotiation_id: negotiationId,
    mandatory: false,
    request_context: {
      mode: "auto_negotiation",
      selected_deal_id: selectedDealId,
    },
  });
}

function acceptRejectAi(
  baseUrl: string,
  gameId: string,
  playerId: string,
  negotiationId: string,
  selectedDealId: string,
): Promise<AiStepPayload> {
  return postJson<AiStepPayload>(baseUrl, `/games/${gameId}/ai/step`, {
    player_id: playerId,
    decision_type: "accept_reject",
    negotiation_id: negotiationId,
    mandatory: false,
    request_context: {
      mode: "auto_negotiation",
      selected_deal_id: selectedDealId,
    },
  });
}

function createDeal(
  baseUrl: string,
  gameId: string,
  payload: Record<string, unknown>,
): Promise<{ deal: { id: string; status: string; terms: Array<Record<string, unknown>> }; status: string }> {
  return postJson<{ deal: { id: string; status: string; terms: Array<Record<string, unknown>> }; status: string }>(
    baseUrl,
    `/games/${gameId}/deals`,
    payload,
  );
}

afterEach(async () => {
  await stopMockApi();
});

describe("mock API AI strategy", () => {
  it("lets a four-AI mock game reach a winner through forced bankruptcies", async () => {
    const baseUrl = await startMockApi();
    const game = await createGame(baseUrl, {
      seed: "stage-10-5-four-ai-full-game-forced-bankruptcy",
      players: [
        { name: "Ada", kind: "ai" },
        { name: "Grace", kind: "ai" },
        { name: "Lin", kind: "ai" },
        { name: "Katherine", kind: "ai" },
      ],
    });

    const played = await playAiTurnsUntilTerminal(baseUrl, game.id, 20);

    expect(played.steps.map((step) => step.status)).not.toContain("rejected");
    expect(played.game).toEqual(
      expect.objectContaining({
        status: "ended",
        current_phase: "GAME_OVER",
      }),
    );
    expect(played.game.players[0]).toEqual(
      expect.objectContaining({
        id: game.players[0].id,
        status: "active",
      }),
    );
    expect(played.game.players.slice(1).map((player) => player.status)).toEqual(["bankrupt", "bankrupt", "bankrupt"]);

    const events = await getJson<{ events: Array<{ event_type: string; payload: Record<string, unknown> }> }>(
      baseUrl,
      `/games/${game.id}/events`,
    );
    expect(events.events.filter((event) => event.event_type === "BANKRUPTCY_DECLARED")).toHaveLength(3);
    expect(events.events.at(-1)).toEqual(
      expect.objectContaining({
        event_type: "GAME_ENDED",
        payload: expect.objectContaining({ winner_player_id: game.players[0].id }),
      }),
    );
  });

  it("bids above face value for a monopoly auction but passes beyond strategic value", async () => {
    const baseUrl = await startMockApi();
    const game = await createGame(baseUrl, {
      seed: "stage-5-auction-ai-value",
      players: [
        { name: "Ada", kind: "ai" },
        { name: "Grace", kind: "ai" },
        { name: "Linus", kind: "ai" },
      ],
      settings: {
        player_colors: [
          { seat_order: 0, color: "#0f766e" },
          { seat_order: 1, color: "#7c3aed" },
          { seat_order: 2, color: "#2563eb" },
        ],
        negotiation_cutoffs: {
          max_rounds: 8,
          max_proposals_per_player: 12,
        },
        debug_allocations: {
          property_owners: [{ property_id: "property_baltic_avenue", seat_order: 1 }],
        },
      },
    });
    const ada = game.players[0];
    const grace = game.players[1];
    const linus = game.players[2];

    await submitGameAction(baseUrl, game.id, { actor_id: ada.id, type: "ROLL_DICE", payload: {} });
    await submitGameAction(baseUrl, game.id, {
      actor_id: ada.id,
      type: "START_AUCTION",
      payload: { property_id: "property_mediterranean_avenue" },
    });
    await submitGameAction(baseUrl, game.id, {
      actor_id: linus.id,
      type: "BID_AUCTION",
      payload: { property_id: "property_mediterranean_avenue", amount: 75 },
    });

    const strategicBid = await stepAi(baseUrl, game.id, grace.id);
    expect(strategicBid.accepted_events).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          event_type: "ACTIVE_AUCTION_SET",
          payload: expect.objectContaining({
            high_bid_amount: 76,
            high_bidder_id: grace.id,
            property_id: "property_mediterranean_avenue",
          }),
        }),
      ]),
    );

    await submitGameAction(baseUrl, game.id, {
      actor_id: linus.id,
      type: "BID_AUCTION",
      payload: { property_id: "property_mediterranean_avenue", amount: 135 },
    });

    const pricedOutStep = await stepAi(baseUrl, game.id, grace.id);
    expect(pricedOutStep.accepted_events).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          event_type: "ACTIVE_AUCTION_SET",
          payload: expect.objectContaining({
            high_bid_amount: 135,
            high_bidder_id: linus.id,
            passed_player_ids: expect.arrayContaining([grace.id]),
            property_id: "property_mediterranean_avenue",
          }),
        }),
      ]),
    );
    expect(pricedOutStep.accepted_events).not.toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          event_type: "AUCTION_RESULT",
        }),
      ]),
    );
  });

  it("mortgages assets to settle debt before forced bankruptcy", async () => {
    const baseUrl = await startMockApi();
    const game = await createGame(baseUrl, {
      seed: "stage-10-5-two-human-full-round-debug-mortgage-before-bankruptcy",
      players: [
        { name: "Ada", kind: "ai" },
        { name: "Grace", kind: "ai" },
      ],
      settings: {
        player_colors: [
          { seat_order: 0, color: "#0f766e" },
          { seat_order: 1, color: "#7c3aed" },
        ],
        negotiation_cutoffs: {
          max_rounds: 8,
          max_proposals_per_player: 12,
        },
        debug_allocations: {
          player_cash: [
            { seat_order: 0, cash: 40 },
            { seat_order: 1, cash: 1 },
          ],
          property_owners: [
            { property_id: "property_mediterranean_avenue", seat_order: 0 },
            { property_id: "property_reading_railroad", seat_order: 1 },
          ],
        },
      },
    });
    const ada = game.players[0];
    const grace = game.players[1];

    await stepAi(baseUrl, game.id, ada.id);
    await stepAi(baseUrl, game.id, ada.id);
    const graceRoll = await stepAi(baseUrl, game.id, grace.id);
    expect(graceRoll.accepted_events.map((event) => event.event_type)).toContain("ACTIVE_PAYMENT_SET");

    const liquidationStep = await stepAi(baseUrl, game.id, grace.id);

    expect(liquidationStep.status).toBe("accepted");
    expect(liquidationStep.accepted_events).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          event_type: "PLAYER_CASH_DELTA",
          payload: expect.objectContaining({ amount: 100, player_id: grace.id }),
        }),
        expect.objectContaining({
          event_type: "PROPERTY_MORTGAGE_SET",
          payload: expect.objectContaining({ mortgaged: true, property_id: "property_reading_railroad" }),
        }),
      ]),
    );
    expect(liquidationStep.accepted_events.map((event) => event.event_type)).not.toContain("BANKRUPTCY_DECLARED");

    const settlementStep = await stepAi(baseUrl, game.id, grace.id);
    expect(settlementStep.accepted_events).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          event_type: "RENT_PAID",
          payload: expect.objectContaining({
            amount: 2,
            creditor_player_id: ada.id,
            debtor_player_id: grace.id,
            property_id: "property_mediterranean_avenue",
          }),
        }),
      ]),
    );

    const stateAfterSettlement = await getJson<{
      state: {
        players: Array<{ cash: number; id: string }>;
        property_ownership: Array<{ mortgaged?: boolean; property_id: string }>;
      };
    }>(baseUrl, `/games/${game.id}/state`);
    expect(stateAfterSettlement.state.players.find((player) => player.id === grace.id)?.cash).toBe(99);
    expect(
      stateAfterSettlement.state.property_ownership.find(
        (ownership) => ownership.property_id === "property_reading_railroad",
      ),
    ).toEqual(expect.objectContaining({ mortgaged: true }));
  });

  it("mortgages any eligible owned property to avoid forced bankruptcy", async () => {
    const baseUrl = await startMockApi();
    const game = await createGame(baseUrl, {
      seed: "stage-10-5-two-human-full-round-debug-generic-mortgage-before-bankruptcy",
      players: [
        { name: "Ada", kind: "ai" },
        { name: "Grace", kind: "ai" },
      ],
      settings: {
        player_colors: [
          { seat_order: 0, color: "#0f766e" },
          { seat_order: 1, color: "#7c3aed" },
        ],
        negotiation_cutoffs: {
          max_rounds: 8,
          max_proposals_per_player: 12,
        },
        debug_allocations: {
          player_cash: [
            { seat_order: 0, cash: 40 },
            { seat_order: 1, cash: 1 },
          ],
          property_owners: [
            { property_id: "property_mediterranean_avenue", seat_order: 0 },
            { property_id: "property_tennessee_avenue", seat_order: 1 },
          ],
        },
      },
    });
    const ada = game.players[0];
    const grace = game.players[1];

    await stepAi(baseUrl, game.id, ada.id);
    await stepAi(baseUrl, game.id, ada.id);
    const graceRoll = await stepAi(baseUrl, game.id, grace.id);
    expect(graceRoll.accepted_events.map((event) => event.event_type)).toContain("ACTIVE_PAYMENT_SET");

    const liquidationStep = await stepAi(baseUrl, game.id, grace.id);

    expect(liquidationStep.accepted_events).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          event_type: "PLAYER_CASH_DELTA",
          payload: expect.objectContaining({ amount: 90, player_id: grace.id }),
        }),
        expect.objectContaining({
          event_type: "PROPERTY_MORTGAGE_SET",
          payload: expect.objectContaining({ mortgaged: true, property_id: "property_tennessee_avenue" }),
        }),
      ]),
    );
    expect(liquidationStep.accepted_events.map((event) => event.event_type)).not.toContain("BANKRUPTCY_DECLARED");

    const settlementStep = await stepAi(baseUrl, game.id, grace.id);
    expect(settlementStep.accepted_events).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          event_type: "RENT_PAID",
          payload: expect.objectContaining({
            amount: 2,
            creditor_player_id: ada.id,
            debtor_player_id: grace.id,
          }),
        }),
      ]),
    );
  });

  it("sells developed property improvements to settle debt before forced bankruptcy", async () => {
    const baseUrl = await startMockApi();
    const game = await createGame(baseUrl, {
      seed: "stage-10-5-two-human-full-round-debug-sell-before-bankruptcy",
      players: [
        { name: "Ada", kind: "ai" },
        { name: "Grace", kind: "ai" },
      ],
      settings: {
        player_colors: [
          { seat_order: 0, color: "#0f766e" },
          { seat_order: 1, color: "#7c3aed" },
        ],
        negotiation_cutoffs: {
          max_rounds: 8,
          max_proposals_per_player: 12,
        },
        debug_allocations: {
          player_cash: [
            { seat_order: 0, cash: 40 },
            { seat_order: 1, cash: 1 },
          ],
          property_owners: [
            { property_id: "property_mediterranean_avenue", seat_order: 0 },
            { property_id: "property_oriental_avenue", seat_order: 1 },
            { property_id: "property_vermont_avenue", seat_order: 1 },
            { property_id: "property_connecticut_avenue", seat_order: 1 },
          ],
          property_improvements: [{ property_id: "property_connecticut_avenue", houses: 2, hotel: false }],
        },
      },
    });
    const ada = game.players[0];
    const grace = game.players[1];

    await stepAi(baseUrl, game.id, ada.id);
    await stepAi(baseUrl, game.id, ada.id);
    const graceRoll = await stepAi(baseUrl, game.id, grace.id);
    expect(graceRoll.accepted_events.map((event) => event.event_type)).toContain("ACTIVE_PAYMENT_SET");

    const liquidationStep = await stepAi(baseUrl, game.id, grace.id);

    expect(liquidationStep.accepted_events).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          event_type: "PLAYER_CASH_DELTA",
          payload: expect.objectContaining({ amount: 25, player_id: grace.id }),
        }),
        expect.objectContaining({
          event_type: "PROPERTY_IMPROVEMENTS_SET",
          payload: expect.objectContaining({
            hotel: false,
            houses: 1,
            property_id: "property_connecticut_avenue",
          }),
        }),
      ]),
    );
    expect(liquidationStep.accepted_events.map((event) => event.event_type)).not.toContain("BANKRUPTCY_DECLARED");

    const settlementStep = await stepAi(baseUrl, game.id, grace.id);
    expect(settlementStep.accepted_events).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          event_type: "RENT_PAID",
          payload: expect.objectContaining({
            amount: 2,
            creditor_player_id: ada.id,
            debtor_player_id: grace.id,
          }),
        }),
      ]),
    );

    const stateAfterSettlement = await getJson<{
      state: {
        players: Array<{ cash: number; id: string }>;
        property_ownership: Array<{ houses?: number; property_id: string }>;
      };
    }>(baseUrl, `/games/${game.id}/state`);
    expect(stateAfterSettlement.state.players.find((player) => player.id === grace.id)?.cash).toBe(24);
    expect(
      stateAfterSettlement.state.property_ownership.find(
        (ownership) => ownership.property_id === "property_connecticut_avenue",
      ),
    ).toEqual(expect.objectContaining({ houses: 1 }));
  });

  it("develops a complete color group before rolling a later AI turn and pays for the build", async () => {
    const baseUrl = await startMockApi();
    const game = await createGame(baseUrl);
    const ada = game.players[0];
    const grace = game.players[1];

    await stepAi(baseUrl, game.id, ada.id);
    const buyStep = await stepAi(baseUrl, game.id, ada.id);
    expect(buyStep.accepted_events.map((event) => event.event_type)).toContain("PROPERTY_OWNER_SET");
    await stepAi(baseUrl, game.id, ada.id);
    await stepAi(baseUrl, game.id, grace.id);
    await stepAi(baseUrl, game.id, grace.id);
    await stepAi(baseUrl, game.id, grace.id);

    const stateBeforeDevelopment = await getJson<{
      state: { turn: { current_player_id: string; phase: string } };
    }>(baseUrl, `/games/${game.id}/state`);
    expect(stateBeforeDevelopment.state.turn).toMatchObject({
      current_player_id: ada.id,
      phase: "START_TURN",
    });

    const developmentStep = await stepAi(baseUrl, game.id, ada.id);

    expect(developmentStep.status).toBe("accepted");
    expect(developmentStep.accepted_events).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          event_type: "PLAYER_CASH_DELTA",
          payload: expect.objectContaining({
            player_id: ada.id,
            amount: -50,
          }),
        }),
        expect.objectContaining({
          event_type: "PROPERTY_IMPROVEMENTS_SET",
          payload: expect.objectContaining({
            property_id: "property_mediterranean_avenue",
            houses: 1,
            hotel: false,
          }),
        }),
      ]),
    );
    expect(developmentStep.accepted_events.map((event) => event.event_type)).not.toContain("DICE_ROLLED");

    const stateAfterDevelopment = await getJson<{
      state: { players: Array<{ cash: number; id: string }> };
    }>(baseUrl, `/games/${game.id}/state`);
    expect(stateAfterDevelopment.state.players.find((player) => player.id === ada.id)?.cash).toBe(1392);
  });

  it("develops a debug-allocated orange monopoly before rolling", async () => {
    const baseUrl = await startMockApi();
    const game = await createGame(baseUrl, {
      seed: "stage-10-5-debug-orange-monopoly-development",
      players: [
        { name: "Ada", kind: "ai" },
        { name: "Grace", kind: "ai" },
      ],
      settings: {
        player_colors: [
          { seat_order: 0, color: "#0f766e" },
          { seat_order: 1, color: "#7c3aed" },
        ],
        negotiation_cutoffs: {
          max_rounds: 8,
          max_proposals_per_player: 12,
        },
        debug_allocations: {
          property_owners: [
            { property_id: "property_st_james_place", seat_order: 0 },
            { property_id: "property_tennessee_avenue", seat_order: 0 },
            { property_id: "property_new_york_avenue", seat_order: 0 },
          ],
        },
      },
    });
    const ada = game.players[0];

    const developmentStep = await stepAi(baseUrl, game.id, ada.id);

    expect(developmentStep.status).toBe("accepted");
    expect(developmentStep.accepted_events.map((event) => event.event_type)).not.toContain("DICE_ROLLED");
    expect(developmentStep.accepted_events).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          event_type: "PLAYER_CASH_DELTA",
          payload: expect.objectContaining({ amount: -100, player_id: ada.id }),
        }),
        expect.objectContaining({
          event_type: "PROPERTY_IMPROVEMENTS_SET",
          payload: expect.objectContaining({
            property_id: "property_new_york_avenue",
            houses: 1,
            hotel: false,
          }),
        }),
      ]),
    );

    const stateAfterDevelopment = await getJson<{
      state: {
        players: Array<{ cash: number; id: string }>;
        property_ownership: Array<{ houses?: number; property_id: string }>;
      };
    }>(baseUrl, `/games/${game.id}/state`);
    expect(stateAfterDevelopment.state.players.find((player) => player.id === ada.id)?.cash).toBe(1400);
    expect(
      stateAfterDevelopment.state.property_ownership.find(
        (ownership) => ownership.property_id === "property_new_york_avenue",
      ),
    ).toEqual(expect.objectContaining({ houses: 1 }));
  });

  it("defers monopoly development when it would break the AI cash reserve", async () => {
    const baseUrl = await startMockApi();
    const game = await createGame(baseUrl, {
      seed: "stage-10-5-debug-orange-monopoly-cash-reserve",
      players: [
        { name: "Ada", kind: "ai" },
        { name: "Grace", kind: "ai" },
      ],
      settings: {
        player_colors: [
          { seat_order: 0, color: "#0f766e" },
          { seat_order: 1, color: "#7c3aed" },
        ],
        negotiation_cutoffs: {
          max_rounds: 8,
          max_proposals_per_player: 12,
        },
        debug_allocations: {
          player_cash: [
            { seat_order: 0, cash: 350 },
            { seat_order: 1, cash: 1500 },
          ],
          property_owners: [
            { property_id: "property_st_james_place", seat_order: 0 },
            { property_id: "property_tennessee_avenue", seat_order: 0 },
            { property_id: "property_new_york_avenue", seat_order: 0 },
          ],
        },
      },
    });
    const ada = game.players[0];

    const legalActions = await getJson<LegalActionsPayload>(
      baseUrl,
      `/games/${game.id}/legal-actions?actor_player_id=${encodeURIComponent(ada.id)}`,
    );
    expect(legalActions.legal_actions.map((action) => action.type)).not.toContain("BUY_HOUSE");

    const aiStep = await stepAi(baseUrl, game.id, ada.id);

    expect(aiStep.accepted_events.map((event) => event.event_type)).toContain("DICE_ROLLED");
    expect(aiStep.accepted_events.map((event) => event.event_type)).not.toContain("PROPERTY_IMPROVEMENTS_SET");
    const stateAfterStep = await getJson<{
      state: {
        players: Array<{ cash: number; id: string }>;
        property_ownership: Array<{ houses?: number; property_id: string }>;
      };
    }>(baseUrl, `/games/${game.id}/state`);
    expect(stateAfterStep.state.players.find((player) => player.id === ada.id)?.cash).toBe(350);
    expect(
      stateAfterStep.state.property_ownership.find((ownership) => ownership.property_id === "property_new_york_avenue"),
    ).toEqual(expect.objectContaining({ houses: 0 }));
  });

  it("applies debug allocations and withholds unaffordable AI build actions", async () => {
    const baseUrl = await startMockApi();
    const game = await createGame(baseUrl, {
      seed: "stage-10-5-debug-low-cash-management",
      settings: {
        player_colors: [
          { seat_order: 0, color: "#0f766e" },
          { seat_order: 1, color: "#7c3aed" },
        ],
        negotiation_cutoffs: {
          max_rounds: 8,
          max_proposals_per_player: 12,
        },
        debug_allocations: {
          player_cash: [
            { seat_order: 0, cash: 40 },
            { seat_order: 1, cash: 1500 },
          ],
          property_owners: [
            { property_id: "property_mediterranean_avenue", seat_order: 0 },
            { property_id: "property_baltic_avenue", seat_order: 0 },
          ],
        },
      },
    });
    const ada = game.players[0];

    const state = await getJson<{
      state: {
        players: Array<{ cash: number; id: string }>;
        property_ownership: Array<{ owner_id: string | null; property_id: string }>;
      };
    }>(baseUrl, `/games/${game.id}/state`);
    expect(state.state.players.find((player) => player.id === ada.id)?.cash).toBe(40);
    expect(
      state.state.property_ownership.filter((ownership) =>
        ["property_mediterranean_avenue", "property_baltic_avenue"].includes(ownership.property_id),
      ),
    ).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ owner_id: ada.id, property_id: "property_mediterranean_avenue" }),
        expect.objectContaining({ owner_id: ada.id, property_id: "property_baltic_avenue" }),
      ]),
    );

    const legalActions = await getJson<LegalActionsPayload>(
      baseUrl,
      `/games/${game.id}/legal-actions?actor_player_id=${encodeURIComponent(ada.id)}`,
    );
    expect(legalActions.legal_actions.map((action) => action.type)).not.toContain("BUY_HOUSE");

    const aiStep = await stepAi(baseUrl, game.id, ada.id);
    expect(aiStep.accepted_events.map((event) => event.event_type)).toContain("DICE_ROLLED");
    expect(aiStep.accepted_events.map((event) => event.event_type)).not.toContain("PROPERTY_IMPROVEMENTS_SET");

    const stateAfterStep = await getJson<{
      state: { players: Array<{ cash: number; id: string }> };
    }>(baseUrl, `/games/${game.id}/state`);
    expect(stateAfterStep.state.players.find((player) => player.id === ada.id)?.cash).toBe(40);
  });

  it("applies debug starting property improvements to seeded game state", async () => {
    const baseUrl = await startMockApi();
    const game = await createGame(baseUrl, {
      seed: "stage-10-5-debug-starting-improvements",
      players: [
        { name: "Ada", kind: "ai" },
        { name: "Grace", kind: "ai" },
      ],
      settings: {
        player_colors: [
          { seat_order: 0, color: "#0f766e" },
          { seat_order: 1, color: "#7c3aed" },
        ],
        negotiation_cutoffs: {
          max_rounds: 8,
          max_proposals_per_player: 12,
        },
        debug_allocations: {
          property_owners: [
            { property_id: "property_mediterranean_avenue", seat_order: 0 },
            { property_id: "property_baltic_avenue", seat_order: 0 },
            { property_id: "property_boardwalk", seat_order: 1 },
          ],
          property_improvements: [
            { property_id: "property_mediterranean_avenue", houses: 3, hotel: false },
            { property_id: "property_boardwalk", houses: 0, hotel: true },
            { property_id: "property_reading_railroad", houses: 4, hotel: false },
          ],
        },
      },
    });
    const ada = game.players[0];
    const grace = game.players[1];

    const state = await getJson<{
      state: {
        bank_inventory: { hotels: number; houses: number };
        property_ownership: Array<{
          hotel?: boolean;
          hotels?: number;
          houses?: number;
          owner_id: string | null;
          property_id: string;
        }>;
      };
    }>(baseUrl, `/games/${game.id}/state`);

    expect(
      state.state.property_ownership.find((ownership) => ownership.property_id === "property_mediterranean_avenue"),
    ).toEqual(expect.objectContaining({ houses: 3, hotel: false, hotels: 0, owner_id: ada.id }));
    expect(
      state.state.property_ownership.find((ownership) => ownership.property_id === "property_boardwalk"),
    ).toEqual(expect.objectContaining({ houses: 0, hotel: true, hotels: 1, owner_id: grace.id }));
    expect(
      state.state.property_ownership.find((ownership) => ownership.property_id === "property_reading_railroad"),
    ).toEqual(expect.objectContaining({ houses: 0, hotel: false, hotels: 0, owner_id: null }));
    expect(state.state.bank_inventory).toEqual({ houses: 29, hotels: 11 });
  });

  it("applies debug starting player positions to seeded game state", async () => {
    const baseUrl = await startMockApi();
    const game = await createGame(baseUrl, {
      seed: "stage-10-5-debug-player-positions",
      players: [
        { name: "Ada", kind: "ai" },
        { name: "Grace", kind: "ai" },
      ],
      settings: {
        player_colors: [
          { seat_order: 0, color: "#0f766e" },
          { seat_order: 1, color: "#7c3aed" },
        ],
        negotiation_cutoffs: {
          max_rounds: 8,
          max_proposals_per_player: 12,
        },
        debug_allocations: {
          player_positions: [
            { seat_order: 0, position: 5 },
            { seat_order: 1, position: 39 },
          ],
        },
      },
    });

    const state = await getJson<{
      state: {
        players: Array<{ id: string; position?: number }>;
      };
    }>(baseUrl, `/games/${game.id}/state`);

    expect(state.state.players.find((player) => player.id === game.players[0].id)?.position).toBe(5);
    expect(state.state.players.find((player) => player.id === game.players[1].id)?.position).toBe(39);
  });

  it("applies a debug current player to seeded turn state", async () => {
    const baseUrl = await startMockApi();
    const game = await createGame(baseUrl, {
      seed: "stage-10-5-debug-current-player",
      players: [
        { name: "Ada", kind: "ai" },
        { name: "Grace", kind: "ai" },
        { name: "Lin", kind: "ai" },
      ],
      settings: {
        player_colors: [
          { seat_order: 0, color: "#0f766e" },
          { seat_order: 1, color: "#7c3aed" },
          { seat_order: 2, color: "#2563eb" },
        ],
        negotiation_cutoffs: {
          max_rounds: 8,
          max_proposals_per_player: 12,
        },
        debug_allocations: {
          current_player_seat_order: 2,
        },
      },
    });

    const state = await getJson<{
      state: {
        turn: { current_player_id: string | null; current_player_index: number };
      };
    }>(baseUrl, `/games/${game.id}/state`);

    expect(state.state.turn).toEqual(
      expect.objectContaining({
        current_player_id: game.players[2].id,
        current_player_index: 2,
      }),
    );
  });

  it("applies a debug turn phase so targeted purchase scenarios start immediately", async () => {
    const baseUrl = await startMockApi();
    const game = await createGame(baseUrl, {
      seed: "stage-10-5-debug-purchase-phase",
      players: [
        { name: "Ada", kind: "ai" },
        { name: "Grace", kind: "ai" },
      ],
      settings: {
        player_colors: [
          { seat_order: 0, color: "#0f766e" },
          { seat_order: 1, color: "#7c3aed" },
        ],
        negotiation_cutoffs: {
          max_rounds: 8,
          max_proposals_per_player: 12,
        },
        debug_allocations: {
          current_player_seat_order: 1,
          current_phase: "PURCHASE_OR_AUCTION",
          player_positions: [{ seat_order: 1, position: 5 }],
        },
      },
    });
    const grace = game.players[1];

    const state = await getJson<{
      state: {
        turn: { current_player_id: string | null; current_player_index: number; phase: string };
      };
    }>(baseUrl, `/games/${game.id}/state`);
    expect(state.state.turn).toEqual(
      expect.objectContaining({
        current_player_id: grace.id,
        current_player_index: 1,
        phase: "PURCHASE_OR_AUCTION",
      }),
    );

    const legalActions = await getJson<LegalActionsPayload>(
      baseUrl,
      `/games/${game.id}/legal-actions?actor_player_id=${encodeURIComponent(grace.id)}`,
    );
    expect(legalActions.legal_actions).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          type: "BUY_PROPERTY",
          payload: expect.objectContaining({ property_id: "property_reading_railroad" }),
        }),
        expect.objectContaining({
          type: "START_AUCTION",
          payload: expect.objectContaining({ property_id: "property_reading_railroad" }),
        }),
      ]),
    );
  });

  it("starts an auction instead of trying an unaffordable property purchase", async () => {
    const baseUrl = await startMockApi();
    const game = await createGame(baseUrl, {
      seed: "stage-10-5-debug-low-cash-purchase-auction",
      players: [
        { name: "Ada", kind: "ai" },
        { name: "Grace", kind: "ai" },
      ],
      settings: {
        player_colors: [
          { seat_order: 0, color: "#0f766e" },
          { seat_order: 1, color: "#7c3aed" },
        ],
        negotiation_cutoffs: {
          max_rounds: 8,
          max_proposals_per_player: 12,
        },
        debug_allocations: {
          current_phase: "PURCHASE_OR_AUCTION",
          player_cash: [
            { seat_order: 0, cash: 100 },
            { seat_order: 1, cash: 1500 },
          ],
          player_positions: [{ seat_order: 0, position: 39 }],
        },
      },
    });
    const ada = game.players[0];

    const legalActions = await getJson<LegalActionsPayload>(
      baseUrl,
      `/games/${game.id}/legal-actions?actor_player_id=${encodeURIComponent(ada.id)}`,
    );
    expect(legalActions.legal_actions).toEqual([
      expect.objectContaining({
        type: "START_AUCTION",
        payload: expect.objectContaining({ property_id: "property_boardwalk" }),
      }),
    ]);

    const aiStep = await stepAi(baseUrl, game.id, ada.id);

    expect(aiStep.status).toBe("accepted");
    expect(aiStep.accepted_events).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          event_type: "ACTIVE_AUCTION_SET",
          payload: expect.objectContaining({ active: true, property_id: "property_boardwalk" }),
        }),
      ]),
    );
  });

  it("starts an auction when a direct purchase would break the AI cash reserve", async () => {
    const baseUrl = await startMockApi();
    const game = await createGame(baseUrl, {
      seed: "stage-10-5-debug-reserve-purchase-auction",
      players: [
        { name: "Ada", kind: "ai" },
        { name: "Grace", kind: "ai" },
      ],
      settings: {
        player_colors: [
          { seat_order: 0, color: "#0f766e" },
          { seat_order: 1, color: "#7c3aed" },
        ],
        negotiation_cutoffs: {
          max_rounds: 8,
          max_proposals_per_player: 12,
        },
        debug_allocations: {
          current_phase: "PURCHASE_OR_AUCTION",
          player_cash: [
            { seat_order: 0, cash: 450 },
            { seat_order: 1, cash: 1500 },
          ],
          player_positions: [{ seat_order: 0, position: 39 }],
        },
      },
    });
    const ada = game.players[0];

    const legalActions = await getJson<LegalActionsPayload>(
      baseUrl,
      `/games/${game.id}/legal-actions?actor_player_id=${encodeURIComponent(ada.id)}`,
    );
    expect(legalActions.legal_actions).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          type: "BUY_PROPERTY",
          payload: expect.objectContaining({ price: 400, property_id: "property_boardwalk" }),
        }),
        expect.objectContaining({
          type: "START_AUCTION",
          payload: expect.objectContaining({ property_id: "property_boardwalk" }),
        }),
      ]),
    );

    const aiStep = await stepAi(baseUrl, game.id, ada.id);

    expect(aiStep.status).toBe("accepted");
    expect(aiStep.accepted_events).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          event_type: "ACTIVE_AUCTION_SET",
          payload: expect.objectContaining({ active: true, property_id: "property_boardwalk" }),
        }),
      ]),
    );
    expect(aiStep.accepted_events.map((event) => event.event_type)).not.toContain("PROPERTY_PURCHASED");
  });

  it("buys an unowned property directly when the AI can preserve its cash reserve", async () => {
    const baseUrl = await startMockApi();
    const game = await createGame(baseUrl, {
      seed: "stage-10-5-debug-reserve-direct-purchase",
      players: [
        { name: "Ada", kind: "ai" },
        { name: "Grace", kind: "ai" },
      ],
      settings: {
        player_colors: [
          { seat_order: 0, color: "#0f766e" },
          { seat_order: 1, color: "#7c3aed" },
        ],
        negotiation_cutoffs: {
          max_rounds: 8,
          max_proposals_per_player: 12,
        },
        debug_allocations: {
          current_phase: "PURCHASE_OR_AUCTION",
          player_cash: [
            { seat_order: 0, cash: 800 },
            { seat_order: 1, cash: 1500 },
          ],
          player_positions: [{ seat_order: 0, position: 39 }],
        },
      },
    });
    const ada = game.players[0];

    const aiStep = await stepAi(baseUrl, game.id, ada.id);

    expect(aiStep.status).toBe("accepted");
    expect(aiStep.accepted_events).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          event_type: "PROPERTY_PURCHASED",
          payload: expect.objectContaining({
            buyer_player_id: ada.id,
            price: 400,
            property_id: "property_boardwalk",
          }),
        }),
      ]),
    );
    expect(aiStep.accepted_events.map((event) => event.event_type)).not.toContain("ACTIVE_AUCTION_SET");
  });

  it("unmortgages a generic debug-allocated property before rolling when cash allows", async () => {
    const baseUrl = await startMockApi();
    const game = await createGame(baseUrl, {
      seed: "stage-10-5-debug-generic-unmortgage",
      players: [
        { name: "Ada", kind: "ai" },
        { name: "Grace", kind: "ai" },
      ],
      settings: {
        player_colors: [
          { seat_order: 0, color: "#0f766e" },
          { seat_order: 1, color: "#7c3aed" },
        ],
        negotiation_cutoffs: {
          max_rounds: 8,
          max_proposals_per_player: 12,
        },
        debug_allocations: {
          player_cash: [
            { seat_order: 0, cash: 1200 },
            { seat_order: 1, cash: 1500 },
          ],
          property_owners: [{ property_id: "property_tennessee_avenue", seat_order: 0 }],
          property_mortgages: [{ property_id: "property_tennessee_avenue", mortgaged: true }],
        },
      },
    });
    const ada = game.players[0];

    const legalActions = await getJson<LegalActionsPayload>(
      baseUrl,
      `/games/${game.id}/legal-actions?actor_player_id=${encodeURIComponent(ada.id)}`,
    );
    expect(legalActions.legal_actions).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          type: "UNMORTGAGE_PROPERTY",
          payload: expect.objectContaining({ cost: 99, property_id: "property_tennessee_avenue" }),
        }),
      ]),
    );

    const aiStep = await stepAi(baseUrl, game.id, ada.id);

    expect(aiStep.accepted_events.map((event) => event.event_type)).not.toContain("DICE_ROLLED");
    expect(aiStep.accepted_events).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          event_type: "PLAYER_CASH_DELTA",
          payload: expect.objectContaining({ amount: -99, player_id: ada.id }),
        }),
        expect.objectContaining({
          event_type: "PROPERTY_MORTGAGE_SET",
          payload: expect.objectContaining({ mortgaged: false, property_id: "property_tennessee_avenue" }),
        }),
      ]),
    );

    const stateAfterUnmortgage = await getJson<{
      state: {
        players: Array<{ cash: number; id: string }>;
        property_ownership: Array<{ mortgaged?: boolean; property_id: string }>;
      };
    }>(baseUrl, `/games/${game.id}/state`);
    expect(stateAfterUnmortgage.state.players.find((player) => player.id === ada.id)?.cash).toBe(1101);
    expect(
      stateAfterUnmortgage.state.property_ownership.find(
        (ownership) => ownership.property_id === "property_tennessee_avenue",
      ),
    ).toEqual(expect.objectContaining({ mortgaged: false }));
  });

  it("uses targeted trade opportunities when opening mock AI debug negotiations", async () => {
    const baseUrl = await startMockApi();
    const game = await createGame(baseUrl, {
      seed: "stage-10-5-debug-near-monopoly-negotiation",
      players: [
        { name: "Ada", kind: "ai" },
        { name: "Grace", kind: "ai" },
        { name: "Lin", kind: "ai" },
      ],
      settings: {
        player_colors: [
          { seat_order: 0, color: "#0f766e" },
          { seat_order: 1, color: "#7c3aed" },
          { seat_order: 2, color: "#2563eb" },
        ],
        negotiation_cutoffs: {
          max_rounds: 8,
          max_proposals_per_player: 12,
        },
        debug_allocations: {
          property_owners: [
            { property_id: "property_st_james_place", seat_order: 0 },
            { property_id: "property_new_york_avenue", seat_order: 0 },
            { property_id: "property_tennessee_avenue", seat_order: 2 },
          ],
        },
      },
    });
    const ada = game.players[0];
    const lin = game.players[2];

    const result = await openNegotiationAi(baseUrl, game.id, ada.id, {
      kind: "complete_street_group",
      group: "orange",
      group_name: "Orange",
      property_group_kind: "street",
      actor_owned_property_ids: ["property_st_james_place", "property_new_york_avenue"],
      actor_owned_property_names: ["St. James Place", "New York Avenue"],
      target_property_id: "property_tennessee_avenue",
      target_property_name: "Tennessee Avenue",
      target_owner_id: lin.id,
      target_owner_name: lin.name,
      participants: [ada.id, lin.id],
      strategic_reason: "Completing Orange unlocks development and materially raises rent pressure.",
    });

    expect(result.status).toBe("done");
    expect(result.negotiation).toEqual(
      expect.objectContaining({
        participant_player_ids: [ada.id, lin.id],
        topic: "Trade for Tennessee Avenue to complete Orange",
        context: expect.stringContaining("Completing Orange unlocks development"),
      }),
    );

    const negotiations = await getJson<{ negotiations: Array<{ participant_player_ids: string[]; topic: string }> }>(
      baseUrl,
      `/games/${game.id}/negotiations`,
    );
    expect(negotiations.negotiations[0]).toEqual(
      expect.objectContaining({
        participant_player_ids: [ada.id, lin.id],
        topic: "Trade for Tennessee Avenue to complete Orange",
      }),
    );
  });

  it("proposes a targeted cash-for-property deal from a mock AI debug negotiation", async () => {
    const baseUrl = await startMockApi();
    const game = await createGame(baseUrl, {
      seed: "stage-10-5-debug-near-monopoly-deal-proposal",
      players: [
        { name: "Ada", kind: "ai" },
        { name: "Grace", kind: "ai" },
        { name: "Lin", kind: "ai" },
      ],
      settings: {
        player_colors: [
          { seat_order: 0, color: "#0f766e" },
          { seat_order: 1, color: "#7c3aed" },
          { seat_order: 2, color: "#2563eb" },
        ],
        negotiation_cutoffs: {
          max_rounds: 8,
          max_proposals_per_player: 12,
        },
        debug_allocations: {
          property_owners: [
            { property_id: "property_st_james_place", seat_order: 0 },
            { property_id: "property_new_york_avenue", seat_order: 0 },
            { property_id: "property_tennessee_avenue", seat_order: 2 },
          ],
        },
      },
    });
    const ada = game.players[0];
    const lin = game.players[2];

    const opened = await openNegotiationAi(baseUrl, game.id, ada.id, {
      kind: "complete_street_group",
      group: "orange",
      group_name: "Orange",
      property_group_kind: "street",
      actor_owned_property_ids: ["property_st_james_place", "property_new_york_avenue"],
      actor_owned_property_names: ["St. James Place", "New York Avenue"],
      target_property_id: "property_tennessee_avenue",
      target_property_name: "Tennessee Avenue",
      target_owner_id: lin.id,
      target_owner_name: lin.name,
      participants: [ada.id, lin.id],
      strategic_reason: "Completing Orange unlocks development and materially raises rent pressure.",
    });

    const proposed = await proposeDealAi(baseUrl, game.id, ada.id, opened.negotiation?.id ?? "");

    expect(proposed.status).toBe("done");
    expect(proposed.deal?.terms).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          kind: "immediate_cash_transfer",
          from_player_id: ada.id,
          to_player_id: lin.id,
          amount: 220,
        }),
        expect.objectContaining({
          kind: "immediate_property_transfer",
          from_player_id: lin.id,
          to_player_id: ada.id,
          property_id: "property_tennessee_avenue",
        }),
      ]),
    );
    expect(proposed.deal?.terms).not.toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          kind: "rent_share",
          property_id: "property_reading_railroad",
        }),
      ]),
    );

    const negotiationsAfterProposal = await getJson<{
      negotiations: Array<{ current_deal_id?: string | null; id: string; status: string }>;
    }>(baseUrl, `/games/${game.id}/negotiations`);
    expect(negotiationsAfterProposal.negotiations.find((negotiation) => negotiation.id === opened.negotiation?.id)).toEqual(
      expect.objectContaining({
        current_deal_id: proposed.deal?.id,
        status: "active",
      }),
    );

    const accepted = await acceptRejectAi(baseUrl, game.id, lin.id, opened.negotiation?.id ?? "", proposed.deal?.id ?? "");
    expect(accepted.deal).toEqual(expect.objectContaining({ id: proposed.deal?.id, status: "accepted" }));

    const executed = await postJson<{ negotiation: { status: string }; status: string }>(
      baseUrl,
      `/games/${game.id}/negotiations/${opened.negotiation?.id ?? ""}/execute`,
      {},
    );
    expect(executed).toEqual(
      expect.objectContaining({
        status: "ok",
        negotiation: expect.objectContaining({ status: "executed" }),
      }),
    );

    const stateAfterExecution = await getJson<{
      state: {
        players: Array<{ cash: number; id: string }>;
        property_ownership: Array<{ owner_id: string | null; property_id: string }>;
      };
    }>(baseUrl, `/games/${game.id}/state`);
    expect(stateAfterExecution.state.property_ownership).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ owner_id: ada.id, property_id: "property_tennessee_avenue" }),
      ]),
    );
    expect(stateAfterExecution.state.players.find((player) => player.id === ada.id)?.cash).toBe(1280);
    expect(stateAfterExecution.state.players.find((player) => player.id === lin.id)?.cash).toBe(1720);

    const developmentStep = await stepAi(baseUrl, game.id, ada.id);

    expect(developmentStep.status).toBe("accepted");
    expect(developmentStep.accepted_events.map((event) => event.event_type)).not.toContain("DICE_ROLLED");
    expect(developmentStep.accepted_events).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          event_type: "PROPERTY_IMPROVEMENTS_SET",
          payload: expect.objectContaining({
            property_id: "property_new_york_avenue",
            houses: 1,
            hotel: false,
          }),
        }),
      ]),
    );
  });

  it("counters an overpriced targeted property deal and retires the old proposal", async () => {
    const baseUrl = await startMockApi();
    const game = await createGame(baseUrl, {
      seed: "stage-10-5-debug-overpriced-counteroffer",
      players: [
        { name: "Ada", kind: "ai" },
        { name: "Grace", kind: "ai" },
        { name: "Lin", kind: "ai" },
      ],
      settings: {
        player_colors: [
          { seat_order: 0, color: "#0f766e" },
          { seat_order: 1, color: "#7c3aed" },
          { seat_order: 2, color: "#2563eb" },
        ],
        negotiation_cutoffs: {
          max_rounds: 8,
          max_proposals_per_player: 12,
        },
        debug_allocations: {
          property_owners: [
            { property_id: "property_st_james_place", seat_order: 0 },
            { property_id: "property_new_york_avenue", seat_order: 0 },
            { property_id: "property_tennessee_avenue", seat_order: 2 },
          ],
        },
      },
    });
    const ada = game.players[0];
    const lin = game.players[2];
    const opened = await openNegotiationAi(baseUrl, game.id, ada.id, {
      kind: "complete_street_group",
      group: "orange",
      group_name: "Orange",
      property_group_kind: "street",
      actor_owned_property_ids: ["property_st_james_place", "property_new_york_avenue"],
      actor_owned_property_names: ["St. James Place", "New York Avenue"],
      target_property_id: "property_tennessee_avenue",
      target_property_name: "Tennessee Avenue",
      target_owner_id: lin.id,
      target_owner_name: lin.name,
      participants: [ada.id, lin.id],
      strategic_reason: "Completing Orange unlocks development and materially raises rent pressure.",
    });

    const overpriced = await createDeal(baseUrl, game.id, {
      negotiation_id: opened.negotiation?.id,
      proposer_player_id: lin.id,
      participant_player_ids: [ada.id, lin.id],
      parent_deal_id: null,
      terms: [
        {
          kind: "immediate_cash_transfer",
          from_player_id: ada.id,
          to_player_id: lin.id,
          amount: 400,
        },
        {
          kind: "immediate_property_transfer",
          from_player_id: lin.id,
          to_player_id: ada.id,
          property_id: "property_tennessee_avenue",
        },
      ],
    });

    const countered = await counterofferAi(baseUrl, game.id, ada.id, opened.negotiation?.id ?? "", overpriced.deal.id);

    expect(countered.status).toBe("done");
    expect(countered.deal).toEqual(
      expect.objectContaining({
        parent_deal_id: overpriced.deal.id,
        status: "proposed",
      }),
    );
    expect(countered.deal?.terms).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          kind: "immediate_cash_transfer",
          from_player_id: ada.id,
          to_player_id: lin.id,
          amount: 220,
        }),
      ]),
    );

    const dealsAfterCounter = await getJson<{
      deals: Array<{ id: string; parent_deal_id?: string | null; status: string }>;
    }>(baseUrl, `/games/${game.id}/deals`);
    expect(dealsAfterCounter.deals.find((deal) => deal.id === overpriced.deal.id)).toEqual(
      expect.objectContaining({ status: "rejected" }),
    );
    expect(dealsAfterCounter.deals.find((deal) => deal.id === countered.deal?.id)).toEqual(
      expect.objectContaining({ parent_deal_id: overpriced.deal.id, status: "proposed" }),
    );

    const accepted = await acceptRejectAi(baseUrl, game.id, lin.id, opened.negotiation?.id ?? "", countered.deal?.id ?? "");
    expect(accepted.deal).toEqual(
      expect.objectContaining({
        id: countered.deal?.id,
        status: "accepted",
      }),
    );
  });

  it("rejects an overpriced targeted deal when accept/reject is requested", async () => {
    const baseUrl = await startMockApi();
    const game = await createGame(baseUrl, {
      seed: "stage-10-5-debug-overpriced-accept-reject",
      players: [
        { name: "Ada", kind: "ai" },
        { name: "Grace", kind: "ai" },
        { name: "Lin", kind: "ai" },
      ],
      settings: {
        player_colors: [
          { seat_order: 0, color: "#0f766e" },
          { seat_order: 1, color: "#7c3aed" },
          { seat_order: 2, color: "#2563eb" },
        ],
        negotiation_cutoffs: {
          max_rounds: 8,
          max_proposals_per_player: 12,
        },
        debug_allocations: {
          property_owners: [
            { property_id: "property_st_james_place", seat_order: 0 },
            { property_id: "property_new_york_avenue", seat_order: 0 },
            { property_id: "property_tennessee_avenue", seat_order: 2 },
          ],
        },
      },
    });
    const ada = game.players[0];
    const lin = game.players[2];
    const opened = await openNegotiationAi(baseUrl, game.id, ada.id, {
      kind: "complete_street_group",
      group: "orange",
      group_name: "Orange",
      property_group_kind: "street",
      actor_owned_property_ids: ["property_st_james_place", "property_new_york_avenue"],
      actor_owned_property_names: ["St. James Place", "New York Avenue"],
      target_property_id: "property_tennessee_avenue",
      target_property_name: "Tennessee Avenue",
      target_owner_id: lin.id,
      target_owner_name: lin.name,
      participants: [ada.id, lin.id],
      strategic_reason: "Completing Orange unlocks development and materially raises rent pressure.",
    });
    const overpriced = await createDeal(baseUrl, game.id, {
      negotiation_id: opened.negotiation?.id,
      proposer_player_id: lin.id,
      participant_player_ids: [ada.id, lin.id],
      parent_deal_id: null,
      terms: [
        {
          kind: "immediate_cash_transfer",
          from_player_id: ada.id,
          to_player_id: lin.id,
          amount: 400,
        },
        {
          kind: "immediate_property_transfer",
          from_player_id: lin.id,
          to_player_id: ada.id,
          property_id: "property_tennessee_avenue",
        },
      ],
    });

    const rejected = await acceptRejectAi(baseUrl, game.id, ada.id, opened.negotiation?.id ?? "", overpriced.deal.id);

    expect(rejected.status).toBe("done");
    expect(rejected.outcome).toEqual(expect.objectContaining({ decision: "reject", deal_id: overpriced.deal.id }));
    expect(rejected.deal).toEqual(expect.objectContaining({ id: overpriced.deal.id, status: "rejected" }));

    const stateAfterRejection = await getJson<{
      state: {
        players: Array<{ cash: number; id: string }>;
        property_ownership: Array<{ owner_id: string | null; property_id: string }>;
      };
    }>(baseUrl, `/games/${game.id}/state`);
    expect(stateAfterRejection.state.property_ownership).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ owner_id: lin.id, property_id: "property_tennessee_avenue" }),
      ]),
    );
    expect(stateAfterRejection.state.players.find((player) => player.id === ada.id)?.cash).toBe(1500);
    expect(stateAfterRejection.state.players.find((player) => player.id === lin.id)?.cash).toBe(1500);
  });

  it("rejects an underpriced offer that would complete the buyer monopoly", async () => {
    const baseUrl = await startMockApi();
    const game = await createGame(baseUrl, {
      seed: "stage-10-5-debug-underpriced-seller-protection",
      players: [
        { name: "Ada", kind: "ai" },
        { name: "Grace", kind: "ai" },
        { name: "Lin", kind: "ai" },
      ],
      settings: {
        player_colors: [
          { seat_order: 0, color: "#0f766e" },
          { seat_order: 1, color: "#7c3aed" },
          { seat_order: 2, color: "#2563eb" },
        ],
        negotiation_cutoffs: {
          max_rounds: 8,
          max_proposals_per_player: 12,
        },
        debug_allocations: {
          property_owners: [
            { property_id: "property_st_james_place", seat_order: 0 },
            { property_id: "property_new_york_avenue", seat_order: 0 },
            { property_id: "property_tennessee_avenue", seat_order: 2 },
          ],
        },
      },
    });
    const ada = game.players[0];
    const lin = game.players[2];
    const opened = await openNegotiationAi(baseUrl, game.id, ada.id, {
      kind: "complete_street_group",
      group: "orange",
      group_name: "Orange",
      property_group_kind: "street",
      actor_owned_property_ids: ["property_st_james_place", "property_new_york_avenue"],
      actor_owned_property_names: ["St. James Place", "New York Avenue"],
      target_property_id: "property_tennessee_avenue",
      target_property_name: "Tennessee Avenue",
      target_owner_id: lin.id,
      target_owner_name: lin.name,
      participants: [ada.id, lin.id],
      strategic_reason: "Completing Orange unlocks development and materially raises rent pressure.",
    });
    const underpriced = await createDeal(baseUrl, game.id, {
      negotiation_id: opened.negotiation?.id,
      proposer_player_id: ada.id,
      participant_player_ids: [ada.id, lin.id],
      parent_deal_id: null,
      terms: [
        {
          kind: "immediate_cash_transfer",
          from_player_id: ada.id,
          to_player_id: lin.id,
          amount: 120,
        },
        {
          kind: "immediate_property_transfer",
          from_player_id: lin.id,
          to_player_id: ada.id,
          property_id: "property_tennessee_avenue",
        },
      ],
    });

    const rejected = await acceptRejectAi(baseUrl, game.id, lin.id, opened.negotiation?.id ?? "", underpriced.deal.id);

    expect(rejected.status).toBe("done");
    expect(rejected.outcome).toEqual(
      expect.objectContaining({
        decision: "reject",
        reason: expect.stringContaining("below strategic floor"),
      }),
    );
    expect(rejected.deal).toEqual(expect.objectContaining({ id: underpriced.deal.id, status: "rejected" }));

    const stateAfterRejection = await getJson<{
      state: {
        players: Array<{ cash: number; id: string }>;
        property_ownership: Array<{ owner_id: string | null; property_id: string }>;
      };
    }>(baseUrl, `/games/${game.id}/state`);
    expect(stateAfterRejection.state.property_ownership).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ owner_id: lin.id, property_id: "property_tennessee_avenue" }),
      ]),
    );
    expect(stateAfterRejection.state.players.find((player) => player.id === ada.id)?.cash).toBe(1500);
    expect(stateAfterRejection.state.players.find((player) => player.id === lin.id)?.cash).toBe(1500);
  });

  it("rejects an underpriced offer that weakens the seller near-monopoly", async () => {
    const baseUrl = await startMockApi();
    const game = await createGame(baseUrl, {
      seed: "stage-10-5-debug-underpriced-seller-group-protection",
      players: [
        { name: "Ada", kind: "ai" },
        { name: "Grace", kind: "ai" },
        { name: "Lin", kind: "ai" },
      ],
      settings: {
        player_colors: [
          { seat_order: 0, color: "#0f766e" },
          { seat_order: 1, color: "#7c3aed" },
          { seat_order: 2, color: "#2563eb" },
        ],
        negotiation_cutoffs: {
          max_rounds: 8,
          max_proposals_per_player: 12,
        },
        debug_allocations: {
          property_owners: [
            { property_id: "property_st_james_place", seat_order: 2 },
            { property_id: "property_tennessee_avenue", seat_order: 2 },
          ],
        },
      },
    });
    const ada = game.players[0];
    const lin = game.players[2];
    const opened = await openNegotiationAi(baseUrl, game.id, ada.id, {
      kind: "block_opponent_street_group",
      group: "orange",
      group_name: "Orange",
      property_group_kind: "street",
      actor_owned_property_ids: [],
      actor_owned_property_names: [],
      opponent_player_id: lin.id,
      opponent_player_name: lin.name,
      opponent_owned_property_ids: ["property_st_james_place", "property_tennessee_avenue"],
      opponent_owned_property_names: ["St. James Place", "Tennessee Avenue"],
      target_property_id: "property_tennessee_avenue",
      target_property_name: "Tennessee Avenue",
      target_owner_id: lin.id,
      target_owner_name: lin.name,
      participants: [ada.id, lin.id],
      strategic_reason: "Buying Tennessee Avenue blocks Lin's Orange progress.",
    });
    const underpriced = await createDeal(baseUrl, game.id, {
      negotiation_id: opened.negotiation?.id,
      proposer_player_id: ada.id,
      participant_player_ids: [ada.id, lin.id],
      parent_deal_id: null,
      terms: [
        {
          kind: "immediate_cash_transfer",
          from_player_id: ada.id,
          to_player_id: lin.id,
          amount: 190,
        },
        {
          kind: "immediate_property_transfer",
          from_player_id: lin.id,
          to_player_id: ada.id,
          property_id: "property_tennessee_avenue",
        },
      ],
    });

    const rejected = await acceptRejectAi(baseUrl, game.id, lin.id, opened.negotiation?.id ?? "", underpriced.deal.id);

    expect(rejected.status).toBe("done");
    expect(rejected.outcome).toEqual(
      expect.objectContaining({
        decision: "reject",
        reason: expect.stringContaining("seller group floor"),
      }),
    );
    expect(rejected.deal).toEqual(expect.objectContaining({ id: underpriced.deal.id, status: "rejected" }));

    const stateAfterRejection = await getJson<{
      state: {
        players: Array<{ cash: number; id: string }>;
        property_ownership: Array<{ owner_id: string | null; property_id: string }>;
      };
    }>(baseUrl, `/games/${game.id}/state`);
    expect(stateAfterRejection.state.property_ownership).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ owner_id: lin.id, property_id: "property_tennessee_avenue" }),
      ]),
    );
    expect(stateAfterRejection.state.players.find((player) => player.id === ada.id)?.cash).toBe(1500);
    expect(stateAfterRejection.state.players.find((player) => player.id === lin.id)?.cash).toBe(1500);
  });

  it("rejects a cash offer when the buyer cannot pay the proposed amount", async () => {
    const baseUrl = await startMockApi();
    const game = await createGame(baseUrl, {
      seed: "stage-10-5-debug-unpayable-cash-offer",
      players: [
        { name: "Ada", kind: "ai" },
        { name: "Grace", kind: "ai" },
        { name: "Lin", kind: "ai" },
      ],
      settings: {
        player_colors: [
          { seat_order: 0, color: "#0f766e" },
          { seat_order: 1, color: "#7c3aed" },
          { seat_order: 2, color: "#2563eb" },
        ],
        negotiation_cutoffs: {
          max_rounds: 8,
          max_proposals_per_player: 12,
        },
        debug_allocations: {
          player_cash: [
            { seat_order: 0, cash: 100 },
            { seat_order: 1, cash: 1500 },
            { seat_order: 2, cash: 1500 },
          ],
          property_owners: [{ property_id: "property_reading_railroad", seat_order: 2 }],
        },
      },
    });
    const ada = game.players[0];
    const lin = game.players[2];
    const opened = await openNegotiationAi(baseUrl, game.id, ada.id, {
      kind: "complete_railroad_group",
      group: "railroad",
      group_name: "Railroads",
      property_group_kind: "railroad",
      actor_owned_property_ids: [],
      actor_owned_property_names: [],
      target_property_id: "property_reading_railroad",
      target_property_name: "Reading Railroad",
      target_owner_id: lin.id,
      target_owner_name: lin.name,
      participants: [ada.id, lin.id],
      strategic_reason: "Ada wants Reading Railroad but must not make an impossible cash promise.",
    });
    const unpayable = await createDeal(baseUrl, game.id, {
      negotiation_id: opened.negotiation?.id,
      proposer_player_id: ada.id,
      participant_player_ids: [ada.id, lin.id],
      parent_deal_id: null,
      terms: [
        {
          kind: "immediate_cash_transfer",
          from_player_id: ada.id,
          to_player_id: lin.id,
          amount: 500,
        },
        {
          kind: "immediate_property_transfer",
          from_player_id: lin.id,
          to_player_id: ada.id,
          property_id: "property_reading_railroad",
        },
      ],
    });

    const rejected = await acceptRejectAi(baseUrl, game.id, lin.id, opened.negotiation?.id ?? "", unpayable.deal.id);

    expect(rejected.status).toBe("done");
    expect(rejected.outcome).toEqual(
      expect.objectContaining({
        decision: "reject",
        reason: expect.stringContaining("cannot pay"),
      }),
    );
    expect(rejected.deal).toEqual(expect.objectContaining({ id: unpayable.deal.id, status: "rejected" }));

    const stateAfterRejection = await getJson<{
      state: {
        players: Array<{ cash: number; id: string }>;
        property_ownership: Array<{ owner_id: string | null; property_id: string }>;
      };
    }>(baseUrl, `/games/${game.id}/state`);
    expect(stateAfterRejection.state.property_ownership).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ owner_id: lin.id, property_id: "property_reading_railroad" }),
      ]),
    );
    expect(stateAfterRejection.state.players.find((player) => player.id === ada.id)?.cash).toBe(100);
    expect(stateAfterRejection.state.players.find((player) => player.id === lin.id)?.cash).toBe(1500);
  });

  it("refuses to execute an accepted negotiation when a payer no longer has the cash", async () => {
    const baseUrl = await startMockApi();
    const game = await createGame(baseUrl, {
      seed: "stage-10-5-debug-unpayable-execute",
      players: [
        { name: "Ada", kind: "ai" },
        { name: "Grace", kind: "ai" },
        { name: "Lin", kind: "ai" },
      ],
      settings: {
        player_colors: [
          { seat_order: 0, color: "#0f766e" },
          { seat_order: 1, color: "#7c3aed" },
          { seat_order: 2, color: "#2563eb" },
        ],
        negotiation_cutoffs: {
          max_rounds: 8,
          max_proposals_per_player: 12,
        },
        debug_allocations: {
          player_cash: [
            { seat_order: 0, cash: 100 },
            { seat_order: 1, cash: 1500 },
            { seat_order: 2, cash: 1500 },
          ],
          property_owners: [{ property_id: "property_reading_railroad", seat_order: 2 }],
        },
      },
    });
    const ada = game.players[0];
    const lin = game.players[2];
    const opened = await openNegotiationAi(baseUrl, game.id, ada.id, {
      kind: "complete_railroad_group",
      group: "railroad",
      group_name: "Railroads",
      property_group_kind: "railroad",
      actor_owned_property_ids: [],
      actor_owned_property_names: [],
      target_property_id: "property_reading_railroad",
      target_property_name: "Reading Railroad",
      target_owner_id: lin.id,
      target_owner_name: lin.name,
      participants: [ada.id, lin.id],
      strategic_reason: "Ada wants Reading Railroad but cannot pay this deal.",
    });
    const unpayable = await createDeal(baseUrl, game.id, {
      negotiation_id: opened.negotiation?.id,
      proposer_player_id: ada.id,
      participant_player_ids: [ada.id, lin.id],
      parent_deal_id: null,
      terms: [
        {
          kind: "immediate_cash_transfer",
          from_player_id: ada.id,
          to_player_id: lin.id,
          amount: 500,
        },
        {
          kind: "immediate_property_transfer",
          from_player_id: lin.id,
          to_player_id: ada.id,
          property_id: "property_reading_railroad",
        },
      ],
    });
    await postJson<{ deal: { id: string; status: string }; status: string }>(
      baseUrl,
      `/games/${game.id}/deals/${unpayable.deal.id}/accept`,
      {},
    );

    const rejectedExecution = await postJsonRejected<{
      reason_code: string;
      validation_errors: Array<{ message: string }>;
    }>(baseUrl, `/games/${game.id}/negotiations/${opened.negotiation?.id ?? ""}/execute`, {}, 409);

    expect(rejectedExecution).toEqual(
      expect.objectContaining({
        reason_code: "insufficient_cash_for_deal",
      }),
    );
    expect(rejectedExecution.validation_errors[0]?.message).toContain("cannot pay");

    const stateAfterRejection = await getJson<{
      state: {
        players: Array<{ cash: number; id: string }>;
        property_ownership: Array<{ owner_id: string | null; property_id: string }>;
      };
    }>(baseUrl, `/games/${game.id}/state`);
    expect(stateAfterRejection.state.property_ownership).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ owner_id: lin.id, property_id: "property_reading_railroad" }),
      ]),
    );
    expect(stateAfterRejection.state.players.find((player) => player.id === ada.id)?.cash).toBe(100);
    expect(stateAfterRejection.state.players.find((player) => player.id === lin.id)?.cash).toBe(1500);
  });

  it("rejects a cash offer that would break the seller's completed monopoly", async () => {
    const baseUrl = await startMockApi();
    const game = await createGame(baseUrl, {
      seed: "stage-10-5-debug-completed-seller-monopoly-protection",
      players: [
        { name: "Ada", kind: "ai" },
        { name: "Grace", kind: "ai" },
        { name: "Lin", kind: "ai" },
      ],
      settings: {
        player_colors: [
          { seat_order: 0, color: "#0f766e" },
          { seat_order: 1, color: "#7c3aed" },
          { seat_order: 2, color: "#2563eb" },
        ],
        negotiation_cutoffs: {
          max_rounds: 8,
          max_proposals_per_player: 12,
        },
        debug_allocations: {
          property_owners: [
            { property_id: "property_st_james_place", seat_order: 2 },
            { property_id: "property_tennessee_avenue", seat_order: 2 },
            { property_id: "property_new_york_avenue", seat_order: 2 },
          ],
        },
      },
    });
    const ada = game.players[0];
    const lin = game.players[2];
    const opened = await openNegotiationAi(baseUrl, game.id, ada.id, {
      kind: "block_opponent_street_group",
      group: "orange",
      group_name: "Orange",
      property_group_kind: "street",
      actor_owned_property_ids: [],
      actor_owned_property_names: [],
      opponent_player_id: lin.id,
      opponent_player_name: lin.name,
      opponent_owned_property_ids: [
        "property_st_james_place",
        "property_tennessee_avenue",
        "property_new_york_avenue",
      ],
      opponent_owned_property_names: ["St. James Place", "Tennessee Avenue", "New York Avenue"],
      target_property_id: "property_tennessee_avenue",
      target_property_name: "Tennessee Avenue",
      target_owner_id: lin.id,
      target_owner_name: lin.name,
      participants: [ada.id, lin.id],
      strategic_reason: "Buying Tennessee Avenue would dismantle Lin's completed Orange monopoly.",
    });
    const offer = await createDeal(baseUrl, game.id, {
      negotiation_id: opened.negotiation?.id,
      proposer_player_id: ada.id,
      participant_player_ids: [ada.id, lin.id],
      parent_deal_id: null,
      terms: [
        {
          kind: "immediate_cash_transfer",
          from_player_id: ada.id,
          to_player_id: lin.id,
          amount: 400,
        },
        {
          kind: "immediate_property_transfer",
          from_player_id: lin.id,
          to_player_id: ada.id,
          property_id: "property_tennessee_avenue",
        },
      ],
    });

    const rejected = await acceptRejectAi(baseUrl, game.id, lin.id, opened.negotiation?.id ?? "", offer.deal.id);

    expect(rejected.status).toBe("done");
    expect(rejected.outcome).toEqual(
      expect.objectContaining({
        decision: "reject",
        reason: expect.stringContaining("seller group floor"),
      }),
    );
    expect(rejected.deal).toEqual(expect.objectContaining({ id: offer.deal.id, status: "rejected" }));

    const stateAfterRejection = await getJson<{
      state: {
        players: Array<{ cash: number; id: string }>;
        property_ownership: Array<{ owner_id: string | null; property_id: string }>;
      };
    }>(baseUrl, `/games/${game.id}/state`);
    expect(stateAfterRejection.state.property_ownership).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ owner_id: lin.id, property_id: "property_tennessee_avenue" }),
      ]),
    );
    expect(stateAfterRejection.state.players.find((player) => player.id === ada.id)?.cash).toBe(1500);
    expect(stateAfterRejection.state.players.find((player) => player.id === lin.id)?.cash).toBe(1500);
  });

  it("rejects a cash offer for a property from an improved seller color group", async () => {
    const baseUrl = await startMockApi();
    const game = await createGame(baseUrl, {
      seed: "stage-10-5-debug-improved-seller-group-protection",
      players: [
        { name: "Ada", kind: "ai" },
        { name: "Grace", kind: "ai" },
        { name: "Lin", kind: "ai" },
      ],
      settings: {
        player_colors: [
          { seat_order: 0, color: "#0f766e" },
          { seat_order: 1, color: "#7c3aed" },
          { seat_order: 2, color: "#2563eb" },
        ],
        negotiation_cutoffs: {
          max_rounds: 8,
          max_proposals_per_player: 12,
        },
        debug_allocations: {
          property_owners: [
            { property_id: "property_st_james_place", seat_order: 2 },
            { property_id: "property_tennessee_avenue", seat_order: 2 },
            { property_id: "property_new_york_avenue", seat_order: 2 },
          ],
          property_improvements: [{ property_id: "property_new_york_avenue", houses: 1, hotel: false }],
        },
      },
    });
    const ada = game.players[0];
    const lin = game.players[2];
    const opened = await openNegotiationAi(baseUrl, game.id, ada.id, {
      kind: "block_opponent_street_group",
      group: "orange",
      group_name: "Orange",
      property_group_kind: "street",
      actor_owned_property_ids: [],
      actor_owned_property_names: [],
      opponent_player_id: lin.id,
      opponent_player_name: lin.name,
      opponent_owned_property_ids: [
        "property_st_james_place",
        "property_tennessee_avenue",
        "property_new_york_avenue",
      ],
      opponent_owned_property_names: ["St. James Place", "Tennessee Avenue", "New York Avenue"],
      target_property_id: "property_tennessee_avenue",
      target_property_name: "Tennessee Avenue",
      target_owner_id: lin.id,
      target_owner_name: lin.name,
      participants: [ada.id, lin.id],
      strategic_reason: "Buying Tennessee Avenue would dismantle an improved Orange monopoly.",
    });
    const offer = await createDeal(baseUrl, game.id, {
      negotiation_id: opened.negotiation?.id,
      proposer_player_id: ada.id,
      participant_player_ids: [ada.id, lin.id],
      parent_deal_id: null,
      terms: [
        {
          kind: "immediate_cash_transfer",
          from_player_id: ada.id,
          to_player_id: lin.id,
          amount: 900,
        },
        {
          kind: "immediate_property_transfer",
          from_player_id: lin.id,
          to_player_id: ada.id,
          property_id: "property_tennessee_avenue",
        },
      ],
    });

    const rejected = await acceptRejectAi(baseUrl, game.id, lin.id, opened.negotiation?.id ?? "", offer.deal.id);

    expect(rejected.status).toBe("done");
    expect(rejected.outcome).toEqual(
      expect.objectContaining({
        decision: "reject",
        reason: expect.stringContaining("improvements"),
      }),
    );
    expect(rejected.deal).toEqual(expect.objectContaining({ id: offer.deal.id, status: "rejected" }));

    const stateAfterRejection = await getJson<{
      state: {
        players: Array<{ cash: number; id: string }>;
        property_ownership: Array<{ houses?: number; owner_id: string | null; property_id: string }>;
      };
    }>(baseUrl, `/games/${game.id}/state`);
    expect(stateAfterRejection.state.property_ownership).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ owner_id: lin.id, property_id: "property_tennessee_avenue" }),
        expect.objectContaining({ houses: 1, owner_id: lin.id, property_id: "property_new_york_avenue" }),
      ]),
    );
    expect(stateAfterRejection.state.players.find((player) => player.id === ada.id)?.cash).toBe(1500);
    expect(stateAfterRejection.state.players.find((player) => player.id === lin.id)?.cash).toBe(1500);
  });
});
