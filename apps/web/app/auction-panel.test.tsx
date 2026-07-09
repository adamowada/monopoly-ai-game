import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AuctionPanel } from "./auction-panel";
import type { AcceptedEvent, GameStateResponse, LegalAction } from "../lib/api/gameplay";
import type { GameMetadata } from "../lib/api/games";

const createdAt = "2026-07-04T00:00:00.000Z";
const gameId = "game-auction";
const adaId = "player-1";
const graceId = "player-2";
const linusId = "player-3";

function gameFixture(): GameMetadata {
  return {
    id: gameId,
    status: "active",
    ruleset_version: "classic-v1",
    seed: "auction",
    current_phase: "PURCHASE_OR_AUCTION",
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
        state: {
          cash: 1500,
          position: 1,
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
      {
        id: linusId,
        game_id: gameId,
        seat_order: 2,
        name: "Linus",
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

function stateFixture(activeAuction: Record<string, unknown> | null = null): GameStateResponse {
  return {
    game_id: gameId,
    state: {
      game_id: gameId,
      seed: "auction",
      players: [
        { id: adaId, cash: 1500, position: 1 },
        { id: graceId, cash: 1500, position: 0 },
        { id: linusId, cash: 1500, position: 0 },
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
      active_auction: activeAuction,
      turn: {
        phase: "PURCHASE_OR_AUCTION",
        current_player_index: 0,
        current_player_id: adaId,
      },
    },
    state_hash: "state-0",
    event_sequence: 0,
  };
}

function legalAction(
  actorId: string,
  type: "START_AUCTION" | "BID_AUCTION" | "PASS_AUCTION",
  payload: Record<string, unknown> = {},
): LegalAction {
  return {
    actor_id: actorId,
    type,
    payload: {
      property_id: "property_mediterranean_avenue",
      ...payload,
    },
    expected_state_hash: "state-0",
    expected_event_sequence: 0,
    description: null,
    schema:
      type === "BID_AUCTION"
        ? {
            properties: {
              amount: {
                type: "integer",
                minimum: 26,
              },
            },
          }
        : {},
  };
}

function renderPanel({
  snapshot = stateFixture(),
  legalActions = [],
  events = [],
  controlsDisabled = false,
  pendingActionType = null,
  onSubmit = vi.fn(),
}: {
  snapshot?: GameStateResponse;
  legalActions?: LegalAction[];
  events?: AcceptedEvent[];
  controlsDisabled?: boolean;
  pendingActionType?: string | null;
  onSubmit?: (action: LegalAction) => void;
} = {}) {
  render(
    <AuctionPanel
      controlsDisabled={controlsDisabled}
      events={events}
      game={gameFixture()}
      legalActions={legalActions}
      onSubmit={onSubmit}
      pendingActionType={pendingActionType}
      snapshot={snapshot}
    />,
  );
  return { onSubmit };
}

describe("AuctionPanel", () => {
  it("starts an auction from a backend-returned START_AUCTION purchase decision", () => {
    const { onSubmit } = renderPanel({
      legalActions: [legalAction(adaId, "START_AUCTION")],
    });

    const auction = screen.getByRole("region", { name: "Auction" });
    expect(auction).toHaveTextContent("Auction state");
    expect(auction).toHaveTextContent("No active auction");
    expect(auction).toHaveTextContent("Mediterranean Avenue");
    expect(auction).toHaveTextContent("Current high bid");
    expect(auction).toHaveTextContent("Remaining bidders");
    expect(auction).not.toHaveTextContent("Auction result");

    fireEvent.click(within(auction).getByRole("button", { name: "Start auction" }));

    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ type: "START_AUCTION" }));
  });

  it("shows active auction state, remaining bidders, passed players, and bid/pass controls by eligible player", () => {
    const { onSubmit } = renderPanel({
      snapshot: stateFixture({
        property_id: "property_mediterranean_avenue",
        high_bidder_id: adaId,
        high_bid_amount: 25,
        passed_player_ids: [linusId],
      }),
      legalActions: [
        legalAction(graceId, "BID_AUCTION", { amount: 26 }),
        legalAction(graceId, "PASS_AUCTION"),
      ],
    });

    const auction = screen.getByRole("region", { name: "Auction" });
    expect(auction).toHaveTextContent(/Auction state\s*Active/);
    expect(auction).toHaveTextContent("Mediterranean Avenue");
    expect(auction).toHaveTextContent(/Current high bid\s*\$25/);
    expect(auction).toHaveTextContent(/Current high bidder\s*Ada/);
    expect(auction).toHaveTextContent(/Remaining bidders\s*Ada, Grace/);
    expect(auction).toHaveTextContent(/Passed players\s*Linus/);

    const graceControls = within(auction).getByRole("group", { name: "Grace auction controls" });
    fireEvent.click(within(graceControls).getByRole("button", { name: "Bid" }));
    fireEvent.click(within(graceControls).getByRole("button", { name: "Pass" }));

    expect(onSubmit).toHaveBeenNthCalledWith(
      1,
      expect.objectContaining({
        actor_id: graceId,
        type: "BID_AUCTION",
        payload: expect.objectContaining({ amount: 26 }),
      }),
    );
    expect(onSubmit).toHaveBeenNthCalledWith(
      2,
      expect.objectContaining({
        actor_id: graceId,
        type: "PASS_AUCTION",
      }),
    );
  });

  it("adds a concrete next bid amount when the legal BID_AUCTION action omits one", () => {
    const { onSubmit } = renderPanel({
      snapshot: stateFixture({
        property_id: "property_mediterranean_avenue",
        high_bidder_id: adaId,
        high_bid_amount: 25,
        passed_player_ids: [],
      }),
      legalActions: [legalAction(graceId, "BID_AUCTION", { amount: undefined })],
    });

    fireEvent.click(screen.getByRole("button", { name: "Bid" }));

    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({
        type: "BID_AUCTION",
        payload: expect.objectContaining({ amount: 26 }),
      }),
    );
  });

  it("lets a bidder type a custom legal bid amount", () => {
    const { onSubmit } = renderPanel({
      snapshot: stateFixture({
        property_id: "property_mediterranean_avenue",
        high_bidder_id: adaId,
        high_bid_amount: 25,
        passed_player_ids: [],
      }),
      legalActions: [legalAction(graceId, "BID_AUCTION", { amount: 26 })],
    });

    const graceControls = screen.getByRole("group", { name: "Grace auction controls" });
    fireEvent.change(within(graceControls).getByRole("spinbutton", { name: "Grace bid amount" }), {
      target: { value: "80" },
    });
    fireEvent.click(within(graceControls).getByRole("button", { name: "Bid" }));

    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({
        type: "BID_AUCTION",
        payload: expect.objectContaining({ amount: 80 }),
      }),
    );
  });

  it("hides auction result until an auction result event exists", () => {
    renderPanel({
      events: [
        {
          id: "event-owner",
          game_id: gameId,
          sequence: 1,
          actor_player_id: adaId,
          event_type: "PROPERTY_OWNER_SET",
          payload: { property_id: "property_mediterranean_avenue", owner_id: adaId },
          state_hash: "state-1",
          created_at: "2026-07-04T00:01:00.000Z",
        },
      ],
    });

    const auction = screen.getByRole("region", { name: "Auction" });
    expect(within(auction).queryByRole("region", { name: "Auction result" })).not.toBeInTheDocument();
    expect(auction).not.toHaveTextContent("Winner Ada");
  });

  it("shows the latest auction result event with property name, winner, and paid bid", () => {
    renderPanel({
      events: [
        {
          id: "event-owner",
          game_id: gameId,
          sequence: 1,
          actor_player_id: graceId,
          event_type: "PROPERTY_OWNER_SET",
          payload: { property_id: "property_mediterranean_avenue", owner_id: graceId },
          state_hash: "state-1",
          created_at: "2026-07-04T00:01:00.000Z",
        },
        {
          id: "event-result",
          game_id: gameId,
          sequence: 2,
          actor_player_id: graceId,
          event_type: "AUCTION_RESULT",
          payload: {
            property_id: "property_mediterranean_avenue",
            winner_id: graceId,
            winning_bid: 26,
          },
          state_hash: "state-2",
          created_at: "2026-07-04T00:01:01.000Z",
        },
      ],
    });

    const auction = screen.getByRole("region", { name: "Auction" });
    expect(auction).toHaveTextContent("Auction result");
    expect(auction).toHaveTextContent("Grace won Mediterranean Avenue for $26");
  });
});
