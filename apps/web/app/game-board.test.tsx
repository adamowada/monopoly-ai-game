import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { BOARD_SPACES, PROPERTIES_BY_ID } from "@monopoly-ai-game/schemas";

import { DECK_ART, SPACE_ART_BY_ID } from "./board-art";
import { ClassicGameBoard } from "./game-board";
import type { GameMetadata } from "../lib/api/games";
import type { GameStateResponse } from "../lib/api/gameplay";

const createdAt = "2026-07-04T00:00:00.000Z";
const nonStreetMotifCount = BOARD_SPACES.filter((space) => {
  if (!space.property_id) {
    return true;
  }
  return PROPERTIES_BY_ID[space.property_id]?.kind !== "street";
}).length;

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
      player_icons: [
        { seat_order: 0, icon: "🚗" },
        { seat_order: 1, icon: "🎩" },
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

function stackedTokenGameFixture(): GameMetadata {
  const base = gameFixture([0, 0]);
  const names = ["Ada", "Grace", "Linus", "Marie", "Nia"];
  const colors = ["#0f766e", "#7c3aed", "#2563eb", "#dc2626", "#ca8a04"];
  return {
    ...base,
    settings: {
      ...base.settings,
      player_colors: colors.map((color, seat_order) => ({ color, seat_order })),
      player_icons: ["🚗", "🎩", "🚂", "🚢", "💎"].map((icon, seat_order) => ({ icon, seat_order })),
    },
    players: names.map((name, index) => ({
      id: `player-${index + 1}`,
      game_id: "game-board-test",
      seat_order: index,
      name,
      controller_type: index % 2 === 0 ? "human" : "ai",
      status: "active",
      state: {
        cash: 1500,
        position: 0,
      },
      created_at: createdAt,
      updated_at: createdAt,
    })),
  };
}

function stateFixture(): GameStateResponse {
  return {
    game_id: "game-board-test",
    event_sequence: 12,
    state_hash: "state-board-art",
    state: {
      game_id: "game-board-test",
      seed: "board-test",
      players: [
        { id: "player-1", cash: 1500, position: 0 },
        { id: "player-2", cash: 1500, position: 39 },
      ],
      property_ownership: [
        {
          property_id: "property_boardwalk",
          owner_id: "player-2",
          mortgaged: false,
          houses: 0,
          hotels: 1,
          hotel: true,
        },
        {
          property_id: "property_illinois_avenue",
          owner_id: "player-1",
          mortgaged: false,
          houses: 2,
          hotels: 0,
          hotel: false,
        },
        {
          property_id: "property_states_avenue",
          owner_id: "player-2",
          mortgaged: false,
          houses: 1,
          hotels: 0,
          hotel: false,
        },
        {
          property_id: "property_mediterranean_avenue",
          owner_id: "player-1",
          mortgaged: false,
          houses: 3,
          hotels: 0,
          hotel: false,
        },
      ],
      turn: {
        phase: "START_TURN",
        current_player_index: 0,
        current_player_id: "player-1",
      },
    },
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

  it("replaces the board center with a winner and stars graphic when the game is over", () => {
    const game = gameFixture();
    game.status = "ended";
    game.current_phase = "GAME_OVER";
    game.players[0] = {
      ...game.players[0],
      name: "Player 1",
      status: "active",
      state: { ...game.players[0].state, is_bankrupt: false },
    };
    game.players[1] = {
      ...game.players[1],
      name: "Player 2",
      status: "bankrupt",
      state: { ...game.players[1].state, is_bankrupt: true },
    };

    const { rerender } = render(<ClassicGameBoard game={game} />);

    const board = screen.getByRole("region", { name: "Classic Monopoly-style board" });
    const winnerStatus = within(board).getByRole("status", { name: "Winner Player 1!" });
    expect(winnerStatus).toHaveAttribute("data-winner-celebration");
    expect(winnerStatus).toHaveTextContent("Winner Player 1!");
    expect(board.querySelector("[data-winner-stars]")).toBeInTheDocument();

    rerender(<ClassicGameBoard game={gameFixture()} />);

    expect(screen.queryByRole("status", { name: "Winner Player 1!" })).not.toBeInTheDocument();
  });

  it("renders visible motif art only for non-street board spaces", () => {
    render(<ClassicGameBoard game={gameFixture()} />);

    const board = screen.getByRole("region", { name: "Classic Monopoly-style board" });

    expect(board.querySelectorAll("[data-space-art]")).toHaveLength(nonStreetMotifCount);
    for (const space of BOARD_SPACES) {
      const boardSpace = board.querySelector(`[data-space-index='${space.position}']`);
      const property = space.property_id ? PROPERTIES_BY_ID[space.property_id] : null;
      if (property?.kind === "street") {
        expect(boardSpace?.querySelector("[data-space-art]"), `${space.name} should not render motif art`).toBeNull();
      } else {
        expect(boardSpace?.querySelector("[data-space-art]"), `${space.name} should render motif art`).toBeInTheDocument();
      }
    }
  });

  it("renders street property cells without motif art while keeping band, markers, and hover details", () => {
    render(<ClassicGameBoard game={gameFixture()} snapshot={stateFixture()} />);

    const board = screen.getByRole("region", { name: "Classic Monopoly-style board" });
    const boardwalk = board.querySelector("[data-space-index='39']");
    const illinois = board.querySelector("[data-space-index='24']");
    const states = board.querySelector("[data-space-index='13']");
    const mediterranean = board.querySelector("[data-space-index='1']");

    expect(boardwalk).toHaveAttribute("data-space-kind", "street-property");
    expect(boardwalk?.querySelector("[data-property-color-band]")).toBeTruthy();
    expect(boardwalk?.querySelector("[data-space-art]")).toBeNull();
    expect(boardwalk?.querySelector("[data-owner-marker]")).toHaveAttribute(
      "aria-label",
      "Owner marker: Grace owns Boardwalk",
    );
    expect(boardwalk?.querySelector("[data-owner-marker]")).toHaveAttribute("data-marker-edge", "perimeter");
    expect(boardwalk?.querySelector("[data-owner-marker]")).toHaveAttribute("data-marker-role", "ownership");
    expect(boardwalk?.querySelector("[data-owner-marker]")).toHaveAttribute("data-marker-board-zone", "perimeter");
    expect(boardwalk?.querySelector("[data-owner-marker]")).toHaveAttribute(
      "data-marker-placement",
      "owner-perimeter",
    );
    expect(boardwalk?.querySelector("[data-owner-marker]")).toHaveAttribute("data-marker-anchor", "perimeter-price-edge");
    expect(boardwalk?.querySelector("[data-owner-marker]")).toHaveAttribute("data-marker-side", "right");
    expect(boardwalk?.querySelector("[data-owner-marker]")).toHaveAttribute("data-marker-card-slot", "bottom");
    expect(boardwalk?.querySelector("[data-owner-marker]")).toHaveClass("bottom-0");
    expect(boardwalk?.querySelector("[data-owner-marker]")).not.toHaveClass("top-0");
    expect(boardwalk?.querySelector("[data-owner-marker]")?.closest("[data-space-orientation]")).toHaveAttribute(
      "data-space-orientation",
      "-90",
    );
    expect(boardwalk?.querySelector("[data-development-marker]")).toHaveAttribute(
      "aria-label",
      "Development marker: Boardwalk has a hotel",
    );
    expect(boardwalk?.querySelector("[data-development-marker]")).toHaveAttribute("data-marker-edge", "interior");
    expect(boardwalk?.querySelector("[data-development-marker]")).toHaveAttribute("data-marker-role", "development");
    expect(boardwalk?.querySelector("[data-development-marker]")).toHaveAttribute(
      "data-marker-board-zone",
      "interior",
    );
    expect(boardwalk?.querySelector("[data-development-marker]")).toHaveAttribute(
      "data-marker-placement",
      "development-interior",
    );
    expect(boardwalk?.querySelector("[data-development-marker]")).toHaveAttribute(
      "data-marker-anchor",
      "interior-development-edge",
    );
    expect(boardwalk?.querySelector("[data-development-marker]")).toHaveAttribute("data-marker-side", "left");
    expect(boardwalk?.querySelector("[data-development-marker]")).toHaveAttribute("data-marker-card-slot", "top");
    expect(boardwalk?.querySelector("[data-development-marker]")).toHaveClass("top-0");
    expect(boardwalk?.querySelector("[data-development-marker]")).not.toHaveClass("bottom-0");
    expect(boardwalk?.querySelector("[data-development-marker]")?.closest("[data-space-orientation]")).toHaveAttribute(
      "data-space-orientation",
      "-90",
    );
    expect(boardwalk?.querySelector("[data-space-bottom-label]")).toHaveTextContent("$400");
    expect(board.querySelector("[data-property-hover]")).toBeNull();
    fireEvent.mouseEnter(boardwalk as Element);
    expect(board.querySelector("[data-property-hover]")).toHaveTextContent("Rent $50");
    expect(board.querySelector("[data-property-hover]")).toHaveTextContent("Mortgage value $200");
    expect(boardwalk?.querySelector("[data-space-name]")).toHaveClass("uppercase");

    expect(mediterranean?.querySelector("[data-owner-marker]")).toHaveAttribute(
      "aria-label",
      "Owner marker: Ada owns Mediterranean Avenue",
    );
    expect(mediterranean?.querySelector("[data-owner-marker]")).toHaveAttribute("data-marker-edge", "perimeter");
    expect(mediterranean?.querySelector("[data-owner-marker]")).toHaveAttribute("data-marker-role", "ownership");
    expect(mediterranean?.querySelector("[data-owner-marker]")).toHaveAttribute(
      "data-marker-board-zone",
      "perimeter",
    );
    expect(mediterranean?.querySelector("[data-owner-marker]")).toHaveAttribute(
      "data-marker-placement",
      "owner-perimeter",
    );
    expect(mediterranean?.querySelector("[data-owner-marker]")).toHaveAttribute(
      "data-marker-anchor",
      "perimeter-price-edge",
    );
    expect(mediterranean?.querySelector("[data-owner-marker]")).toHaveAttribute("data-marker-side", "bottom");
    expect(mediterranean?.querySelector("[data-owner-marker]")).toHaveAttribute("data-marker-board-zone", "perimeter");
    expect(mediterranean?.querySelector("[data-owner-marker]")).toHaveAttribute("data-marker-card-slot", "bottom");
    expect(mediterranean?.querySelector("[data-owner-marker]")).toHaveClass("bottom-0");
    expect(mediterranean?.querySelector("[data-owner-marker]")).not.toHaveClass("top-0");
    expect(mediterranean?.querySelector("[data-development-marker]")).toHaveAttribute(
      "aria-label",
      "Development marker: Mediterranean Avenue has 3 houses",
    );
    expect(mediterranean?.querySelector("[data-development-marker]")).toHaveAttribute(
      "data-marker-edge",
      "interior",
    );
    expect(mediterranean?.querySelector("[data-development-marker]")).toHaveAttribute(
      "data-marker-role",
      "development",
    );
    expect(mediterranean?.querySelector("[data-development-marker]")).toHaveAttribute(
      "data-marker-board-zone",
      "interior",
    );
    expect(mediterranean?.querySelector("[data-development-marker]")).toHaveAttribute(
      "data-marker-placement",
      "development-interior",
    );
    expect(mediterranean?.querySelector("[data-development-marker]")).toHaveAttribute(
      "data-marker-anchor",
      "interior-development-edge",
    );
    expect(mediterranean?.querySelector("[data-development-marker]")).toHaveAttribute("data-marker-side", "top");
    expect(mediterranean?.querySelector("[data-development-marker]")).toHaveAttribute(
      "data-marker-board-zone",
      "interior",
    );
    expect(mediterranean?.querySelector("[data-development-marker]")).toHaveAttribute("data-marker-card-slot", "top");
    expect(mediterranean?.querySelector("[data-development-marker]")).toHaveClass("top-0");
    expect(mediterranean?.querySelector("[data-development-marker]")).not.toHaveClass("bottom-0");
    expect(mediterranean?.querySelector("[data-development-marker]")).not.toHaveClass("top-1");

    expect(illinois?.querySelector("[data-owner-marker]")).toHaveAttribute("data-marker-side", "top");
    expect(illinois?.querySelector("[data-owner-marker]")).toHaveAttribute("data-marker-card-slot", "bottom");
    expect(illinois?.querySelector("[data-owner-marker]")).toHaveClass("bottom-0");
    expect(illinois?.querySelector("[data-owner-marker]")).not.toHaveClass("top-0");
    expect(illinois?.querySelector("[data-development-marker]")).toHaveAttribute("data-marker-side", "bottom");
    expect(illinois?.querySelector("[data-development-marker]")).toHaveAttribute("data-marker-card-slot", "top");
    expect(illinois?.querySelector("[data-development-marker]")).toHaveClass("top-0");
    expect(illinois?.querySelector("[data-development-marker]")).not.toHaveClass("bottom-0");
    expect(illinois?.querySelector("[data-development-marker]")).not.toHaveClass("bottom-1");
    expect(states?.querySelector("[data-owner-marker]")).toHaveAttribute("data-marker-side", "left");
    expect(states?.querySelector("[data-owner-marker]")).toHaveAttribute("data-marker-card-slot", "bottom");
    expect(states?.querySelector("[data-owner-marker]")).toHaveClass("bottom-0");
    expect(states?.querySelector("[data-owner-marker]")).not.toHaveClass("top-0");
    expect(states?.querySelector("[data-development-marker]")).toHaveAttribute("data-marker-side", "right");
    expect(states?.querySelector("[data-development-marker]")).toHaveAttribute("data-marker-card-slot", "top");
    expect(states?.querySelector("[data-development-marker]")).toHaveClass("top-0");
    expect(states?.querySelector("[data-development-marker]")).not.toHaveClass("bottom-0");
    expect(states?.querySelector("[data-development-marker]")).not.toHaveClass("right-1");
  });

  it("renders non-street board cells with a top name, large logo, and relevant bottom labels", () => {
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
    fireEvent.mouseEnter(readingRailroad as Element);
    expect(board.querySelector("[data-property-hover]")).toHaveTextContent("Rent $25");
    fireEvent.mouseLeave(readingRailroad as Element);
    expect(board.querySelector("[data-property-hover]")).toBeNull();

    expect(communityChest?.querySelector("[data-space-art]")).toBeTruthy();
    expect(communityChest?.querySelector("[data-space-bottom-label]")).toBeNull();
    fireEvent.mouseEnter(communityChest as Element);
    expect(board.querySelector("[data-property-hover]")).toBeNull();

    expect(luxuryTax?.querySelector("[data-space-bottom-label]")).toHaveTextContent("pay $100.00");
    fireEvent.mouseEnter(chance as Element);
    expect(board.querySelector("[data-property-hover]")).toBeNull();
  });

  it("derives visible token labels from player positions and updates after rerender", () => {
    const { rerender } = render(<ClassicGameBoard game={gameFixture([0, 7])} />);

    expect(screen.getByLabelText("Ada token at GO, position 0")).toHaveAttribute("data-player-token");
    expect(screen.getByLabelText("Ada token at GO, position 0")).toHaveAttribute("data-token-shape", "shield");
    expect(screen.getByLabelText("Ada token at GO, position 0")).toHaveAttribute("data-token-icon", "🚗");
    expect(
      screen.getByLabelText("Ada token at GO, position 0").querySelector("[data-player-token-icon]"),
    ).toHaveTextContent("🚗");
    expect(screen.getByLabelText("Ada token at GO, position 0").querySelector("[data-token-puck]")).toBeInTheDocument();
    expect(screen.getByLabelText("Ada token at GO, position 0").querySelector("[data-token-silhouette]")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Grace token at Chance, position 7")).toHaveAttribute("data-player-token");
    expect(screen.getByLabelText("Grace token at Chance, position 7")).toHaveAttribute("data-token-shape", "diamond");
    expect(screen.getByLabelText("Grace token at Chance, position 7")).toHaveAttribute("data-token-icon", "🎩");
    expect(screen.getByLabelText("Ada token at GO, position 0")).toHaveAttribute("title", "Ada");
    expect(
      screen.getByLabelText("Ada token at GO, position 0").querySelector("[data-player-token-label]"),
    ).toHaveTextContent("Ada");

    rerender(<ClassicGameBoard game={gameFixture([24, 7])} />);

    expect(screen.queryByLabelText("Ada token at GO, position 0")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Ada token at Illinois Avenue, position 24")).toHaveAttribute("data-player-token");
  });

  it("uses distinct emoji pucks when multiple players share one space", () => {
    render(<ClassicGameBoard game={stackedTokenGameFixture()} />);

    const board = screen.getByRole("region", { name: "Classic Monopoly-style board" });
    const tokens = within(board).getAllByLabelText(/token at GO, position 0/);
    const shapes = tokens.map((token) => token.getAttribute("data-token-shape"));
    const icons = tokens.map((token) => token.getAttribute("data-token-icon"));

    expect(tokens).toHaveLength(5);
    expect(new Set(shapes)).toEqual(new Set(["shield", "diamond", "tag", "hex", "crest"]));
    expect(new Set(icons)).toEqual(new Set(["🚗", "🎩", "🚂", "🚢", "💎"]));
    for (const token of tokens) {
      expect(token.querySelector("[data-token-puck]")).toBeInTheDocument();
      expect(token.querySelector("[data-token-silhouette]")).not.toBeInTheDocument();
      expect(token.querySelector("[data-player-token-icon]")).toBeInTheDocument();
    }
  });

  it("can show dice motion and a travelling token position while an accepted roll resolves", () => {
    const { rerender } = render(
      <ClassicGameBoard
        game={gameFixture([7, 0])}
        motion={{
          dice: [3, 4],
          displayPosition: 0,
          fromPosition: 0,
          landedSpaceName: "Chance",
          playerId: "player-1",
          playerName: "Ada",
          status: "moving",
          toPosition: 7,
          total: 7,
        }}
      />,
    );

    const diceStatus = screen.getByRole("status", { name: "Dice roll animation" });
    expect(diceStatus).toHaveAttribute("data-dice-motion", "moving");
    expect(diceStatus).toHaveTextContent("3 + 4");
    const movingToken = screen.getByLabelText("Ada token at GO, position 0");
    expect(movingToken).toHaveAttribute("data-token-motion-overlay", "true");
    expect(movingToken).toHaveAttribute("data-token-slide", "true");
    expect(movingToken).toHaveClass("board-token-motion-overlay");
    expect(movingToken).toHaveAttribute("data-token-moving", "true");
    expect(movingToken.querySelector("[data-token-puck]")).toBeInTheDocument();
    expect(movingToken.querySelector("[data-token-silhouette]")).not.toBeInTheDocument();
    expect(movingToken.querySelector("[data-token-trail]")).toBeInTheDocument();

    rerender(
      <ClassicGameBoard
        game={gameFixture([7, 0])}
        motion={{
          dice: [3, 4],
          displayPosition: 3,
          fromPosition: 0,
          landedSpaceName: "Chance",
          playerId: "player-1",
          playerName: "Ada",
          status: "moving",
          toPosition: 7,
          total: 7,
        }}
      />,
    );

    expect(screen.getByLabelText("Ada token at Baltic Avenue, position 3")).toHaveAttribute(
      "data-token-motion-overlay",
      "true",
    );
    const movementBanner = screen.getByRole("status", { name: "Board movement" });
    expect(movementBanner).toHaveTextContent("Ada moving to Baltic Avenue");
    expect(movementBanner).toHaveAttribute("data-board-motion-placement", "center-stack");
    expect(movementBanner).toHaveAttribute("data-board-motion-size", "micro-route-chip");
    expect(movementBanner).toHaveAttribute("data-board-motion-layer", "top");
    expect(movementBanner).toHaveAttribute("data-board-motion-overlap", "separate-fixed-lanes");
    expect(movementBanner).toHaveClass("max-w-[4.5rem]");
    expect(movementBanner).not.toHaveClass("max-w-[4.25rem]");
    expect(movementBanner).not.toHaveClass("max-w-[5.25rem]");
    expect(movementBanner).not.toHaveClass("max-w-[5.75rem]");
    expect(movementBanner).not.toHaveClass("max-w-[6rem]");
    expect(movementBanner).not.toHaveClass("max-w-[7rem]");
    expect(movementBanner).not.toHaveClass("max-w-[8.25rem]");
    expect(movementBanner).not.toHaveClass("max-w-[10rem]");
    expect(movementBanner).not.toHaveClass("w-full");
    const centerBoard = screen.getByTestId("center-board-art");
    const motionStack = centerBoard.querySelector("[data-center-motion-stack]");
    expect(motionStack).toBeInTheDocument();
    expect(motionStack).toHaveAttribute("data-center-motion-layout", "separated-fixed-lanes");
    expect(motionStack).toHaveAttribute("data-center-motion-gap", "collision-proof");
    const movementLane = centerBoard.querySelector("[data-center-motion-lane='movement']");
    const diceLane = centerBoard.querySelector("[data-center-motion-lane='dice']");
    expect(movementLane).toHaveAttribute("data-center-motion-lane-position", "above-dice");
    expect(movementLane).toHaveClass("top-[24%]");
    expect(movementLane).not.toHaveClass("top-1/2");
    expect(diceLane).toHaveAttribute("data-center-motion-lane-position", "below-movement");
    expect(diceLane).toHaveClass("top-[76%]");
    expect(diceLane).not.toHaveClass("top-1/2");
    const centeredDiceStatus = within(centerBoard).getByRole("status", { name: "Dice roll animation" });
    expect(movementBanner.compareDocumentPosition(centeredDiceStatus) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(centeredDiceStatus).toHaveAttribute(
      "data-dice-placement",
      "center-board",
    );
    expect(centeredDiceStatus).toHaveAttribute("data-dice-layer", "below-motion-banner");
    expect(centeredDiceStatus).toHaveAttribute("data-dice-size", "compact-center");
    expect(screen.queryByLabelText("Ada token at GO, position 0")).not.toBeInTheDocument();
  });

  it("renders rolling dice as a staged animation and marks the landing token", () => {
    const { rerender } = render(
      <ClassicGameBoard
        game={gameFixture([7, 0])}
        motion={{
          dice: [3, 4],
          landedSpaceName: "Chance",
          playerId: "player-1",
          playerName: "Ada",
          status: "rolling",
          total: 7,
        }}
      />,
    );

    const diceStatus = screen.getByRole("status", { name: "Dice roll animation" });
    expect(diceStatus).toHaveAttribute("data-dice-motion", "rolling");
    expect(diceStatus.querySelectorAll("[data-dice-tumble]")).toHaveLength(2);
    expect(diceStatus.querySelector("[data-dice-value='3']")).toBeInTheDocument();
    expect(diceStatus.querySelector("[data-dice-value='4']")).toBeInTheDocument();
    expect(diceStatus.querySelectorAll("[data-dice-pip]")).toHaveLength(7);
    expect(diceStatus).toHaveTextContent("3 + 4 = 7");

    rerender(
      <ClassicGameBoard
        game={gameFixture([7, 0])}
        motion={{
          dice: [3, 4],
          displayPosition: 7,
          fromPosition: 0,
          landedSpaceName: "Chance",
          playerId: "player-1",
          playerName: "Ada",
          status: "settled",
          toPosition: 7,
          total: 7,
        }}
      />,
    );

    const landedToken = screen.getByLabelText("Ada token at Chance, position 7");
    expect(landedToken).toHaveAttribute("data-token-landing", "true");
    expect(landedToken.querySelector("[data-token-trail]")).toBeInTheDocument();
    expect(screen.getByRole("status", { name: "Dice roll animation" })).not.toHaveTextContent("Ada landed on Chance");
    const centerBoard = screen.getByTestId("center-board-art");
    expect(within(centerBoard).getByRole("status", { name: "Board landing" })).toHaveTextContent(
      "Ada landed on Chance",
    );
  });

  it("keeps the landed space visible when the latest roll was doubles", () => {
    render(
      <ClassicGameBoard
        game={gameFixture([10, 0])}
        lastRoll={{
          dice: [1, 1],
          eventId: "double-roll",
          isDoubles: true,
          landedSpaceName: "Jail / Just Visiting",
          playerName: "Ada",
          total: 2,
        }}
      />,
    );

    const diceStatus = screen.getByRole("status", { name: "Dice roll animation" });
    expect(diceStatus).toHaveTextContent("1 + 1 = 2");
    expect(diceStatus).toHaveTextContent("Double 1s");
    expect(diceStatus).toHaveTextContent("Ada rolled to Jail / Just Visiting");
  });

  it("presents drawn cards with deck art and keyboard dismissal", () => {
    const onDismiss = vi.fn();
    render(
      <ClassicGameBoard
        drawnCard={{
          description: "Move to GO and collect the payout.",
          deckLabel: "Chance",
          eventId: "card-event-1",
          playerName: "Ada",
          title: "Move to GO",
        }}
        game={gameFixture([7, 0])}
        onDismissDrawnCard={onDismiss}
      />,
    );

    const board = screen.getByRole("region", { name: "Classic Monopoly-style board" });
    const modal = within(board).getByRole("dialog", { name: "Chance card" });
    expect(modal).toHaveAttribute("data-card-reveal");
    expect(modal).toHaveAttribute("data-card-deck", "chance");
    expect(within(modal).getByRole("img", { name: "Chance card art" })).toBeInTheDocument();
    expect(modal).toHaveTextContent("Move to GO");
    expect(modal).not.toHaveTextContent("card-event-1");

    fireEvent.keyDown(modal, { key: "Escape" });
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });
});
