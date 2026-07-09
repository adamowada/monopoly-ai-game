import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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

function expectedGeneratedSeed(timestamp: number, randomValue: number): string {
  return `setup-${timestamp.toString(36)}-${Math.floor(randomValue * 100_000)
    .toString(36)
    .padStart(4, "0")}`;
}

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

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders setup controls for local 2-5 player game creation", () => {
    const timestamp = 1_789_000_000_000;
    const randomValue = 0.31415;
    vi.spyOn(Date, "now").mockReturnValue(timestamp);
    vi.spyOn(Math, "random").mockReturnValue(randomValue);

    render(<GameSetupPanel />);

    expect(screen.getByRole("region", { name: "Choose seats" })).toBeInTheDocument();
    expect(screen.queryByText("Local tabletop setup")).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { level: 2, name: "Choose seats" })).not.toBeInTheDocument();
    expect(screen.queryByText("Build the table as seats and tokens, then open the board when everyone is ready.")).not.toBeInTheDocument();
    expect(screen.queryByText("2 ready")).not.toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Seed" })).toHaveValue(
      expectedGeneratedSeed(timestamp, randomValue),
    );
    expect(screen.getByRole("button", { name: "Generate seed" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add player" })).toBeInTheDocument();
    expect(screen.queryByRole("table", { name: "Configured players" })).not.toBeInTheDocument();

    const seats = screen.getByRole("region", { name: "Choose seats" });
    const seat1 = within(seats).getByRole("group", { name: "Seat 1 token setup" });
    const seat2 = within(seats).getByRole("group", { name: "Seat 2 token setup" });
    expect(within(seat1).queryByText("Seat 1")).not.toBeInTheDocument();
    expect(within(seat2).queryByText("Seat 2")).not.toBeInTheDocument();
    expect(within(seat1).getByRole("textbox", { name: "Player 1 name" })).toHaveValue("Player 1");
    expect(within(seat1).getByRole("combobox", { name: "Player 1 type" })).toHaveValue("human");
    expect(within(seat1).getByRole("textbox", { name: "Player 1 color hex" })).toHaveValue("#0f766e");
    expect(within(seat1).getByRole("button", { name: "Player 1 token icon Car" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(within(seat2).getByRole("textbox", { name: "Player 2 name" })).toHaveValue("Player 2");
    expect(within(seat2).getByRole("button", { name: "Player 2 token icon Top hat" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    expect(screen.getByRole("spinbutton", { name: "Max negotiation rounds" })).toHaveValue(3);
    expect(screen.getByRole("spinbutton", { name: "Proposal limit per player" })).toHaveValue(4);
  });

  it("starts each setup with a generated seed", () => {
    const firstTimestamp = 1_789_000_000_000;
    const secondTimestamp = 1_789_000_001_000;
    const firstRandomValue = 0.12345;
    const secondRandomValue = 0.98765;
    vi.spyOn(Date, "now")
      .mockReturnValueOnce(firstTimestamp)
      .mockReturnValueOnce(secondTimestamp);
    vi.spyOn(Math, "random")
      .mockReturnValueOnce(firstRandomValue)
      .mockReturnValueOnce(secondRandomValue);

    const firstRender = render(<GameSetupPanel />);
    const firstSeed = screen.getByRole("textbox", { name: "Seed" });
    expect(firstSeed).toHaveValue(expectedGeneratedSeed(firstTimestamp, firstRandomValue));
    const firstSeedValue = (firstSeed as HTMLInputElement).value;

    firstRender.unmount();
    render(<GameSetupPanel />);

    const secondSeed = screen.getByRole("textbox", { name: "Seed" });
    expect(secondSeed).toHaveValue(expectedGeneratedSeed(secondTimestamp, secondRandomValue));
    expect(secondSeed).not.toHaveValue(firstSeedValue);
  });

  it("sends player colors and negotiation cutoffs through settings before navigating", async () => {
    createGameMock.mockResolvedValue({ state: "loaded", game: gameMetadata() });
    render(<GameSetupPanel />);

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
    fireEvent.click(screen.getByRole("button", { name: "Player 2 token icon Train" }));
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
          player_icons: [
            { seat_order: 0, icon: "🚗" },
            { seat_order: 1, icon: "🚂" },
          ],
          negotiation_cutoffs: {
            max_rounds: 4,
            max_proposals_per_player: 3,
          },
        },
      });
    });
    await waitFor(() => expect(push).toHaveBeenCalledWith("/games/game-created", { scroll: true }));
  });

  it("sends optional debug cash and property allocations before navigating", async () => {
    createGameMock.mockResolvedValue({ state: "loaded", game: gameMetadata() });
    render(<GameSetupPanel />);

    fireEvent.change(screen.getByRole("textbox", { name: "Player 1 name" }), {
      target: { value: "Ada" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "Player 2 name" }), {
      target: { value: "Grace" },
    });
    fireEvent.click(screen.getByRole("checkbox", { name: "Enable debug setup" }));
    fireEvent.change(screen.getByRole("spinbutton", { name: "Player 1 starting cash" }), {
      target: { value: "2200" },
    });
    fireEvent.change(screen.getByRole("combobox", { name: "Mediterranean Avenue owner" }), {
      target: { value: "0" },
    });

    fireEvent.click(screen.getByRole("button", { name: "Create game" }));

    await waitFor(() => {
      expect(createGameMock).toHaveBeenCalledWith(
        expect.objectContaining({
          settings: expect.objectContaining({
            debug_allocations: {
              player_cash: [
                { seat_order: 0, cash: 2200 },
                { seat_order: 1, cash: 1500 },
              ],
              property_owners: [{ property_id: "property_mediterranean_avenue", seat_order: 0 }],
            },
          }),
        }),
      );
    });
    await waitFor(() => expect(push).toHaveBeenCalledWith("/games/game-created", { scroll: true }));
  });

  it("sends optional debug street improvements for richer scenario setup", async () => {
    createGameMock.mockResolvedValue({ state: "loaded", game: gameMetadata() });
    render(<GameSetupPanel />);

    fireEvent.change(screen.getByRole("textbox", { name: "Player 1 name" }), {
      target: { value: "Ada" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "Player 2 name" }), {
      target: { value: "Grace" },
    });
    fireEvent.click(screen.getByRole("checkbox", { name: "Enable debug setup" }));
    fireEvent.change(screen.getByRole("combobox", { name: "Mediterranean Avenue owner" }), {
      target: { value: "0" },
    });
    fireEvent.change(screen.getByRole("combobox", { name: "Mediterranean Avenue improvements" }), {
      target: { value: "3" },
    });
    fireEvent.change(screen.getByRole("combobox", { name: "Boardwalk owner" }), {
      target: { value: "1" },
    });
    fireEvent.change(screen.getByRole("combobox", { name: "Boardwalk improvements" }), {
      target: { value: "hotel" },
    });

    fireEvent.click(screen.getByRole("button", { name: "Create game" }));

    await waitFor(() => {
      expect(createGameMock).toHaveBeenCalledWith(
        expect.objectContaining({
          settings: expect.objectContaining({
            debug_allocations: expect.objectContaining({
              property_improvements: [
                { property_id: "property_mediterranean_avenue", houses: 3, hotel: false },
                { property_id: "property_boardwalk", houses: 0, hotel: true },
              ],
            }),
          }),
        }),
      );
    });
  });

  it("sends optional debug property mortgage state for scenario setup", async () => {
    createGameMock.mockResolvedValue({ state: "loaded", game: gameMetadata() });
    render(<GameSetupPanel />);

    fireEvent.change(screen.getByRole("textbox", { name: "Player 1 name" }), {
      target: { value: "Ada" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "Player 2 name" }), {
      target: { value: "Grace" },
    });
    fireEvent.click(screen.getByRole("checkbox", { name: "Enable debug setup" }));
    fireEvent.change(screen.getByRole("combobox", { name: "Reading Railroad owner" }), {
      target: { value: "0" },
    });
    fireEvent.click(screen.getByRole("checkbox", { name: "Reading Railroad mortgaged" }));

    fireEvent.click(screen.getByRole("button", { name: "Create game" }));

    await waitFor(() => {
      expect(createGameMock).toHaveBeenCalledWith(
        expect.objectContaining({
          settings: expect.objectContaining({
            debug_allocations: expect.objectContaining({
              property_mortgages: [{ property_id: "property_reading_railroad", mortgaged: true }],
            }),
          }),
        }),
      );
    });
  });

  it("sends optional debug starting board positions for scenario setup", async () => {
    createGameMock.mockResolvedValue({ state: "loaded", game: gameMetadata() });
    render(<GameSetupPanel />);

    fireEvent.change(screen.getByRole("textbox", { name: "Player 1 name" }), {
      target: { value: "Ada" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "Player 2 name" }), {
      target: { value: "Grace" },
    });
    fireEvent.click(screen.getByRole("checkbox", { name: "Enable debug setup" }));
    fireEvent.change(screen.getByRole("combobox", { name: "Player 2 starting square" }), {
      target: { value: "5" },
    });

    fireEvent.click(screen.getByRole("button", { name: "Create game" }));

    await waitFor(() => {
      expect(createGameMock).toHaveBeenCalledWith(
        expect.objectContaining({
          settings: expect.objectContaining({
            debug_allocations: expect.objectContaining({
              player_positions: [{ seat_order: 1, position: 5 }],
            }),
          }),
        }),
      );
    });
  });

  it("sends an optional debug current player for targeted scenario setup", async () => {
    createGameMock.mockResolvedValue({ state: "loaded", game: gameMetadata() });
    render(<GameSetupPanel />);

    fireEvent.change(screen.getByRole("textbox", { name: "Player 1 name" }), {
      target: { value: "Ada" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "Player 2 name" }), {
      target: { value: "Grace" },
    });
    fireEvent.click(screen.getByRole("checkbox", { name: "Enable debug setup" }));
    fireEvent.change(screen.getByRole("combobox", { name: "Current player" }), {
      target: { value: "1" },
    });

    fireEvent.click(screen.getByRole("button", { name: "Create game" }));

    await waitFor(() => {
      expect(createGameMock).toHaveBeenCalledWith(
        expect.objectContaining({
          settings: expect.objectContaining({
            debug_allocations: expect.objectContaining({
              current_player_seat_order: 1,
            }),
          }),
        }),
      );
    });
  });

  it("can allocate an entire debug property set for faster AI scenario setup", async () => {
    createGameMock.mockResolvedValue({ state: "loaded", game: gameMetadata() });
    render(<GameSetupPanel />);

    fireEvent.change(screen.getByRole("textbox", { name: "Player 1 name" }), {
      target: { value: "Ada" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "Player 2 name" }), {
      target: { value: "Grace" },
    });
    fireEvent.click(screen.getByRole("checkbox", { name: "Enable debug setup" }));
    fireEvent.change(screen.getByRole("combobox", { name: "Orange set owner" }), {
      target: { value: "1" },
    });

    fireEvent.click(screen.getByRole("button", { name: "Create game" }));

    await waitFor(() => {
      expect(createGameMock).toHaveBeenCalledWith(
        expect.objectContaining({
          settings: expect.objectContaining({
            debug_allocations: expect.objectContaining({
              property_owners: expect.arrayContaining([
                { property_id: "property_st_james_place", seat_order: 1 },
                { property_id: "property_tennessee_avenue", seat_order: 1 },
                { property_id: "property_new_york_avenue", seat_order: 1 },
              ]),
            }),
          }),
        }),
      );
    });
  });

  it("allows individual debug owner overrides after a property-set allocation", async () => {
    createGameMock.mockResolvedValue({ state: "loaded", game: gameMetadata() });
    render(<GameSetupPanel />);

    fireEvent.change(screen.getByRole("textbox", { name: "Player 1 name" }), {
      target: { value: "Ada" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "Player 2 name" }), {
      target: { value: "Grace" },
    });
    fireEvent.click(screen.getByRole("checkbox", { name: "Enable debug setup" }));
    fireEvent.change(screen.getByRole("combobox", { name: "Orange set owner" }), {
      target: { value: "1" },
    });
    fireEvent.change(screen.getByRole("combobox", { name: "Tennessee Avenue owner" }), {
      target: { value: "" },
    });

    fireEvent.click(screen.getByRole("button", { name: "Create game" }));

    await waitFor(() => {
      const request = createGameMock.mock.calls[0]?.[0];
      const debugAllocations = request?.settings?.debug_allocations as
        | { property_owners?: Array<{ property_id: string; seat_order: number }> }
        | undefined;
      const propertyOwners = debugAllocations?.property_owners ?? [];
      expect(propertyOwners).toEqual(
        expect.arrayContaining([
          { property_id: "property_st_james_place", seat_order: 1 },
          { property_id: "property_new_york_avenue", seat_order: 1 },
        ]),
      );
      expect(propertyOwners).not.toEqual(
        expect.arrayContaining([{ property_id: "property_tennessee_avenue", seat_order: 1 }]),
      );
    });
  });

  it("auto-generates common names when seats become AI players", () => {
    render(<GameSetupPanel />);

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
    render(<GameSetupPanel />);

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
    render(<GameSetupPanel />);

    fireEvent.click(screen.getByRole("button", { name: "Create game" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Server rejected setup: unsupported negotiation cutoff",
    );
    expect(push).not.toHaveBeenCalled();
  });
});
