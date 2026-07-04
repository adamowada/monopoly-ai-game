import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AiAuditPanel } from "./ai-audit-panel";
import type {
  AiDecision,
  AiMemoryEntry,
  AiProfile,
  AiRejectedOutput,
  AiRetrievalRecord,
  AiSelfDialogueRecord,
} from "../lib/api/ai-audit";
import type { GameMetadata } from "../lib/api/games";

const apiBaseUrl = "http://api.test";
const gameId = "game-ai-audit";
const createdAt = "2026-07-04T00:00:00.000Z";
const adaId = "player-1";
const graceId = "player-2";
const linusId = "player-3";
const graceProfileId = "profile-grace";
const decisionId = "decision-grace-1";
const memoryEntryId = "memory-grace-1";
const retrievalRecordId = "retrieval-grace-1";

type FetchMock = ReturnType<typeof vi.fn<typeof fetch>>;

function gameFixture(): GameMetadata {
  return {
    id: gameId,
    status: "active",
    ruleset_version: "classic-v1",
    seed: "stage-5-8-unit",
    current_phase: "START_TURN",
    settings: {
      player_colors: [
        { seat_order: 0, color: "#0f766e" },
        { seat_order: 1, color: "#7c3aed" },
        { seat_order: 2, color: "#c2410c" },
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
        state: { cash: 1500, position: 0 },
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
        state: { cash: 1500, position: 0 },
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
        state: { cash: 1500, position: 0 },
        created_at: createdAt,
        updated_at: createdAt,
      },
    ],
  };
}

function profilesFixture(): AiProfile[] {
  return [
    {
      ai_profile_id: graceProfileId,
      game_id: gameId,
      player_id: graceId,
      display_name: "Grace audit profile",
      traits: ["risk-aware", "rent-focused"],
      personality: "Careful analyst",
      play_style: "Builds cash buffers before auctions.",
      created_at: "2026-07-04T00:01:00.000Z",
    },
    {
      ai_profile_id: "profile-linus",
      game_id: gameId,
      player_id: linusId,
      display_name: "Linus audit profile",
      traits: ["opportunistic"],
      personality: "Fast negotiator",
      play_style: "Prefers short-term cash pressure.",
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
      state_hash: "mock-state-ai-audit-1",
      legal_actions: [
        {
          actor_id: graceId,
          type: "ROLL_DICE",
          payload: {},
          expected_state_hash: "mock-state-ai-audit-1",
          expected_event_sequence: 4,
          description: "Grace can roll dice.",
          schema: {},
        },
      ],
      prompt_context: {
        phase: "START_TURN",
        board_position: 0,
        legal_action_count: 1,
      },
      raw_output: "{\"action\":\"ROLL_DICE\"}",
      parsed_output: {
        action: "ROLL_DICE",
        confidence: 0.81,
      },
      validation_errors: [],
      memory_entry_ids: [memoryEntryId],
      retrieval_record_ids: [retrievalRecordId],
      status: "accepted",
      created_at: "2026-07-04T00:02:00.000Z",
    },
  ];
}

function selfDialogueFixture(): AiSelfDialogueRecord[] {
  return [
    {
      self_dialogue_id: "dialogue-1",
      game_id: gameId,
      ai_decision_id: decisionId,
      ai_profile_id: graceProfileId,
      sequence: 1,
      role: "critic",
      content: "The legal action set is narrow, so preserve tempo with ROLL_DICE.",
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
      kind: "strategy",
      content: "Grace remembers Ada prefers keeping $200 cash after trades.",
      importance: 0.74,
      created_at: "2026-07-04T00:01:30.000Z",
    },
  ];
}

function retrievalFixture(): AiRetrievalRecord[] {
  return [
    {
      retrieval_record_id: retrievalRecordId,
      game_id: gameId,
      ai_decision_id: decisionId,
      ai_profile_id: graceProfileId,
      memory_entry_id: memoryEntryId,
      source_type: "memory",
      source_id: memoryEntryId,
      score: 0.93,
      content: "Retrieved context confirms Ada cash-reserve behavior.",
      created_at: "2026-07-04T00:01:45.000Z",
    },
  ];
}

function rejectedOutputsFixture(): AiRejectedOutput[] {
  return [
    {
      rejected_output_id: "rejected-output-1",
      game_id: gameId,
      ai_decision_id: decisionId,
      ai_profile_id: graceProfileId,
      player_id: graceId,
      state_hash: "mock-state-ai-audit-1",
      raw_output: "{\"action\":\"BUY_PROPERTY\"}",
      parsed_output: {
        action: "BUY_PROPERTY",
        property_id: "property_boardwalk",
      },
      validation_errors: [
        {
          code: "illegal_action",
          message: "BUY_PROPERTY is not in the Legal actions snapshot.",
          field: "parsed_output.action",
        },
      ],
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

function renderPanel(fetchMock: FetchMock) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: 0 },
      mutations: { retry: false },
    },
  });
  vi.stubGlobal("fetch", fetchMock);

  function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
  }

  render(<AiAuditPanel apiBaseUrl={apiBaseUrl} game={gameFixture()} gameId={gameId} />, { wrapper: Wrapper });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("AiAuditPanel", () => {
  it("renders profiles, decision traceability, memory, retrievals, dialogue, and rejected outputs from server data", async () => {
    renderPanel(createAiAuditFetchMock());

    const panel = await screen.findByRole("region", { name: "AI audit" });
    await within(panel).findByText("Grace audit profile");

    expect(panel).toHaveTextContent("Private local research view");
    expect(panel).toHaveTextContent("AI profile");
    expect(panel).toHaveTextContent("Grace");
    expect(panel).toHaveTextContent("Traits");
    expect(panel).toHaveTextContent("risk-aware, rent-focused");
    expect(panel).toHaveTextContent("Personality");
    expect(panel).toHaveTextContent("Careful analyst");
    expect(panel).toHaveTextContent("Play style");
    expect(panel).toHaveTextContent("Builds cash buffers before auctions.");

    expect(panel).toHaveTextContent("Decision history");
    expect(panel).toHaveTextContent("ai_decision_id decision-grace-1");
    expect(panel).toHaveTextContent("ai_profile_id profile-grace");
    expect(panel).toHaveTextContent("state_hash mock-state-ai-audit-1");
    expect(panel).toHaveTextContent("Legal actions snapshot");
    expect(panel).toHaveTextContent("ROLL_DICE");
    expect(panel).toHaveTextContent("Prompt context");
    expect(panel).toHaveTextContent("board_position");
    expect(panel).toHaveTextContent("Raw output");
    expect(panel).toHaveTextContent("{\"action\":\"ROLL_DICE\"}");
    expect(panel).toHaveTextContent("Parsed output");
    expect(panel).toHaveTextContent("\"confidence\": 0.81");

    expect(panel).toHaveTextContent("Self-dialogue timeline");
    expect(panel).toHaveTextContent("self_dialogue_id dialogue-1");
    expect(panel).toHaveTextContent("Linked decision decision-grace-1");
    expect(panel).toHaveTextContent("The legal action set is narrow");

    expect(panel).toHaveTextContent("Memory entries");
    expect(panel).toHaveTextContent("memory_entry_id memory-grace-1");
    expect(panel).toHaveTextContent("Used by decision decision-grace-1");
    expect(panel).toHaveTextContent("Grace remembers Ada prefers keeping $200 cash after trades.");

    expect(panel).toHaveTextContent("Retrieved context records");
    expect(panel).toHaveTextContent("retrieval_record_id retrieval-grace-1");
    expect(panel).toHaveTextContent("Retrieved context confirms Ada cash-reserve behavior.");

    expect(panel).toHaveTextContent("Rejected AI outputs");
    expect(panel).toHaveTextContent("rejected_output_id rejected-output-1");
    expect(panel).toHaveTextContent("Validation errors");
    expect(panel).toHaveTextContent("parsed_output.action: BUY_PROPERTY is not in the Legal actions snapshot.");
  });
});
