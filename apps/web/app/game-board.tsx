"use client";

import { useState, type CSSProperties, type ReactNode } from "react";
import { BOARD_SPACES, PROPERTIES_BY_ID, PROPERTY_GROUPS, type StaticDataBoardSpace, type StaticDataProperty } from "@monopoly-ai-game/schemas";
import { RotateCw, X } from "lucide-react";

import type { GameMetadata, GamePlayer } from "../lib/api/games";
import type { GameStateResponse } from "../lib/api/gameplay";
import { cn } from "../lib/ui";
import { DECK_ART, DeckArtPreview, SPACE_ART_BY_ID, SpaceMotif } from "./board-art";
import { getPlayerIcon } from "./player-icons";
import { PropertyDeedCard } from "./property-deed-card";

type BoardCoordinates = {
  row: number;
  column: number;
  rowSpan: number;
  columnSpan: number;
};

type BoardEdge = "bottom" | "left" | "top" | "right";

type PlayerColorSetting = {
  seat_order: number;
  color: string;
};

type PropertyOwnershipView = {
  property_id: string;
  owner_id: string | null;
  mortgaged: boolean;
  houses: number;
  hotels: number;
  hotel: boolean;
};

export type BoardMotion =
  | {
      status: "rolling";
      playerId: string | null;
      displayPosition?: number;
      fromPosition?: number;
      toPosition?: number;
      dice?: number[];
      landedSpaceName?: string;
      playerName?: string;
      total?: number;
    }
  | {
      status: "moving" | "settled";
      playerId: string;
      fromPosition: number;
      toPosition: number;
      displayPosition: number;
      dice?: number[];
      landedSpaceName?: string;
      playerName?: string;
      total?: number;
    };

export type DrawnCardView = {
  eventId: string;
  deckLabel: string;
  title: string;
  description: string;
  playerName: string | null;
};

export type LastRollView = {
  dice: number[];
  eventId: string;
  isDoubles: boolean;
  landedSpaceName?: string;
  playerId?: string;
  playerName?: string;
  total: number;
};

const boardGridSize = 13;
const fallbackPlayerColor = "#525866";
const boardSurfaceColor = "#eaf3d7";
const tokenShapes = ["shield", "diamond", "tag", "hex", "crest"] as const;
const groupColorById = new Map(PROPERTY_GROUPS.map((group) => [group.id, group.color]));
const propertyById = new Map<string, StaticDataProperty>(
  Object.values(PROPERTIES_BY_ID).map((property) => [property.id, property]),
);
const propertySpaceById = new Map<string, StaticDataBoardSpace>(
  BOARD_SPACES.flatMap((space) => (space.property_id ? [[space.property_id, space] as const] : [])),
);

function boardCoordinates(position: number): BoardCoordinates {
  if (position === 0) {
    return { row: 12, column: 12, rowSpan: 2, columnSpan: 2 };
  }
  if (position > 0 && position < 10) {
    return { row: 12, column: 12 - position, rowSpan: 2, columnSpan: 1 };
  }
  if (position === 10) {
    return { row: 12, column: 1, rowSpan: 2, columnSpan: 2 };
  }
  if (position > 10 && position < 20) {
    return { row: 22 - position, column: 1, rowSpan: 1, columnSpan: 2 };
  }
  if (position === 20) {
    return { row: 1, column: 1, rowSpan: 2, columnSpan: 2 };
  }
  if (position > 20 && position < 30) {
    return { row: 1, column: position - 18, rowSpan: 2, columnSpan: 1 };
  }
  if (position === 30) {
    return { row: 1, column: 12, rowSpan: 2, columnSpan: 2 };
  }
  return { row: position - 28, column: 12, rowSpan: 1, columnSpan: 2 };
}

function isPlayerColorSetting(value: unknown): value is PlayerColorSetting {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return false;
  }
  const candidate = value as Partial<PlayerColorSetting>;
  return typeof candidate.seat_order === "number" && typeof candidate.color === "string";
}

export function getPlayerColor(game: GameMetadata, seatOrder: number): string {
  const colors = game.settings.player_colors;
  if (!Array.isArray(colors)) {
    return fallbackPlayerColor;
  }
  return colors.find((entry): entry is PlayerColorSetting => isPlayerColorSetting(entry) && entry.seat_order === seatOrder)
    ?.color ?? fallbackPlayerColor;
}

function normalizedPosition(rawPosition: unknown): number {
  if (typeof rawPosition !== "number" || !Number.isInteger(rawPosition)) {
    return 0;
  }
  return ((rawPosition % BOARD_SPACES.length) + BOARD_SPACES.length) % BOARD_SPACES.length;
}

function spaceNameForPosition(position: number | undefined): string | null {
  if (typeof position !== "number" || !Number.isInteger(position)) {
    return null;
  }
  return BOARD_SPACES[normalizedPosition(position)]?.name ?? null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function playerIsBankrupt(player: GamePlayer): boolean {
  const status = player.status.toLowerCase();
  return status === "bankrupt" || (isRecord(player.state) && player.state.is_bankrupt === true);
}

function winnerForGame(game: GameMetadata): GamePlayer | null {
  const gameStatus = game.status.toLowerCase();
  if (gameStatus !== "ended" && game.current_phase !== "GAME_OVER") {
    return null;
  }

  const survivingPlayers = game.players.filter((player) => !playerIsBankrupt(player));
  if (survivingPlayers.length === 1) {
    return survivingPlayers[0];
  }

  const activeSurvivors = survivingPlayers.filter((player) => player.status.toLowerCase() === "active");
  return activeSurvivors.length === 1 ? activeSurvivors[0] : null;
}

function readString(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function readInteger(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isInteger(value) ? value : fallback;
}

function readBoolean(value: unknown, fallback = false): boolean {
  return typeof value === "boolean" ? value : fallback;
}

function snapshotPlayerRecord(snapshot: GameStateResponse | undefined, playerId: string): Record<string, unknown> | null {
  const players = snapshot?.state.players;
  if (!Array.isArray(players)) {
    return null;
  }
  const player = players.find((entry) => isRecord(entry) && entry.id === playerId);
  return isRecord(player) ? player : null;
}

function snapshotPlayerPosition(snapshot: GameStateResponse | undefined, playerId: string): number | null {
  const player = snapshotPlayerRecord(snapshot, playerId);
  if (!player) {
    return null;
  }
  const position = player.position;
  return typeof position === "number" && Number.isInteger(position) ? normalizedPosition(position) : null;
}

function playerJailStatus(player: GamePlayer, snapshot: GameStateResponse | undefined): { inJail: boolean; turns: number } {
  const snapshotPlayer = snapshotPlayerRecord(snapshot, player.id);
  const playerState = isRecord(player.state) ? player.state : {};
  const inJail = readBoolean(
    snapshotPlayer?.in_jail ?? snapshotPlayer?.is_in_jail,
    readBoolean(playerState.in_jail ?? playerState.is_in_jail),
  );
  const turns = readInteger(
    snapshotPlayer?.jail_turns ?? snapshotPlayer?.turns_in_jail,
    readInteger(playerState.jail_turns ?? playerState.turns_in_jail),
  );
  return { inJail, turns: Math.max(0, turns) };
}

function playerPosition(player: GamePlayer, snapshot: GameStateResponse | undefined, motion: BoardMotion | undefined): number {
  if (motion?.status === "rolling" && motion.playerId === player.id && typeof motion.displayPosition === "number") {
    return normalizedPosition(motion.displayPosition);
  }
  if ((motion?.status === "moving" || motion?.status === "settled") && motion.playerId === player.id) {
    return normalizedPosition(motion.displayPosition);
  }
  return snapshotPlayerPosition(snapshot, player.id) ?? normalizedPosition(player.state.position);
}

type TokenShape = (typeof tokenShapes)[number];

function tokenShapeForSeat(seatOrder: number): TokenShape {
  return tokenShapes[seatOrder % tokenShapes.length] ?? "shield";
}

function TokenPuck({
  color,
}: Readonly<{
  color: string;
}>) {
  return (
    <span
      aria-hidden="true"
      className="absolute inset-0 rounded-full border-2 bg-[#fffbea]"
      data-token-puck=""
      style={{
        borderColor: color,
        boxShadow: `inset 0 -2px 0 ${color}, 0 1px 0 rgba(47, 36, 24, 0.32), 0 0 0 1px rgba(47, 36, 24, 0.28)`,
      }}
    />
  );
}

function readableTextColor(hexColor: string): string {
  if (!/^#[0-9a-fA-F]{6}$/.test(hexColor)) {
    return "#ffffff";
  }
  const red = Number.parseInt(hexColor.slice(1, 3), 16);
  const green = Number.parseInt(hexColor.slice(3, 5), 16);
  const blue = Number.parseInt(hexColor.slice(5, 7), 16);
  const luminance = (0.2126 * red + 0.7152 * green + 0.0722 * blue) / 255;
  return luminance > 0.62 ? "#171717" : "#ffffff";
}

function propertyForSpace(space: StaticDataBoardSpace): StaticDataProperty | null {
  if (!space.property_id) {
    return null;
  }
  return PROPERTIES_BY_ID[space.property_id] ?? null;
}

function streetPropertyBandColor(property: StaticDataProperty | null): string | null {
  if (!property || property.kind !== "street") {
    return null;
  }
  return groupColorById.get(property.group) ?? null;
}

function spaceKind(space: StaticDataBoardSpace, property: StaticDataProperty | null): string {
  if (property?.kind === "street") {
    return "street-property";
  }
  return space.type;
}

function money(amount: number): string {
  return `$${amount.toLocaleString("en-US")}`;
}

function bottomLabel(space: StaticDataBoardSpace, property: StaticDataProperty | null): string | null {
  if (property) {
    return money(property.price);
  }
  if (space.type === "tax") {
    const taxAmount = space.amount ?? 0;
    return `pay $${taxAmount.toFixed(2)}`;
  }
  return null;
}

function defaultOwnership(propertyId: string): PropertyOwnershipView {
  return {
    property_id: propertyId,
    owner_id: null,
    mortgaged: false,
    houses: 0,
    hotels: 0,
    hotel: false,
  };
}

function ownershipFromRecord(record: Record<string, unknown>): PropertyOwnershipView | null {
  const propertyId = readString(record.property_id);
  if (!propertyId) {
    return null;
  }

  const hasHotel = record.hotel === true || readInteger(record.hotels, 0) > 0;
  return {
    property_id: propertyId,
    owner_id: readString(record.owner_id),
    mortgaged: record.mortgaged === true,
    houses: Math.max(0, readInteger(record.houses, 0)),
    hotels: hasHotel ? Math.max(1, readInteger(record.hotels, 1)) : 0,
    hotel: hasHotel,
  };
}

function ownershipByProperty(snapshot: GameStateResponse | undefined): Map<string, PropertyOwnershipView> {
  const ownerships: Map<string, PropertyOwnershipView> = new Map(
    Object.values(PROPERTIES_BY_ID).map((property) => [property.id, defaultOwnership(property.id)]),
  );
  const property_ownership = snapshot?.state.property_ownership;
  if (!Array.isArray(property_ownership)) {
    return ownerships;
  }

  for (const value of property_ownership) {
    if (!isRecord(value)) {
      continue;
    }
    const ownership = ownershipFromRecord(value);
    if (ownership && ownerships.has(ownership.property_id)) {
      ownerships.set(ownership.property_id, ownership);
    }
  }
  return ownerships;
}

function ownerPlayer(game: GameMetadata, ownerId: string | null): GamePlayer | null {
  if (!ownerId) {
    return null;
  }
  return game.players.find((player) => player.id === ownerId) ?? null;
}

function contentRotationForPosition(position: number): number {
  if (position > 10 && position < 20) {
    return 90;
  }
  if (position > 20 && position < 30) {
    return 180;
  }
  if (position > 30) {
    return -90;
  }
  return 0;
}

function perimeterEdgeForPosition(position: number): BoardEdge {
  if (position > 10 && position < 20) {
    return "left";
  }
  if (position > 20 && position < 30) {
    return "top";
  }
  if (position > 30) {
    return "right";
  }
  return "bottom";
}

function oppositeEdge(edge: BoardEdge): BoardEdge {
  switch (edge) {
    case "bottom":
      return "top";
    case "left":
      return "right";
    case "top":
      return "bottom";
    case "right":
      return "left";
  }
}

function propertyMarkerEdgesForPosition(position: number): {
  developmentEdge: BoardEdge;
  ownershipEdge: BoardEdge;
} {
  const ownershipEdge = perimeterEdgeForPosition(position);
  return {
    developmentEdge: oppositeEdge(ownershipEdge),
    ownershipEdge,
  };
}

const markerSideClasses: Record<BoardEdge, string> = {
  bottom: "bottom-0 left-1/2 -translate-x-1/2 translate-y-px rounded-b-none",
  left: "left-0 top-1/2 -translate-x-px -translate-y-1/2 rounded-l-none",
  right: "right-0 top-1/2 translate-x-px -translate-y-1/2 rounded-r-none",
  top: "left-1/2 top-0 -translate-x-1/2 -translate-y-px rounded-t-none",
};

function developmentMarkerAxisClass(edge: BoardEdge): string {
  return edge === "left" || edge === "right" ? "flex-col" : "flex-row";
}

function orientedContentStyle(rotation: number): CSSProperties {
  const sideways = Math.abs(rotation) === 90;
  return {
    height: sideways ? "200%" : "100%",
    transform: `translate(-50%, -50%) rotate(${rotation}deg)`,
    width: sideways ? "50%" : "100%",
  };
}

function BoardTitleMark() {
  return (
    <div className="relative mx-auto grid max-w-sm place-items-center px-3 py-2 text-center">
      <svg aria-hidden="true" className="absolute inset-0 h-full w-full" viewBox="0 0 360 150" preserveAspectRatio="none">
        <path d="M28 75 C54 18 123 13 180 34 C237 13 306 18 332 75 C306 132 237 137 180 116 C123 137 54 132 28 75 Z" fill="#173c45" />
        <path d="M43 75 C66 32 124 28 180 48 C236 28 294 32 317 75 C294 118 236 122 180 102 C124 122 66 118 43 75 Z" fill="none" stroke="#d7a84c" strokeWidth="7" />
        <path d="M93 75 H267" stroke="#f7e6ad" strokeWidth="2" strokeLinecap="round" opacity="0.75" />
      </svg>
      <div className="relative py-3">
        <h2 className="font-serif text-3xl font-black leading-none text-[#fff7dc] [text-shadow:0_2px_0_rgba(47,36,24,0.5)]">
          Monopoly 2.0
        </h2>
      </div>
    </div>
  );
}

function WinnerStarsGraphic() {
  return (
    <svg
      aria-hidden="true"
      className="absolute inset-0 h-full w-full"
      data-winner-stars=""
      preserveAspectRatio="none"
      viewBox="0 0 480 320"
    >
      <path d="M240 24 L263 96 L339 96 L277 140 L301 212 L240 168 L179 212 L203 140 L141 96 L217 96 Z" fill="#f7d977" opacity="0.9" />
      <path d="M96 58 L109 95 L149 95 L117 119 L130 156 L96 133 L62 156 L75 119 L43 95 L83 95 Z" fill="#fff7dc" opacity="0.85" />
      <path d="M388 72 L401 110 L441 110 L408 133 L421 171 L388 148 L355 171 L368 133 L335 110 L375 110 Z" fill="#fff2ca" opacity="0.82" />
      <path d="M132 218 L142 247 L173 247 L148 265 L157 294 L132 276 L107 294 L116 265 L91 247 L122 247 Z" fill="#d7a84c" opacity="0.82" />
      <path d="M350 216 L360 245 L391 245 L366 263 L375 292 L350 274 L325 292 L334 263 L309 245 L340 245 Z" fill="#d7a84c" opacity="0.82" />
      <path d="M60 190 C112 158 151 156 206 178" fill="none" stroke="#d7a84c" strokeLinecap="round" strokeWidth="6" opacity="0.4" />
      <path d="M274 178 C329 156 368 158 420 190" fill="none" stroke="#d7a84c" strokeLinecap="round" strokeWidth="6" opacity="0.4" />
      <circle cx="240" cy="238" fill="#173c45" opacity="0.1" r="132" />
    </svg>
  );
}

function WinnerBoardCelebration({ winner }: Readonly<{ winner: GamePlayer }>) {
  const message = `Winner ${winner.name}!`;

  return (
    <div
      aria-label={message}
      aria-live="polite"
      className="relative grid h-full min-h-0 place-items-center overflow-hidden rounded border-2 border-[#2f2418] bg-[#fffbea] px-4 py-5 text-center text-[#1f2a1f] shadow-[inset_0_0_0_5px_rgba(215,168,76,0.22)]"
      data-winner-celebration=""
      role="status"
    >
      <WinnerStarsGraphic />
      <div className="relative grid max-w-sm place-items-center gap-3">
        <span aria-hidden="true" className="rounded-full border border-[#d7a84c] bg-[#173c45] px-3 py-1 text-[10px] font-black uppercase text-[#f7d977]">
          Game over
        </span>
        <h2 className="max-w-[92%] break-words font-serif font-black leading-tight text-[#173c45] [text-shadow:0_2px_0_rgba(247,217,119,0.7)]">
          <span className="block text-xl">Winner </span>
          <span className="block whitespace-nowrap text-xl">{winner.name}!</span>
        </h2>
        <div aria-hidden="true" className="h-1 w-28 max-w-full rounded-full bg-[#d7a84c]" />
      </div>
    </div>
  );
}

function BoardMotionBanner({ motion }: Readonly<{ motion?: BoardMotion }>) {
  if (!motion || motion.status === "rolling") {
    return null;
  }
  const playerLabel = motion.playerName?.trim() || "Player";
  const currentSpaceName = motion.status === "moving" ? spaceNameForPosition(motion.displayPosition) : null;
  const landedSpaceName = motion.landedSpaceName ?? spaceNameForPosition(motion.toPosition);
  const isLanding = motion.status === "settled";
  const message = isLanding
    ? `${playerLabel} landed on ${landedSpaceName ?? "the board"}`
    : `${playerLabel} moving to ${currentSpaceName ?? landedSpaceName ?? "next space"}`;

  return (
    <div
      aria-label={isLanding ? "Board landing" : "Board movement"}
      aria-live="polite"
      className="relative z-[80] mx-auto w-fit max-w-[6rem] overflow-hidden rounded-sm border-2 border-[#2f2418] bg-[#fffbea] px-1.5 py-1 text-center text-[#1f2a1f] shadow-[0_5px_0_rgba(47,36,24,0.16)]"
      data-board-motion-banner={motion.status}
      data-board-motion-layer="top"
      data-board-motion-overlap="stacked-clearance"
      data-board-motion-placement="center-stack"
      data-board-motion-size="compact-route-pill"
      role="status"
    >
      <div className="break-words text-[9px] font-black leading-[1.08]">{message}</div>
    </div>
  );
}

function CenterMotionStack({
  lastRoll,
  motion,
}: Readonly<{
  lastRoll?: LastRollView | null;
  motion?: BoardMotion;
}>) {
  if (!motion && !lastRoll) {
    return null;
  }
  const showMotionBanner = Boolean(motion && motion.status !== "rolling");

  return (
    <div
      className="pointer-events-none absolute inset-0 z-[65]"
      data-center-motion-gap="collision-proof"
      data-center-motion-layout="stacked-route-and-dice"
      data-center-motion-stack=""
    >
      <div
        className="absolute left-1/2 top-1/2 grid w-[7rem] max-w-[64%] -translate-x-1/2 -translate-y-1/2 justify-items-center gap-1.5"
        data-center-motion-stack-inner=""
      >
        {showMotionBanner ? (
          <div
            className="relative z-[80] w-full max-w-[6rem]"
            data-center-motion-banner-layer=""
            data-center-motion-lane="movement"
            data-center-motion-lane-position="route-above-dice"
          >
            <BoardMotionBanner motion={motion} />
          </div>
        ) : null}
        <div
          className="relative z-30 w-fit max-w-[5.75rem]"
          data-center-dice-layer=""
          data-center-motion-lane="dice"
          data-center-motion-lane-position={showMotionBanner ? "dice-below-route" : "centered"}
        >
          <DiceMotionStatus lastRoll={lastRoll} motion={motion} placement="center-board" />
        </div>
      </div>
    </div>
  );
}

function CenterBoardArt({
  lastRoll,
  motion,
  winner,
}: Readonly<{ lastRoll?: LastRollView | null; motion?: BoardMotion; winner: GamePlayer | null }>) {
  return (
    <div
      className="col-start-3 col-end-12 row-start-3 row-end-12 overflow-hidden border-4 border-[#2f2418] bg-[#eaf3d7] p-3 text-[#2f2418] shadow-inner"
      data-center-board-art=""
      data-testid="center-board-art"
    >
      <div className="relative flex h-full min-h-0 flex-col justify-between overflow-hidden border border-[#2f2418]/20 bg-[#eaf3d7] p-3">
        {winner ? (
          <WinnerBoardCelebration winner={winner} />
        ) : (
          <>
            <div className="relative">
              <BoardTitleMark />
            </div>

            <div className="relative grid min-h-0 grid-cols-2 gap-3">
              <DeckArtPreview deck={DECK_ART.chance} />
              <DeckArtPreview deck={DECK_ART.community_chest} />
            </div>

            <CenterMotionStack lastRoll={lastRoll} motion={motion} />
          </>
        )}
      </div>
    </div>
  );
}

const dicePipCells: Record<number, number[]> = {
  1: [4],
  2: [0, 8],
  3: [0, 4, 8],
  4: [0, 2, 6, 8],
  5: [0, 2, 4, 6, 8],
  6: [0, 2, 3, 5, 6, 8],
};

function DiceFace({ index, rolling, value }: Readonly<{ index: number; rolling: boolean; value: number | "?" }>) {
  const style = rolling ? ({ "--dice-delay": `${index * 90}ms` } as CSSProperties) : undefined;
  const pipCells = typeof value === "number" ? (dicePipCells[value] ?? []) : [];

  return (
    <span
      aria-hidden="true"
      className="dice-motion-face grid size-9 place-items-center rounded-md border-2 border-[#1f2a1f] bg-white text-base font-black text-[#1f2a1f] shadow-sm"
      data-dice-face=""
      data-dice-value={typeof value === "number" ? value : undefined}
      data-dice-tumble={rolling ? "true" : undefined}
      style={style}
    >
      {pipCells.length > 0 ? (
        <span className="grid size-6 grid-cols-3 grid-rows-3 gap-0.5" data-dice-pips="">
          {Array.from({ length: 9 }, (_, cellIndex) => (
            <span
              key={cellIndex}
              aria-hidden="true"
              className={pipCells.includes(cellIndex) ? "size-1.5 self-center justify-self-center rounded-full bg-[#1f2a1f]" : ""}
              data-dice-pip={pipCells.includes(cellIndex) ? "" : undefined}
            />
          ))}
        </span>
      ) : (
        value
      )}
    </span>
  );
}

function DiceMotionStatus({
  lastRoll,
  motion,
  placement = "board-overlay",
}: Readonly<{
  lastRoll?: LastRollView | null;
  motion?: BoardMotion;
  placement?: "board-overlay" | "center-board";
}>) {
  if (!motion && !lastRoll) {
    return null;
  }
  const rolling = motion?.status === "rolling";
  const diceSource = motion?.dice && motion.dice.length > 0 ? motion.dice : lastRoll?.dice;
  const dice: Array<number | "?"> = diceSource && diceSource.length > 0 ? diceSource : ["?", "?"];
  const diceLabel = diceSource && diceSource.length > 0 ? diceSource.join(" + ") : "Rolling dice";
  const total = typeof motion?.total === "number" ? motion.total : lastRoll?.total;
  const totalLabel = typeof total === "number" ? ` = ${total}` : "";
  const primaryLabel = diceSource && diceSource.length > 0 ? `${diceLabel}${totalLabel}` : "Rolling dice";
  const doublesLabel =
    lastRoll && !motion && lastRoll.isDoubles && lastRoll.dice.length >= 2
      ? `Double ${lastRoll.dice[0]}s`
      : null;
  const rollDestinationLabel =
    !motion && lastRoll
      ? `${lastRoll.playerName ? `${lastRoll.playerName} rolled` : "Last roll"}${lastRoll.landedSpaceName ? ` to ${lastRoll.landedSpaceName}` : ""}`
      : null;
  const lastRollLabel =
    doublesLabel && rollDestinationLabel
      ? `${doublesLabel} - ${rollDestinationLabel}`
      : (doublesLabel ?? rollDestinationLabel);
  const movementLabel =
    motion?.status === "rolling"
      ? "Dice in motion"
      : motion?.status === "moving"
        ? "Token moving"
        : motion?.status === "settled"
          ? "Dice resolved"
          : lastRollLabel ?? "Last roll";

  return (
    <div
      aria-label="Dice roll animation"
      aria-live="polite"
      className={cn(
        "dice-motion-panel z-40 rounded-md border-2 border-[#1f2a1f] bg-[#fffbea]/95 text-center text-[#1f2a1f] shadow-[0_14px_30px_rgba(31,42,31,0.22)]",
        placement === "board-overlay"
          ? "absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 px-3 py-2"
          : "relative w-fit max-w-full px-2 py-1.5",
      )}
      data-dice-placement={placement}
      data-dice-motion={motion?.status ?? "last-roll"}
      data-dice-layer={placement === "center-board" ? "below-motion-banner" : undefined}
      data-dice-size={placement === "center-board" ? "compact-center" : undefined}
      role="status"
    >
      <span aria-hidden="true" className="dice-motion-ring" />
      <div className="relative flex justify-center gap-1.5">
        {dice.map((value, index) => (
          <DiceFace key={`${value}-${index}`} index={index} rolling={rolling} value={value} />
        ))}
      </div>
      <div className="relative mt-1 text-[10px] font-black uppercase">{primaryLabel}</div>
      <div className="text-[9px] font-semibold uppercase text-[#456038]">{movementLabel}</div>
    </div>
  );
}

function DrawnCardModal({
  card,
  onDismiss,
}: Readonly<{
  card?: DrawnCardView | null;
  onDismiss?: () => void;
}>) {
  if (!card) {
    return null;
  }
  const deckKey = card.deckLabel.toLowerCase().includes("community") ? "community_chest" : "chance";
  const deck = DECK_ART[deckKey];
  const [main, background, accent] = deck.palette;

  return (
    <div className="absolute inset-0 z-[55] grid place-items-center bg-[#1f2a1f]/35 p-4" data-card-modal="">
      <article
        aria-label={`${card.deckLabel} card`}
        aria-modal="true"
        className="drawn-card-reveal w-full max-w-sm rounded-md border-2 border-[#1f2a1f] bg-[#fffbea] p-4 text-left text-[#1f2a1f] shadow-[0_22px_60px_rgba(31,42,31,0.38)]"
        data-card-deck={deckKey}
        data-card-reveal=""
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            onDismiss?.();
          }
        }}
        role="dialog"
        tabIndex={-1}
      >
        <div className="flex items-start justify-between gap-3 border-b border-[#1f2a1f]/25 pb-3">
          <div className="min-w-0">
            <div className="text-[11px] font-black uppercase text-[#456038]">{card.deckLabel}</div>
            <h3 className="mt-1 text-xl font-black uppercase leading-tight">{card.title}</h3>
          </div>
          <button
            aria-label="Dismiss card"
            className="grid size-8 shrink-0 place-items-center rounded-md border border-[#1f2a1f]/35 bg-white/80 text-[#1f2a1f] transition hover:bg-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#0f766e]"
            onClick={onDismiss}
            type="button"
          >
            <X aria-hidden="true" className="size-4" />
          </button>
        </div>
        <div
          aria-label={`${card.deckLabel} card art`}
          className="mt-3 grid min-h-28 place-items-center rounded border-2 px-4 py-3"
          data-card-art=""
          role="img"
          style={{
            backgroundColor: background,
            borderColor: main,
            color: main,
          }}
        >
          <svg aria-hidden="true" className="h-16 w-24" viewBox="0 0 96 64">
            {deckKey === "chance" ? (
              <>
                <path d="M47 10 C36 10 29 17 29 26 H42 C42 23 44 21 48 21 C52 21 55 23 55 27 C55 31 52 33 48 35 C43 38 41 42 42 49 H54 C53 45 55 43 59 40 C65 36 69 32 69 25 C69 16 61 10 47 10 Z" fill={main} />
                <circle cx="48" cy="56" r="5" fill={accent} />
              </>
            ) : (
              <>
                <path d="M18 25 H78 V54 H18 Z" fill={main} />
                <path d="M24 18 H72 L78 25 H18 Z" fill={accent} />
                <path d="M48 18 V54" stroke={background} strokeWidth="5" />
                <circle cx="48" cy="39" r="6" fill={background} />
              </>
            )}
          </svg>
        </div>
        <div className="mt-3 text-sm font-semibold leading-6">{card.description}</div>
        {card.playerName ? (
          <div className="mt-3 text-xs font-bold uppercase text-[#456038]">{card.playerName}</div>
        ) : null}
      </article>
    </div>
  );
}

function PropertyHoverOverlay({
  game,
  property,
  snapshot,
  tooltipId,
}: Readonly<{
  game: GameMetadata;
  property: StaticDataProperty | null;
  snapshot?: GameStateResponse;
  tooltipId: string;
}>) {
  if (!property) {
    return null;
  }

  const ownership = ownershipByProperty(snapshot).get(property.id) ?? defaultOwnership(property.id);
  return (
    <div
      aria-label={`Property detail: ${property.name}`}
      className="pointer-events-none absolute inset-0 z-50 grid place-items-center bg-[#1f2a1f]/20 p-4"
      data-property-hover=""
      id={tooltipId}
      role="tooltip"
    >
      <PropertyDeedCard
        className="w-full max-w-md text-left shadow-[0_22px_60px_rgba(31,42,31,0.35)]"
        game={game}
        ownership={ownership}
        property={property}
      />
    </div>
  );
}

function OrientedSpaceContent({
  children,
  rotation,
}: Readonly<{
  children: ReactNode;
  rotation: number;
}>) {
  return (
    <div
      className="absolute left-1/2 top-1/2 flex min-h-0 min-w-0 flex-col"
      data-space-orientation={rotation}
      style={orientedContentStyle(rotation)}
    >
      {children}
    </div>
  );
}

function RotateBoardButton({ onRotate }: Readonly<{ onRotate: () => void }>) {
  return (
    <button
      aria-label="Rotate board 90 degrees"
      className="absolute right-4 top-4 z-[60] grid size-9 place-items-center rounded-md border-2 border-[#1f2a1f] bg-[#fffbea]/95 text-[#1f2a1f] shadow-md transition hover:bg-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#0f766e]"
      onClick={onRotate}
      title="Rotate board 90 degrees"
      type="button"
    >
      <RotateCw aria-hidden="true" className="size-4" />
    </button>
  );
}

function BoardOwnerMarker({
  game,
  ownership,
  position,
  property,
}: Readonly<{
  game: GameMetadata;
  ownership: PropertyOwnershipView;
  position: number;
  property: StaticDataProperty;
}>) {
  const owner = ownerPlayer(game, ownership.owner_id);
  if (!owner) {
    return null;
  }

  const color = getPlayerColor(game, owner.seat_order);
  const icon = getPlayerIcon(game, owner.seat_order);
  const { ownershipEdge } = propertyMarkerEdgesForPosition(position);

  return (
    <span
      aria-label={`Owner marker: ${owner.name} owns ${property.name}`}
      className={cn(
        "absolute z-20 grid size-4 place-items-center rounded-sm border border-[#2f2418]/70 text-[9px] font-black shadow-sm",
        markerSideClasses[ownershipEdge],
      )}
      data-marker-anchor="perimeter-price-edge"
      data-marker-board-zone="perimeter"
      data-marker-card-slot={ownershipEdge}
      data-marker-edge="perimeter"
      data-marker-placement="owner-perimeter"
      data-marker-role="ownership"
      data-marker-side={ownershipEdge}
      data-owner-marker=""
      data-token-icon={icon}
      role="img"
      style={{
        backgroundColor: color,
        color: readableTextColor(color),
      }}
      title={`${owner.name} owns ${property.name}`}
    >
      <span aria-hidden="true" className="leading-none" data-player-token-icon="">
        {icon}
      </span>
    </span>
  );
}

function DevelopmentMarker({
  ownership,
  position,
  property,
}: Readonly<{
  ownership: PropertyOwnershipView;
  position: number;
  property: StaticDataProperty;
}>) {
  if (property.kind !== "street") {
    return null;
  }

  const hasHotel = ownership.hotel || ownership.hotels > 0;
  const houses = Math.max(0, ownership.houses);
  if (!hasHotel && houses === 0) {
    return null;
  }

  const label = hasHotel
    ? `Development marker: ${property.name} has a hotel`
    : `Development marker: ${property.name} has ${houses} ${houses === 1 ? "house" : "houses"}`;
  const { developmentEdge } = propertyMarkerEdgesForPosition(position);

  return (
    <span
      aria-label={label}
      className={cn(
        "absolute z-20 flex items-center justify-center gap-0.5 rounded-sm border border-[#2f2418]/50 bg-[#fffbea]/95 px-1 py-0.5 shadow-sm",
        developmentMarkerAxisClass(developmentEdge),
        markerSideClasses[developmentEdge],
      )}
      data-development-marker=""
      data-marker-anchor="interior-development-edge"
      data-marker-board-zone="interior"
      data-marker-card-slot={developmentEdge}
      data-marker-edge="interior"
      data-marker-placement="development-interior"
      data-marker-role="development"
      data-marker-side={developmentEdge}
      role="img"
      title={label.replace("Development marker: ", "")}
    >
      {hasHotel ? (
        <span className="text-[8px] font-black leading-none text-[#7f1d1d]">H</span>
      ) : (
        Array.from({ length: Math.min(houses, 4) }, (_, index) => (
          <span key={index} aria-hidden="true" className="size-1.5 rounded-[2px] border border-[#2f2418]/40 bg-[#2f8f46]" />
        ))
      )}
    </span>
  );
}

function TokenStack({
  game,
  motion,
  players,
  space,
}: Readonly<{
  game: GameMetadata;
  motion?: BoardMotion;
  players: GamePlayer[];
  space: StaticDataBoardSpace;
}>) {
  return (
    <div className="flex min-h-6 flex-wrap items-center justify-center gap-1" aria-label={`Tokens on ${space.name}`}>
      {players.map((player) => {
        const color = getPlayerColor(game, player.seat_order);
        const icon = getPlayerIcon(game, player.seat_order);
        const shape = tokenShapeForSeat(player.seat_order);
        const isMovingToken = motion?.status === "moving" && motion.playerId === player.id;
        const isLandingToken = motion?.status === "settled" && motion.playerId === player.id;
        return (
          <span
            key={player.id}
            aria-label={`${player.name} token at ${space.name}, position ${space.position}`}
            className={`board-token group/token relative inline-grid size-6 place-items-center rounded-full text-base font-black focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-[#0f766e] ${
              isMovingToken ? "board-token-moving z-20" : ""
            } ${isLandingToken ? "board-token-landing z-20" : ""}`}
            data-token-landing={isLandingToken ? "true" : undefined}
            data-token-moving={isMovingToken ? "true" : undefined}
            data-player-id={player.id}
            data-player-token=""
            data-token-icon={icon}
            data-token-shape={shape}
            data-space-index={space.position}
            style={{
              color: readableTextColor(color),
            }}
            tabIndex={0}
            title={player.name}
          >
            {isMovingToken || isLandingToken ? <span aria-hidden="true" className="board-token-trail" data-token-trail="" /> : null}
            <TokenPuck color={color} />
            <span
              aria-hidden="true"
              className="relative z-10 translate-y-px leading-none drop-shadow-[0_1px_0_rgba(255,255,255,0.75)]"
              data-player-token-icon=""
            >
              {icon}
            </span>
            <span className="pointer-events-none absolute left-1/2 top-full z-40 mt-1 -translate-x-1/2 whitespace-nowrap rounded bg-[#1f2a1f] px-1.5 py-0.5 text-[9px] font-bold text-white opacity-0 shadow-sm transition-opacity group-hover/token:opacity-100 group-focus/token:opacity-100" data-player-token-label="">
              {player.name}
            </span>
          </span>
        );
      })}
    </div>
  );
}

function tokenOverlayStyle(position: number, color: string): CSSProperties {
  const coordinates = boardCoordinates(normalizedPosition(position));
  const centerColumn = coordinates.column - 1 + coordinates.columnSpan / 2;
  const centerRow = coordinates.row - 1 + coordinates.rowSpan / 2;
  return {
    color: readableTextColor(color),
    left: `${(centerColumn / boardGridSize) * 100}%`,
    top: `${(centerRow / boardGridSize) * 100}%`,
  };
}

function MotionTokenOverlay({
  game,
  motion,
}: Readonly<{
  game: GameMetadata;
  motion?: BoardMotion;
}>) {
  if (motion?.status !== "moving") {
    return null;
  }
  const player = game.players.find((candidate) => candidate.id === motion.playerId);
  if (!player) {
    return null;
  }
  const position = normalizedPosition(motion.displayPosition);
  const space = BOARD_SPACES[position];
  if (!space) {
    return null;
  }
  const color = getPlayerColor(game, player.seat_order);
  const icon = getPlayerIcon(game, player.seat_order);
  const shape = tokenShapeForSeat(player.seat_order);

  return (
    <span
      aria-label={`${player.name} token at ${space.name}, position ${space.position}`}
      className="board-token board-token-moving board-token-motion-overlay group/token absolute z-50 grid size-8 place-items-center rounded-full text-xl font-black focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-[#0f766e]"
      data-player-id={player.id}
      data-player-token=""
      data-space-index={space.position}
      data-token-motion-overlay="true"
      data-token-moving="true"
      data-token-slide="true"
      data-token-icon={icon}
      data-token-shape={shape}
      style={tokenOverlayStyle(position, color)}
      tabIndex={0}
      title={player.name}
    >
      <span aria-hidden="true" className="board-token-trail" data-token-trail="" />
      <TokenPuck color={color} />
      <span
        aria-hidden="true"
        className="relative z-10 translate-y-px leading-none drop-shadow-[0_1px_0_rgba(255,255,255,0.75)]"
        data-player-token-icon=""
      >
        {icon}
      </span>
      <span className="pointer-events-none absolute left-1/2 top-full z-40 mt-1 -translate-x-1/2 whitespace-nowrap rounded bg-[#1f2a1f] px-1.5 py-0.5 text-[9px] font-bold text-white opacity-0 shadow-sm transition-opacity group-hover/token:opacity-100 group-focus/token:opacity-100" data-player-token-label="">
        {player.name}
      </span>
    </span>
  );
}

function StreetPropertyCell({
  bandColor,
  game,
  motion,
  players,
  property,
  space,
}: Readonly<{
  bandColor: string;
  game: GameMetadata;
  motion?: BoardMotion;
  players: GamePlayer[];
  property: StaticDataProperty;
  space: StaticDataBoardSpace;
}>) {
  return (
    <>
      <span aria-hidden="true" className="h-3 w-full shrink-0" data-property-color-band="" style={{ backgroundColor: bandColor }} />
      <div className="flex min-h-0 flex-1 flex-col justify-between gap-0.5 px-1 pb-1 pt-1">
        <div className="break-words text-[8px] font-black leading-[0.9] text-[#1f2a1f] uppercase" data-space-name="">
          {space.name}
        </div>
        <TokenStack game={game} motion={motion} players={players} space={space} />
        <div className="text-[9px] font-bold leading-none text-[#1f2a1f]" data-space-bottom-label="">
          {money(property.price)}
        </div>
      </div>
    </>
  );
}

function JailTurnMeter({ turns }: Readonly<{ turns: number }>) {
  const cappedTurns = Math.max(0, Math.min(3, turns));
  return (
    <div className="mt-1 grid gap-0.5" data-jail-turn-meter="">
      <div className="flex items-center justify-center gap-0.5" aria-hidden="true">
        {Array.from({ length: 3 }, (_, index) => (
          <span
            key={index}
            className={cn(
              "size-1.5 rounded-full border border-[#2f2418]/35",
              index < cappedTurns ? "bg-[#d9552b]" : "bg-white/70",
            )}
          />
        ))}
      </div>
      <div className="text-[7px] font-black uppercase leading-none text-[#6f604c]">
        {turns} {turns === 1 ? "turn" : "turns"}
      </div>
    </div>
  );
}

function JailSpaceCell({
  game,
  motion,
  players,
  snapshot,
  space,
}: Readonly<{
  game: GameMetadata;
  motion?: BoardMotion;
  players: GamePlayer[];
  snapshot?: GameStateResponse;
  space: StaticDataBoardSpace;
}>) {
  const jailedPlayers = players.filter((player) => playerJailStatus(player, snapshot).inJail);
  const visitingPlayers = players.filter((player) => !playerJailStatus(player, snapshot).inJail);

  return (
    <div className="grid h-full min-h-0 grid-rows-[auto_minmax(0,1fr)] gap-1 px-1 py-1" data-jail-space-cell="">
      <div className="text-[9px] font-black uppercase leading-none text-[#1f2a1f]" data-space-name="">
        Jail / Just Visiting
      </div>
      <div className="grid min-h-0 grid-cols-[1.05fr_0.95fr] gap-1">
        <div className="grid min-h-0 content-between rounded-sm border-2 border-[#2f2418] bg-[#f9e2bf] px-1 py-1" data-jail-zone="jailed">
          <div className="text-center">
            <div className="text-[8px] font-black uppercase leading-none text-[#2f2418]">Jail</div>
            <div aria-hidden="true" className="mt-1 grid grid-cols-4 gap-0.5">
              {Array.from({ length: 8 }, (_, index) => (
                <span key={index} className="h-5 rounded-sm bg-[#2f2418]/75" />
              ))}
            </div>
          </div>
          <TokenStack game={game} motion={motion} players={jailedPlayers} space={space} />
          {jailedPlayers.length > 0 ? (
            <div className="grid gap-0.5">
              {jailedPlayers.map((player) => {
                const jailStatus = playerJailStatus(player, snapshot);
                return (
                  <div key={player.id} className="rounded-sm bg-white/70 px-1 py-0.5 text-center" data-jail-player-turns="">
                    <div className="truncate text-[7px] font-black leading-none text-[#2f2418]">{player.name}</div>
                    <JailTurnMeter turns={jailStatus.turns} />
                  </div>
                );
              })}
            </div>
          ) : (
            <span aria-hidden="true" className="h-2" />
          )}
        </div>
        <div
          className="grid min-h-0 content-between rounded-sm border-2 border-dashed border-[#2f2418]/45 bg-[#fffbea] px-1 py-1"
          data-jail-zone="visiting"
        >
          <div className="text-center">
            <div className="text-[8px] font-black uppercase leading-none text-[#2f2418]">Just Visiting</div>
            <SpaceMotif art={SPACE_ART_BY_ID[space.id]} className="mx-auto mt-1 h-10 w-full max-w-12" />
          </div>
          <TokenStack game={game} motion={motion} players={visitingPlayers} space={space} />
          <span aria-hidden="true" className="h-2" />
        </div>
      </div>
    </div>
  );
}

function isLargeArtworkSpace(space: StaticDataBoardSpace): boolean {
  return (
    space.id === "space_go" ||
    space.id === "space_free_parking" ||
    space.id === "space_go_to_jail" ||
    space.type === "chance" ||
    space.type === "community_chest"
  );
}

function OtherSpaceCell({
  bottom,
  game,
  isCorner,
  motion,
  players,
  space,
}: Readonly<{
  bottom: string | null;
  game: GameMetadata;
  isCorner: boolean;
  motion?: BoardMotion;
  players: GamePlayer[];
  space: StaticDataBoardSpace;
}>) {
  const art = SPACE_ART_BY_ID[space.id];
  const largeArtwork = isLargeArtworkSpace(space);
  const artClassName = largeArtwork
    ? isCorner
      ? "mx-auto h-[58%] min-h-14 w-full max-w-24 shrink"
      : "mx-auto h-[52%] min-h-10 w-full max-w-20 shrink"
    : "mx-auto h-[42%] min-h-6 w-full max-w-14 shrink";
  return (
    <div
      className={cn(
        "flex min-h-0 flex-1 flex-col justify-between gap-0.5 px-1 py-1",
        largeArtwork ? "items-stretch" : "",
      )}
      data-large-space-art={largeArtwork ? "true" : undefined}
    >
      <div
        className={`${largeArtwork ? (isCorner ? "text-[12px]" : "text-[9px]") : isCorner ? "text-[10px]" : "text-[8px]"} break-words font-black leading-[0.9] text-[#1f2a1f] uppercase`}
        data-space-name=""
      >
        {space.name}
      </div>
      <SpaceMotif art={art} className={artClassName} />
      <TokenStack game={game} motion={motion} players={players} space={space} />
      {bottom ? (
        <div className="text-[8px] font-bold leading-none text-[#1f2a1f]" data-space-bottom-label="">
          {bottom}
        </div>
      ) : (
        <span aria-hidden="true" className="h-2" />
      )}
    </div>
  );
}

type ClassicGameBoardProps = {
  drawnCard?: DrawnCardView | null;
  game: GameMetadata;
  lastRoll?: LastRollView | null;
  motion?: BoardMotion;
  onDismissDrawnCard?: () => void;
  snapshot?: GameStateResponse;
};

export function ClassicGameBoard({ drawnCard, game, lastRoll, motion, onDismissDrawnCard, snapshot }: ClassicGameBoardProps) {
  const [boardRotation, setBoardRotation] = useState(0);
  const [hoveredPropertyId, setHoveredPropertyId] = useState<string | null>(null);
  const propertyOwnerships = ownershipByProperty(snapshot);
  const playersByPosition = new Map<number, GamePlayer[]>();
  const movingPlayerId = motion?.status === "moving" ? motion.playerId : null;
  for (const player of game.players) {
    if (player.id === movingPlayerId) {
      continue;
    }
    const position = playerPosition(player, snapshot, motion);
    const players = playersByPosition.get(position) ?? [];
    players.push(player);
    playersByPosition.set(position, players);
  }
  const hoveredProperty = hoveredPropertyId ? (propertyById.get(hoveredPropertyId) ?? null) : null;
  const hoveredSpace = hoveredProperty ? propertySpaceById.get(hoveredProperty.id) : null;
  const hoveredTooltipId = hoveredSpace ? `${hoveredSpace.id}-property-details` : "board-property-details";
  const winner = winnerForGame(game);

  return (
    <section
      aria-label="Classic Monopoly-style board"
      className="relative rounded-md border border-[#2f2418]/35 bg-[#eaf3d7] p-2 shadow-[0_18px_40px_rgba(47,36,24,0.18)]"
      data-board-motion={motion?.status ?? "idle"}
    >
      <RotateBoardButton onRotate={() => setBoardRotation((rotation) => (rotation + 90) % 360)} />
      <div
        className="relative aspect-square w-full overflow-hidden rounded border-4 border-[#2f2418] bg-[#eaf3d7]"
        data-board-rotation={boardRotation}
      >
        <div
          className="absolute inset-0 grid bg-[#eaf3d7] transition-transform duration-200 ease-out"
          data-board-surface="cream-light-green"
          style={{
            gridTemplateColumns: `repeat(${boardGridSize}, minmax(0, 1fr))`,
            gridTemplateRows: `repeat(${boardGridSize}, minmax(0, 1fr))`,
            transform: `rotate(${boardRotation}deg)`,
            transformOrigin: "center",
          }}
        >
          <CenterBoardArt lastRoll={lastRoll} motion={motion} winner={winner} />

          {BOARD_SPACES.map((space) => {
            const coordinates = boardCoordinates(space.position);
            const property = propertyForSpace(space);
            const ownership = property ? (propertyOwnerships.get(property.id) ?? defaultOwnership(property.id)) : null;
            const bandColor = streetPropertyBandColor(property);
            const players = playersByPosition.get(space.position) ?? [];
            const isCorner = space.position % 10 === 0;
            const tooltipId = property ? `${space.id}-property-details` : undefined;
            const label = bottomLabel(space, property);
            const contentRotation = contentRotationForPosition(space.position);
            const clearHoveredProperty = () => {
              if (!property) {
                return;
              }
              setHoveredPropertyId((currentPropertyId) => (currentPropertyId === property.id ? null : currentPropertyId));
            };

            return (
              <div
                key={space.id}
                aria-describedby={tooltipId}
                aria-label={space.name}
                className={`relative flex min-h-0 min-w-0 flex-col overflow-hidden border border-[#2f2418]/45 bg-[#eaf3d7] text-center ${
                  property ? "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-[#0f766e]" : ""
                }`}
                data-board-space=""
                data-space-background="cream-light-green"
                data-space-index={space.position}
                data-space-kind={spaceKind(space, property)}
                data-space-shape={isCorner ? "square" : "rectangle"}
                onBlur={(event) => {
                  if (property && !event.currentTarget.contains(event.relatedTarget as Node | null)) {
                    clearHoveredProperty();
                  }
                }}
                onFocus={() => {
                  if (property) {
                    setHoveredPropertyId(property.id);
                  }
                }}
                onMouseEnter={() => {
                  if (property) {
                    setHoveredPropertyId(property.id);
                  }
                }}
                onMouseLeave={clearHoveredProperty}
                tabIndex={property ? 0 : undefined}
                style={{
                  backgroundColor: boardSurfaceColor,
                  gridColumn: `${coordinates.column} / span ${coordinates.columnSpan}`,
                  gridRow: `${coordinates.row} / span ${coordinates.rowSpan}`,
                }}
              >
                {property && ownership ? (
                  <BoardOwnerMarker
                    game={game}
                    ownership={ownership}
                    position={space.position}
                    property={property}
                  />
                ) : null}
                {property?.kind === "street" && ownership ? (
                  <DevelopmentMarker ownership={ownership} position={space.position} property={property} />
                ) : null}
                <OrientedSpaceContent rotation={contentRotation}>
                  {space.id === "space_jail" ? (
                    <JailSpaceCell
                      game={game}
                      motion={motion}
                      players={players}
                      snapshot={snapshot}
                      space={space}
                    />
                  ) : property?.kind === "street" && bandColor ? (
                    <StreetPropertyCell
                      bandColor={bandColor}
                      game={game}
                      motion={motion}
                      players={players}
                      property={property}
                      space={space}
                    />
                  ) : (
                    <OtherSpaceCell
                      bottom={label}
                      game={game}
                      isCorner={isCorner}
                      motion={motion}
                      players={players}
                      space={space}
                    />
                  )}
                </OrientedSpaceContent>
              </div>
            );
          })}
          <MotionTokenOverlay game={game} motion={motion} />
        </div>
        <DrawnCardModal card={drawnCard} onDismiss={onDismissDrawnCard} />
        <PropertyHoverOverlay game={game} property={hoveredProperty} snapshot={snapshot} tooltipId={hoveredTooltipId} />
      </div>
    </section>
  );
}
