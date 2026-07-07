"use client";

import { useState, type CSSProperties, type ReactNode } from "react";
import { BOARD_SPACES, PROPERTIES_BY_ID, PROPERTY_GROUPS, type StaticDataBoardSpace, type StaticDataProperty } from "@monopoly-ai-game/schemas";
import { RotateCw, X } from "lucide-react";

import type { GameMetadata, GamePlayer } from "../lib/api/games";
import type { GameStateResponse } from "../lib/api/gameplay";
import { DECK_ART, DeckArtPreview, SPACE_ART_BY_ID, SpaceMotif } from "./board-art";

type BoardCoordinates = {
  row: number;
  column: number;
  rowSpan: number;
  columnSpan: number;
};

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
      total?: number;
    }
  | {
      status: "moving" | "settled";
      playerId: string;
      fromPosition: number;
      toPosition: number;
      displayPosition: number;
      dice?: number[];
      total?: number;
    };

export type DrawnCardView = {
  eventId: string;
  deckLabel: string;
  title: string;
  description: string;
  playerName: string | null;
};

const boardGridSize = 13;
const fallbackPlayerColor = "#525866";
const boardSurfaceColor = "#eaf3d7";
const groupColorById = new Map(PROPERTY_GROUPS.map((group) => [group.id, group.color]));
const groupById = new Map(PROPERTY_GROUPS.map((group) => [group.id, group]));
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

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function readString(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function readInteger(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isInteger(value) ? value : fallback;
}

function snapshotPlayerPosition(snapshot: GameStateResponse | undefined, playerId: string): number | null {
  const players = snapshot?.state.players;
  if (!Array.isArray(players)) {
    return null;
  }
  const player = players.find((entry) => {
    if (entry === null || typeof entry !== "object" || Array.isArray(entry)) {
      return false;
    }
    return (entry as Record<string, unknown>).id === playerId;
  });
  if (player === null || typeof player !== "object" || Array.isArray(player)) {
    return null;
  }
  const position = (player as Record<string, unknown>).position;
  return typeof position === "number" && Number.isInteger(position) ? normalizedPosition(position) : null;
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

function tokenText(name: string, seatOrder: number): string {
  const firstLetter = name.trim().charAt(0);
  return firstLetter ? firstLetter.toUpperCase() : String(seatOrder + 1);
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
    const taxAmount = space.id === "space_luxury_tax" ? 75 : (space.amount ?? 0);
    return `pay $${taxAmount.toFixed(2)}`;
  }
  if (space.type === "community_chest") {
    return "Follow instructions on top card";
  }
  return null;
}

function propertyFacts(property: StaticDataProperty): string[] {
  if (property.kind === "street") {
    return [
      `Rent ${money(property.rents[0])} base`,
      `1 house ${money(property.rents[1])}`,
      `Hotel rent ${money(property.rents[5])}`,
      `House cost ${money(property.house_cost)}`,
    ];
  }
  if (property.kind === "railroad") {
    return [
      `Rent ${money(property.rent_by_owned_count[0])}-${money(
        property.rent_by_owned_count[property.rent_by_owned_count.length - 1],
      )} by railroads owned`,
    ];
  }
  return [`Rent multiplier ${property.rent_multipliers[0]}x/${property.rent_multipliers[1]}x dice`];
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

function ownerName(game: GameMetadata, ownerId: string | null): string {
  if (!ownerId) {
    return "Bank/unowned";
  }
  return game.players.find((player) => player.id === ownerId)?.name ?? ownerId;
}

function propertyGroupName(property: StaticDataProperty): string {
  return groupById.get(property.group)?.name ?? property.group;
}

function hotelConversionText(property: StaticDataProperty, ownership: PropertyOwnershipView): string {
  if (property.kind !== "street") {
    return "Hotel conversion: Not available for railroads or utilities.";
  }
  if (ownership.hotel || ownership.hotels > 0) {
    return "Hotel conversion: hotel-to-houses status appears in property management.";
  }
  if (ownership.houses === 4) {
    return "Hotel conversion: four-house-to-hotel status appears in property management.";
  }
  return "Hotel conversion: Not at conversion threshold.";
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
        <p className="text-[11px] font-black uppercase tracking-normal text-[#f7d977]">Local tabletop edition</p>
        <h2 className="mt-0.5 font-serif text-3xl font-black leading-none text-[#fff7dc] [text-shadow:0_2px_0_rgba(47,36,24,0.5)]">
          Monopoly 2.0
        </h2>
        <p className="mt-1 text-[10px] font-bold uppercase tracking-normal text-[#f7d977]">AI strategy board</p>
      </div>
    </div>
  );
}

function CenterBoardArt() {
  return (
    <div className="col-start-3 col-end-12 row-start-3 row-end-12 overflow-hidden border-4 border-[#2f2418] bg-[#eaf3d7] p-3 text-[#2f2418] shadow-inner">
      <div className="relative flex h-full min-h-0 flex-col justify-between overflow-hidden border border-[#2f2418]/20 bg-[#eaf3d7] p-3">
        <div className="relative">
          <BoardTitleMark />
        </div>

        <div className="relative grid min-h-0 grid-cols-2 gap-3">
          <DeckArtPreview deck={DECK_ART.chance} />
          <DeckArtPreview deck={DECK_ART.community_chest} />
        </div>
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

function DiceMotionStatus({ motion }: Readonly<{ motion?: BoardMotion }>) {
  if (!motion) {
    return null;
  }
  const rolling = motion.status === "rolling";
  const dice: Array<number | "?"> = motion.dice && motion.dice.length > 0 ? motion.dice : ["?", "?"];
  const diceLabel = motion.dice && motion.dice.length > 0 ? motion.dice.join(" + ") : "Rolling dice";
  const totalLabel = typeof motion.total === "number" ? ` = ${motion.total}` : "";
  const primaryLabel = motion.dice && motion.dice.length > 0 ? `${diceLabel}${totalLabel}` : "Rolling dice";
  const movementLabel =
    motion.status === "moving"
      ? `Moving ${motion.fromPosition} to ${motion.toPosition}`
      : rolling
        ? "Dice in motion"
        : "Landed";

  return (
    <div
      aria-label="Dice roll animation"
      aria-live="polite"
      className="dice-motion-panel absolute left-1/2 top-1/2 z-40 -translate-x-1/2 -translate-y-1/2 rounded-md border-2 border-[#1f2a1f] bg-[#fffbea]/95 px-3 py-2 text-center text-[#1f2a1f] shadow-[0_14px_30px_rgba(31,42,31,0.22)]"
      data-dice-motion={motion.status}
      role="status"
    >
      <span aria-hidden="true" className="dice-motion-ring" />
      <div className="relative flex justify-center gap-1.5">
        {dice.map((value, index) => (
          <DiceFace key={`${value}-${index}`} index={index} rolling={rolling} value={value} />
        ))}
      </div>
      <p className="relative mt-1 text-[10px] font-black uppercase">{primaryLabel}</p>
      <p className="text-[9px] font-semibold uppercase text-[#456038]">{movementLabel}</p>
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

  return (
    <div className="absolute inset-0 z-[55] grid place-items-center bg-[#1f2a1f]/35 p-4" data-card-modal="">
      <article
        aria-label={`${card.deckLabel} card`}
        aria-modal="true"
        className="drawn-card-reveal w-full max-w-sm rounded-md border-2 border-[#1f2a1f] bg-[#fffbea] p-4 text-left text-[#1f2a1f] shadow-[0_22px_60px_rgba(31,42,31,0.38)]"
        data-card-reveal=""
        role="dialog"
      >
        <div className="flex items-start justify-between gap-3 border-b border-[#1f2a1f]/25 pb-3">
          <div>
            <p className="text-[11px] font-black uppercase text-[#456038]">{card.deckLabel}</p>
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
        <p className="mt-3 text-sm font-semibold leading-6">{card.description}</p>
        {card.playerName ? (
          <p className="mt-3 text-xs font-bold uppercase text-[#456038]">{card.playerName}</p>
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
  const facts = propertyFacts(property);
  const boardSpace = propertySpaceById.get(property.id);
  const art = boardSpace ? SPACE_ART_BY_ID[boardSpace.id] : null;

  return (
    <div
      aria-label={`Property detail: ${property.name}`}
      className="pointer-events-none absolute inset-0 z-50 grid place-items-center bg-[#1f2a1f]/20 p-4"
      data-property-hover=""
      id={tooltipId}
      role="tooltip"
    >
      <article className="w-full max-w-md rounded-md border-2 border-[#1f2a1f] bg-white p-4 text-left text-[#1f2a1f] shadow-[0_22px_60px_rgba(31,42,31,0.35)]">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-[10px] font-semibold uppercase text-neutral-500">Property detail</p>
            <h4 className="mt-1 text-base font-semibold text-neutral-950">{property.name}</h4>
            <p className="mt-1 text-xs font-medium text-neutral-600">{propertyGroupName(property)}</p>
          </div>
          {art ? (
            <div
              className="grid size-16 shrink-0 place-items-center rounded border border-neutral-200 bg-neutral-50 p-1"
              data-property-art=""
            >
              <SpaceMotif art={art} className="size-14" />
            </div>
          ) : (
            <span
              aria-hidden="true"
              className="mt-1 size-5 shrink-0 rounded-sm border border-neutral-300"
              style={{ backgroundColor: groupColorById.get(property.group) ?? "#d4d4d4" }}
            />
          )}
        </div>

        <div className="mt-3 grid gap-1.5 text-xs text-neutral-700">
          <p>Price {money(property.price)}</p>
          <p>Mortgage value {money(property.mortgage_value)}</p>
          <p>Owner {ownerName(game, ownership.owner_id)}</p>
          <p>{ownership.mortgaged ? "Mortgaged" : "Unmortgaged"}</p>
          <p>Houses: {ownership.houses}</p>
          <p>Hotels: {ownership.hotels}</p>
          {facts.map((fact) => (
            <p key={fact}>{fact}</p>
          ))}
          <p>{hotelConversionText(property, ownership)}</p>
        </div>
      </article>
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
    <div className="flex min-h-4 flex-wrap items-center justify-center gap-0.5" aria-label={`Tokens on ${space.name}`}>
      {players.map((player) => {
        const color = getPlayerColor(game, player.seat_order);
        const isMovingToken = motion?.status === "moving" && motion.playerId === player.id;
        const isLandingToken = motion?.status === "settled" && motion.playerId === player.id;
        return (
          <span
            key={player.id}
            aria-label={`${player.name} token at ${space.name}, position ${space.position}`}
            className={`board-token group/token relative inline-flex size-4 items-center justify-center rounded-full border border-white text-[9px] font-black shadow-sm ring-2 ring-[#2f2418]/25 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-[#0f766e] ${
              isMovingToken ? "board-token-moving z-20" : ""
            } ${isLandingToken ? "board-token-landing z-20" : ""}`}
            data-token-landing={isLandingToken ? "true" : undefined}
            data-token-moving={isMovingToken ? "true" : undefined}
            data-player-id={player.id}
            data-player-token=""
            data-space-index={space.position}
            style={{
              backgroundColor: color,
              color: readableTextColor(color),
            }}
            tabIndex={0}
            title={player.name}
          >
            {isMovingToken || isLandingToken ? <span aria-hidden="true" className="board-token-trail" data-token-trail="" /> : null}
            {tokenText(player.name, player.seat_order)}
            <span className="pointer-events-none absolute left-1/2 top-full z-40 mt-1 -translate-x-1/2 whitespace-nowrap rounded bg-[#1f2a1f] px-1.5 py-0.5 text-[9px] font-bold text-white opacity-0 shadow-sm transition-opacity group-hover/token:opacity-100 group-focus/token:opacity-100" data-player-token-label="">
              {player.name}
            </span>
          </span>
        );
      })}
    </div>
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
        <p className="break-words text-[8px] font-black leading-[0.9] text-[#1f2a1f] uppercase" data-space-name="">
          {space.name}
        </p>
        <TokenStack game={game} motion={motion} players={players} space={space} />
        <p className="text-[9px] font-bold leading-none text-[#1f2a1f]" data-space-bottom-label="">
          {money(property.price)}
        </p>
      </div>
    </>
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
  return (
    <div className="flex min-h-0 flex-1 flex-col justify-between gap-0.5 px-1 py-1">
      <p className={`${isCorner ? "text-[10px]" : "text-[8px]"} break-words font-black leading-[0.9] text-[#1f2a1f] uppercase`} data-space-name="">
        {space.name}
      </p>
      <SpaceMotif art={art} className="mx-auto h-[42%] min-h-6 w-full max-w-14 shrink" />
      <TokenStack game={game} motion={motion} players={players} space={space} />
      {bottom ? (
        <p className="text-[8px] font-bold leading-none text-[#1f2a1f]" data-space-bottom-label="">
          {bottom}
        </p>
      ) : (
        <span aria-hidden="true" className="h-2" />
      )}
    </div>
  );
}

type ClassicGameBoardProps = {
  drawnCard?: DrawnCardView | null;
  game: GameMetadata;
  motion?: BoardMotion;
  onDismissDrawnCard?: () => void;
  snapshot?: GameStateResponse;
};

export function ClassicGameBoard({ drawnCard, game, motion, onDismissDrawnCard, snapshot }: ClassicGameBoardProps) {
  const [boardRotation, setBoardRotation] = useState(0);
  const [hoveredPropertyId, setHoveredPropertyId] = useState<string | null>(null);
  const playersByPosition = new Map<number, GamePlayer[]>();
  for (const player of game.players) {
    const position = playerPosition(player, snapshot, motion);
    const players = playersByPosition.get(position) ?? [];
    players.push(player);
    playersByPosition.set(position, players);
  }
  const hoveredProperty = hoveredPropertyId ? (propertyById.get(hoveredPropertyId) ?? null) : null;
  const hoveredSpace = hoveredProperty ? propertySpaceById.get(hoveredProperty.id) : null;
  const hoveredTooltipId = hoveredSpace ? `${hoveredSpace.id}-property-details` : "board-property-details";

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
          <CenterBoardArt />

          {BOARD_SPACES.map((space) => {
            const coordinates = boardCoordinates(space.position);
            const property = propertyForSpace(space);
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
                aria-label={`${space.position}: ${space.name}`}
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
                <OrientedSpaceContent rotation={contentRotation}>
                  {property?.kind === "street" && bandColor ? (
                    <StreetPropertyCell bandColor={bandColor} game={game} motion={motion} players={players} property={property} space={space} />
                  ) : (
                    <OtherSpaceCell bottom={label} game={game} isCorner={isCorner} motion={motion} players={players} space={space} />
                  )}
                </OrientedSpaceContent>
              </div>
            );
          })}
        </div>
        <DiceMotionStatus motion={motion} />
        <DrawnCardModal card={drawnCard} onDismiss={onDismissDrawnCard} />
        <PropertyHoverOverlay game={game} property={hoveredProperty} snapshot={snapshot} tooltipId={hoveredTooltipId} />
      </div>
    </section>
  );
}
