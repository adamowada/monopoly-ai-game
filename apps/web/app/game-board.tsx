import { BOARD_SPACES, PROPERTIES_BY_ID, PROPERTY_GROUPS, type StaticDataBoardSpace } from "@monopoly-ai-game/schemas";
import { UsersRound } from "lucide-react";

import type { GameMetadata, GamePlayer } from "../lib/api/games";
import { DECK_ART, DeckArtPreview, SPACE_ART_BY_ID, SpaceMotif } from "./board-art";

type BoardCoordinates = {
  row: number;
  column: number;
};

type PlayerColorSetting = {
  seat_order: number;
  color: string;
};

const boardGridSize = 11;
const fallbackPlayerColor = "#525866";
const groupColorById = new Map(PROPERTY_GROUPS.map((group) => [group.id, group.color]));

function boardCoordinates(position: number): BoardCoordinates {
  if (position === 0) {
    return { row: 11, column: 11 };
  }
  if (position > 0 && position < 10) {
    return { row: 11, column: 11 - position };
  }
  if (position === 10) {
    return { row: 11, column: 1 };
  }
  if (position > 10 && position < 20) {
    return { row: 21 - position, column: 1 };
  }
  if (position === 20) {
    return { row: 1, column: 1 };
  }
  if (position > 20 && position < 30) {
    return { row: 1, column: position - 19 };
  }
  if (position === 30) {
    return { row: 1, column: 11 };
  }
  return { row: position - 29, column: 11 };
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

function playerPosition(player: GamePlayer): number {
  const rawPosition = player.state.position;
  if (typeof rawPosition !== "number" || !Number.isInteger(rawPosition)) {
    return 0;
  }
  return ((rawPosition % BOARD_SPACES.length) + BOARD_SPACES.length) % BOARD_SPACES.length;
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

function propertyBandColor(space: StaticDataBoardSpace): string | null {
  if (!space.property_id) {
    return null;
  }
  const property = PROPERTIES_BY_ID[space.property_id];
  return property ? (groupColorById.get(property.group) ?? null) : null;
}

function spaceDetailLabel(space: StaticDataBoardSpace): string {
  if (space.property_id) {
    const property = PROPERTIES_BY_ID[space.property_id];
    return property ? `$${property.price}` : "For sale";
  }
  if (space.type === "tax") {
    return typeof space.amount === "number" ? `$${space.amount}` : "Tax";
  }
  switch (space.type) {
    case "go":
      return "Collect $200";
    case "community_chest":
      return "Draw card";
    case "chance":
      return "Draw card";
    case "jail":
      return "Just visiting";
    case "free_parking":
      return "Free stop";
    case "go_to_jail":
      return "Move to jail";
    default:
      return "Space";
  }
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
    <div className="col-start-2 col-end-11 row-start-2 row-end-11 overflow-hidden border-4 border-[#2f2418] bg-[#f3dfb8] p-3 text-[#2f2418] shadow-inner">
      <div className="relative flex h-full min-h-0 flex-col justify-between overflow-hidden rounded-sm border border-[#a06b2d]/45 bg-[#f8edcf] p-3">
        <svg aria-hidden="true" className="absolute inset-0 h-full w-full opacity-45" viewBox="0 0 720 720" preserveAspectRatio="none">
          <defs>
            <pattern id="board-paper-grid" width="36" height="36" patternUnits="userSpaceOnUse">
              <path d="M36 0H0V36" fill="none" stroke="#c99a55" strokeOpacity="0.16" strokeWidth="2" />
            </pattern>
          </defs>
          <rect width="720" height="720" fill="url(#board-paper-grid)" />
          <path d="M58 58 H662 V662 H58 Z" fill="none" stroke="#8a5b24" strokeWidth="6" />
          <path d="M94 94 H626 V626 H94 Z" fill="none" stroke="#8a5b24" strokeWidth="2" strokeDasharray="12 12" />
        </svg>

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

type ClassicGameBoardProps = {
  game: GameMetadata;
};

export function ClassicGameBoard({ game }: ClassicGameBoardProps) {
  const playersByPosition = new Map<number, GamePlayer[]>();
  for (const player of game.players) {
    const position = playerPosition(player);
    const players = playersByPosition.get(position) ?? [];
    players.push(player);
    playersByPosition.set(position, players);
  }

  return (
    <section
      aria-label="Classic Monopoly-style board"
      className="rounded-md border border-[#2f2418]/35 bg-[#7a4b2a] p-3 shadow-[0_18px_40px_rgba(47,36,24,0.24)]"
    >
      <div
        className="relative grid aspect-square w-full overflow-hidden rounded border-4 border-[#2f2418] bg-[#f3dfb8]"
        style={{
          gridTemplateColumns: `repeat(${boardGridSize}, minmax(0, 1fr))`,
          gridTemplateRows: `repeat(${boardGridSize}, minmax(0, 1fr))`,
        }}
      >
        <CenterBoardArt />

        {BOARD_SPACES.map((space) => {
          const coordinates = boardCoordinates(space.position);
          const bandColor = propertyBandColor(space);
          const players = playersByPosition.get(space.position) ?? [];
          const isCorner = space.position % 10 === 0;
          const art = SPACE_ART_BY_ID[space.id];
          const [, paperColor] = art.palette;
          const detailLabel = spaceDetailLabel(space);

          return (
            <div
              key={space.id}
              aria-label={`${space.position}: ${space.name}`}
              className={`relative flex min-h-0 min-w-0 flex-col overflow-hidden border border-[#2f2418]/35 ${
                isCorner ? "p-1.5" : "p-1"
              }`}
              data-board-space=""
              data-space-index={space.position}
              style={{
                gridColumn: coordinates.column,
                gridRow: coordinates.row,
                backgroundColor: paperColor,
              }}
            >
              {bandColor ? (
                <span
                  aria-hidden="true"
                  className="mb-0.5 h-1.5 shrink-0 rounded-sm shadow-[inset_0_-1px_0_rgba(47,36,24,0.25)]"
                  style={{ backgroundColor: bandColor }}
                />
              ) : null}

              <div className="flex min-h-0 flex-1 flex-col justify-between gap-0.5">
                <div>
                  <div className="flex items-center justify-between gap-1">
                    <span className="text-[8px] font-black text-[#6c5130]">{space.position}</span>
                    <span className="truncate text-[8px] font-black uppercase text-[#6c5130]">{detailLabel}</span>
                  </div>
                  <p className={`${isCorner ? "text-[10px]" : "text-[9px]"} mt-0.5 font-black leading-[0.95] text-[#2f2418]`}>
                    {space.name}
                  </p>
                  <SpaceMotif art={art} className="mx-auto mt-0.5 h-[32%] min-h-4 w-full max-w-12 shrink" />
                </div>

                <div className="grid grid-cols-2 gap-0.5" aria-label={`Tokens on ${space.name}`}>
                  {players.map((player) => {
                    const color = getPlayerColor(game, player.seat_order);
                    return (
                      <span
                        key={player.id}
                        aria-label={`${player.name} token at ${space.name}, position ${space.position}`}
                        className="inline-flex aspect-square min-h-4 min-w-4 items-center justify-center rounded-full border border-white text-[9px] font-black shadow-sm ring-2 ring-[#2f2418]/25"
                        data-player-token=""
                        data-player-id={player.id}
                        data-space-index={space.position}
                        style={{
                          backgroundColor: color,
                          color: readableTextColor(color),
                        }}
                        title={`${player.name}: ${space.name}`}
                      >
                        {tokenText(player.name, player.seat_order)}
                      </span>
                    );
                  })}
                </div>
              </div>
            </div>
          );
        })}
      </div>

      <div className="mt-3 flex items-center gap-2 text-xs text-neutral-600">
        <UsersRound aria-hidden="true" className="size-4 text-[#7a4b2a]" />
        <span>Player tokens are grouped on their current board spaces and labeled for assistive technology.</span>
      </div>
    </section>
  );
}
