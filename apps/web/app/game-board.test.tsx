import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

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
  it("renders the named board region with all 40 stable board spaces", () => {
    render(<ClassicGameBoard game={gameFixture()} />);

    const board = screen.getByRole("region", { name: "Classic Monopoly-style board" });
    const spaces = board.querySelectorAll("[data-board-space]");

    expect(spaces).toHaveLength(40);
    spaces.forEach((space, index) => {
      expect(space).toHaveAttribute("data-space-index", String(index));
    });
    expect(within(board).getByText("GO")).toBeInTheDocument();
    expect(within(board).getByText("Boardwalk")).toBeInTheDocument();
  });

  it("derives visible token labels from player positions and updates after rerender", () => {
    const { rerender } = render(<ClassicGameBoard game={gameFixture([0, 7])} />);

    expect(screen.getByLabelText("Ada token at GO, position 0")).toHaveAttribute("data-player-token");
    expect(screen.getByLabelText("Grace token at Chance, position 7")).toHaveAttribute("data-player-token");

    rerender(<ClassicGameBoard game={gameFixture([24, 7])} />);

    expect(screen.queryByLabelText("Ada token at GO, position 0")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Ada token at Illinois Avenue, position 24")).toHaveAttribute("data-player-token");
  });
});
