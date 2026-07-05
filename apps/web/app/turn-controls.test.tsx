import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { GamePlaySurface } from "./game-play-surface";
import type { GameMetadata } from "../lib/api/games";

const createdAt = "2026-07-04T00:00:00.000Z";
const apiBaseUrl = "http://api.test";
const gameId = "game-turn-controls";
const adaId = "player-1";
const graceId = "player-2";

type FetchMock = ReturnType<typeof vi.fn<typeof fetch>>;

function gameFixture(position = 0): GameMetadata {
  return {
    id: gameId,
    status: "active",
    ruleset_version: "classic-v1",
    seed: "turn-controls",
    current_phase: "START_TURN",
    settings: {
      player_colors: [
        { seat_order: 0, color: "#0f766e" },
        { seat_order: 1, color: "#7c3aed" },
      ],
    },
    created_at: createdAt,
    updated_at: createdAt,
    players: [
      {
        id: adaId,
        game_id: gameId,
        seat_order: 0,
        name: "Ada",
        controller_type: "human",
        status: "active",
        state: {
          cash: 1500,
          position,
        },
        created_at: createdAt,
        updated_at: createdAt,
      },
      {
        id: graceId,
        game_id: gameId,
        seat_order: 1,
        name: "Grace",
        controller_type: "ai",
        status: "active",
        state: {
          cash: 1500,
          position: 0,
        },
        created_at: createdAt,
        updated_at: createdAt,
      },
    ],
  };
}

function stateFixture(position = 0, eventSequence = 0) {
  return {
    game_id: gameId,
    state: {
      game_id: gameId,
      seed: "turn-controls",
      players: [
        { id: adaId, cash: 1500, position },
        { id: graceId, cash: 1500, position: 0 },
      ],
      turn: {
        phase: "START_TURN",
        current_player_index: 0,
        current_player_id: adaId,
      },
    },
    state_hash: `state-${eventSequence}`,
    event_sequence: eventSequence,
  };
}

function aiStateFixture(eventSequence = 0) {
  return {
    game_id: gameId,
    state: {
      game_id: gameId,
      seed: "turn-controls-ai",
      players: [
        { id: adaId, cash: 1500, position: 0 },
        { id: graceId, cash: 1500, position: 0 },
      ],
      turn: {
        phase: "START_TURN",
        current_player_index: 1,
        current_player_id: graceId,
      },
    },
    state_hash: `ai-state-${eventSequence}`,
    event_sequence: eventSequence,
  };
}

function metadataFallbackAiGame(): GameMetadata {
  const game = gameFixture();
  return {
    ...game,
    players: [
      {
        ...game.players[0],
        name: "Ada AI",
        controller_type: "ai",
      },
      {
        ...game.players[1],
        name: "Grace AI",
        controller_type: "ai",
      },
    ],
  };
}

function legalAction(
  type: string,
  payload: Record<string, unknown> = {},
  expectedStateHash = "state-0",
  expectedEventSequence = 0,
) {
  return {
    actor_id: adaId,
    type,
    payload,
    expected_state_hash: expectedStateHash,
    expected_event_sequence: expectedEventSequence,
    description: null,
    schema: {},
  };
}

function aiLegalAction(
  type: string,
  payload: Record<string, unknown> = {},
  expectedStateHash = "ai-state-0",
  expectedEventSequence = 0,
) {
  return {
    ...legalAction(type, payload, expectedStateHash, expectedEventSequence),
    actor_id: graceId,
  };
}

function eventsFixture(events: Array<Record<string, unknown>> = []) {
  return { events };
}

function rejectedActionsFixture(records: Array<Record<string, unknown>> = []) {
  return { rejected_actions: records };
}

const aiAuditResponses = [
  { path: "/ai/profiles", payload: { profiles: [] } },
  { path: "/ai/decisions", payload: { decisions: [] } },
  { path: "/ai/self-dialogue", payload: { self_dialogue: [] } },
  { path: "/ai/memory", payload: { memory_entries: [] } },
  { path: "/ai/retrieval-records", payload: { retrieval_records: [] } },
  { path: "/ai/rejected-outputs", payload: { rejected_outputs: [] } },
];

function aiAuditUrl(path: string): string {
  return `${apiBaseUrl}/games/${gameId}${path}`;
}

function aiAuditFetchCount(fetchMock: FetchMock, path: string): number {
  const url = aiAuditUrl(path);
  return fetchMock.mock.calls.filter(([input]) => String(input) === url).length;
}

function acceptedRollResponse() {
  return {
    status: "accepted",
    game_id: gameId,
    accepted_events: [
      {
        id: "event-1",
        game_id: gameId,
        sequence: 1,
        actor_player_id: adaId,
        event_type: "DICE_ROLLED",
        payload: { dice: [3, 4], total: 7 },
        state_hash: "state-1",
        created_at: "2026-07-04T00:01:00.000Z",
      },
      {
        id: "event-2",
        game_id: gameId,
        sequence: 2,
        actor_player_id: adaId,
        event_type: "TOKEN_MOVED",
        payload: { player_id: adaId, from_position: 0, to_position: 7 },
        state_hash: "state-2",
        created_at: "2026-07-04T00:01:01.000Z",
      },
    ],
    state: stateFixture(7, 2).state,
    state_hash: "state-2",
    event_sequence: 2,
  };
}

function auctionPurchaseStateFixture() {
  return {
    game_id: gameId,
    state: {
      game_id: gameId,
      seed: "turn-controls-auction",
      players: [
        { id: adaId, cash: 1500, position: 1 },
        { id: graceId, cash: 1500, position: 0 },
      ],
      property_ownership: [
        {
          property_id: "property_mediterranean_avenue",
          owner_id: null,
          mortgaged: false,
          houses: 0,
          hotels: 0,
          hotel: false,
        },
      ],
      active_auction: null,
      turn: {
        phase: "PURCHASE_OR_AUCTION",
        current_player_index: 0,
        current_player_id: adaId,
      },
    },
    state_hash: "state-2",
    event_sequence: 2,
  };
}

function mixedAuctionAiTurnStateFixture(eventSequence = 8) {
  return {
    game_id: gameId,
    state: {
      game_id: gameId,
      seed: "turn-controls-mixed-auction",
      players: [
        { id: adaId, cash: 1500, position: 1 },
        { id: graceId, cash: 1500, position: 0 },
      ],
      property_ownership: [
        {
          property_id: "property_mediterranean_avenue",
          owner_id: null,
          mortgaged: false,
          houses: 0,
          hotels: 0,
          hotel: false,
        },
      ],
      active_auction: {
        property_id: "property_mediterranean_avenue",
        high_bidder_id: null,
        high_bid_amount: null,
        passed_player_ids: [],
      },
      turn: {
        phase: "AUCTION",
        current_player_index: 1,
        current_player_id: graceId,
      },
    },
    state_hash: `auction-ai-state-${eventSequence}`,
    event_sequence: eventSequence,
  };
}

function acceptedAuctionRollResponse() {
  return {
    ...acceptedRollResponse(),
    accepted_events: [
      {
        id: "event-1",
        game_id: gameId,
        sequence: 1,
        actor_player_id: adaId,
        event_type: "DICE_ROLLED",
        payload: { dice: [1], total: 1 },
        state_hash: "state-1",
        created_at: "2026-07-04T00:01:00.000Z",
      },
      {
        id: "event-2",
        game_id: gameId,
        sequence: 2,
        actor_player_id: adaId,
        event_type: "TOKEN_MOVED",
        payload: { player_id: adaId, from_position: 0, to_position: 1 },
        state_hash: "state-2",
        created_at: "2026-07-04T00:01:01.000Z",
      },
    ],
    state: auctionPurchaseStateFixture().state,
    state_hash: "state-2",
    event_sequence: 2,
  };
}

function rejectedRollResponse() {
  return {
    status: "rejected",
    rejected_action_id: "rejection-1",
    reason_code: "stale_action",
    validation_errors: [
      {
        code: "stale_action",
        message: "action expected state no longer matches current state",
        field: "expected_state_hash",
      },
    ],
    legal_action_context: {
      legal_actions: ["ROLL_DICE"],
    },
    submitted_action: legalAction("ROLL_DICE"),
  };
}

function aiStepResponse(status: "accepted" | "rejected" | "blocked" | "done", patch: Record<string, unknown> = {}) {
  return {
    status,
    game_id: gameId,
    player_id: graceId,
    decision_type: "action_decision",
    negotiation_id: null,
    ai_decision_id: `ai-decision-${status}`,
    accepted_events: [],
    accepted_event_id: null,
    rejected_action_id: status === "rejected" || status === "blocked" ? `rejected-${status}` : null,
    game_status: status === "blocked" ? "AI_BLOCKED" : "active",
    consumed_response_opportunity: false,
    consumed_negotiation_opportunity: null,
    outcome: { kind: status === "accepted" ? "action_decision" : `ai_${status}`, status },
    reason_code: status === "rejected" ? "illegal_action" : status === "blocked" ? "codex_exec_timeout" : null,
    validation_errors:
      status === "rejected" || status === "blocked"
        ? [
            {
              code: status === "blocked" ? "codex_exec_timeout" : "illegal_action",
              message: status === "blocked" ? "codex exec timed out" : "action is not legal",
              field: null,
            },
          ]
        : [],
    ...patch,
  };
}

function renderSurface(fetchMock: FetchMock, initialGame: GameMetadata = gameFixture()) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: Number.POSITIVE_INFINITY },
      mutations: { retry: false },
    },
  });

  vi.stubGlobal("fetch", fetchMock);

  return render(
    <QueryClientProvider client={queryClient}>
      <GamePlaySurface apiBaseUrl={apiBaseUrl} gameId={gameId} initialGame={initialGame} />
    </QueryClientProvider>,
  );
}

function jsonResponse(payload: unknown, init?: ResponseInit) {
  return Promise.resolve(Response.json(payload, init));
}

function baseFetchMock({
  game = gameFixture(),
  state = stateFixture(),
  legalActions = [legalAction("ROLL_DICE")],
  events = eventsFixture(),
  rejectedActions = rejectedActionsFixture(),
  actionResponse,
}: {
  game?: GameMetadata;
  state?: ReturnType<typeof stateFixture>;
  legalActions?: Array<ReturnType<typeof legalAction>>;
  events?: ReturnType<typeof eventsFixture>;
  rejectedActions?: ReturnType<typeof rejectedActionsFixture>;
  actionResponse?: unknown;
} = {}) {
  return vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input);
    if (url === `${apiBaseUrl}/games/${gameId}`) {
      return Response.json(game);
    }
    if (url === `${apiBaseUrl}/games/${gameId}/state`) {
      return Response.json(state);
    }
    if (url === `${apiBaseUrl}/games/${gameId}/legal-actions?actor_player_id=${adaId}`) {
      return Response.json({
        game_id: gameId,
        actor_player_id: adaId,
        legal_actions: legalActions,
        state_hash: state.state_hash,
        event_sequence: state.event_sequence,
      });
    }
    if (url === `${apiBaseUrl}/games/${gameId}/events`) {
      return Response.json(events);
    }
    if (url === `${apiBaseUrl}/games/${gameId}/rejected-actions`) {
      return Response.json(rejectedActions);
    }
    if (url === `${apiBaseUrl}/games/${gameId}/actions` && init?.method === "POST" && actionResponse) {
      return Response.json(actionResponse, {
        status: typeof actionResponse === "object" && actionResponse !== null && "reason_code" in actionResponse ? 409 : 200,
      });
    }
    throw new Error(`Unexpected fetch ${url}`);
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("GamePlaySurface turn controls", () => {
  it("renders enabled action buttons only for backend-returned legal actions", async () => {
    renderSurface(
      baseFetchMock({
        legalActions: [legalAction("ROLL_DICE"), legalAction("PAY_JAIL_FINE", { amount: 50 })],
      }),
    );

    const controls = await screen.findByRole("region", { name: "Turn controls" });

    expect(await within(controls).findByRole("button", { name: "Roll dice" })).toBeEnabled();
    expect(within(controls).getByRole("button", { name: "Pay jail fine" })).toBeEnabled();
    expect(within(controls).getByRole("button", { name: "End turn" })).toBeDisabled();
    expect(within(controls).queryByRole("button", { name: "Buy property" })).not.toBeInTheDocument();
    expect(within(controls).queryByRole("button", { name: "Start auction" })).not.toBeInTheDocument();
    expect(within(controls).queryByRole("button", { name: "Bid auction" })).not.toBeInTheDocument();
    expect(within(controls).queryByRole("button", { name: "Pass auction" })).not.toBeInTheDocument();
    expect(within(controls).queryByRole("button", { name: "Settle debt" })).not.toBeInTheDocument();
  });

  it("updates the board, active player position, and Game log after an accepted action is refetched", async () => {
    let accepted = false;
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input);
      if (url === `${apiBaseUrl}/games/${gameId}`) {
        return Response.json(accepted ? gameFixture(7) : gameFixture(0));
      }
      if (url === `${apiBaseUrl}/games/${gameId}/state`) {
        return Response.json(accepted ? stateFixture(7, 2) : stateFixture(0, 0));
      }
      if (url === `${apiBaseUrl}/games/${gameId}/legal-actions?actor_player_id=${adaId}`) {
        return Response.json({
          game_id: gameId,
          actor_player_id: adaId,
          legal_actions: accepted ? [legalAction("BUY_PROPERTY", { property_id: "property_chance_7" })] : [legalAction("ROLL_DICE")],
          state_hash: accepted ? "state-2" : "state-0",
          event_sequence: accepted ? 2 : 0,
        });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/events`) {
        return Response.json(accepted ? eventsFixture(acceptedRollResponse().accepted_events) : eventsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/rejected-actions`) {
        return Response.json(rejectedActionsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/actions` && init?.method === "POST") {
        accepted = true;
        return Response.json(acceptedRollResponse());
      }
      throw new Error(`Unexpected fetch ${url}`);
    });

    renderSurface(fetchMock);

    expect(await screen.findByLabelText("Ada token at GO, position 0")).toBeVisible();
    fireEvent.click(await screen.findByRole("button", { name: "Roll dice" }));

    expect(await screen.findByLabelText("Ada token at Chance, position 7")).toBeVisible();
    const activePlayer = screen.getByRole("region", { name: "Active player" });
    expect(within(activePlayer).getByText("Position")).toBeInTheDocument();
    expect(within(activePlayer).getByText("7")).toBeInTheDocument();
    const log = screen.getByRole("region", { name: "Game log" });
    expect(within(log).getByText(/DICE_ROLLED/)).toBeInTheDocument();
    expect(within(log).getByText(/TOKEN_MOVED/)).toBeInTheDocument();
  });

  it("refreshes auction start controls from post-roll legal actions", async () => {
    let accepted = false;
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input);
      if (url === `${apiBaseUrl}/games/${gameId}`) {
        return Response.json(accepted ? gameFixture(1) : gameFixture(0));
      }
      if (url === `${apiBaseUrl}/games/${gameId}/state`) {
        return Response.json(accepted ? auctionPurchaseStateFixture() : stateFixture(0, 0));
      }
      if (url === `${apiBaseUrl}/games/${gameId}/legal-actions?actor_player_id=${adaId}`) {
        return Response.json({
          game_id: gameId,
          actor_player_id: adaId,
          legal_actions: accepted
            ? [
                legalAction(
                  "START_AUCTION",
                  { property_id: "property_mediterranean_avenue" },
                  "state-2",
                  2,
                ),
              ]
            : [legalAction("ROLL_DICE")],
          state_hash: accepted ? "state-2" : "state-0",
          event_sequence: accepted ? 2 : 0,
        });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/events`) {
        return Response.json(accepted ? eventsFixture(acceptedAuctionRollResponse().accepted_events) : eventsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/rejected-actions`) {
        return Response.json(rejectedActionsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/actions` && init?.method === "POST") {
        accepted = true;
        return Response.json(acceptedAuctionRollResponse());
      }
      throw new Error(`Unexpected fetch ${url}`);
    });

    renderSurface(fetchMock);

    fireEvent.click(await screen.findByRole("button", { name: "Roll dice" }));

    const auction = await screen.findByRole("region", { name: "Auction" });
    await waitFor(() => expect(auction).toHaveTextContent("Mediterranean Avenue"));
    expect(within(auction).getByRole("button", { name: "Start auction" })).toBeEnabled();
  });

  it("shows a Rejected action alert and leaves prior visible board state intact after rejection", async () => {
    renderSurface(
      baseFetchMock({
        actionResponse: rejectedRollResponse(),
      }),
    );

    expect(await screen.findByLabelText("Ada token at GO, position 0")).toBeVisible();
    fireEvent.click(await screen.findByRole("button", { name: "Roll dice" }));

    const alert = await screen.findByRole("alert", { name: "Rejected action" });
    expect(alert).toHaveTextContent("Rejected action");
    expect(alert).toHaveTextContent("stale_action");
    expect(alert).toHaveTextContent("action expected state no longer matches current state");
    expect(screen.getByLabelText("Ada token at GO, position 0")).toBeVisible();
    expect(screen.queryByLabelText("Ada token at Chance, position 7")).not.toBeInTheDocument();
  });

  it("disables controls while legal actions load and while an action submission is pending", async () => {
    let resolveLegalActions: (payload: unknown) => void = () => {};
    let resolveAction: (response: Response) => void = () => {};
    const legalActionsPayloadPromise = new Promise<unknown>((resolve) => {
      resolveLegalActions = resolve;
    });
    const actionPromise = new Promise<Response>((resolve) => {
      resolveAction = resolve;
    });
    const fetchMock = vi.fn<typeof fetch>((input, init) => {
      const url = String(input);
      if (url === `${apiBaseUrl}/games/${gameId}`) {
        return jsonResponse(gameFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/state`) {
        return jsonResponse(stateFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/legal-actions?actor_player_id=${adaId}`) {
        return legalActionsPayloadPromise.then((payload) => Response.json(payload));
      }
      if (url === `${apiBaseUrl}/games/${gameId}/events`) {
        return jsonResponse(eventsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/rejected-actions`) {
        return jsonResponse(rejectedActionsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/actions` && init?.method === "POST") {
        return actionPromise;
      }
      throw new Error(`Unexpected fetch ${url}`);
    });

    renderSurface(fetchMock);

    const controls = await screen.findByRole("region", { name: "Turn controls" });
    expect(within(controls).getByRole("button", { name: "End turn" })).toBeDisabled();
    expect(within(controls).getByText("Loading legal actions")).toBeInTheDocument();

    resolveLegalActions({
      game_id: gameId,
      actor_player_id: adaId,
      legal_actions: [legalAction("ROLL_DICE")],
      state_hash: "state-0",
      event_sequence: 0,
    });

    const rollButton = await within(controls).findByRole("button", { name: "Roll dice" });
    fireEvent.click(rollButton);
    expect(rollButton).toBeDisabled();
    expect(rollButton).toHaveTextContent("Submitting");

    resolveAction(Response.json(acceptedRollResponse()));
    await waitFor(() => expect(rollButton).not.toHaveTextContent("Submitting"));
  });

  it("submits the Manual AI step control for the active AI player", async () => {
    // Manual AI step control
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input);
      if (url === `${apiBaseUrl}/games/${gameId}`) {
        return Response.json(gameFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/state`) {
        return Response.json(aiStateFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/legal-actions?actor_player_id=${graceId}`) {
        return Response.json({
          game_id: gameId,
          actor_player_id: graceId,
          legal_actions: [],
          state_hash: "ai-state-0",
          event_sequence: 0,
        });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/events`) {
        return Response.json(eventsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/rejected-actions`) {
        return Response.json(rejectedActionsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/ai/step` && init?.method === "POST") {
        return Response.json(aiStepResponse("done"));
      }
      throw new Error(`Unexpected fetch ${url}`);
    });

    renderSurface(fetchMock);

    const stepButton = await screen.findByRole("button", { name: "Step AI" });
    await waitFor(() => expect(stepButton).toBeEnabled());
    fireEvent.click(stepButton);

    await waitFor(() => expect(screen.getByRole("status", { name: "AI step status" })).toHaveTextContent("AI done"));
    const aiStepCall = fetchMock.mock.calls.find(
      ([url, init]) => String(url) === `${apiBaseUrl}/games/${gameId}/ai/step` && init?.method === "POST",
    );
    expect(JSON.parse(String(aiStepCall?.[1]?.body))).toMatchObject({
      player_id: graceId,
      decision_type: "action_decision",
      mandatory: true,
      request_context: { mode: "manual" },
    });
  });

  it("refetches AI audit records after a successful Manual AI step", async () => {
    let resolveAiStep: (response: Response) => void = () => {};
    const fetchMock = vi.fn<typeof fetch>((input, init) => {
      const url = String(input);
      const aiAuditResponse = aiAuditResponses.find((response) => url === aiAuditUrl(response.path));
      if (aiAuditResponse) {
        return jsonResponse(aiAuditResponse.payload);
      }
      if (url === `${apiBaseUrl}/games/${gameId}`) {
        return jsonResponse(gameFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/state`) {
        return jsonResponse(aiStateFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/legal-actions?actor_player_id=${graceId}`) {
        return jsonResponse({
          game_id: gameId,
          actor_player_id: graceId,
          legal_actions: [],
          state_hash: "ai-state-0",
          event_sequence: 0,
        });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/events`) {
        return jsonResponse(eventsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/rejected-actions`) {
        return jsonResponse(rejectedActionsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/ai/step` && init?.method === "POST") {
        return new Promise<Response>((resolve) => {
          resolveAiStep = resolve;
        });
      }
      throw new Error(`Unexpected fetch ${url}`);
    });

    renderSurface(fetchMock);

    for (const response of aiAuditResponses) {
      await waitFor(() => expect(aiAuditFetchCount(fetchMock, response.path)).toBe(1));
    }

    const stepButton = await screen.findByRole("button", { name: "Step AI" });
    await waitFor(() => expect(stepButton).toBeEnabled());
    fireEvent.click(stepButton);

    await waitFor(() => expect(screen.getByRole("status", { name: "AI step status" })).toHaveTextContent("AI thinking"));
    for (const response of aiAuditResponses) {
      expect(aiAuditFetchCount(fetchMock, response.path)).toBe(1);
    }

    resolveAiStep(Response.json(aiStepResponse("done")));

    await waitFor(() => {
      for (const response of aiAuditResponses) {
        expect(aiAuditFetchCount(fetchMock, response.path)).toBe(2);
      }
    });

    const aiStepCallIndex = fetchMock.mock.calls.findIndex(
      ([url, init]) => String(url) === `${apiBaseUrl}/games/${gameId}/ai/step` && init?.method === "POST",
    );
    for (const response of aiAuditResponses) {
      let refetchCallIndex = -1;
      fetchMock.mock.calls.forEach(([url], index) => {
        if (String(url) === aiAuditUrl(response.path)) {
          refetchCallIndex = index;
        }
      });
      expect(refetchCallIndex).toBeGreaterThan(aiStepCallIndex);
    }
  });

  it("Manual AI step waits for loaded turn state", async () => {
    let resolveState: (response: Response) => void = () => {};
    const statePromise = new Promise<Response>((resolve) => {
      resolveState = resolve;
    });
    const fetchMock = vi.fn<typeof fetch>((input, init) => {
      const url = String(input);
      if (url === `${apiBaseUrl}/games/${gameId}`) {
        return jsonResponse(metadataFallbackAiGame());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/state`) {
        return statePromise;
      }
      if (url === `${apiBaseUrl}/games/${gameId}/legal-actions?actor_player_id=${adaId}`) {
        return jsonResponse({
          game_id: gameId,
          actor_player_id: adaId,
          legal_actions: [],
          state_hash: "metadata-fallback-state",
          event_sequence: 0,
        });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/legal-actions?actor_player_id=${graceId}`) {
        return jsonResponse({
          game_id: gameId,
          actor_player_id: graceId,
          legal_actions: [],
          state_hash: "ai-state-0",
          event_sequence: 0,
        });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/events`) {
        return jsonResponse(eventsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/rejected-actions`) {
        return jsonResponse(rejectedActionsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/ai/step` && init?.method === "POST") {
        return jsonResponse(aiStepResponse("done"));
      }
      throw new Error(`Unexpected fetch ${url}`);
    });

    renderSurface(fetchMock, metadataFallbackAiGame());

    expect(await screen.findByRole("region", { name: "Turn controls" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Step AI" })).not.toBeInTheDocument();
    expect(
      fetchMock.mock.calls.some(
        ([url, init]) => String(url) === `${apiBaseUrl}/games/${gameId}/ai/step` && init?.method === "POST",
      ),
    ).toBe(false);

    resolveState(Response.json(aiStateFixture()));

    const stepButton = await screen.findByRole("button", { name: "Step AI" });
    await waitFor(() => expect(stepButton).toBeEnabled());
    fireEvent.click(stepButton);

    await waitFor(() => expect(screen.getByRole("status", { name: "AI step status" })).toHaveTextContent("AI done"));
    const aiStepCall = fetchMock.mock.calls.find(
      ([url, init]) => String(url) === `${apiBaseUrl}/games/${gameId}/ai/step` && init?.method === "POST",
    );
    expect(JSON.parse(String(aiStepCall?.[1]?.body))).toMatchObject({
      player_id: graceId,
      decision_type: "action_decision",
      mandatory: true,
      request_context: { mode: "manual" },
    });
  });

  it("Lifecycle-only AI step rejections parse into status panel results", async () => {
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input);
      if (url === `${apiBaseUrl}/games/${gameId}`) {
        return Response.json(gameFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/state`) {
        return Response.json(aiStateFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/legal-actions?actor_player_id=${graceId}`) {
        return Response.json({
          game_id: gameId,
          actor_player_id: graceId,
          legal_actions: [],
          state_hash: "ai-state-0",
          event_sequence: 0,
        });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/events`) {
        return Response.json(eventsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/rejected-actions`) {
        return Response.json(rejectedActionsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/ai/step` && init?.method === "POST") {
        return Response.json(
          {
            status: "rejected",
            reason_code: "game_ai_blocked",
            validation_errors: [
              {
                code: "game_ai_blocked",
                message: "AI stepping is blocked for this game lifecycle state.",
                field: "game_id",
              },
            ],
          },
          { status: 409 },
        );
      }
      throw new Error(`Unexpected fetch ${url}`);
    });

    renderSurface(fetchMock);

    const stepButton = await screen.findByRole("button", { name: "Step AI" });
    await waitFor(() => expect(stepButton).toBeEnabled());
    fireEvent.click(stepButton);

    const status = await screen.findByRole("status", { name: "AI step status" });
    await waitFor(() => expect(status).toHaveTextContent("AI rejected"));
    expect(status).toHaveTextContent("game_ai_blocked");
  });

  it("disables direct action controls for AI turns", async () => {
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input);
      if (url === `${apiBaseUrl}/games/${gameId}`) {
        return Response.json(gameFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/state`) {
        return Response.json(aiStateFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/legal-actions?actor_player_id=${graceId}`) {
        return Response.json({
          game_id: gameId,
          actor_player_id: graceId,
          legal_actions: [aiLegalAction("ROLL_DICE")],
          state_hash: "ai-state-0",
          event_sequence: 0,
        });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/events`) {
        return Response.json(eventsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/rejected-actions`) {
        return Response.json(rejectedActionsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/ai/step` && init?.method === "POST") {
        return Response.json(aiStepResponse("done"));
      }
      if (url === `${apiBaseUrl}/games/${gameId}/actions` && init?.method === "POST") {
        throw new Error("AI direct actions must not be submitted from turn controls");
      }
      throw new Error(`Unexpected fetch ${url}`);
    });

    renderSurface(fetchMock);

    const controls = await screen.findByRole("region", { name: "Turn controls" });
    const rollButton = await within(controls).findByRole("button", { name: "Roll dice" });
    expect(rollButton).toBeDisabled();
    fireEvent.click(rollButton);
    expect(
      fetchMock.mock.calls.some(
        ([url, init]) => String(url) === `${apiBaseUrl}/games/${gameId}/actions` && init?.method === "POST",
      ),
    ).toBe(false);

    const stepButton = within(controls).getByRole("button", { name: "Step AI" });
    expect(stepButton).toBeEnabled();
    fireEvent.click(stepButton);

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          ([url, init]) => String(url) === `${apiBaseUrl}/games/${gameId}/ai/step` && init?.method === "POST",
        ),
      ).toBe(true),
    );
    expect(
      fetchMock.mock.calls.some(
        ([url, init]) => String(url) === `${apiBaseUrl}/games/${gameId}/actions` && init?.method === "POST",
      ),
    ).toBe(false);
  });

  it("preserves human auction controls during mixed AI turns", async () => {
    const auctionState = mixedAuctionAiTurnStateFixture();
    const humanBid = legalAction(
      "BID_AUCTION",
      { property_id: "property_mediterranean_avenue", amount: 26 },
      auctionState.state_hash,
      auctionState.event_sequence,
    );
    const humanPass = legalAction(
      "PASS_AUCTION",
      { property_id: "property_mediterranean_avenue" },
      auctionState.state_hash,
      auctionState.event_sequence,
    );
    const aiBid = aiLegalAction(
      "BID_AUCTION",
      { property_id: "property_mediterranean_avenue", amount: 27 },
      auctionState.state_hash,
      auctionState.event_sequence,
    );
    const aiPass = aiLegalAction(
      "PASS_AUCTION",
      { property_id: "property_mediterranean_avenue" },
      auctionState.state_hash,
      auctionState.event_sequence,
    );

    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input);
      if (url === `${apiBaseUrl}/games/${gameId}`) {
        return Response.json(gameFixture(1));
      }
      if (url === `${apiBaseUrl}/games/${gameId}/state`) {
        return Response.json(auctionState);
      }
      if (url === `${apiBaseUrl}/games/${gameId}/legal-actions?actor_player_id=${adaId}`) {
        return Response.json({
          game_id: gameId,
          actor_player_id: adaId,
          legal_actions: [humanBid, humanPass],
          state_hash: auctionState.state_hash,
          event_sequence: auctionState.event_sequence,
        });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/legal-actions?actor_player_id=${graceId}`) {
        return Response.json({
          game_id: gameId,
          actor_player_id: graceId,
          legal_actions: [aiBid, aiPass],
          state_hash: auctionState.state_hash,
          event_sequence: auctionState.event_sequence,
        });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/events`) {
        return Response.json(eventsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/rejected-actions`) {
        return Response.json(rejectedActionsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/actions` && init?.method === "POST") {
        return Response.json({
          status: "accepted",
          game_id: gameId,
          accepted_events: [
            {
              id: "event-auction-bid",
              game_id: gameId,
              sequence: auctionState.event_sequence + 1,
              actor_player_id: adaId,
              event_type: "AUCTION_BID_PLACED",
              payload: { property_id: "property_mediterranean_avenue", bidder_id: adaId, amount: 26 },
              state_hash: "auction-ai-state-after-human-bid",
              created_at: "2026-07-04T00:02:00.000Z",
            },
          ],
          state: {
            ...auctionState.state,
            active_auction: {
              property_id: "property_mediterranean_avenue",
              high_bidder_id: adaId,
              high_bid_amount: 26,
              passed_player_ids: [],
            },
          },
          state_hash: "auction-ai-state-after-human-bid",
          event_sequence: auctionState.event_sequence + 1,
        });
      }
      throw new Error(`Unexpected fetch ${url}`);
    });

    renderSurface(fetchMock);

    const auction = await screen.findByRole("region", { name: "Auction" });
    const adaControls = await within(auction).findByRole("group", { name: "Ada auction controls" });
    const graceControls = await within(auction).findByRole("group", { name: "Grace auction controls" });
    const adaBidButton = within(adaControls).getByRole("button", { name: "Bid" });
    const graceBidButton = within(graceControls).getByRole("button", { name: "Bid" });
    const gracePassButton = within(graceControls).getByRole("button", { name: "Pass" });

    await waitFor(() => expect(adaBidButton).toBeEnabled());
    expect(graceBidButton).toBeDisabled();
    expect(gracePassButton).toBeDisabled();

    fireEvent.click(adaBidButton);

    await waitFor(() => {
      const actionCalls = fetchMock.mock.calls.filter(
        ([url, init]) => String(url) === `${apiBaseUrl}/games/${gameId}/actions` && init?.method === "POST",
      );
      expect(actionCalls).toHaveLength(1);
      expect(JSON.parse(String(actionCalls[0]?.[1]?.body))).toMatchObject({
        actor_id: adaId,
        type: "BID_AUCTION",
        payload: { property_id: "property_mediterranean_avenue", amount: 26 },
      });
    });

    fireEvent.click(graceBidButton);
    fireEvent.click(gracePassButton);

    const actionBodies = fetchMock.mock.calls
      .filter(([url, init]) => String(url) === `${apiBaseUrl}/games/${gameId}/actions` && init?.method === "POST")
      .map(([, init]) => JSON.parse(String(init?.body)));
    expect(actionBodies.some((body) => body.actor_id === graceId)).toBe(false);
  });

  it("runs the Automatic AI step control while an active AI player is idle", async () => {
    // Automatic AI step control
    const fetchMock = baseFetchMock({
      state: aiStateFixture(),
      legalActions: [],
    });
    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input);
      if (url === `${apiBaseUrl}/games/${gameId}`) {
        return Response.json(gameFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/state`) {
        return Response.json(aiStateFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/legal-actions?actor_player_id=${graceId}`) {
        return Response.json({
          game_id: gameId,
          actor_player_id: graceId,
          legal_actions: [],
          state_hash: "ai-state-0",
          event_sequence: 0,
        });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/events`) {
        return Response.json(eventsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/rejected-actions`) {
        return Response.json(rejectedActionsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/ai/step` && init?.method === "POST") {
        return Response.json(aiStepResponse("done"));
      }
      throw new Error(`Unexpected fetch ${url}`);
    });

    renderSurface(fetchMock);

    fireEvent.click(await screen.findByRole("checkbox", { name: "Auto-step AI" }));

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          ([url, init]) => String(url) === `${apiBaseUrl}/games/${gameId}/ai/step` && init?.method === "POST",
        ),
      ).toBe(true),
    );
    expect(screen.getByRole("status", { name: "AI step status" })).toHaveTextContent("AI done");
  });

  it("shows UI indication when AI is thinking, rejected, blocked, or done", async () => {
    // UI indication when AI is thinking, rejected, blocked, or done
    let resolveAiStep: (response: Response) => void = () => {};
    let aiStepPayload = aiStepResponse("rejected");
    const fetchMock = vi.fn<typeof fetch>((input, init) => {
      const url = String(input);
      if (url === `${apiBaseUrl}/games/${gameId}`) {
        return jsonResponse(gameFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/state`) {
        return jsonResponse(aiStateFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/legal-actions?actor_player_id=${graceId}`) {
        return jsonResponse({
          game_id: gameId,
          actor_player_id: graceId,
          legal_actions: [],
          state_hash: "ai-state-0",
          event_sequence: 0,
        });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/events`) {
        return jsonResponse(eventsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/rejected-actions`) {
        return jsonResponse(rejectedActionsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/ai/step` && init?.method === "POST") {
        return new Promise<Response>((resolve) => {
          resolveAiStep = resolve;
        });
      }
      throw new Error(`Unexpected fetch ${url}`);
    });

    renderSurface(fetchMock);

    const stepButton = await screen.findByRole("button", { name: "Step AI" });
    await waitFor(() => expect(stepButton).toBeEnabled());
    fireEvent.click(stepButton);
    expect(await screen.findByRole("status", { name: "AI step status" })).toHaveTextContent("AI thinking");
    resolveAiStep(Response.json(aiStepPayload));
    await waitFor(() => expect(screen.getByRole("status", { name: "AI step status" })).toHaveTextContent("AI rejected"));

    aiStepPayload = aiStepResponse("blocked");
    await waitFor(() => expect(stepButton).toBeEnabled());
    fireEvent.click(stepButton);
    await waitFor(() => expect(screen.getByRole("status", { name: "AI step status" })).toHaveTextContent("AI thinking"));
    resolveAiStep(Response.json(aiStepPayload));
    await waitFor(() => expect(screen.getByRole("status", { name: "AI step status" })).toHaveTextContent("AI blocked"));

    aiStepPayload = aiStepResponse("done");
    await waitFor(() => expect(stepButton).toBeEnabled());
    fireEvent.click(stepButton);
    await waitFor(() => expect(screen.getByRole("status", { name: "AI step status" })).toHaveTextContent("AI thinking"));
    resolveAiStep(Response.json(aiStepPayload));
    await waitFor(() => expect(screen.getByRole("status", { name: "AI step status" })).toHaveTextContent("AI done"));
  });
});
