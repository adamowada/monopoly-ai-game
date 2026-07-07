import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { BOARD_SPACES } from "@monopoly-ai-game/schemas";

import { DECK_ART, SPACE_ART_BY_ID } from "./board-art";
import { ClassicGameBoard } from "./game-board";
import type { GameMetadata } from "../lib/api/games";

const createdAt = "2026-07-04T00:00:00.000Z";

function gameFixture(positions: number[] = [0, 7]): GameMetadata {
  return {
    id: "game-board-test",
    status: "active",
    ruleset_version: "classic-v1",
    seed: "board-test",
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
        id: "player-1",
        game_id: "game-board-test",
        seat_order: 0,
        name: "Ada",
        controller_type: "human",
        status: "active",
        state: {
          cash: 1500,
          position: positions[0],
        },
        created_at: createdAt,
        updated_at: createdAt,
      },
      {
        id: "player-2",
        game_id: "game-board-test",
        seat_order: 1,
        name: "Grace",
        controller_type: "ai",
        status: "active",
        state: {
          cash: 1500,
          position: positions[1],
        },
        created_at: createdAt,
        updated_at: createdAt,
      },
    ],
  };
}

describe("ClassicGameBoard", () => {
  it("defines original art metadata for every board space and both decks", () => {
    for (const space of BOARD_SPACES) {
      const art = SPACE_ART_BY_ID[space.id];
      expect(art, `${space.id} should have art metadata`).toBeDefined();
      expect(art.title).toBeTruthy();
      expect(art.motif).toBeTruthy();
      expect(art.palette.length).toBeGreaterThanOrEqual(2);
    }

    expect(DECK_ART.chance.title).toBe("Chance");
    expect(DECK_ART.community_chest.title).toBe("Community Chest");
  });

  it("renders the named board region with all 40 stable board spaces", () => {
    render(<ClassicGameBoard game={gameFixture()} />);

    const board = screen.getByRole("region", { name: "Classic Monopoly-style board" });
    const spaces = board.querySelectorAll("[data-board-space]");

    expect(spaces).toHaveLength(40);
    spaces.forEach((space, index) => {
      expect(space).toHaveAttribute("data-space-index", String(index));
    });
    expect(within(board).getByText("GO")).toBeInTheDocument();
    expect(board.querySelector("[data-space-index='39'] [data-space-name]")).toHaveTextContent("Boardwalk");
    expect(board.querySelector("[data-space-index='39'] [data-space-bottom-label]")).toHaveTextContent("$400");
  });

  it("uses classic board geometry with only the four corners rendered as squares", () => {
    render(<ClassicGameBoard game={gameFixture()} />);

    const board = screen.getByRole("region", { name: "Classic Monopoly-style board" });
    const squareSpaces = [...board.querySelectorAll("[data-space-shape='square']")];
    const rectangleSpaces = [...board.querySelectorAll("[data-space-shape='rectangle']")];

    expect(squareSpaces.map((space) => space.getAttribute("data-space-index"))).toEqual(["0", "10", "20", "30"]);
    expect(rectangleSpaces).toHaveLength(36);
    expect(board.querySelector("[data-board-surface='cream-light-green']")).toBeTruthy();
  });

  it("renders a game-facing center title and physical deck art without research copy", () => {
    render(<ClassicGameBoard game={gameFixture()} />);

    const board = screen.getByRole("region", { name: "Classic Monopoly-style board" });

    expect(within(board).getByText("Monopoly 2.0")).toBeInTheDocument();
    expect(within(board).getByLabelText("Chance deck art")).toBeInTheDocument();
    expect(within(board).getByLabelText("Community Chest deck art")).toBeInTheDocument();
    expect(within(board).queryByText("Local research table")).not.toBeInTheDocument();
    expect(within(board).queryByText("Original vector board surface. Token locations are rendered from backend player state.")).not.toBeInTheDocument();
    expect(within(board).queryByText("40 spaces")).not.toBeInTheDocument();
    expect(within(board).queryByText("Stable 0-39 indexes")).not.toBeInTheDocument();
    expect(within(board).queryByText("No board scans")).not.toBeInTheDocument();
  });

  it("renders classic street property cells with only a top color band, name, price, and hover details", () => {
    render(<ClassicGameBoard game={gameFixture()} />);

    const board = screen.getByRole("region", { name: "Classic Monopoly-style board" });
    const boardwalk = board.querySelector("[data-space-index='39']");

    expect(boardwalk).toHaveAttribute("data-space-kind", "street-property");
    expect(boardwalk?.querySelector("[data-property-color-band]")).toBeTruthy();
    expect(boardwalk?.querySelector("[data-space-art]")).toBeNull();
    expect(boardwalk?.querySelector("[data-space-bottom-label]")).toHaveTextContent("$400");
    expect(boardwalk?.querySelector("[data-property-hover]")).toHaveTextContent("Rent $50");
    expect(boardwalk?.querySelector("[data-property-hover]")).toHaveTextContent("Mortgage $200");
    expect(boardwalk?.querySelector("[data-space-name]")).toHaveClass("uppercase");
  });

  it("renders non-street board cells with a top name, large logo, and required bottom instruction", () => {
    render(<ClassicGameBoard game={gameFixture()} />);

    const board = screen.getByRole("region", { name: "Classic Monopoly-style board" });
    const readingRailroad = board.querySelector("[data-space-index='5']");
    const communityChest = board.querySelector("[data-space-index='2']");
    const luxuryTax = board.querySelector("[data-space-index='38']");
    const chance = board.querySelector("[data-space-index='7']");

    expect(readingRailroad).toHaveAttribute("data-space-kind", "railroad");
    expect(readingRailroad?.querySelector("[data-property-color-band]")).toBeNull();
    expect(readingRailroad?.querySelector("[data-space-art]")).toBeTruthy();
    expect(readingRailroad?.querySelector("[data-space-bottom-label]")).toHaveTextContent("$200");
    expect(readingRailroad?.querySelector("[data-property-hover]")).toHaveTextContent("Rent $25");

    expect(communityChest?.querySelector("[data-space-art]")).toBeTruthy();
    expect(communityChest?.querySelector("[data-space-bottom-label]")).toHaveTextContent("Follow instructions on top card");
    expect(communityChest?.querySelector("[data-property-hover]")).toBeNull();

    expect(luxuryTax?.querySelector("[data-space-bottom-label]")).toHaveTextContent("pay $75.00");
    expect(chance?.querySelector("[data-property-hover]")).toBeNull();
  });

  it("derives visible token labels from player positions and updates after rerender", () => {
    const { rerender } = render(<ClassicGameBoard game={gameFixture([0, 7])} />);

    expect(screen.getByLabelText("Ada token at GO, position 0")).toHaveAttribute("data-player-token");
    expect(screen.getByLabelText("Grace token at Chance, position 7")).toHaveAttribute("data-player-token");
    expect(screen.getByLabelText("Ada token at GO, position 0")).toHaveAttribute("title", "Ada");
    expect(
      screen.getByLabelText("Ada token at GO, position 0").querySelector("[data-player-token-label]"),
    ).toHaveTextContent("Ada");

    rerender(<ClassicGameBoard game={gameFixture([24, 7])} />);

    expect(screen.queryByLabelText("Ada token at GO, position 0")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Ada token at Illinois Avenue, position 24")).toHaveAttribute("data-player-token");
  });

  it("can show dice motion and a travelling token position while an accepted roll resolves", () => {
    const { rerender } = render(
      <ClassicGameBoard
        game={gameFixture([7, 0])}
        motion={{
          dice: [3, 4],
          displayPosition: 0,
          fromPosition: 0,
          playerId: "player-1",
          status: "moving",
          toPosition: 7,
          total: 7,
        }}
      />,
    );

    const diceStatus = screen.getByRole("status", { name: "Dice roll animation" });
    expect(diceStatus).toHaveTextContent("3 + 4");
    expect(screen.getByLabelText("Ada token at GO, position 0")).toBeInTheDocument();

    rerender(
      <ClassicGameBoard
        game={gameFixture([7, 0])}
        motion={{
          dice: [3, 4],
          displayPosition: 3,
          fromPosition: 0,
          playerId: "player-1",
          status: "moving",
          toPosition: 7,
          total: 7,
        }}
      />,
    );

    expect(screen.getByLabelText("Ada token at Baltic Avenue, position 3")).toBeInTheDocument();
    expect(screen.queryByLabelText("Ada token at GO, position 0")).not.toBeInTheDocument();
  });
});
