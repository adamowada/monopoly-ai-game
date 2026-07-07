export type PlayerIconOption = {
  icon: string;
  label: string;
};

export type PlayerIconSetting = {
  seat_order: number;
  icon: string;
};

export const PLAYER_ICON_OPTIONS = [
  { icon: "🚗", label: "Car" },
  { icon: "🎩", label: "Top hat" },
  { icon: "🚂", label: "Train" },
  { icon: "🚢", label: "Ship" },
  { icon: "💎", label: "Gem" },
  { icon: "🔑", label: "Key" },
] as const satisfies readonly PlayerIconOption[];

const playerIconValues = new Set<string>(PLAYER_ICON_OPTIONS.map((option) => option.icon));

function isPlayerIconSetting(value: unknown): value is PlayerIconSetting {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return false;
  }
  const candidate = value as Partial<PlayerIconSetting>;
  return typeof candidate.seat_order === "number" && typeof candidate.icon === "string";
}

export function defaultPlayerIcon(seatOrder: number): string {
  return PLAYER_ICON_OPTIONS[seatOrder % PLAYER_ICON_OPTIONS.length]?.icon ?? PLAYER_ICON_OPTIONS[0].icon;
}

export function isPlayerIconOption(icon: string): boolean {
  return playerIconValues.has(icon);
}

export function playerIconLabel(icon: string): string {
  return PLAYER_ICON_OPTIONS.find((option) => option.icon === icon)?.label ?? "Token";
}

export function getPlayerIcon(game: { settings: Record<string, unknown> }, seatOrder: number): string {
  const icons = game.settings.player_icons;
  if (!Array.isArray(icons)) {
    return defaultPlayerIcon(seatOrder);
  }
  const icon = icons.find((entry): entry is PlayerIconSetting => isPlayerIconSetting(entry) && entry.seat_order === seatOrder)
    ?.icon;
  return icon && isPlayerIconOption(icon) ? icon : defaultPlayerIcon(seatOrder);
}
