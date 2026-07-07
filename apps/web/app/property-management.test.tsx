import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PROPERTIES } from "@monopoly-ai-game/schemas";

import { GamePlaySurface } from "./game-play-surface";
import { PropertyManagementPanel } from "./property-management";
import type { GameStateResponse, LegalAction } from "../lib/api/gameplay";
import type { GameMetadata } from "../lib/api/games";

const routerMock = vi.hoisted(() => ({
  push: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => routerMock,
}));

const createdAt = "2026-07-04T00:00:00.000Z";
const apiBaseUrl = "http://api.test";
const gameId = "game-property-management";
const adaId = "player-1";
const graceId = "player-2";

type FetchMock = ReturnType<typeof vi.fn<typeof fetch>>;

function gameFixture(): GameMetadata {
  return {
    id: gameId,
    status: "active",
    ruleset_version: "classic-v1",
    seed: "property-management",
    current_phase: "PRE_ROLL_MANAGEMENT",
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
          position: 0,
        },
        created_at: createdAt,
        updated_at: createdAt,
      },
      {
        id: graceId,
        game_id: gameId,
        seat_order: 1,
        name: "Grace",
        controller_type: "human",
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

function ownershipFixture(overrides: Array<Record<string, unknown>> = []) {
  const defaults = [
    {
      property_id: "property_mediterranean_avenue",
      owner_id: adaId,
      mortgaged: false,
      houses: 2,
      hotel: false,
      hotels: 0,
    },
    {
      property_id: "property_baltic_avenue",
      owner_id: adaId,
      mortgaged: false,
      houses: 1,
      hotel: false,
      hotels: 0,
    },
    {
      property_id: "property_park_place",
      owner_id: graceId,
      mortgaged: true,
      houses: 0,
      hotel: false,
      hotels: 0,
    },
    {
      property_id: "property_boardwalk",
      owner_id: graceId,
      mortgaged: false,
      houses: 0,
      hotel: true,
      hotels: 1,
    },
    {
      property_id: "property_reading_railroad",
      owner_id: null,
      mortgaged: false,
      houses: 0,
      hotel: false,
      hotels: 0,
    },
  ];

  return defaults.map((ownership) => ({
    ...ownership,
    ...(overrides.find((override) => override.property_id === ownership.property_id) ?? {}),
  }));
}

function stateFixture({
  ownership = ownershipFixture(),
  bankInventory = { houses: 29, hotels: 11 },
  eventSequence = 0,
}: {
  ownership?: Array<Record<string, unknown>>;
  bankInventory?: Record<string, unknown>;
  eventSequence?: number;
} = {}): GameStateResponse {
  return {
    game_id: gameId,
    state: {
      game_id: gameId,
      seed: "property-management",
      players: [
        { id: adaId, cash: 1500, position: 0 },
        { id: graceId, cash: 1500, position: 0 },
      ],
      turn: {
        phase: "PRE_ROLL_MANAGEMENT",
        current_player_index: 0,
        current_player_id: adaId,
      },
      property_ownership: ownership,
      bank_inventory: bankInventory,
    },
    state_hash: `state-${eventSequence}`,
    event_sequence: eventSequence,
  };
}

function legalAction(type: string, propertyId: string, payload: Record<string, unknown> = {}): LegalAction {
  return {
    actor_id: adaId,
    type,
    payload: { property_id: propertyId, ...payload },
    expected_state_hash: "state-0",
    expected_event_sequence: 0,
    description: null,
    schema: {},
  };
}

function renderPanel({
  game = gameFixture(),
  snapshot = stateFixture(),
  legalActions = [],
  controlsDisabled = false,
  pendingActionType = null,
  onSubmit = vi.fn(),
}: {
  game?: GameMetadata;
  snapshot?: GameStateResponse;
  legalActions?: LegalAction[];
  controlsDisabled?: boolean;
  pendingActionType?: string | null;
  onSubmit?: (action: LegalAction) => void;
} = {}) {
  return render(
    <PropertyManagementPanel
      controlsDisabled={controlsDisabled}
      game={game}
      legalActions={legalActions}
      onSubmit={onSubmit}
      pendingActionType={pendingActionType}
      snapshot={snapshot}
    />,
  );
}

function renderSurface(fetchMock: FetchMock, initialGame: GameMetadata = gameFixture()) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: Number.POSITIVE_INFINITY },
      mutations: { retry: false },
    },
  });

  vi.stubGlobal("fetch", fetchMock);

  function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
  }

  return render(<GamePlaySurface apiBaseUrl={apiBaseUrl} gameId={gameId} initialGame={initialGame} />, {
    wrapper: Wrapper,
  });
}

function eventsFixture(events: Array<Record<string, unknown>> = []) {
  return { events };
}

function rejectedActionsFixture(records: Array<Record<string, unknown>> = []) {
  return { rejected_actions: records };
}

function acceptedBuildResponse() {
  return {
    status: "accepted",
    game_id: gameId,
    accepted_events: [
      {
        id: "event-bank",
        game_id: gameId,
        sequence: 1,
        actor_player_id: adaId,
        event_type: "BANK_INVENTORY_SET",
        payload: { houses: 28, hotels: 11 },
        state_hash: "state-1",
        created_at: "2026-07-04T00:01:00.000Z",
      },
      {
        id: "event-improvement",
        game_id: gameId,
        sequence: 2,
        actor_player_id: adaId,
        event_type: "PROPERTY_IMPROVEMENTS_SET",
        payload: { property_id: "property_baltic_avenue", houses: 2, hotel: false },
        state_hash: "state-2",
        created_at: "2026-07-04T00:01:01.000Z",
      },
    ],
    state: stateFixture({
      ownership: ownershipFixture([{ property_id: "property_baltic_avenue", houses: 2 }]),
      bankInventory: { houses: 28, hotels: 11 },
      eventSequence: 2,
    }).state,
    state_hash: "state-2",
    event_sequence: 2,
  };
}

function rejectedBuildResponse() {
  return {
    status: "rejected",
    rejected_action_id: "rejection-1",
    reason_code: "even_building_rule",
    validation_errors: [
      {
        code: "even_building_rule",
        message: "building must follow the even building rule",
        field: "payload.property_id",
      },
    ],
    legal_action_context: {
      legal_actions: ["BUY_HOUSE"],
    },
    submitted_action: legalAction("BUY_HOUSE", "property_baltic_avenue", { cost: 50 }),
  };
}

function baseFetchMock({
  game = gameFixture(),
  initialState = stateFixture(),
  acceptedState = stateFixture({
    ownership: ownershipFixture([{ property_id: "property_baltic_avenue", houses: 2 }]),
    bankInventory: { houses: 28, hotels: 11 },
    eventSequence: 2,
  }),
  legalActions = [legalAction("BUY_HOUSE", "property_baltic_avenue", { cost: 50 })],
  actionResponse = acceptedBuildResponse(),
  rejectedActions = rejectedActionsFixture(),
}: {
  game?: GameMetadata;
  initialState?: GameStateResponse;
  acceptedState?: GameStateResponse;
  legalActions?: LegalAction[];
  actionResponse?: unknown;
  rejectedActions?: ReturnType<typeof rejectedActionsFixture>;
} = {}) {
  let accepted = false;
  return vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input);
    const state = accepted ? acceptedState : initialState;

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
        legal_actions: accepted ? [] : legalActions,
        state_hash: state.state_hash,
        event_sequence: state.event_sequence,
      });
    }
    if (url === `${apiBaseUrl}/games/${gameId}/events`) {
      return Response.json(accepted ? eventsFixture(acceptedBuildResponse().accepted_events) : eventsFixture());
    }
    if (url === `${apiBaseUrl}/games/${gameId}/rejected-actions`) {
      return Response.json(rejectedActions);
    }
    if (url === `${apiBaseUrl}/games/${gameId}/actions` && init?.method === "POST") {
      accepted = typeof actionResponse === "object" && actionResponse !== null && "accepted_events" in actionResponse;
      return Response.json(actionResponse, {
        status: accepted ? 200 : 409,
      });
    }
    throw new Error(`Unexpected fetch ${url}`);
  });
}

afterEach(() => {
  routerMock.push.mockReset();
  vi.unstubAllGlobals();
});

describe("PropertyManagementPanel", () => {
  it("groups the Property list by owner with bank, mortgage, and improvement indicators", () => {
    renderPanel();

    const list = screen.getByRole("region", { name: "Property list by owner" });
    const bankGroup = within(list).getByRole("group", { name: "Bank/unowned properties" });
    const adaGroup = within(list).getByRole("group", { name: "Ada" });
    const graceGroup = within(list).getByRole("group", { name: "Grace" });

    expect(within(bankGroup).getByText("Reading Railroad")).toBeInTheDocument();
    expect(within(adaGroup).getByText("Mediterranean Avenue")).toBeInTheDocument();
    expect(within(adaGroup).getByText("Baltic Avenue")).toBeInTheDocument();
    expect(within(adaGroup).getByText("Houses: 2")).toBeInTheDocument();
    expect(within(graceGroup).getByText("Park Place")).toBeInTheDocument();
    expect(within(graceGroup).getByText("Mortgaged")).toBeInTheDocument();
    expect(within(graceGroup).getByText("Hotel")).toBeInTheDocument();
  });

  it("shows Property detail, Bank inventory, and Monopoly groups from static data plus state", () => {
    const { container } = renderPanel();

    const detail = screen.getByRole("region", { name: "Property detail: Mediterranean Avenue" });
    expect(detail).toHaveTextContent("Property detail");
    expect(within(detail).getByRole("img", { name: "Mediterranean courtyard motif" })).toBeInTheDocument();
    expect(container.querySelectorAll("[data-property-art]")).toHaveLength(PROPERTIES.length);
    expect(detail).toHaveTextContent("Brown");
    expect(detail).toHaveTextContent("Price $60");
    expect(detail).toHaveTextContent("Mortgage value $30");
    expect(detail).toHaveTextContent("Rent $2 base");
    expect(detail).toHaveTextContent("Owner Ada");
    expect(detail).toHaveTextContent("Unmortgaged");
    expect(detail).toHaveTextContent("Houses: 2");
    expect(detail).toHaveTextContent("Hotels: 0");

    const bankInventory = screen.getByRole("region", { name: "Bank inventory" });
    expect(bankInventory).toHaveTextContent("Houses remaining 29");
    expect(bankInventory).toHaveTextContent("Hotels remaining 11");

    const monopolyGroups = screen.getByRole("region", { name: "Monopoly groups" });
    expect(monopolyGroups).toHaveTextContent("Brown");
    expect(monopolyGroups).toHaveTextContent("Complete for Ada");
    expect(monopolyGroups).toHaveTextContent("Unmortgaged");
    expect(monopolyGroups).toHaveTextContent("Improved");
    expect(monopolyGroups).toHaveTextContent("Light Blue");
    expect(monopolyGroups).toHaveTextContent("Incomplete");
  });

  it("enables Mortgage, Unmortgage, Build house, and Sell house only for matching legal actions", () => {
    renderPanel({
      legalActions: [
        legalAction("BUY_HOUSE", "property_mediterranean_avenue", { cost: 50 }),
        legalAction("SELL_HOUSE", "property_mediterranean_avenue", { proceeds: 25 }),
        legalAction("MORTGAGE_PROPERTY", "property_baltic_avenue", { proceeds: 30 }),
        legalAction("UNMORTGAGE_PROPERTY", "property_park_place", { cost: 33 }),
      ],
    });

    const mediterranean = screen.getByRole("region", { name: "Property detail: Mediterranean Avenue" });
    expect(within(mediterranean).getByRole("button", { name: "Build house" })).toBeEnabled();
    expect(within(mediterranean).getByRole("button", { name: "Sell house" })).toBeEnabled();
    expect(within(mediterranean).queryByRole("button", { name: "Mortgage" })).not.toBeInTheDocument();

    const baltic = screen.getByRole("region", { name: "Property detail: Baltic Avenue" });
    expect(within(baltic).getByRole("button", { name: "Mortgage" })).toBeEnabled();
    expect(within(baltic).queryByRole("button", { name: "Build house" })).not.toBeInTheDocument();

    const parkPlace = screen.getByRole("region", { name: "Property detail: Park Place" });
    expect(within(parkPlace).getByRole("button", { name: "Unmortgage" })).toBeEnabled();

    const boardwalk = screen.getByRole("region", { name: "Property detail: Boardwalk" });
    expect(within(boardwalk).queryByRole("button")).not.toBeInTheDocument();
  });

  it("shows Hotel conversion text for four-house and hotel cases using BUY_HOUSE and SELL_HOUSE controls", () => {
    renderPanel({
      snapshot: stateFixture({
        ownership: ownershipFixture([
          { property_id: "property_mediterranean_avenue", houses: 4, hotel: false, hotels: 0 },
          { property_id: "property_baltic_avenue", houses: 4, hotel: false, hotels: 0 },
        ]),
      }),
      legalActions: [
        legalAction("BUY_HOUSE", "property_mediterranean_avenue", { cost: 50 }),
        legalAction("SELL_HOUSE", "property_boardwalk", { proceeds: 100 }),
      ],
    });

    const mediterranean = screen.getByRole("region", { name: "Property detail: Mediterranean Avenue" });
    expect(mediterranean).toHaveTextContent("Hotel conversion");
    expect(mediterranean).toHaveTextContent("Build house converts four houses to one hotel");
    expect(within(mediterranean).getByRole("button", { name: "Build house" })).toBeEnabled();

    const boardwalk = screen.getByRole("region", { name: "Property detail: Boardwalk" });
    expect(boardwalk).toHaveTextContent("Hotel conversion");
    expect(boardwalk).toHaveTextContent("Sell house converts one hotel to four houses");
    expect(within(boardwalk).getByRole("button", { name: "Sell house" })).toBeEnabled();
  });
});

describe("GamePlaySurface property management integration", () => {
  it("updates improvement and bank state and logs accepted events after an accepted management submission", async () => {
    renderSurface(baseFetchMock());

    const baltic = await screen.findByRole("region", { name: "Property detail: Baltic Avenue" });
    await waitFor(() => expect(baltic).toHaveTextContent("Houses: 1"));
    await waitFor(() =>
      expect(screen.getByRole("region", { name: "Bank inventory" })).toHaveTextContent("Houses remaining 29"),
    );

    fireEvent.click(within(baltic).getByRole("button", { name: "Build house" }));

    await waitFor(() => expect(baltic).toHaveTextContent("Houses: 2"));
    expect(screen.getByRole("region", { name: "Bank inventory" })).toHaveTextContent("Houses remaining 28");
    const log = screen.getByRole("region", { name: "Game log" });
    expect(within(log).getByText(/BANK_INVENTORY_SET/)).toBeInTheDocument();
    expect(within(log).getByText(/PROPERTY_IMPROVEMENTS_SET/)).toBeInTheDocument();
  });

  it("displays Rejected action and leaves visible mortgage, houses, hotels, and bank_inventory state intact", async () => {
    renderSurface(
      baseFetchMock({
        actionResponse: rejectedBuildResponse(),
        rejectedActions: rejectedActionsFixture(),
      }),
    );

    const baltic = await screen.findByRole("region", { name: "Property detail: Baltic Avenue" });
    const bankInventory = screen.getByRole("region", { name: "Bank inventory" });
    await waitFor(() => expect(baltic).toHaveTextContent("Unmortgaged"));
    await waitFor(() => expect(baltic).toHaveTextContent("Houses: 1"));
    expect(baltic).toHaveTextContent("Hotels: 0");
    await waitFor(() => expect(bankInventory).toHaveTextContent("Houses remaining 29"));

    fireEvent.click(within(baltic).getByRole("button", { name: "Build house" }));

    const alert = await screen.findByRole("alert", { name: "Rejected action" });
    expect(alert).toHaveTextContent("Rejected action");
    expect(alert).toHaveTextContent("even_building_rule");
    expect(alert).toHaveTextContent("building must follow the even building rule");
    expect(baltic).toHaveTextContent("Unmortgaged");
    expect(baltic).toHaveTextContent("Houses: 1");
    expect(baltic).toHaveTextContent("Hotels: 0");
    expect(bankInventory).toHaveTextContent("Houses remaining 29");
  });
});
