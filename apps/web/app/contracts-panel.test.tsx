import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ContractsPanel } from "./contracts-panel";
import type { AcceptedEvent } from "../lib/api/gameplay";
import type { GameMetadata } from "../lib/api/games";
import type { RejectedActionRecord } from "../lib/api/rejected-actions";
import type { ContractOutcomeExplanation, ContractRecord, ObligationRecord } from "../lib/api/contracts";
import type { Deal } from "../lib/api/negotiations";

const apiBaseUrl = "http://api.test";
const createdAt = "2026-07-04T00:00:00.000Z";
const gameId = "game-contracts";
const adaId = "player-1";
const graceId = "player-2";
const linusId = "player-3";

type FetchMock = ReturnType<typeof vi.fn<typeof fetch>>;

function gameFixture(): GameMetadata {
  return {
    id: gameId,
    status: "active",
    ruleset_version: "classic-v1",
    seed: "stage-5-7-unit",
    current_phase: "PRE_ROLL_MANAGEMENT",
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
        controller_type: "human",
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

function contractFixture(): ContractRecord {
  return {
    id: "contract-1",
    game_id: gameId,
    deal_id: "deal-1",
    source_agreement_id: "agreement-1",
    effective_event_id: "event-deal",
    party_player_ids: [adaId, graceId],
    status: "active",
    terms: [
      {
        kind: "rent_share",
        summary: "Ada pays Grace $50 when the orange rent is collected.",
      },
    ],
    term_summary: "Ada pays Grace $50 when the orange rent is collected.",
    created_at: "2026-07-04T00:01:00.000Z",
    effective_at: "2026-07-04T00:02:00.000Z",
  };
}

function obligationsFixture(): ObligationRecord[] {
  return [
    {
      id: "obligation-upcoming",
      game_id: gameId,
      contract_id: "contract-1",
      obligated_player_id: adaId,
      counterparty_player_id: graceId,
      status: "pending",
      due_turn: 6,
      due_condition: "next orange rent collection",
      amount: 50,
      asset_summary: "$50 cash transfer",
      transfer_summary: null,
      triggering_event_id: null,
      settled_at: null,
      created_at: "2026-07-04T00:02:00.000Z",
    },
    {
      id: "obligation-settled",
      game_id: gameId,
      contract_id: "contract-1",
      obligated_player_id: adaId,
      counterparty_player_id: graceId,
      status: "settled",
      due_turn: 4,
      due_condition: "first railroad rent collection",
      amount: 75,
      asset_summary: "$75 cash transfer",
      transfer_summary: "Ada paid Grace $75 from the source agreement.",
      triggering_event_id: "event-transfer",
      settled_at: "2026-07-04T00:05:00.000Z",
      created_at: "2026-07-04T00:02:00.000Z",
    },
  ];
}

function outcomesFixture(): ContractOutcomeExplanation[] {
  return [
    {
      id: "contract-1:obligation-settled",
      game_id: gameId,
      source_deal_id: "deal-1",
      contract_id: "contract-1",
      obligation_id: "obligation-settled",
      obligation_type: "rent_share",
      trigger: { type: "rent_collected", property_id: "property_orange" },
      classic_rule_interaction: {
        policy: { rent_share_reduced_rent: "share_actual_paid", impossible_state_prevention: "strict" },
        policy_key: "rent_share_reduced_rent",
        policy_value: "share_actual_paid",
        deterministic: true,
      },
      decision: { status: "settled", decision: "rent_share_cash_transfer" },
      resulting_state_effect: { cash_transfers: [{ player_id: graceId, amount: 75 }] },
      explanation_text:
        "Contract outcome explanation: source deal deal-1 produced contract contract-1 and obligation obligation-settled with trigger rent_collected; decision rent_share_cash_transfer.",
    },
  ];
}

function dealFixture(): Deal {
  return {
    id: "deal-1",
    game_id: gameId,
    negotiation_id: "neg-1",
    proposer_player_id: adaId,
    participant_player_ids: [adaId, graceId],
    parent_deal_id: null,
    version: 1,
    status: "accepted",
    terms: [
      {
        kind: "rent_share",
        summary: "Ada pays Grace $50 when the orange rent is collected.",
      },
    ],
    validation_errors: [],
    accepted_at: "2026-07-04T00:02:00.000Z",
    rejected_at: null,
    created_at: "2026-07-04T00:01:00.000Z",
    updated_at: "2026-07-04T00:02:00.000Z",
  };
}

function eventsFixture(): AcceptedEvent[] {
  return [
    {
      id: "event-action",
      game_id: gameId,
      sequence: 1,
      actor_player_id: adaId,
      event_type: "DICE_ROLLED",
      payload: { dice: [3, 4], total: 7 },
      state_hash: "state-1",
      created_at: "2026-07-04T00:00:30.000Z",
    },
    {
      id: "event-deal",
      game_id: gameId,
      sequence: 2,
      actor_player_id: adaId,
      event_type: "DEAL_ACCEPTED",
      payload: { deal_id: "deal-1", source_agreement_id: "agreement-1" },
      state_hash: "state-2",
      created_at: "2026-07-04T00:02:00.000Z",
    },
    {
      id: "event-ai",
      game_id: gameId,
      sequence: 3,
      actor_player_id: linusId,
      event_type: "AI_DECISION_RECORDED",
      payload: { decision: "Linus declined a risky rent-share counteroffer." },
      state_hash: "state-3",
      created_at: "2026-07-04T00:03:00.000Z",
    },
    {
      id: "event-transfer",
      game_id: gameId,
      sequence: 4,
      actor_player_id: null,
      event_type: "CONTRACT_TRIGGERED_TRANSFER",
      payload: {
        contract_id: "contract-1",
        obligation_id: "obligation-settled",
        deal_id: "deal-1",
        source_agreement_id: "agreement-1",
        from_player_id: adaId,
        to_player_id: graceId,
        amount: 75,
        summary: "Ada paid Grace $75 from the source agreement.",
      },
      state_hash: "state-4",
      created_at: "2026-07-04T00:05:00.000Z",
    },
  ];
}

function rejectedActionFixture(): RejectedActionRecord {
  return {
    id: "rejection-1",
    game_id: gameId,
    actor_player_id: adaId,
    action_type: "BUY_PROPERTY",
    payload: { property_id: "property_boardwalk" },
    reason_code: "illegal_action",
    validation_errors: [
      {
        code: "illegal_action",
        message: "BUY_PROPERTY is not currently legal",
        field: "type",
      },
    ],
    legal_action_context: { phase: "PRE_ROLL_MANAGEMENT" },
    phase: "PRE_ROLL_MANAGEMENT",
    state_hash: "state-4",
    created_at: "2026-07-04T00:06:00.000Z",
  };
}

function createContractsFetchMock({
  contracts = [contractFixture()],
  obligations = obligationsFixture(),
  outcomes = outcomesFixture(),
  deals = [dealFixture()],
}: {
  contracts?: ContractRecord[];
  obligations?: ObligationRecord[];
  outcomes?: ContractOutcomeExplanation[];
  deals?: Deal[];
} = {}): FetchMock {
  return vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? "GET";

    if (url === `${apiBaseUrl}/games/${gameId}/contracts` && method === "GET") {
      return Response.json({ contracts });
    }

    if (url === `${apiBaseUrl}/games/${gameId}/obligations` && method === "GET") {
      return Response.json({ obligations });
    }

    if (url === `${apiBaseUrl}/games/${gameId}/contracts/outcomes` && method === "GET") {
      return Response.json({ outcomes });
    }

    if (url === `${apiBaseUrl}/games/${gameId}/deals` && method === "GET") {
      return Response.json({ deals });
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

  render(
    <ContractsPanel
      apiBaseUrl={apiBaseUrl}
      events={eventsFixture()}
      game={gameFixture()}
      gameId={gameId}
      rejectedActions={[rejectedActionFixture()]}
    />,
    { wrapper: Wrapper },
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ContractsPanel", () => {
  it("renders active contracts, upcoming obligations, settlement history, and source-linked transfers", async () => {
    renderPanel(createContractsFetchMock());

    const panel = await screen.findByRole("region", { name: "Contracts obligations panel" });
    await within(panel).findByText("Contract contract-1");

    expect(panel).toHaveTextContent("Active contracts");
    expect(panel).toHaveTextContent("Contract contract-1");
    expect(panel).toHaveTextContent("Parties Ada, Grace");
    expect(panel).toHaveTextContent("Status active");
    expect(panel).toHaveTextContent("deal_id deal-1");
    expect(panel).toHaveTextContent("source_agreement_id agreement-1");
    expect(panel).toHaveTextContent("effective_event_id event-deal");
    expect(panel).toHaveTextContent("Ada pays Grace $50 when the orange rent is collected.");
    expect(panel).toHaveTextContent("Created Jul 04, 2026");
    expect(panel).toHaveTextContent("Effective Jul 04, 2026");

    expect(panel).toHaveTextContent("Upcoming obligations");
    expect(panel).toHaveTextContent("obligation_id obligation-upcoming");
    expect(panel).toHaveTextContent("contract_id contract-1");
    expect(panel).toHaveTextContent("due_turn 6");
    expect(panel).toHaveTextContent("next orange rent collection");
    expect(panel).toHaveTextContent("$50 cash transfer");
    expect(panel).toHaveTextContent("Counterparty Grace");

    expect(panel).toHaveTextContent("Obligation settlement history");
    expect(panel).toHaveTextContent("settled_at Jul 04, 2026");
    expect(panel).toHaveTextContent("triggering event event-transfer");
    expect(panel).toHaveTextContent("linked contract_id contract-1");
    expect(panel).toHaveTextContent("Ada paid Grace $75 from the source agreement.");

    expect(panel).toHaveTextContent("Contract outcome explanation");
    expect(panel).toHaveTextContent("contract_id contract-1");
    expect(panel).toHaveTextContent("obligation_id obligation-settled");
    expect(panel).toHaveTextContent("source_deal_id deal-1");
    expect(panel).toHaveTextContent("decision rent_share_cash_transfer");

    const log = within(panel).getByRole("region", { name: "Game log" });
    expect(log).toHaveTextContent("Full game log");
    expect(log).toHaveTextContent("Actions");
    expect(log).toHaveTextContent("Deals");
    expect(log).toHaveTextContent("AI decisions");
    expect(log).toHaveTextContent("Rejections");
    expect(log).toHaveTextContent("DICE_ROLLED");
    expect(log).toHaveTextContent("Deal deal-1");
    expect(log).toHaveTextContent("AI_DECISION_RECORDED");
    expect(log).toHaveTextContent("Rejected action");
    expect(log).toHaveTextContent("CONTRACT_TRIGGERED_TRANSFER");
    expect(log).toHaveTextContent("Contract-triggered transfer");
    expect(log).toHaveTextContent("Source agreement agreement-1");
    expect(log).toHaveTextContent("deal deal-1");
  });

  it("filters full game log entries while keeping rejections separate from accepted events", async () => {
    renderPanel(createContractsFetchMock());

    const log = await screen.findByRole("region", { name: "Game log" });
    await within(log).findByText("Deal deal-1");
    expect(log).toHaveTextContent("DICE_ROLLED");
    expect(log).toHaveTextContent("Deal deal-1");
    expect(log).toHaveTextContent("AI_DECISION_RECORDED");
    expect(log).toHaveTextContent("Rejected action");

    fireEvent.click(within(log).getByLabelText("Actions"));
    await waitFor(() => expect(log).not.toHaveTextContent("DICE_ROLLED"));
    expect(log).not.toHaveTextContent("CONTRACT_TRIGGERED_TRANSFER");
    expect(log).toHaveTextContent("Deal deal-1");
    expect(log).toHaveTextContent("AI_DECISION_RECORDED");
    expect(log).toHaveTextContent("Rejected action");

    fireEvent.click(within(log).getByLabelText("Deals"));
    expect(log).not.toHaveTextContent("Deal deal-1");
    expect(log).toHaveTextContent("AI_DECISION_RECORDED");
    expect(log).toHaveTextContent("Rejected action");

    fireEvent.click(within(log).getByLabelText("AI decisions"));
    expect(log).not.toHaveTextContent("AI_DECISION_RECORDED");
    expect(log).toHaveTextContent("Rejected action");

    fireEvent.click(within(log).getByLabelText("Rejections"));
    expect(log).not.toHaveTextContent("Rejected action");
    expect(log).toHaveTextContent("No log entries match the selected filters.");
  });
});
