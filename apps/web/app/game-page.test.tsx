import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import GameBoardPage from "./games/[gameId]/page";
import { readGame, type GameMetadata } from "../lib/api/games";

vi.mock("../lib/api/games", async () => {
  const actual = await vi.importActual<typeof import("../lib/api/games")>("../lib/api/games");
  return {
    ...actual,
    readGame: vi.fn(),
  };
});

vi.mock("./game-play-surface", () => ({
  GamePlaySurface: () => <div data-testid="game-play-surface" />,
}));

const readGameMock = vi.mocked(readGame);

function gameMetadata(): GameMetadata {
  return {
    id: "game-page-test",
    status: "active",
    ruleset_version: "classic-v1",
    seed: "page-test",
    current_phase: "START_TURN",
    settings: {},
    created_at: "2026-07-04T00:00:00.000Z",
    updated_at: "2026-07-04T00:00:00.000Z",
    players: [],
  };
}

describe("GameBoardPage", () => {
  it("renders the setup navigation as an active header button", async () => {
    readGameMock.mockResolvedValue({ state: "loaded", game: gameMetadata() });

    render(await GameBoardPage({ params: Promise.resolve({ gameId: "game-page-test" }) }));

    const setupLink = screen.getByRole("link", { name: "Setup" });
    expect(setupLink).toHaveAttribute("href", "/");
    expect(setupLink).toHaveClass("text-teal-950");
    expect(setupLink).not.toHaveClass("text-neutral-400");
    expect(screen.getByTestId("game-play-surface")).toBeInTheDocument();
  });
});
