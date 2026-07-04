import { BOARD_SPACES, PROPERTIES_BY_ID, PROPERTY_GROUPS, type StaticDataBoardSpace } from "@monopoly-ai-game/schemas";
import { BadgeDollarSign, Building2, Car, Landmark, LockKeyhole, Train, UsersRound } from "lucide-react";

import type { GameMetadata, GamePlayer } from "../lib/api/games";

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

function spaceKindLabel(space: StaticDataBoardSpace): string {
  switch (space.type) {
    case "go":
      return "Start";
    case "community_chest":
      return "Community";
    case "tax":
      return "Tax";
    case "railroad":
      return "Railroad";
    case "chance":
      return "Chance";
    case "jail":
      return "Corner";
    case "utility":
      return "Utility";
    case "free_parking":
      return "Corner";
    case "go_to_jail":
      return "Corner";
    case "street":
      return "Property";
    default:
      return "Space";
  }
}

function SpaceIcon({ space }: { space: StaticDataBoardSpace }) {
  if (space.type === "railroad") {
    return <Train aria-hidden="true" className="size-3 text-neutral-700" />;
  }
  if (space.type === "utility") {
    return <Building2 aria-hidden="true" className="size-3 text-neutral-700" />;
  }
  if (space.type === "tax") {
    return <BadgeDollarSign aria-hidden="true" className="size-3 text-neutral-700" />;
  }
  if (space.type === "go_to_jail" || space.type === "jail") {
    return <LockKeyhole aria-hidden="true" className="size-3 text-neutral-700" />;
  }
  if (space.type === "free_parking") {
    return <Car aria-hidden="true" className="size-3 text-neutral-700" />;
  }
  if (space.type === "go") {
    return <Landmark aria-hidden="true" className="size-3 text-teal-700" />;
  }
  return null;
}

function CardDeckPreview({ title, tone }: { title: string; tone: "chance" | "community" }) {
  const colorClass = tone === "chance" ? "border-violet-200 bg-violet-50 text-violet-800" : "border-teal-200 bg-teal-50 text-teal-800";
  return (
    <div className={`rounded-md border px-3 py-2 ${colorClass}`}>
      <div className="text-[10px] font-semibold uppercase">{title}</div>
      <div className="mt-2 h-12 rounded border border-current/20 bg-white/65" aria-hidden="true">
        <svg viewBox="0 0 120 64" role="img" aria-label={`${title} card preview`} className="h-full w-full">
          <rect x="10" y="10" width="100" height="44" rx="6" fill="none" stroke="currentColor" strokeWidth="3" />
          <path d="M24 40 C38 18 52 18 66 40 S94 62 104 24" fill="none" stroke="currentColor" strokeWidth="4" />
        </svg>
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
      className="rounded-md border border-[var(--color-border)] bg-white p-3 shadow-sm"
    >
      <div
        className="relative grid aspect-square w-full overflow-hidden rounded border border-neutral-300 bg-[#f6f7f5]"
        style={{
          gridTemplateColumns: `repeat(${boardGridSize}, minmax(0, 1fr))`,
          gridTemplateRows: `repeat(${boardGridSize}, minmax(0, 1fr))`,
        }}
      >
        <div className="col-start-2 col-end-11 row-start-2 row-end-11 flex flex-col justify-between border border-neutral-200 bg-[#eef2f1] p-4 text-neutral-950">
          <div>
            <p className="text-xs font-semibold uppercase text-teal-700">Local research table</p>
            <h2 className="mt-1 text-lg font-semibold">Classic Monopoly-style board</h2>
            <p className="mt-2 max-w-md text-xs leading-5 text-neutral-600">
              Original vector board surface. Token locations are rendered from backend player state.
            </p>
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <CardDeckPreview title="Chance" tone="chance" />
            <CardDeckPreview title="Community Chest" tone="community" />
          </div>

          <div className="grid gap-2 text-xs text-neutral-700 sm:grid-cols-3">
            <div className="rounded border border-neutral-200 bg-white px-3 py-2">
              <span className="block font-semibold text-neutral-950">40 spaces</span>
              <span>Stable 0-39 indexes</span>
            </div>
            <div className="rounded border border-neutral-200 bg-white px-3 py-2">
              <span className="block font-semibold text-neutral-950">{game.players.length} tokens</span>
              <span>Live positions</span>
            </div>
            <div className="rounded border border-neutral-200 bg-white px-3 py-2">
              <span className="block font-semibold text-neutral-950">Vector-only</span>
              <span>No board scans</span>
            </div>
          </div>
        </div>

        {BOARD_SPACES.map((space) => {
          const coordinates = boardCoordinates(space.position);
          const bandColor = propertyBandColor(space);
          const players = playersByPosition.get(space.position) ?? [];
          const isCorner = space.position % 10 === 0;

          return (
            <div
              key={space.id}
              aria-label={`${space.position}: ${space.name}`}
              className={`relative flex min-h-0 min-w-0 flex-col overflow-hidden border border-neutral-300 bg-white ${
                isCorner ? "p-1.5" : "p-1"
              }`}
              data-board-space=""
              data-space-index={space.position}
              style={{
                gridColumn: coordinates.column,
                gridRow: coordinates.row,
              }}
            >
              {bandColor ? (
                <span
                  aria-hidden="true"
                  className="mb-1 h-1.5 shrink-0 rounded-sm"
                  style={{ backgroundColor: bandColor }}
                />
              ) : null}

              <div className="flex min-h-0 flex-1 flex-col justify-between gap-1">
                <div>
                  <div className="flex items-center justify-between gap-1">
                    <span className="text-[9px] font-semibold text-neutral-500">{space.position}</span>
                    <SpaceIcon space={space} />
                  </div>
                  <p className={`${isCorner ? "text-[10px]" : "text-[9px]"} mt-0.5 font-semibold leading-tight text-neutral-950`}>
                    {space.name}
                  </p>
                  <p className="mt-0.5 text-[8px] font-medium uppercase text-neutral-500">{spaceKindLabel(space)}</p>
                </div>

                <div className="grid grid-cols-2 gap-0.5" aria-label={`Tokens on ${space.name}`}>
                  {players.map((player) => {
                    const color = getPlayerColor(game, player.seat_order);
                    return (
                      <span
                        key={player.id}
                        aria-label={`${player.name} token at ${space.name}, position ${space.position}`}
                        className="inline-flex aspect-square min-h-4 min-w-4 items-center justify-center rounded-full border border-white text-[9px] font-bold shadow-sm ring-1 ring-neutral-900/10"
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
        <UsersRound aria-hidden="true" className="size-4 text-teal-700" />
        <span>Player tokens are grouped on their current board spaces and labeled for assistive technology.</span>
      </div>
    </section>
  );
}
