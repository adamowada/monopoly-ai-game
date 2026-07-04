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

function eventsFixture(events: Array<Record<string, unknown>> = []) {
  return { events };
}

function rejectedActionsFixture(records: Array<Record<string, unknown>> = []) {
  return { rejected_actions: records };
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
});
