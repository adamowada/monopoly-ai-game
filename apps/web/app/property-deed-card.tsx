"use client";

import { Building2, CircleDollarSign, Home, Landmark, TrainFront, Zap } from "lucide-react";
import { PROPERTIES_BY_ID, PROPERTY_GROUPS, type StaticDataProperty } from "@monopoly-ai-game/schemas";

import type { GameMetadata } from "../lib/api/games";
import { cn } from "../lib/ui";
import { getPlayerIcon } from "./player-icons";

export type PropertyDeedOwnership = {
  property_id: string;
  owner_id: string | null;
  mortgaged: boolean;
  houses: number;
  hotels: number;
  hotel: boolean;
};

type PropertyDeedCardProps = {
  className?: string;
  game: GameMetadata;
  ownership: PropertyDeedOwnership;
  property: StaticDataProperty;
  variant?: "full" | "compact";
};

type PropertyReferenceProps = {
  className?: string;
  game: GameMetadata;
  ownerId?: string | null;
  propertyId: string | null | undefined;
};

type PlayerColorSetting = {
  seat_order: number;
  color: string;
};

const groupById = new Map(PROPERTY_GROUPS.map((group) => [group.id, group]));
const fallbackPlayerColor = "#525866";
const propertyIdPattern = /property_[a-z0-9_]+/gi;
const propertiesById = PROPERTIES_BY_ID as Readonly<Record<string, StaticDataProperty | undefined>>;

function money(value: number): string {
  return `$${value.toLocaleString("en-US")}`;
}

function isPlayerColorSetting(value: unknown): value is PlayerColorSetting {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return false;
  }
  const candidate = value as Partial<PlayerColorSetting>;
  return typeof candidate.seat_order === "number" && typeof candidate.color === "string";
}

function getPlayerColor(game: GameMetadata, seatOrder: number): string {
  const colors = game.settings.player_colors;
  if (!Array.isArray(colors)) {
    return fallbackPlayerColor;
  }
  return colors.find((entry): entry is PlayerColorSetting => isPlayerColorSetting(entry) && entry.seat_order === seatOrder)
    ?.color ?? fallbackPlayerColor;
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

function ownerForProperty(game: GameMetadata, ownerId: string | null) {
  if (!ownerId) {
    return null;
  }
  return game.players.find((player) => player.id === ownerId) ?? null;
}

function propertyKindIcon(property: StaticDataProperty) {
  if (property.kind === "railroad") {
    return TrainFront;
  }
  if (property.kind === "utility") {
    return Zap;
  }
  return Landmark;
}

function rentRows(property: StaticDataProperty): Array<{ label: string; value: string; developmentIndex?: number; legacyText?: string }> {
  if (property.kind === "street") {
    return [
      { label: "Rent", value: money(property.rents[0]), developmentIndex: 0, legacyText: `Rent ${money(property.rents[0])} base` },
      { label: "With 1 house", value: money(property.rents[1]), developmentIndex: 1 },
      { label: "With 2 houses", value: money(property.rents[2]), developmentIndex: 2 },
      { label: "With 3 houses", value: money(property.rents[3]), developmentIndex: 3 },
      { label: "With 4 houses", value: money(property.rents[4]), developmentIndex: 4 },
      { label: "With hotel", value: money(property.rents[5]), developmentIndex: 5 },
    ];
  }
  if (property.kind === "railroad") {
    return property.rent_by_owned_count.map((rent, index) => ({
      label: index === 0 ? "Rent" : `${index + 1} railroads`,
      legacyText: index === 0 ? `Rent ${money(rent)}` : undefined,
      value: money(rent),
    }));
  }
  return [
    { label: "1 utility", value: `${property.rent_multipliers[0]}x dice` },
    { label: "2 utilities", value: `${property.rent_multipliers[1]}x dice` },
  ];
}

function DevelopmentStrip({
  ownership,
  property,
}: Readonly<{
  ownership: PropertyDeedOwnership;
  property: StaticDataProperty;
}>) {
  if (property.kind !== "street") {
    return null;
  }
  const hasHotel = ownership.hotel || ownership.hotels > 0;
  const houseCount = Math.max(0, Math.min(4, ownership.houses));
  if (!hasHotel && houseCount === 0) {
    return (
      <div className="mt-2 flex items-center justify-center rounded border border-dashed border-[#2f2418]/25 bg-white/55 px-2 py-1 text-[10px] font-black uppercase text-[#6f604c]">
        Unimproved
      </div>
    );
  }

  return (
    <div
      aria-label={
        hasHotel
          ? `${property.name} has a hotel`
          : `${property.name} has ${houseCount} ${houseCount === 1 ? "house" : "houses"}`
      }
      className="mt-2 flex min-h-8 items-center justify-center gap-1 rounded border border-[#2f2418]/20 bg-white/70 px-2 py-1"
      data-development-strip=""
      role="img"
    >
      {hasHotel ? (
        <span
          className="inline-flex items-center gap-1 rounded-sm border border-[#7f1d1d]/40 bg-[#fee2e2] px-2 py-1 text-[10px] font-black uppercase text-[#7f1d1d]"
          data-property-hotel-icon=""
        >
          <Building2 aria-hidden="true" className="size-3.5" />
          Hotel
        </span>
      ) : (
        Array.from({ length: houseCount }, (_, index) => (
          <span
            key={index}
            aria-hidden="true"
            className="grid size-5 place-items-center rounded-sm border border-[#14532d]/35 bg-[#dcfce7] text-[#166534]"
            data-property-house-icon=""
          >
            <Home aria-hidden="true" className="size-3.5" />
          </span>
        ))
      )}
    </div>
  );
}

export function PropertyDeedCard({
  className,
  game,
  ownership,
  property,
  variant = "full",
}: Readonly<PropertyDeedCardProps>) {
  const group = groupById.get(property.group);
  const bandColor = group?.color ?? "#d4d4d4";
  const owner = ownerForProperty(game, ownership.owner_id);
  const ownerColor = owner ? getPlayerColor(game, owner.seat_order) : "#f8fafc";
  const ownerIcon = owner ? getPlayerIcon(game, owner.seat_order) : null;
  const Icon = propertyKindIcon(property);
  const hasHotel = ownership.hotel || ownership.hotels > 0;
  const activeDevelopmentIndex = property.kind === "street" ? (hasHotel ? 5 : Math.max(0, ownership.houses)) : undefined;
  const rows = rentRows(property);
  const compact = variant === "compact";

  return (
    <article
      aria-label={`Property card: ${property.name}`}
      className={cn(
        "relative overflow-hidden rounded-md border-2 border-[#2f2418] bg-[#fffbea] text-[#2f2418] shadow-[0_5px_0_rgba(47,36,24,0.16)]",
        compact ? "p-2" : "p-3",
        className,
      )}
      data-property-deed-card=""
      data-property-kind={property.kind}
      data-property-mortgaged={ownership.mortgaged ? "true" : undefined}
    >
      {ownership.mortgaged ? (
        <div
          aria-label={`${property.name} is mortgaged`}
          className="absolute right-[-2.8rem] top-5 z-20 rotate-45 bg-[#7f1d1d] px-12 py-1 text-[10px] font-black uppercase text-white shadow-sm"
          data-mortgage-banner=""
          role="status"
        >
          Mortgaged
        </div>
      ) : null}

      <div
        className={cn(
          "rounded border-2 border-[#2f2418]/80 px-2 text-center",
          compact ? "py-1.5" : "py-2",
        )}
        data-property-deed-band=""
        style={{ backgroundColor: bandColor, color: readableTextColor(bandColor) }}
      >
        <p className="text-[10px] font-black uppercase leading-none">{group?.name ?? property.group}</p>
        <h3 className={cn("mt-1 break-words font-black uppercase leading-[0.95]", compact ? "text-xs" : "text-lg")}>
          {property.name}
        </h3>
      </div>

      <div className={cn("flex items-start justify-between gap-3", compact ? "mt-2" : "mt-3")}>
        <div className="min-w-0">
          <span className="sr-only">Price {money(property.price)}</span>
          <p className="text-[10px] font-black uppercase text-[#6f604c]">Price</p>
          <p className={cn("font-black text-[#173c45]", compact ? "text-sm" : "text-xl")}>{money(property.price)}</p>
        </div>
        <div className="flex items-center gap-1.5">
          {owner ? (
            <span
              aria-label={`${owner.name} owns ${property.name}`}
              className="grid size-9 place-items-center rounded-sm border-2 border-[#2f2418] text-base font-black shadow-[0_2px_0_rgba(47,36,24,0.22)]"
              data-property-owner-token=""
              data-token-icon={ownerIcon ?? undefined}
              role="img"
              style={{ backgroundColor: ownerColor, color: readableTextColor(ownerColor) }}
              title={`${owner.name} owns ${property.name}`}
            >
              <span aria-hidden="true" className="leading-none">
                {ownerIcon}
              </span>
            </span>
          ) : (
            <span className="rounded border border-[#2f2418]/20 bg-white/70 px-2 py-1 text-[10px] font-black uppercase text-[#6f604c]">
              Bank
            </span>
          )}
          <span className="grid size-8 place-items-center rounded-sm border border-[#2f2418]/25 bg-white/65 text-[#173c45]">
            <Icon aria-hidden="true" className="size-4" />
          </span>
        </div>
      </div>

      <DevelopmentStrip ownership={ownership} property={property} />

      <div className={cn("flex flex-wrap gap-1.5", compact ? "mt-2" : "mt-3")}>
        <span className="rounded-sm border border-[#2f2418]/15 bg-white/60 px-2 py-1 text-[10px] font-black uppercase text-[#6f604c]">
          Owner {owner?.name ?? "Bank/unowned"}
        </span>
        <span className="rounded-sm border border-[#2f2418]/15 bg-white/60 px-2 py-1 text-[10px] font-black uppercase text-[#6f604c]">
          {ownership.mortgaged ? "Mortgaged" : "Unmortgaged"}
        </span>
        {property.kind === "street" ? (
          <>
            <span className="rounded-sm border border-[#2f2418]/15 bg-white/60 px-2 py-1 text-[10px] font-black uppercase text-[#6f604c]">
              Houses: {ownership.houses}
            </span>
            <span className="rounded-sm border border-[#2f2418]/15 bg-white/60 px-2 py-1 text-[10px] font-black uppercase text-[#6f604c]">
              Hotels: {ownership.hotels}
            </span>
          </>
        ) : null}
      </div>

      <dl className={cn("grid gap-1", compact ? "mt-2 text-[10px]" : "mt-3 text-xs")}>
        {rows.map((row) => {
          const active =
            typeof row.developmentIndex === "number" && typeof activeDevelopmentIndex === "number"
              ? row.developmentIndex === activeDevelopmentIndex
              : false;
          return (
            <div
              key={row.label}
              className={cn(
                "grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2 rounded-sm px-2 py-1",
                active ? "border border-[#14532d]/35 bg-[#dcfce7] font-black text-[#14532d]" : "bg-white/55 text-[#2f2418]",
              )}
              data-property-rent-row=""
              data-rent-active={active ? "true" : undefined}
            >
              {row.legacyText ? <span className="sr-only">{row.legacyText}</span> : null}
              <dt className="min-w-0 truncate font-semibold">{row.label}</dt>
              <dd className="font-black">{row.value}</dd>
            </div>
          );
        })}
      </dl>

      {!compact ? (
        <div className="mt-3 grid grid-cols-2 gap-2 text-[10px] font-black uppercase text-[#6f604c]">
          <span className="rounded border border-[#2f2418]/15 bg-white/60 px-2 py-1">
            Mortgage value {money(property.mortgage_value)}
          </span>
          <span className="inline-flex items-center gap-1 rounded border border-[#2f2418]/15 bg-white/60 px-2 py-1">
            <CircleDollarSign aria-hidden="true" className="size-3" />
            {ownership.mortgaged ? "Inactive rent" : "Rent active"}
          </span>
        </div>
      ) : null}
    </article>
  );
}

export function propertyIdsFromText(value: string): string[] {
  const ids = new Set<string>();
  for (const match of value.matchAll(propertyIdPattern)) {
    const propertyId = match[0];
    if (propertiesById[propertyId]) {
      ids.add(propertyId);
    }
  }
  return [...ids];
}

export function PropertyReference({
  className,
  game,
  ownerId = null,
  propertyId,
}: Readonly<PropertyReferenceProps>) {
  const property = propertyId ? propertiesById[propertyId] : undefined;
  if (!property) {
    return propertyId ? (
      <span className={cn("rounded-sm bg-neutral-100 px-1.5 py-0.5 font-medium text-neutral-700", className)}>
        {propertyId}
      </span>
    ) : null;
  }

  return (
    <span className={cn("group/property-ref relative inline-flex align-baseline", className)} data-property-reference="">
      <button
        aria-label={`Show property card for ${property.name}`}
        className="inline-flex items-center rounded-sm border border-[#2f2418]/20 bg-[#fffbea] px-1.5 py-0.5 text-[11px] font-black text-[#173c45] shadow-sm transition hover:bg-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#0f766e]"
        type="button"
      >
        {property.name}
      </button>
      <span
        className="pointer-events-none absolute left-0 top-full z-50 mt-2 hidden w-72 max-w-[80vw] group-hover/property-ref:block group-focus-within/property-ref:block"
        data-property-reference-card=""
      >
        <PropertyDeedCard
          game={game}
          ownership={{
            property_id: property.id,
            owner_id: ownerId ?? null,
            mortgaged: false,
            houses: 0,
            hotels: 0,
            hotel: false,
          }}
          property={property}
          variant="compact"
        />
      </span>
    </span>
  );
}
