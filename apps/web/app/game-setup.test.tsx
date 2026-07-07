import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AI_PLAYER_NAMES, GameSetupPanel } from "./game-setup";
import { createGame, type GameMetadata } from "../lib/api/games";

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

function gameMetadata(overrides: Partial<GameMetadata> = {}): GameMetadata {
  return {
    id: "game-created",
    status: "active",
    ruleset_version: "classic-v1",
    seed: "manual-seed",
    current_phase: "START_TURN",
    settings: {
      player_colors: [
        { seat_order: 0, color: "#0f766e" },
        { seat_order: 1, color: "#7c3aed" },
      ],
      negotiation_cutoffs: {
        max_rounds: 4,
        max_proposals_per_player: 3,
      },
    },
    created_at: "2026-07-04T00:00:00.000Z",
    updated_at: "2026-07-04T00:00:00.000Z",
    players: [
      {
        id: "player-1",
        game_id: "game-created",
        seat_order: 0,
        name: "Ada",
        controller_type: "human",
        status: "active",
        state: {},
        created_at: "2026-07-04T00:00:00.000Z",
        updated_at: "2026-07-04T00:00:00.000Z",
      },
      {
        id: "player-2",
        game_id: "game-created",
        seat_order: 1,
        name: "Grace",
        controller_type: "ai",
        status: "active",
        state: {},
        created_at: "2026-07-04T00:00:00.000Z",
        updated_at: "2026-07-04T00:00:00.000Z",
      },
    ],
    ...overrides,
  };
}

describe("GameSetupPanel", () => {
  beforeEach(() => {
    createGameMock.mockReset();
    push.mockReset();
  });

  it("renders setup controls for local 2-5 player game creation", () => {
    render(<GameSetupPanel initialSeed="seed-fixed" />);

    expect(screen.getByRole("heading", { level: 2, name: "Game setup" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Seed" })).toHaveValue("seed-fixed");
    expect(screen.getByRole("button", { name: "Generate seed" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add player" })).toBeInTheDocument();

    const players = screen.getByRole("table", { name: "Configured players" });
    expect(within(players).getByRole("textbox", { name: "Player 1 name" })).toHaveValue("Player 1");
    expect(within(players).getByRole("combobox", { name: "Player 1 type" })).toHaveValue("human");
    expect(within(players).getByRole("textbox", { name: "Player 1 color hex" })).toHaveValue("#0f766e");
    expect(within(players).getByRole("textbox", { name: "Player 2 name" })).toHaveValue("Player 2");

    expect(screen.getByRole("spinbutton", { name: "Max negotiation rounds" })).toHaveValue(3);
    expect(screen.getByRole("spinbutton", { name: "Proposal limit per player" })).toHaveValue(4);
  });

  it("uses a stable default seed when no initial seed is provided", () => {
    render(<GameSetupPanel />);

    expect(screen.getByRole("textbox", { name: "Seed" })).toHaveValue("setup-local-table");
  });

  it("sends player colors and negotiation cutoffs through settings before navigating", async () => {
    createGameMock.mockResolvedValue({ state: "loaded", game: gameMetadata() });
    render(<GameSetupPanel initialSeed="seed-fixed" />);

    fireEvent.change(screen.getByRole("textbox", { name: "Seed" }), {
      target: { value: "manual-seed" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "Player 1 name" }), {
      target: { value: "Ada" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "Player 2 name" }), {
      target: { value: "Grace" },
    });
    fireEvent.change(screen.getByRole("combobox", { name: "Player 2 type" }), {
      target: { value: "ai" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "Player 2 color hex" }), {
      target: { value: "#7c3aed" },
    });
    fireEvent.change(screen.getByRole("spinbutton", { name: "Max negotiation rounds" }), {
      target: { value: "4" },
    });
    fireEvent.change(screen.getByRole("spinbutton", { name: "Proposal limit per player" }), {
      target: { value: "3" },
    });

    fireEvent.click(screen.getByRole("button", { name: "Create game" }));

    await waitFor(() => {
      expect(createGameMock).toHaveBeenCalledWith({
        seed: "manual-seed",
        players: [
          { name: "Ada", kind: "human" },
          { name: "Grace", kind: "ai" },
        ],
        settings: {
          player_colors: [
            { seat_order: 0, color: "#0f766e" },
            { seat_order: 1, color: "#7c3aed" },
          ],
          negotiation_cutoffs: {
            max_rounds: 4,
            max_proposals_per_player: 3,
          },
        },
      });
    });
    await waitFor(() => expect(push).toHaveBeenCalledWith("/games/game-created"));
  });

  it("auto-generates common names when seats become AI players", () => {
    render(<GameSetupPanel initialSeed="seed-fixed" />);

    fireEvent.change(screen.getByRole("combobox", { name: "Player 2 type" }), {
      target: { value: "ai" },
    });

    const player2Name = screen.getByRole("textbox", { name: "Player 2 name" });
    expect(player2Name).not.toHaveValue("Player 2");
    expect(AI_PLAYER_NAMES).toContain(player2Name.getAttribute("value"));
    expect(AI_PLAYER_NAMES).toHaveLength(30);

    fireEvent.click(screen.getByRole("button", { name: "Add player" }));
    fireEvent.change(screen.getByRole("combobox", { name: "Player 3 type" }), {
      target: { value: "ai" },
    });

    const player3Name = screen.getByRole("textbox", { name: "Player 3 name" });
    expect(player3Name).not.toHaveValue("Player 3");
    expect(AI_PLAYER_NAMES).toContain(player3Name.getAttribute("value"));
    expect(player3Name).not.toHaveValue(player2Name.getAttribute("value"));
  });

  it("blocks invalid setup choices before calling the backend", async () => {
    render(<GameSetupPanel initialSeed="seed-fixed" />);

    fireEvent.change(screen.getByRole("textbox", { name: "Player 1 name" }), {
      target: { value: "Ada" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "Player 2 name" }), {
      target: { value: "Ada" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "Player 1 color hex" }), {
      target: { value: "teal" },
    });
    fireEvent.change(screen.getByRole("spinbutton", { name: "Max negotiation rounds" }), {
      target: { value: "0" },
    });

    fireEvent.click(screen.getByRole("button", { name: "Create game" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Player names must be unique");
    expect(screen.getByRole("alert")).toHaveTextContent("Player colors must be valid hex colors");
    expect(screen.getByRole("alert")).toHaveTextContent("Max negotiation rounds must be at least 1");
    expect(createGameMock).not.toHaveBeenCalled();
    expect(push).not.toHaveBeenCalled();
  });

  it("shows backend validation errors returned by the create-game API", async () => {
    createGameMock.mockResolvedValue({
      state: "error",
      error: "Server rejected setup: unsupported negotiation cutoff",
    });
    render(<GameSetupPanel initialSeed="seed-fixed" />);

    fireEvent.click(screen.getByRole("button", { name: "Create game" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Server rejected setup: unsupported negotiation cutoff",
    );
    expect(push).not.toHaveBeenCalled();
  });
});
