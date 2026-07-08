import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AiAuditPanel } from "./ai-audit-panel";
import { canSettleObligation, ContractsPanel } from "./contracts-panel";
import { ClassicGameBoard } from "./game-board";
import { GamePlaySurface } from "./game-play-surface";
import { GameSetupPanel } from "./game-setup";
import { NegotiationPanel } from "./negotiation-panel";
import { PropertyManagementPanel } from "./property-management";
import { RejectedActionAuditView } from "./rejected-action-audit";
import type {
  AiDecision,
  AiMemoryEntry,
  AiProfile,
  AiRejectedOutput,
  AiRetrievalRecord,
  AiSelfDialogueRecord,
} from "../lib/api/ai-audit";
import type { ContractOutcomeExplanation, ContractRecord, ObligationRecord } from "../lib/api/contracts";
import type { AcceptedEvent, GameStateResponse, LegalAction } from "../lib/api/gameplay";
import { createGame, type GameMetadata } from "../lib/api/games";
import type { Deal, Negotiation, NegotiationMessage } from "../lib/api/negotiations";
import type { RejectedActionRecord } from "../lib/api/rejected-actions";

const push = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push,
  }),
}));

vi.mock("../lib/api/games", async () => {
  const actual = await vi.importActual<typeof import("../lib/api/games")>("../lib/api/games");
  return {
    ...actual,
    createGame: vi.fn(),
  };
});

const createGameMock = vi.mocked(createGame);

const apiBaseUrl = "http://api.test";
const createdAt = "2026-07-04T00:00:00.000Z";
const gameId = "game-stage-10-4";
const adaId = "player-ada";
const graceId = "player-grace";
const linusId = "player-linus";
const graceProfileId = "profile-grace-stage-10-4";
const decisionId = "decision-grace-stage-10-4";
const memoryEntryId = "memory-grace-stage-10-4";
const retrievalRecordId = "retrieval-grace-stage-10-4";

type FetchMock = ReturnType<typeof vi.fn<typeof fetch>>;

function renderWithQueryClient(ui: ReactElement, fetchMock?: FetchMock) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: 0 },
      mutations: { retry: false },
    },
  });

  if (fetchMock) {
    vi.stubGlobal("fetch", fetchMock);
  }

  function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
  }

  return { ...render(ui, { wrapper: Wrapper }), queryClient };
}

function jsonResponse(payload: unknown, init?: ResponseInit): Promise<Response> {
  return Promise.resolve(Response.json(payload, init));
}

function deferred<T>() {
  let resolve: (value: T) => void = () => undefined;
  const promise = new Promise<T>((innerResolve) => {
    resolve = innerResolve;
  });
  return { promise, resolve };
}

function gameFixture(overrides: Partial<GameMetadata> = {}): GameMetadata {
  return {
    id: gameId,
    status: "active",
    ruleset_version: "classic-v1",
    seed: "stage-10-4-fixture",
    current_phase: "START_TURN",
    settings: {
      player_colors: [
        { seat_order: 0, color: "#0f766e" },
        { seat_order: 1, color: "#2563eb" },
        { seat_order: 2, color: "#c2410c" },
      ],
      negotiation_cutoffs: {
        max_rounds: 5,
        max_proposals_per_player: 6,
      },
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
        state: { cash: 1500, position: 0 },
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
        state: { cash: 1325, position: 39 },
        created_at: createdAt,
        updated_at: createdAt,
      },
      {
        id: linusId,
        game_id: gameId,
        seat_order: 2,
        name: "Linus",
        controller_type: "ai",
        status: "active",
        state: { cash: 1600, position: 7 },
        created_at: createdAt,
        updated_at: createdAt,
      },
    ],
    ...overrides,
  };
}

function stateFixture({
  phase = "START_TURN",
  stateHash = "state-stage-10-4-0",
  eventSequence = 0,
}: {
  phase?: string;
  stateHash?: string;
  eventSequence?: number;
} = {}): GameStateResponse {
  return {
    game_id: gameId,
    state: {
      game_id: gameId,
      seed: "stage-10-4-fixture",
      players: [
        { id: adaId, cash: 1500, position: 0 },
        { id: graceId, cash: 1325, position: 39 },
        { id: linusId, cash: 1600, position: 7 },
      ],
      turn: {
        phase,
        current_player_index: 0,
        current_player_id: adaId,
      },
      property_ownership: ownershipFixture(),
      bank_inventory: { houses: 28, hotels: 11 },
    },
    state_hash: stateHash,
    event_sequence: eventSequence,
  };
}

function ownershipFixture(): Array<Record<string, unknown>> {
  return [
    {
      property_id: "property_mediterranean_avenue",
      owner_id: adaId,
      mortgaged: false,
      houses: 0,
      hotels: 0,
      hotel: false,
    },
    {
      property_id: "property_baltic_avenue",
      owner_id: adaId,
      mortgaged: false,
      houses: 1,
      hotels: 0,
      hotel: false,
    },
    {
      property_id: "property_park_place",
      owner_id: graceId,
      mortgaged: true,
      houses: 0,
      hotels: 0,
      hotel: false,
    },
  ];
}

function legalAction(
  type: string,
  payload: Record<string, unknown> = {},
  expectedStateHash = "state-stage-10-4-0",
  expectedEventSequence = 0,
): LegalAction {
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

function rejectedActionResponse() {
  return {
    status: "rejected",
    rejected_action_id: "rejected-stage-10-4",
    reason_code: "stale_action",
    validation_errors: [
      {
        code: "stale_action",
        message: "expected state hash no longer matches the current snapshot",
        field: "expected_state_hash",
      },
    ],
    legal_action_context: { legal_actions: ["ROLL_DICE"] },
    submitted_action: legalAction("ROLL_DICE"),
  };
}

function emptyAiAuditResponse(url: string): unknown | null {
  const emptyPayloads: Array<[string, unknown]> = [
    ["/ai/profiles", { profiles: [] }],
    ["/ai/decisions", { decisions: [] }],
    ["/ai/self-dialogue", { self_dialogue: [] }],
    ["/ai/memory", { memory_entries: [] }],
    ["/ai/retrieval-records", { retrieval_records: [] }],
    ["/ai/rejected-outputs", { rejected_outputs: [] }],
  ];
  return emptyPayloads.find(([path]) => url === `${apiBaseUrl}/games/${gameId}${path}`)?.[1] ?? null;
}

function emptyPanelFetch(url: string): unknown | null {
  const aiPayload = emptyAiAuditResponse(url);
  if (aiPayload) {
    return aiPayload;
  }
  if (url === `${apiBaseUrl}/games/${gameId}/contracts`) {
    return { contracts: [] };
  }
  if (url === `${apiBaseUrl}/games/${gameId}/obligations`) {
    return { obligations: [] };
  }
  if (url === `${apiBaseUrl}/games/${gameId}/contracts/outcomes`) {
    return { outcomes: [] };
  }
  if (url === `${apiBaseUrl}/games/${gameId}/negotiations`) {
    return { negotiations: [] };
  }
  if (url === `${apiBaseUrl}/games/${gameId}/deals`) {
    return { deals: [] };
  }
  return null;
}

function contractFixture(): ContractRecord {
  return {
    id: "contract-stage-10-4",
    game_id: gameId,
    deal_id: "deal-stage-10-4",
    source_agreement_id: "agreement-stage-10-4",
    effective_event_id: "event-contract-stage-10-4",
    party_player_ids: [adaId, graceId],
    status: "active",
    terms: [
      {
        kind: "rent_share",
        summary: "Ada owes Grace $50 when Boardwalk rent is collected.",
      },
    ],
    term_summary: "Ada owes Grace $50 when Boardwalk rent is collected.",
    created_at: "2026-07-04T00:01:00.000Z",
    effective_at: "2026-07-04T00:02:00.000Z",
  };
}

function obligationsFixture(): ObligationRecord[] {
  return [
    {
      id: "obligation-immediate-stage-10-4",
      game_id: gameId,
      contract_id: "contract-stage-10-4",
      obligated_player_id: adaId,
      counterparty_player_id: graceId,
      status: "pending",
      due_turn: null,
      due_condition: null,
      amount: 50,
      asset_summary: "$50 immediate rent-share transfer",
      transfer_summary: null,
      triggering_event_id: null,
      settled_at: null,
      created_at: "2026-07-04T00:02:00.000Z",
    },
    {
      id: "obligation-future-stage-10-4",
      game_id: gameId,
      contract_id: "contract-stage-10-4",
      obligated_player_id: adaId,
      counterparty_player_id: graceId,
      status: "scheduled",
      due_turn: 9,
      due_condition: "future Boardwalk rent collection",
      amount: 25,
      asset_summary: "$25 scheduled rent-share transfer",
      transfer_summary: null,
      triggering_event_id: null,
      settled_at: null,
      created_at: "2026-07-04T00:02:30.000Z",
    },
    {
      id: "obligation-settled-stage-10-4",
      game_id: gameId,
      contract_id: "contract-stage-10-4",
      obligated_player_id: adaId,
      counterparty_player_id: graceId,
      status: "settled",
      due_turn: 4,
      due_condition: "Reading Railroad rent collected",
      amount: 75,
      asset_summary: "$75 railroad transfer",
      transfer_summary: "Ada paid Grace $75 from a previous source agreement.",
      triggering_event_id: "event-transfer-stage-10-4",
      settled_at: "2026-07-04T00:05:00.000Z",
      created_at: "2026-07-04T00:03:00.000Z",
    },
  ];
}

function contractOutcomesFixture(): ContractOutcomeExplanation[] {
  return [
    {
      id: "outcome-stage-10-4",
      game_id: gameId,
      source_deal_id: "deal-stage-10-4",
      contract_id: "contract-stage-10-4",
      obligation_id: "obligation-settled-stage-10-4",
      obligation_type: "rent_share",
      trigger: { type: "rent_collected" },
      classic_rule_interaction: { deterministic: true },
      decision: { status: "settled", decision: "rent_share_cash_transfer" },
      resulting_state_effect: { cash_transfers: [{ player_id: graceId, amount: 75 }] },
      explanation_text: "Contract outcome explanation for a settled rent-share transfer.",
    },
  ];
}

function acceptedEventsFixture(): AcceptedEvent[] {
  return [
    {
      id: "event-contract-stage-10-4",
      game_id: gameId,
      sequence: 1,
      actor_player_id: adaId,
      event_type: "DEAL_ACCEPTED",
      payload: { deal_id: "deal-stage-10-4", source_agreement_id: "agreement-stage-10-4" },
      state_hash: "state-contract-stage-10-4",
      created_at: "2026-07-04T00:02:00.000Z",
    },
  ];
}

function rejectedActionFixture(): RejectedActionRecord {
  return {
    id: "rejection-stage-10-4",
    game_id: gameId,
    actor_player_id: adaId,
    action_type: "BUY_PROPERTY",
    payload: { property_id: "property_boardwalk" },
    reason_code: "illegal_action",
    validation_errors: [
      {
        code: "illegal_action",
        message: "BUY_PROPERTY is not legal now.",
        field: "type",
      },
    ],
    legal_action_context: { phase: "START_TURN" },
    phase: "START_TURN",
    state_hash: "state-stage-10-4-0",
    created_at: "2026-07-04T00:06:00.000Z",
  };
}

function negotiationFixture(patch: Partial<Negotiation> = {}): Negotiation {
  return {
    id: "neg-stage-10-4",
    game_id: gameId,
    opened_by_player_id: adaId,
    participant_player_ids: [adaId, graceId],
    topic: "Railroad and rent-share package",
    context: "Ada wants Reading Railroad and a rent share.",
    status: "opened",
    round_number: 1,
    pending_deal_id: null,
    current_deal_id: null,
    acceptances: {},
    invalidated_acceptances: {},
    created_at: createdAt,
    updated_at: createdAt,
    ...patch,
  };
}

function dealFixture(patch: Partial<Deal> = {}): Deal {
  return {
    id: "deal-stage-10-4",
    game_id: gameId,
    negotiation_id: "neg-stage-10-4",
    proposer_player_id: adaId,
    participant_player_ids: [adaId, graceId],
    parent_deal_id: null,
    version: 1,
    status: "proposed",
    terms: [
      {
        kind: "immediate_cash_transfer",
        from_player_id: adaId,
        to_player_id: graceId,
        amount: 120,
        summary: "Ada pays Grace $120.",
      },
    ],
    validation_errors: [],
    accepted_at: null,
    rejected_at: null,
    created_at: createdAt,
    updated_at: createdAt,
    ...patch,
  };
}

function messageFixture(): NegotiationMessage {
  return {
    id: "message-stage-10-4",
    game_id: gameId,
    negotiation_id: "neg-stage-10-4",
    author_player_id: adaId,
    body: "Opening fixture message.",
    created_at: createdAt,
  };
}

function createNegotiationFetchMock(): FetchMock {
  const state = {
    negotiations: [negotiationFixture()],
    deals: [] as Deal[],
    messages: { "neg-stage-10-4": [messageFixture()] } as Record<string, NegotiationMessage[]>,
  };

  return vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : {};

    if (url === `${apiBaseUrl}/games/${gameId}/negotiations` && method === "GET") {
      return Response.json({ negotiations: state.negotiations });
    }
    if (url === `${apiBaseUrl}/games/${gameId}/deals` && method === "GET") {
      return Response.json({ deals: state.deals });
    }
    if (url === `${apiBaseUrl}/games/${gameId}/deals` && method === "POST") {
      const deal = dealFixture({
        terms: body.terms,
        proposer_player_id: body.proposer_player_id,
        participant_player_ids: body.participant_player_ids,
        parent_deal_id: body.parent_deal_id,
      });
      state.deals = [deal];
      return Response.json({ status: "ok", deal });
    }

    const messagesMatch = url.match(new RegExp(`${apiBaseUrl}/games/${gameId}/negotiations/([^/]+)/messages(?:\\?.*)?$`));
    if (messagesMatch && method === "GET") {
      return Response.json({ messages: state.messages[messagesMatch[1]] ?? [] });
    }

    if (url === `${apiBaseUrl}/games/${gameId}/ai/step` && method === "POST") {
      return Response.json({
        status: "done",
        game_id: gameId,
        player_id: body.player_id,
        decision_type: body.decision_type,
        negotiation_id: body.negotiation_id ?? null,
        ai_decision_id: "ai-step-stage-10-4",
        accepted_events: [],
        accepted_event_id: null,
        rejected_action_id: null,
        game_status: "active",
        consumed_response_opportunity: false,
        consumed_negotiation_opportunity: null,
        outcome: {},
        reason_code: null,
        validation_errors: [],
      });
    }

    throw new Error(`Unexpected fetch ${method} ${url}`);
  });
}

function profilesFixture(): AiProfile[] {
  return [
    {
      ai_profile_id: graceProfileId,
      game_id: gameId,
      player_id: graceId,
      display_name: "Grace component audit profile",
      traits: ["risk-aware", "rent-focused"],
      personality: "Careful analyst",
      play_style: "Builds cash buffers before auctions.",
      persona_summary: "Grace keeps liquidity before pressing for monopolies.",
      created_at: "2026-07-04T00:01:00.000Z",
    },
  ];
}

function decisionsFixture(): AiDecision[] {
  return [
    {
      ai_decision_id: decisionId,
      game_id: gameId,
      ai_profile_id: graceProfileId,
      player_id: graceId,
      decision_type: "action_decision",
      status: "rejected",
      phase: "START_TURN",
      state_hash: "state-ai-stage-10-4",
      prompt_context_hash: "prompt-context-stage-10-4",
      legal_actions: [
        {
          actor_id: graceId,
          type: "ROLL_DICE",
          payload: {},
          expected_state_hash: "state-ai-stage-10-4",
          expected_event_sequence: 4,
          description: "Grace can roll dice.",
          schema: {},
        },
      ],
      prompt_context: { phase: "START_TURN", legal_action_count: 1 },
      raw_output: "{\"action\":\"BUY_PROPERTY\"}",
      parsed_output: { action: "BUY_PROPERTY", property_id: "property_boardwalk" },
      validation_result: { status: "rejected", reason_code: "illegal_action" },
      validation_errors: [
        {
          code: "illegal_action",
          message: "BUY_PROPERTY is not in the legal action snapshot.",
          field: "parsed_output.action",
        },
      ],
      memory_entry_ids: [memoryEntryId],
      retrieval_record_ids: [retrievalRecordId],
      accepted_event_id: null,
      rejected_action_id: "rejected-action-stage-10-4",
      created_at: "2026-07-04T00:02:00.000Z",
    },
  ];
}

function selfDialogueFixture(): AiSelfDialogueRecord[] {
  return [
    {
      self_dialogue_id: "dialogue-stage-10-4",
      game_id: gameId,
      player_id: graceId,
      ai_decision_id: decisionId,
      ai_profile_id: graceProfileId,
      sequence: 1,
      role: "critic",
      status: "provided",
      phase: "START_TURN",
      state_hash: "state-ai-stage-10-4",
      content: "Only ROLL_DICE is legal, so buying property should be rejected.",
      payload: { text: "Only ROLL_DICE is legal." },
      created_at: "2026-07-04T00:02:01.000Z",
    },
  ];
}

function memoryFixture(): AiMemoryEntry[] {
  return [
    {
      memory_entry_id: memoryEntryId,
      game_id: gameId,
      ai_profile_id: graceProfileId,
      player_id: graceId,
      source_decision_id: decisionId,
      source_event_id: null,
      source_negotiation_message_id: null,
      superseded_by_memory_id: null,
      sequence: 1,
      category: "player_trust_model",
      visibility: "private",
      content: "Grace remembers Ada keeps a cash reserve after trades.",
      importance: 7,
      metadata: { fixture: "stage-10-4" },
      created_at: "2026-07-04T00:01:30.000Z",
      updated_at: "2026-07-04T00:01:31.000Z",
    },
  ];
}

function retrievalFixture(): AiRetrievalRecord[] {
  return [
    {
      retrieval_record_id: retrievalRecordId,
      game_id: gameId,
      player_id: graceId,
      ai_decision_id: decisionId,
      ai_profile_id: graceProfileId,
      memory_entry_id: memoryEntryId,
      source_type: "memory",
      source_id: memoryEntryId,
      query_text: "cash reserve",
      query_context: { prompt_context_hash: "prompt-context-stage-10-4" },
      retrieved_context: { id: memoryEntryId },
      score: 0.91,
      content: "Retrieved context confirms Ada's cash-reserve preference.",
      created_at: "2026-07-04T00:01:45.000Z",
    },
  ];
}

function rejectedOutputsFixture(): AiRejectedOutput[] {
  return [
    {
      rejected_output_id: "rejected-output-stage-10-4",
      game_id: gameId,
      ai_decision_id: decisionId,
      source_ai_decision_id: decisionId,
      ai_profile_id: graceProfileId,
      player_id: graceId,
      state_hash: "state-ai-stage-10-4",
      status: "rejected",
      raw_output: "{\"action\":\"BUY_PROPERTY\"}",
      parsed_output: { action: "BUY_PROPERTY" },
      validation_errors: [
        {
          code: "illegal_action",
          message: "BUY_PROPERTY is not legal.",
          field: "parsed_output.action",
        },
      ],
      rejected_action_id: "rejected-action-stage-10-4",
      created_at: "2026-07-04T00:02:30.000Z",
    },
  ];
}

function createAiAuditFetchMock(): FetchMock {
  return vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? "GET";

    if (url === `${apiBaseUrl}/games/${gameId}/ai/profiles` && method === "GET") {
      return Response.json({ profiles: profilesFixture() });
    }
    if (url === `${apiBaseUrl}/games/${gameId}/ai/decisions` && method === "GET") {
      return Response.json({ decisions: decisionsFixture() });
    }
    if (url === `${apiBaseUrl}/games/${gameId}/ai/self-dialogue` && method === "GET") {
      return Response.json({ self_dialogue: selfDialogueFixture() });
    }
    if (url === `${apiBaseUrl}/games/${gameId}/ai/memory` && method === "GET") {
      return Response.json({ memory_entries: memoryFixture() });
    }
    if (url === `${apiBaseUrl}/games/${gameId}/ai/retrieval-records` && method === "GET") {
      return Response.json({ retrieval_records: retrievalFixture() });
    }
    if (url === `${apiBaseUrl}/games/${gameId}/ai/rejected-outputs` && method === "GET") {
      return Response.json({ rejected_outputs: rejectedOutputsFixture() });
    }

    throw new Error(`Unexpected fetch ${method} ${url}`);
  });
}

beforeEach(() => {
  createGameMock.mockReset();
  push.mockReset();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Stage 10.4 frontend component coverage", () => {
  it("setup form renders fixture players and submits create-game payload", async () => {
    createGameMock.mockResolvedValue({
      state: "loaded",
      game: gameFixture({ id: "game-created-stage-10-4" }),
    });

    render(<GameSetupPanel />);

    fireEvent.click(screen.getByRole("button", { name: "Add player" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Seed" }), {
      target: { value: "stage-10-4-seed" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "Player 1 name" }), { target: { value: "Ada" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Player 2 name" }), { target: { value: "Grace" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Player 3 name" }), { target: { value: "Linus" } });
    fireEvent.change(screen.getByRole("combobox", { name: "Player 3 type" }), { target: { value: "ai" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Player 3 color hex" }), { target: { value: "#c2410c" } });
    fireEvent.change(screen.getByRole("spinbutton", { name: "Max negotiation rounds" }), { target: { value: "5" } });
    fireEvent.change(screen.getByRole("spinbutton", { name: "Proposal limit per player" }), {
      target: { value: "6" },
    });

    const seatOne = screen.getByRole("group", { name: "Seat 1 token setup" });
    const seatTwo = screen.getByRole("group", { name: "Seat 2 token setup" });
    const seatThree = screen.getByRole("group", { name: "Seat 3 token setup" });
    expect(within(seatOne).getByRole("textbox", { name: "Player 1 name" })).toHaveValue("Ada");
    expect(within(seatTwo).getByRole("textbox", { name: "Player 2 name" })).toHaveValue("Grace");
    expect(within(seatThree).getByRole("textbox", { name: "Player 3 name" })).toHaveValue("Linus");
    expect(within(seatThree).getByRole("combobox", { name: "Player 3 type" })).toHaveValue("ai");

    fireEvent.click(screen.getByRole("button", { name: "Create game" }));

    await waitFor(() =>
      expect(createGameMock).toHaveBeenCalledWith({
        seed: "stage-10-4-seed",
        players: [
          { name: "Ada", kind: "human" },
          { name: "Grace", kind: "human" },
          { name: "Linus", kind: "ai" },
        ],
        settings: {
          player_colors: [
            { seat_order: 0, color: "#0f766e" },
            { seat_order: 1, color: "#2563eb" },
            { seat_order: 2, color: "#c2410c" },
          ],
          player_icons: [
            { seat_order: 0, icon: "🚗" },
            { seat_order: 1, icon: "🎩" },
            { seat_order: 2, icon: "🚂" },
          ],
          negotiation_cutoffs: {
            max_rounds: 5,
            max_proposals_per_player: 6,
          },
        },
      }),
    );
    expect(push).toHaveBeenCalledWith("/games/game-created-stage-10-4", { scroll: true });
  });

  it("board rendering shows fixture state without overlapping control data", () => {
    render(<ClassicGameBoard game={gameFixture()} />);

    const board = screen.getByRole("region", { name: "Classic Monopoly-style board" });
    expect(board.querySelectorAll("[data-board-space]")).toHaveLength(40);
    expect(within(board).getByLabelText("Ada token at GO, position 0")).toBeInTheDocument();
    expect(within(board).getByLabelText("Grace token at Boardwalk, position 39")).toBeInTheDocument();
    expect(within(board).getByLabelText("Linus token at Chance, position 7")).toBeInTheDocument();
    expect(board.querySelector("[data-space-index='39'] [data-space-name]")).toHaveTextContent("Boardwalk");
    expect(within(board).queryByText("Turn controls")).not.toBeInTheDocument();
    expect(within(board).queryByText("Create game")).not.toBeInTheDocument();
    expect(within(board).queryByText("Propose deal")).not.toBeInTheDocument();
  });

  it("legal action controls submit payloads and honor rejected loading disabled states", async () => {
    const legalActionsDeferred = deferred<unknown>();
    const actionDeferred = deferred<Response>();
    const rollAction = legalAction("ROLL_DICE");
    const fetchMock = vi.fn<typeof fetch>((input, init) => {
      const url = String(input);
      const panelPayload = emptyPanelFetch(url);

      if (url === `${apiBaseUrl}/games/${gameId}`) {
        return jsonResponse(gameFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/state`) {
        return jsonResponse(stateFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/legal-actions?actor_player_id=${adaId}`) {
        return legalActionsDeferred.promise.then((payload) => Response.json(payload));
      }
      if (url === `${apiBaseUrl}/games/${gameId}/events`) {
        return jsonResponse({ events: [] });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/rejected-actions`) {
        return jsonResponse({ rejected_actions: [] });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/actions` && init?.method === "POST") {
        return actionDeferred.promise;
      }
      if (panelPayload) {
        return jsonResponse(panelPayload);
      }
      throw new Error(`Unexpected fetch ${init?.method ?? "GET"} ${url}`);
    });

    renderWithQueryClient(<GamePlaySurface apiBaseUrl={apiBaseUrl} gameId={gameId} initialGame={gameFixture()} />, fetchMock);

    const TurnControls = await screen.findByRole("region", { name: "Turn controls" });
    expect(within(TurnControls).queryByText("Loading moves")).not.toBeInTheDocument();
    expect(within(TurnControls).getByRole("button", { name: "End turn" })).toBeDisabled();

    legalActionsDeferred.resolve({
      game_id: gameId,
      actor_player_id: adaId,
      legal_actions: [rollAction],
      state_hash: "state-stage-10-4-0",
      event_sequence: 0,
    });

    const rollButton = await within(TurnControls).findByRole("button", { name: "Roll dice" });
    await waitFor(() => expect(rollButton).toBeEnabled());

    fireEvent.click(rollButton);

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          ([url, init]) =>
            String(url) === `${apiBaseUrl}/games/${gameId}/actions` &&
            init?.method === "POST" &&
            JSON.parse(String(init.body)).type === "ROLL_DICE",
        ),
      ).toBe(true),
    );
    expect(within(TurnControls).getByRole("button", { name: "Submitting..." })).toBeDisabled();

    actionDeferred.resolve(Response.json(rejectedActionResponse(), { status: 409 }));

    const alert = await screen.findByRole("alert", { name: "Rejected action" });
    expect(alert).toHaveTextContent("stale_action");
    expect(alert).toHaveTextContent("expected state hash no longer matches the current snapshot");
    expect(screen.getByLabelText("Ada token at GO, position 0")).toBeInTheDocument();

    render(<RejectedActionAuditView records={[rejectedActionFixture()]} />);
    expect(screen.getByRole("heading", { level: 2, name: "Rule rulings" })).toBeInTheDocument();
  });

  it("property management controls submit mortgage improvement payloads", () => {
    const mortgageAction = legalAction("MORTGAGE_PROPERTY", {
      property_id: "property_mediterranean_avenue",
      proceeds: 30,
    });
    const buildAction = legalAction("BUY_HOUSE", {
      property_id: "property_baltic_avenue",
      cost: 50,
    });
    const onSubmit = vi.fn();

    render(
      <PropertyManagementPanel
        controlsDisabled={false}
        game={gameFixture()}
        legalActions={[mortgageAction, buildAction]}
        onSubmit={onSubmit}
        pendingActionType={null}
        snapshot={stateFixture({ phase: "PRE_ROLL_MANAGEMENT" })}
      />,
    );

    const mediterranean = screen.getByRole("region", { name: "Property detail: Mediterranean Avenue" });
    const baltic = screen.getByRole("region", { name: "Property detail: Baltic Avenue" });
    expect(mediterranean).toHaveTextContent("Owner Ada");
    expect(baltic).toHaveTextContent("Houses: 1");

    fireEvent.click(within(mediterranean).getByRole("button", { name: "Mortgage" }));
    fireEvent.click(within(baltic).getByRole("button", { name: "Build house" }));

    expect(onSubmit).toHaveBeenNthCalledWith(1, mortgageAction);
    expect(onSubmit).toHaveBeenNthCalledWith(2, buildAction);
  });

  it("negotiation deal builder submits complex deal payloads", async () => {
    const fetchMock = createNegotiationFetchMock();
    renderWithQueryClient(<NegotiationPanel apiBaseUrl={apiBaseUrl} game={gameFixture()} gameId={gameId} />, fetchMock);

    const thread = await screen.findByRole("region", { name: "Negotiation thread" });
    await waitFor(() => expect(thread).toHaveTextContent("Railroad and rent-share package"));

    fireEvent.click(screen.getByRole("button", { name: "Add sample complex instruments" }));
    const preview = screen.getByRole("region", { name: "Contract preview" });
    for (const termKind of [
      "Immediate Cash Transfer",
      "Deferred Cash Payment",
      "Interest Bearing Debt",
      "Property Purchase Option",
      "Rent Share",
      "Insurance Payout",
    ]) {
      expect(preview).toHaveTextContent(termKind);
    }

    fireEvent.click(screen.getByRole("button", { name: "Propose deal" }));

    const deal = await screen.findByRole("region", { name: "Deal v1" });
    expect(deal).toHaveTextContent("Proposed");

    const dealSubmission = fetchMock.mock.calls.find(
      ([url, init]) => String(url) === `${apiBaseUrl}/games/${gameId}/deals` && init?.method === "POST",
    );
    expect(dealSubmission).toBeTruthy();
    expect(JSON.parse(String(dealSubmission?.[1]?.body))).toMatchObject({
      negotiation_id: "neg-stage-10-4",
      proposer_player_id: adaId,
      participant_player_ids: [adaId, graceId],
      parent_deal_id: null,
      terms: expect.arrayContaining([
        expect.objectContaining({ kind: "immediate_cash_transfer" }),
        expect.objectContaining({ kind: "deferred_cash_payment" }),
        expect.objectContaining({ kind: "interest_bearing_debt" }),
        expect.objectContaining({ kind: "property_purchase_option" }),
        expect.objectContaining({ kind: "rent_share" }),
        expect.objectContaining({ kind: "insurance_payout" }),
      ]),
    });
  });

  it("contract panel renders obligations and submits enforcement payloads", async () => {
    const enforcementDeferred = deferred<Response>();
    const settleContractEndpoint = `${apiBaseUrl}/games/${gameId}/contracts/contract-stage-10-4/settle`;
    const obligations = obligationsFixture();
    const immediateObligationRecord = obligations.find(
      (obligation) => obligation.id === "obligation-immediate-stage-10-4",
    );
    const futureObligationRecord = obligations.find((obligation) => obligation.id === "obligation-future-stage-10-4");
    if (!immediateObligationRecord || !futureObligationRecord) {
      throw new Error("Stage 10.4 obligation fixture is incomplete.");
    }
    expect(immediateObligationRecord.status === "pending").toBe(true);
    expect(immediateObligationRecord.due_turn === null).toBe(true);
    expect(immediateObligationRecord.due_condition === null).toBe(true);
    expect(canSettleObligation(immediateObligationRecord)).toBe(true);
    expect(
      canSettleObligation({
        ...immediateObligationRecord,
        id: "obligation-due-stage-10-4",
        status: "due",
        due_turn: 6,
        due_condition: "Boardwalk rent collected",
      }),
    ).toBe(true);
    expect(canSettleObligation(futureObligationRecord)).toBe(false);

    const fetchMock = vi.fn<typeof fetch>((input, init) => {
      const url = String(input);
      const method = init?.method ?? "GET";

      if (url === `${apiBaseUrl}/games/${gameId}/contracts` && method === "GET") {
        return jsonResponse({ contracts: [contractFixture()] });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/obligations` && method === "GET") {
        return jsonResponse({ obligations });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/contracts/outcomes` && method === "GET") {
        return jsonResponse({ outcomes: contractOutcomesFixture() });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/deals` && method === "GET") {
        return jsonResponse({ deals: [dealFixture({ status: "accepted", accepted_at: createdAt })] });
      }
      if (url === settleContractEndpoint && method === "POST") {
        return enforcementDeferred.promise;
      }

      throw new Error(`Unexpected fetch ${method} ${url}`);
    });
    const { queryClient } = renderWithQueryClient(
      <ContractsPanel
        apiBaseUrl={apiBaseUrl}
        events={acceptedEventsFixture()}
        game={gameFixture()}
        gameId={gameId}
        rejectedActions={[rejectedActionFixture()]}
      />,
      fetchMock,
    );
    const invalidateQueries = vi.spyOn(queryClient, "invalidateQueries");

    const panel = await screen.findByRole("region", { name: "Contracts obligations panel" });
    await within(panel).findByText("Agreement between Ada, Grace");
    const contract = within(panel).getByRole("article", { name: "Contract between Ada, Grace" });
    expect(contract).not.toHaveTextContent("contract_id contract-stage-10-4");
    fireEvent.click(within(contract).getByRole("button", { name: "Show contract technical record" }));
    expect(contract).toHaveTextContent("contract_id contract-stage-10-4");

    const obligationArticles = within(panel).getAllByRole("article", { name: "Obligation Ada to Grace" });
    const obligation = obligationArticles.find((article) => article.textContent?.includes("$50 immediate rent-share transfer"));
    expect(obligation).toBeTruthy();
    const immediateObligation = obligation as HTMLElement;
    expect(immediateObligation).not.toHaveTextContent("obligation_id obligation-immediate-stage-10-4");
    fireEvent.click(within(immediateObligation).getByRole("button", { name: "Show obligation technical record" }));
    expect(immediateObligation).toHaveTextContent("obligation_id obligation-immediate-stage-10-4");
    expect(immediateObligation).toHaveTextContent("pending");
    expect(immediateObligation).toHaveTextContent("due condition not set");
    expect(immediateObligation).toHaveTextContent("$50 immediate rent-share transfer");

    const futureObligationArticle = obligationArticles.find((article) =>
      article.textContent?.includes("$25 scheduled rent-share transfer"),
    );
    expect(futureObligationArticle).toBeTruthy();
    const futureObligation = futureObligationArticle as HTMLElement;
    expect(futureObligation).toHaveTextContent("scheduled");
    expect(futureObligation).toHaveTextContent("Turn 9");
    expect(futureObligation).not.toHaveTextContent("due_turn 9");
    expect(futureObligation).toHaveTextContent("$25 scheduled rent-share transfer");
    expect(futureObligation).not.toHaveTextContent("Settlement unavailable until this obligation is due.");
    fireEvent.click(within(futureObligation).getByRole("button", { name: "Show obligation technical record" }));
    expect(futureObligation).toHaveTextContent("due_turn 9");
    const futureControl = within(futureObligation).getByRole("button", { name: "Unavailable until due" });
    expect(futureControl).toBeDisabled();

    fireEvent.click(futureControl);
    expect(
      fetchMock.mock.calls.filter(([url, init]) => String(url) === settleContractEndpoint && init?.method === "POST"),
    ).toHaveLength(0);
    expect(fetchMock).not.toHaveBeenCalledWith(
      settleContractEndpoint,
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ obligation_id: "obligation-future-stage-10-4" }),
      }),
    );

    fireEvent.click(within(immediateObligation).getByRole("button", { name: "Enforce obligation" }));

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          ([url, init]) =>
            String(url) === settleContractEndpoint &&
            init?.method === "POST" &&
            JSON.stringify(JSON.parse(String(init.body))) ===
              JSON.stringify({
                obligation_id: "obligation-immediate-stage-10-4",
              }),
        ),
      ).toBe(true),
    );
    expect(fetchMock.mock.calls.some(([url]) => String(url) === `${apiBaseUrl}/games/${gameId}/contracts/enforce`)).toBe(
      false,
    );
    expect(within(immediateObligation).getByRole("button", { name: "Enforcing..." })).toBeDisabled();

    enforcementDeferred.resolve(
      Response.json({
        status: "ok",
        game_id: gameId,
        settled_obligation_ids: ["obligation-immediate-stage-10-4"],
        defaulted_obligation_ids: [],
        accepted_events: [
          {
            id: "event-enforced-stage-10-4",
            game_id: gameId,
            sequence: 7,
            actor_player_id: null,
            event_type: "CONTRACT_TRIGGERED_TRANSFER",
            payload: {
              contract_id: "contract-stage-10-4",
              obligation_id: "obligation-immediate-stage-10-4",
              amount: 50,
            },
            state_hash: "state-contract-stage-10-4-2",
            created_at: "2026-07-04T00:07:00.000Z",
          },
        ],
        state_hash: "state-contract-stage-10-4-2",
        event_sequence: 7,
      }),
    );

    expect(await within(panel).findByRole("status", { name: "Contract enforcement status" })).toHaveTextContent(
      "settled 1 obligation",
    );
    await waitFor(() => expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ["game-state", gameId] }));
    expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ["game", gameId] });
    expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ["legal-actions", gameId] });
    expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ["contracts", gameId] });
    expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ["obligations", gameId] });
    expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ["contract-outcomes", gameId] });
    expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ["events", gameId] });
  });

  it("ai audit view renders self-dialogue memory decisions and rejected outputs", async () => {
    renderWithQueryClient(<AiAuditPanel apiBaseUrl={apiBaseUrl} game={gameFixture()} gameId={gameId} />, createAiAuditFetchMock());

    const panel = await screen.findByRole("region", { name: "AI audit" });
    await within(panel).findByRole("tablist", { name: "AI notebook sections" });
    expect(panel).not.toHaveTextContent("Grace component audit profile");

    expect(panel).toHaveTextContent("Decision history");
    expect(panel).not.toHaveTextContent(`ai_decision_id ${decisionId}`);
    expect(panel).toHaveTextContent("ROLL_DICE");
    expect(panel).toHaveTextContent("BUY_PROPERTY");
    expect(panel).toHaveTextContent("parsed_output.action: BUY_PROPERTY is not in the legal action snapshot.");
    fireEvent.click(within(panel).getByRole("button", { name: "Show AI technical trace" }));
    expect(panel).toHaveTextContent(`ai_decision_id ${decisionId}`);
    expect(panel).toHaveTextContent("Self-dialogue timeline");
    expect(panel).toHaveTextContent("Only ROLL_DICE is legal, so buying property should be rejected.");
    expect(panel).not.toHaveTextContent(`memory_entry_id ${memoryEntryId}`);
    fireEvent.click(within(panel).getAllByRole("button", { name: "Show memory technical record" })[0]);
    expect(panel).toHaveTextContent(`memory_entry_id ${memoryEntryId}`);
    expect(panel).toHaveTextContent("Grace remembers Ada keeps a cash reserve after trades.");
    expect(panel).not.toHaveTextContent(`retrieval_record_id ${retrievalRecordId}`);
    fireEvent.click(within(panel).getAllByRole("button", { name: "Show retrieval technical record" })[0]);
    expect(panel).toHaveTextContent(`retrieval_record_id ${retrievalRecordId}`);
    expect(panel).toHaveTextContent("Retrieved context confirms Ada's cash-reserve preference.");
    expect(panel).toHaveTextContent("Rejected AI outputs");
    expect(panel).not.toHaveTextContent("rejected_output_id rejected-output-stage-10-4");
    fireEvent.click(within(panel).getByRole("button", { name: "Show rejected output technical record" }));
    expect(panel).toHaveTextContent("rejected_output_id rejected-output-stage-10-4");
    expect(panel).toHaveTextContent("parsed_output.action: BUY_PROPERTY is not legal.");

    fireEvent.click(within(panel).getByRole("tab", { name: /Profiles/ }));
    expect(await within(panel).findByText("Grace component audit profile")).toBeInTheDocument();
  });
});
