import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { NegotiationPanel } from "./negotiation-panel";
import type { Deal, Negotiation, NegotiationMessage } from "../lib/api/negotiations";
import type { GameMetadata } from "../lib/api/games";

const apiBaseUrl = "http://api.test";
const createdAt = "2026-07-04T00:00:00.000Z";
const gameId = "game-negotiation";
const adaId = "player-1";
const graceId = "player-2";
const linusId = "player-3";

type FetchMock = ReturnType<typeof vi.fn<typeof fetch>>;

function gameFixture(): GameMetadata {
  return {
    id: gameId,
    status: "active",
    ruleset_version: "classic-v1",
    seed: "stage-5-6",
    current_phase: "PRE_ROLL_MANAGEMENT",
    settings: {
      player_colors: [
        { seat_order: 0, color: "#0f766e" },
        { seat_order: 1, color: "#7c3aed" },
        { seat_order: 2, color: "#c2410c" },
      ],
      negotiation_cutoffs: {
        max_rounds: 4,
        max_proposals_per_player: 3,
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

  return render(<NegotiationPanel apiBaseUrl={apiBaseUrl} game={gameFixture()} gameId={gameId} />, {
    wrapper: Wrapper,
  });
}

function negotiationFixture(patch: Partial<Negotiation> = {}): Negotiation {
  return {
    id: "neg-1",
    game_id: gameId,
    opened_by_player_id: adaId,
    participant_player_ids: [adaId, graceId],
    topic: "Railroad package",
    context: "Ada wants a rail trade.",
    status: "opened",
    round_number: 1,
    created_at: createdAt,
    updated_at: createdAt,
    ...patch,
  };
}

function dealFixture(patch: Partial<Deal> = {}): Deal {
  return {
    id: "deal-1",
    game_id: gameId,
    negotiation_id: "neg-1",
    proposer_player_id: adaId,
    participant_player_ids: [adaId, graceId],
    parent_deal_id: null,
    version: 1,
    status: "proposed",
    terms: [
      {
        kind: "cash_transfer",
        from_player_id: adaId,
        to_player_id: graceId,
        amount: 100,
        summary: "Ada pays Grace $100",
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

function messageFixture(patch: Partial<NegotiationMessage> = {}): NegotiationMessage {
  return {
    id: "message-1",
    game_id: gameId,
    negotiation_id: "neg-1",
    author_player_id: adaId,
    body: "Opening offer.",
    created_at: createdAt,
    ...patch,
  };
}

function createNegotiationFetchMock({
  negotiations = [],
  deals = [],
  messages = {},
}: {
  negotiations?: Negotiation[];
  deals?: Deal[];
  messages?: Record<string, NegotiationMessage[]>;
} = {}) {
  const state = {
    negotiations: [...negotiations],
    deals: [...deals],
    messages: { ...messages },
    aiSteps: [] as Array<Record<string, unknown>>,
  };
  let negotiationCounter = state.negotiations.length;
  let dealCounter = state.deals.length;
  let messageCounter = Object.values(state.messages).flat().length;

  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : {};

    if (url === `${apiBaseUrl}/games/${gameId}/negotiations` && method === "GET") {
      return Response.json({ negotiations: state.negotiations });
    }

    if (url === `${apiBaseUrl}/games/${gameId}/negotiations` && method === "POST") {
      const negotiation: Negotiation = {
        id: `neg-${++negotiationCounter}`,
        game_id: gameId,
        opened_by_player_id: body.opened_by_player_id,
        participant_player_ids: body.participant_player_ids,
        topic: body.topic,
        context: body.context,
        status: "opened",
        round_number: 1,
        created_at: createdAt,
        updated_at: createdAt,
      };
      state.negotiations.unshift(negotiation);
      state.messages[negotiation.id] = [];
      return Response.json({ status: "ok", negotiation });
    }

    const messagesMatch = url.match(new RegExp(`${apiBaseUrl}/games/${gameId}/negotiations/([^/]+)/messages$`));
    if (messagesMatch && method === "GET") {
      return Response.json({ messages: state.messages[messagesMatch[1]] ?? [] });
    }
    if (messagesMatch && method === "POST") {
      const message: NegotiationMessage = {
        id: `message-${++messageCounter}`,
        game_id: gameId,
        negotiation_id: messagesMatch[1],
        author_player_id: body.author_player_id,
        body: body.body,
        created_at: createdAt,
      };
      state.messages[messagesMatch[1]] = [...(state.messages[messagesMatch[1]] ?? []), message];
      return Response.json({ status: "ok", message });
    }

    if (url === `${apiBaseUrl}/games/${gameId}/deals` && method === "GET") {
      return Response.json({ deals: state.deals });
    }
    if (url === `${apiBaseUrl}/games/${gameId}/deals` && method === "POST") {
      const negotiationDeals = state.deals.filter((deal) => deal.negotiation_id === body.negotiation_id);
      const deal: Deal = {
        id: `deal-${++dealCounter}`,
        game_id: gameId,
        negotiation_id: body.negotiation_id,
        proposer_player_id: body.proposer_player_id,
        participant_player_ids: body.participant_player_ids,
        parent_deal_id: body.parent_deal_id ?? null,
        version: negotiationDeals.length + 1,
        status: "proposed",
        terms: body.terms,
        validation_errors: [],
        accepted_at: null,
        rejected_at: null,
        created_at: createdAt,
        updated_at: createdAt,
      };
      state.deals.unshift(deal);
      return Response.json({ status: "ok", deal });
    }

    if (url === `${apiBaseUrl}/games/${gameId}/ai/step` && method === "POST") {
      state.aiSteps.push(body);
      if (body.decision_type === "open_negotiation") {
        const negotiation: Negotiation = {
          id: `neg-${++negotiationCounter}`,
          game_id: gameId,
          opened_by_player_id: body.player_id,
          participant_player_ids: [body.player_id, adaId],
          topic: "AI-opened negotiation",
          context: "Linus starts a negotiation.",
          status: "opened",
          round_number: 1,
          created_at: createdAt,
          updated_at: createdAt,
        };
        state.negotiations.unshift(negotiation);
        state.messages[negotiation.id] = [];
        return Response.json({
          ...aiStepResponse({ ...body, negotiation_id: negotiation.id }),
          negotiation_id: negotiation.id,
          negotiation,
        });
      }
      return Response.json(aiStepResponse(body));
    }

    const acceptMatch = url.match(new RegExp(`${apiBaseUrl}/games/${gameId}/deals/([^/]+)/accept$`));
    if (acceptMatch && method === "POST") {
      state.deals = state.deals.map((deal) =>
        deal.id === acceptMatch[1] ? { ...deal, status: "accepted", accepted_at: createdAt, updated_at: createdAt } : deal,
      );
      return Response.json({ status: "ok", deal: state.deals.find((deal) => deal.id === acceptMatch[1]) });
    }

    const rejectMatch = url.match(new RegExp(`${apiBaseUrl}/games/${gameId}/deals/([^/]+)/reject$`));
    if (rejectMatch && method === "POST") {
      state.deals = state.deals.map((deal) =>
        deal.id === rejectMatch[1] ? { ...deal, status: "rejected", rejected_at: createdAt, updated_at: createdAt } : deal,
      );
      return Response.json({ status: "ok", deal: state.deals.find((deal) => deal.id === rejectMatch[1]) });
    }

    const expireMatch = url.match(new RegExp(`${apiBaseUrl}/games/${gameId}/negotiations/([^/]+)/expire$`));
    if (expireMatch && method === "POST") {
      state.negotiations = state.negotiations.map((negotiation) =>
        negotiation.id === expireMatch[1] ? { ...negotiation, status: "expired", updated_at: createdAt } : negotiation,
      );
      state.deals = state.deals.map((deal) =>
        deal.negotiation_id === expireMatch[1] && deal.status === "proposed"
          ? { ...deal, status: "expired", updated_at: createdAt }
          : deal,
      );
      return Response.json({ status: "ok", negotiation: state.negotiations.find((item) => item.id === expireMatch[1]) });
    }

    throw new Error(`Unexpected fetch ${method} ${url}`);
  });

  return { fetchMock, state };
}

function aiStepResponse(body: Record<string, unknown>) {
  return {
    status: "done",
    game_id: gameId,
    player_id: body.player_id,
    decision_type: body.decision_type,
    negotiation_id: body.negotiation_id ?? null,
    ai_decision_id: `ai-${String(body.decision_type)}`,
    accepted_events: [],
    accepted_event_id: null,
    rejected_action_id: null,
    game_status: "active",
    consumed_response_opportunity: false,
    consumed_negotiation_opportunity: null,
    outcome: { kind: body.decision_type, status: "done" },
    reason_code: null,
    validation_errors: [],
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("NegotiationPanel", () => {
  it("starts a negotiation, accepts a freeform message, proposes a structured deal, and previews complex instruments", async () => {
    const { fetchMock } = createNegotiationFetchMock();
    renderPanel(fetchMock);

    const inbox = await screen.findByRole("region", { name: "Negotiation inbox" });
    expect(inbox).toHaveTextContent("Negotiation inbox");
    expect(screen.getByRole("region", { name: "Negotiation thread" })).toHaveTextContent("No negotiation selected");
    expect(screen.getByRole("region", { name: "Structured deal builder" })).toHaveTextContent("Structured deal builder");
    expect(screen.getByRole("region", { name: "Contract preview" })).toHaveTextContent("Complex instruments");

    fireEvent.change(screen.getByLabelText("Negotiation topic"), { target: { value: "Opening trade" } });
    fireEvent.change(screen.getByLabelText("Negotiation context"), { target: { value: "Ada wants a railroad swap." } });
    fireEvent.click(screen.getByRole("button", { name: "Start negotiation" }));

    const thread = await screen.findByRole("region", { name: "Negotiation thread" });
    await waitFor(() => expect(thread).toHaveTextContent("Opening trade"));
    expect(thread).toHaveTextContent("round_number 1");
    expect(thread).toHaveTextContent("Participants Ada, Grace");

    fireEvent.change(screen.getByLabelText("Freeform message"), {
      target: { value: "Could swap railroads for cash." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() => expect(thread).toHaveTextContent("Could swap railroads for cash."));

    fireEvent.click(screen.getByRole("button", { name: "Add sample complex instruments" }));
    const preview = screen.getByRole("region", { name: "Contract preview" });
    expect(preview).toHaveTextContent("Contract preview");
    expect(preview).toHaveTextContent("Complex instruments");
    for (const termKind of ["cash_transfer", "property_transfer", "loan", "option", "rent_share", "risk_transfer"]) {
      expect(preview).toHaveTextContent(termKind);
    }

    fireEvent.click(screen.getByRole("button", { name: "Propose deal" }));

    const deal = await screen.findByRole("region", { name: "Deal v1" });
    expect(deal).toHaveTextContent("Deal v1");
    expect(deal).toHaveTextContent("Proposed");
    expect(deal).toHaveTextContent("cash_transfer");

    const dealSubmission = fetchMock.mock.calls.find(
      ([url, init]) => String(url) === `${apiBaseUrl}/games/${gameId}/deals` && init?.method === "POST",
    );
    expect(dealSubmission).toBeTruthy();
    expect(JSON.parse(String(dealSubmission?.[1]?.body))).toMatchObject({
      negotiation_id: "neg-1",
      parent_deal_id: null,
      terms: expect.arrayContaining([
        expect.objectContaining({ kind: "cash_transfer" }),
        expect.objectContaining({ kind: "property_transfer" }),
        expect.objectContaining({ kind: "loan" }),
        expect.objectContaining({ kind: "option" }),
        expect.objectContaining({ kind: "rent_share" }),
        expect.objectContaining({ kind: "risk_transfer" }),
      ]),
    });
  });

  it("creates a counteroffer with parent_deal_id and shows accepted status after API acceptance", async () => {
    const negotiation = negotiationFixture();
    const { fetchMock } = createNegotiationFetchMock({
      negotiations: [negotiation],
      deals: [dealFixture()],
      messages: { [negotiation.id]: [messageFixture()] },
    });
    renderPanel(fetchMock);

    const originalDeal = await screen.findByRole("region", { name: "Deal v1" });
    fireEvent.click(within(originalDeal).getByRole("button", { name: "Counteroffer" }));
    expect(screen.getByRole("region", { name: "Structured deal builder" })).toHaveTextContent("Counteroffer");
    expect(screen.getByRole("region", { name: "Structured deal builder" })).toHaveTextContent("Parent deal deal-1");

    fireEvent.click(screen.getByRole("button", { name: "Add sample complex instruments" }));
    fireEvent.click(screen.getByRole("button", { name: "Propose deal" }));

    const counterDeal = await screen.findByRole("region", { name: "Deal v2" });
    expect(counterDeal).toHaveTextContent("Parent deal deal-1");
    expect(counterDeal).toHaveTextContent("Counteroffer");
    const counterSubmission = fetchMock.mock.calls.find(
      ([url, init]) =>
        String(url) === `${apiBaseUrl}/games/${gameId}/deals` &&
        init?.method === "POST" &&
        JSON.parse(String(init.body)).parent_deal_id === "deal-1",
    );
    expect(counterSubmission).toBeTruthy();

    fireEvent.click(within(counterDeal).getByRole("button", { name: "Accept" }));

    await waitFor(() => expect(counterDeal).toHaveTextContent("Accepted"));
    expect(counterDeal).toHaveTextContent("accepted_at");
    expect(within(counterDeal).queryByRole("button", { name: "Accept" })).not.toBeInTheDocument();
  });

  it("lets AI open a negotiation without selected thread id", async () => {
    const { fetchMock, state } = createNegotiationFetchMock();
    renderPanel(fetchMock);

    expect(await screen.findByRole("region", { name: "Negotiation thread" })).toHaveTextContent(
      "No negotiation selected",
    );

    const aiOpenControls = screen.getByRole("region", { name: "AI open negotiation controls" });
    expect(aiOpenControls).toHaveTextContent("Linus");
    fireEvent.click(within(aiOpenControls).getByRole("button", { name: "Ask AI open negotiation" }));

    await waitFor(() => expect(state.aiSteps.length).toBe(1));
    expect(state.aiSteps[0]).toMatchObject({
      player_id: linusId,
      decision_type: "open_negotiation",
      mandatory: false,
    });
    expect(state.aiSteps[0]).not.toHaveProperty("negotiation_id");
    await waitFor(() =>
      expect(screen.getByRole("region", { name: "Negotiation thread" })).toHaveTextContent("AI-opened negotiation"),
    );
  });

  it("adds AI participation in negotiation windows message and offer controls", async () => {
    // AI participation in negotiation windows; AI ability to propose complex deals
    const negotiation = negotiationFixture({
      participant_player_ids: [adaId, linusId],
      topic: "AI railroad package",
    });
    const { fetchMock, state } = createNegotiationFetchMock({
      negotiations: [negotiation],
      messages: { [negotiation.id]: [messageFixture()] },
    });
    renderPanel(fetchMock);

    const aiControls = await screen.findByRole("region", { name: "AI negotiation controls" });
    expect(aiControls).toHaveTextContent("Linus");
    fireEvent.click(within(aiControls).getByRole("button", { name: "Ask AI message" }));
    fireEvent.click(within(aiControls).getByRole("button", { name: "Ask AI offer" }));

    await waitFor(() => expect(state.aiSteps.length).toBe(2));
    expect(state.aiSteps).toEqual([
      expect.objectContaining({
        player_id: linusId,
        decision_type: "negotiation_message",
        negotiation_id: "neg-1",
        mandatory: false,
      }),
      expect.objectContaining({
        player_id: linusId,
        decision_type: "deal_proposal",
        negotiation_id: "neg-1",
        mandatory: false,
      }),
    ]);
  });

  it("AI negotiation controls stay enabled for backend active statuses", async () => {
    const activeStatuses = ["opened", "active", "countered"] as const;

    for (const status of activeStatuses) {
      const noDealNegotiation = negotiationFixture({
        id: `neg-${status}-no-deal`,
        participant_player_ids: [adaId, linusId],
        status,
        topic: `${status} AI no deal`,
      });
      const noDealMock = createNegotiationFetchMock({
        negotiations: [noDealNegotiation],
        messages: { [noDealNegotiation.id]: [messageFixture({ negotiation_id: noDealNegotiation.id })] },
      });
      const noDealRender = renderPanel(noDealMock.fetchMock);

      const noDealControls = await screen.findByRole("region", { name: "AI negotiation controls" });
      expect(within(noDealControls).getByRole("button", { name: "Ask AI message" })).toBeEnabled();
      expect(within(noDealControls).getByRole("button", { name: "Ask AI offer" })).toBeEnabled();
      expect(within(noDealControls).getByRole("button", { name: "Ask AI counteroffer" })).toBeDisabled();
      expect(within(noDealControls).getByRole("button", { name: "Ask AI accept/reject" })).toBeDisabled();

      fireEvent.click(within(noDealControls).getByRole("button", { name: "Ask AI message" }));
      fireEvent.click(within(noDealControls).getByRole("button", { name: "Ask AI offer" }));
      await waitFor(() => expect(noDealMock.state.aiSteps.length).toBe(2));
      expect(noDealMock.state.aiSteps).toEqual([
        expect.objectContaining({ decision_type: "negotiation_message", negotiation_id: noDealNegotiation.id }),
        expect.objectContaining({ decision_type: "deal_proposal", negotiation_id: noDealNegotiation.id }),
      ]);

      noDealRender.unmount();
      vi.unstubAllGlobals();

      const proposalNegotiation = negotiationFixture({
        id: `neg-${status}-proposal`,
        participant_player_ids: [adaId, linusId],
        status,
        topic: `${status} AI proposal`,
      });
      const proposedDeal = dealFixture({
        id: `deal-${status}`,
        negotiation_id: proposalNegotiation.id,
        participant_player_ids: [adaId, linusId],
        proposer_player_id: adaId,
      });
      const proposalMock = createNegotiationFetchMock({
        negotiations: [proposalNegotiation],
        deals: [proposedDeal],
        messages: { [proposalNegotiation.id]: [messageFixture({ negotiation_id: proposalNegotiation.id })] },
      });
      const proposalRender = renderPanel(proposalMock.fetchMock);

      const proposalControls = await screen.findByRole("region", { name: "AI negotiation controls" });
      expect(within(proposalControls).getByRole("button", { name: "Ask AI message" })).toBeEnabled();
      expect(within(proposalControls).getByRole("button", { name: "Ask AI offer" })).toBeDisabled();
      expect(within(proposalControls).getByRole("button", { name: "Ask AI counteroffer" })).toBeEnabled();
      expect(within(proposalControls).getByRole("button", { name: "Ask AI accept/reject" })).toBeEnabled();

      fireEvent.click(within(proposalControls).getByRole("button", { name: "Ask AI message" }));
      fireEvent.click(within(proposalControls).getByRole("button", { name: "Ask AI offer" }));
      fireEvent.click(within(proposalControls).getByRole("button", { name: "Ask AI counteroffer" }));
      fireEvent.click(within(proposalControls).getByRole("button", { name: "Ask AI accept/reject" }));
      await waitFor(() => expect(proposalMock.state.aiSteps.length).toBe(3));
      expect(proposalMock.state.aiSteps).toEqual([
        expect.objectContaining({ decision_type: "negotiation_message", negotiation_id: proposalNegotiation.id }),
        expect.objectContaining({ decision_type: "counteroffer", negotiation_id: proposalNegotiation.id }),
        expect.objectContaining({ decision_type: "accept_reject", negotiation_id: proposalNegotiation.id }),
      ]);
      expect(proposalMock.state.aiSteps).not.toContainEqual(
        expect.objectContaining({ decision_type: "deal_proposal", negotiation_id: proposalNegotiation.id }),
      );

      proposalRender.unmount();
      vi.unstubAllGlobals();
    }
  });

  it("adds AI response to offers counteroffer and accept reject controls", async () => {
    // AI response to offers
    const negotiation = negotiationFixture({
      participant_player_ids: [adaId, linusId],
      topic: "AI response request",
    });
    const deal = dealFixture({
      participant_player_ids: [adaId, linusId],
      proposer_player_id: adaId,
    });
    const { fetchMock, state } = createNegotiationFetchMock({
      negotiations: [negotiation],
      deals: [deal],
      messages: { [negotiation.id]: [messageFixture()] },
    });
    renderPanel(fetchMock);

    const aiControls = await screen.findByRole("region", { name: "AI negotiation controls" });
    fireEvent.click(within(aiControls).getByRole("button", { name: "Ask AI counteroffer" }));
    fireEvent.click(within(aiControls).getByRole("button", { name: "Ask AI accept/reject" }));

    await waitFor(() => expect(state.aiSteps.length).toBe(2));
    expect(state.aiSteps).toEqual([
      expect.objectContaining({
        player_id: linusId,
        decision_type: "counteroffer",
        negotiation_id: "neg-1",
        mandatory: false,
      }),
      expect.objectContaining({
        player_id: linusId,
        decision_type: "accept_reject",
        negotiation_id: "neg-1",
        mandatory: false,
      }),
    ]);
  });

  it("shows backend terminal negotiation records as closed and removes executable controls", async () => {
    const terminalStatuses = ["accepted", "rejected", "expired", "executed"] as const;
    const terminalNegotiations = terminalStatuses.map((status) =>
      negotiationFixture({
        id: `neg-${status}`,
        participant_player_ids: [adaId, linusId],
        status,
        topic: `${status} terminal test`,
      }),
    );
    const { fetchMock } = createNegotiationFetchMock({
      negotiations: terminalNegotiations,
      deals: terminalStatuses.map((status) =>
        dealFixture({
          id: `deal-${status}`,
          negotiation_id: `neg-${status}`,
          participant_player_ids: [adaId, linusId],
          proposer_player_id: adaId,
        }),
      ),
      messages: Object.fromEntries(terminalStatuses.map((status) => [`neg-${status}`, []])),
    });
    renderPanel(fetchMock);

    await screen.findByRole("button", { name: /accepted terminal test/i });

    for (const status of terminalStatuses) {
      fireEvent.click(screen.getByRole("button", { name: new RegExp(`${status} terminal test`, "i") }));
      const thread = await screen.findByRole("region", { name: "Negotiation thread" });
      await waitFor(() => expect(thread).toHaveTextContent(`${status} terminal test`));
      expect(thread).toHaveTextContent(status);

      const aiControls = within(thread).getByRole("region", { name: "AI negotiation controls" });
      expect(within(aiControls).getByRole("button", { name: "Ask AI message" })).toBeDisabled();
      expect(within(aiControls).getByRole("button", { name: "Ask AI offer" })).toBeDisabled();
      expect(within(aiControls).getByRole("button", { name: "Ask AI counteroffer" })).toBeDisabled();
      expect(within(aiControls).getByRole("button", { name: "Ask AI accept/reject" })).toBeDisabled();

      fireEvent.change(screen.getByLabelText("Freeform message"), {
        target: { value: `Blocked message for ${status}.` },
      });
      expect(screen.getByRole("button", { name: "Send message" })).toBeDisabled();
      expect(screen.getByRole("button", { name: "Add sample complex instruments" })).toBeDisabled();
      expect(screen.queryByRole("button", { name: "Expire negotiation" })).not.toBeInTheDocument();

      const deal = within(thread).getByRole("region", { name: "Deal v1" });
      expect(within(deal).queryByRole("button", { name: "Counteroffer" })).not.toBeInTheDocument();
      expect(within(deal).queryByRole("button", { name: "Accept" })).not.toBeInTheDocument();
      expect(within(deal).queryByRole("button", { name: "Reject" })).not.toBeInTheDocument();
    }
  });
});
