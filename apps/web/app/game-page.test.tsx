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
  it("renders the live game surface without a redundant page heading", async () => {
    readGameMock.mockResolvedValue({ state: "loaded", game: gameMetadata() });

    render(await GameBoardPage({ params: Promise.resolve({ gameId: "game-page-test" }) }));

    expect(screen.queryByRole("heading", { level: 1, name: /Game table/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Open game menu" })).not.toBeInTheDocument();
    expect(screen.getByTestId("game-play-surface")).toBeInTheDocument();
  });
});
