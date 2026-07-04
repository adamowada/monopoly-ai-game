"use client";

import {
  Banknote,
  Building2,
  CircleDollarSign,
  Hammer,
  Home,
  Hotel,
  Loader2,
  LockKeyhole,
  Undo2,
} from "lucide-react";
import { useMemo } from "react";
import {
  PROPERTIES,
  PROPERTIES_BY_ID,
  PROPERTY_GROUPS,
  type StaticDataProperty,
  type StaticDataPropertyGroup,
} from "@monopoly-ai-game/schemas";

import { Button } from "../components/ui/button";
import type { GameStateResponse, LegalAction } from "../lib/api/gameplay";
import type { GameMetadata } from "../lib/api/games";
import { cn } from "../lib/ui";

const MANAGEMENT_ACTION_TYPES = [
  "BUY_HOUSE",
  "SELL_HOUSE",
  "MORTGAGE_PROPERTY",
  "UNMORTGAGE_PROPERTY",
] as const;

type ManagementActionType = (typeof MANAGEMENT_ACTION_TYPES)[number];

type PropertyOwnershipView = {
  property_id: string;
  owner_id: string | null;
  mortgaged: boolean;
  houses: number;
  hotels: number;
  hotel: boolean;
};

type BankInventoryView = {
  houses: number | null;
  hotels: number | null;
};

type OwnerGroup = {
  key: string;
  label: string;
  properties: StaticDataProperty[];
};

export type PropertyManagementPanelProps = {
  game: GameMetadata;
  snapshot: GameStateResponse | undefined;
  legalActions: LegalAction[];
  controlsDisabled: boolean;
  pendingActionType: string | null;
  onSubmit: (action: LegalAction) => void;
};

const groupById = new Map(PROPERTY_GROUPS.map((group) => [group.id, group]));

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function readString(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function readNumber(value: unknown, fallback: number | null): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function readInteger(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isInteger(value) ? value : fallback;
}

function formatMoney(value: number): string {
  return `$${value.toLocaleString("en-US")}`;
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
  const property_ownership = snapshot?.state.property_ownership;
  const ownerships: Map<string, PropertyOwnershipView> = new Map(
    PROPERTIES.map((property) => [property.id, defaultOwnership(property.id)]),
  );

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

function bankInventory(snapshot: GameStateResponse | undefined): BankInventoryView {
  const bank_inventory = snapshot?.state.bank_inventory;
  if (!isRecord(bank_inventory)) {
    return { houses: null, hotels: null };
  }
  return {
    houses: readNumber(bank_inventory.houses, null),
    hotels: readNumber(bank_inventory.hotels, null),
  };
}

function legalActionFor(legalActions: LegalAction[], type: ManagementActionType, propertyId: string): LegalAction | null {
  return (
    legalActions.find((action) => {
      if (action.type !== type) {
        return false;
      }
      const propertyIdValue = isRecord(action.payload) ? action.payload.property_id : null;
      return propertyIdValue === propertyId;
    }) ?? null
  );
}

function isManagementAction(action: LegalAction): boolean {
  return MANAGEMENT_ACTION_TYPES.includes(action.type as ManagementActionType);
}

function propertyFacts(property: StaticDataProperty): string[] {
  if (property.kind === "street") {
    return [
      `Rent ${formatMoney(property.rents[0])} base`,
      `1 house ${formatMoney(property.rents[1])}`,
      `Hotel rent ${formatMoney(property.rents[5])}`,
      `House cost ${formatMoney(property.house_cost)}`,
    ];
  }
  if (property.kind === "railroad") {
    return [
      `Rent ${formatMoney(property.rent_by_owned_count[0])}-${formatMoney(
        property.rent_by_owned_count[property.rent_by_owned_count.length - 1],
      )} by railroads owned`,
    ];
  }
  return [`Rent multiplier ${property.rent_multipliers[0]}x/${property.rent_multipliers[1]}x dice`];
}

function buildOwnerGroups(game: GameMetadata, ownerships: Map<string, PropertyOwnershipView>): OwnerGroup[] {
  const groups: OwnerGroup[] = [
    {
      key: "bank",
      label: "Bank/unowned properties",
      properties: [],
    },
    ...game.players.map((player) => ({
      key: player.id,
      label: player.name,
      properties: [] as StaticDataProperty[],
    })),
  ];
  const groupByOwner = new Map(groups.map((group) => [group.key, group]));

  for (const property of PROPERTIES) {
    const ownership = ownerships.get(property.id) ?? defaultOwnership(property.id);
    const groupKey = ownership.owner_id && groupByOwner.has(ownership.owner_id) ? ownership.owner_id : "bank";
    groupByOwner.get(groupKey)?.properties.push(property);
  }
  return groups;
}

function improvementText(ownership: PropertyOwnershipView): string {
  if (ownership.hotel || ownership.hotels > 0) {
    return "Hotel";
  }
  if (ownership.houses > 0) {
    return `Houses: ${ownership.houses}`;
  }
  return "Unimproved";
}

function monopolyGroupStatus(
  game: GameMetadata,
  group: StaticDataPropertyGroup,
  ownerships: Map<string, PropertyOwnershipView>,
): {
  completion: string;
  mortgage: string;
  improvements: string;
} {
  const groupOwnerships = group.property_ids.map((propertyId) => ownerships.get(propertyId) ?? defaultOwnership(propertyId));
  const ownerIds = new Set(groupOwnerships.map((ownership) => ownership.owner_id).filter((ownerId): ownerId is string => Boolean(ownerId)));
  const complete = ownerIds.size === 1 && groupOwnerships.every((ownership) => ownership.owner_id === [...ownerIds][0]);
  const ownerId = complete ? [...ownerIds][0] : null;
  const anyMortgaged = groupOwnerships.some((ownership) => ownership.mortgaged);
  const anyImproved = groupOwnerships.some((ownership) => ownership.houses > 0 || ownership.hotel || ownership.hotels > 0);

  return {
    completion: complete ? `Complete for ${ownerName(game, ownerId)}` : "Incomplete",
    mortgage: anyMortgaged ? "Mortgaged" : "Unmortgaged",
    improvements: anyImproved ? "Improved" : "Unimproved",
  };
}

function hotelConversionText(
  property: StaticDataProperty,
  ownership: PropertyOwnershipView,
  buyAction: LegalAction | null,
  sellAction: LegalAction | null,
): string {
  if (property.kind !== "street") {
    return "Hotel conversion: Not available for railroads or utilities.";
  }
  if (ownership.hotel || ownership.hotels > 0) {
    return sellAction
      ? "Hotel conversion: hotel-to-houses ready. Sell house converts one hotel to four houses."
      : "Hotel conversion: hotel-to-houses unavailable because SELL_HOUSE was not returned.";
  }
  if (ownership.houses === 4) {
    return buyAction
      ? "Hotel conversion: four-house-to-hotel ready. Build house converts four houses to one hotel."
      : "Hotel conversion: four-house-to-hotel unavailable because BUY_HOUSE was not returned.";
  }
  return "Hotel conversion: Not at conversion threshold.";
}

function ManagementActionButton({
  action,
  label,
  disabled,
  pendingActionType,
  onSubmit,
}: Readonly<{
  action: LegalAction;
  label: "Build house" | "Sell house" | "Mortgage" | "Unmortgage";
  disabled: boolean;
  pendingActionType: string | null;
  onSubmit: (action: LegalAction) => void;
}>) {
  const isPending = pendingActionType === action.type;
  const Icon = label === "Mortgage" ? LockKeyhole : label === "Unmortgage" ? Undo2 : label === "Sell house" ? Home : Hammer;
  return (
    <Button
      onClick={() => onSubmit(action)}
      disabled={disabled}
      className={cn(
        "min-h-9 justify-start px-2.5 py-1.5 text-xs",
        label === "Mortgage" && "bg-neutral-800 hover:bg-neutral-900",
        label === "Sell house" && "bg-amber-700 hover:bg-amber-800",
      )}
    >
      {isPending ? (
        <Loader2 aria-hidden="true" className="size-3.5 animate-spin" />
      ) : (
        <Icon aria-hidden="true" className="size-3.5" />
      )}
      {isPending ? "Submitting..." : label}
    </Button>
  );
}

function OwnerPropertyList({
  groups,
  ownerships,
}: Readonly<{
  groups: OwnerGroup[];
  ownerships: Map<string, PropertyOwnershipView>;
}>) {
  return (
    <section aria-label="Property list by owner" className="rounded-md border border-neutral-200 bg-neutral-50 p-3">
      <div className="flex items-center gap-2">
        <Banknote aria-hidden="true" className="size-4 text-teal-700" />
        <h3 className="text-sm font-semibold text-neutral-950">Property list by owner</h3>
      </div>
      <div className="mt-3 grid gap-3 md:grid-cols-2">
        {groups.map((group) => (
          <div key={group.key} role="group" aria-label={group.label} className="min-w-0 rounded border border-neutral-200 bg-white p-3">
            <div className="flex items-center justify-between gap-2">
              <h4 className="text-xs font-semibold uppercase text-neutral-500">{group.label}</h4>
              <span className="text-xs font-medium text-neutral-500">{group.properties.length}</span>
            </div>
            {group.properties.length === 0 ? (
              <p className="mt-2 text-sm text-neutral-500">No properties.</p>
            ) : (
              <ul className="mt-2 space-y-2 text-sm">
                {group.properties.map((property) => {
                  const ownership = ownerships.get(property.id) ?? defaultOwnership(property.id);
                  return (
                    <li key={property.id} className="rounded border border-neutral-100 bg-neutral-50 px-2.5 py-2">
                      <div className="font-medium text-neutral-950">{property.name}</div>
                      <div className="mt-1 flex flex-wrap gap-1.5 text-[11px] font-medium text-neutral-600">
                        <span>{propertyGroupName(property)}</span>
                        <span>{ownership.mortgaged ? "Mortgaged" : "Unmortgaged"}</span>
                        <span>{improvementText(ownership)}</span>
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}

function BankInventoryPanel({ inventory }: Readonly<{ inventory: BankInventoryView }>) {
  return (
    <section aria-label="Bank inventory" className="rounded-md border border-neutral-200 bg-white p-3">
      <div className="flex items-center gap-2">
        <Building2 aria-hidden="true" className="size-4 text-teal-700" />
        <h3 className="text-sm font-semibold text-neutral-950">Bank inventory</h3>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-3 text-sm">
        <div className="rounded border border-neutral-200 bg-neutral-50 px-3 py-2">
          <p className="text-xs font-medium uppercase text-neutral-500">Houses remaining</p>
          <p className="mt-1 font-semibold text-neutral-950">Houses remaining {inventory.houses ?? "Unknown"}</p>
        </div>
        <div className="rounded border border-neutral-200 bg-neutral-50 px-3 py-2">
          <p className="text-xs font-medium uppercase text-neutral-500">Hotels remaining</p>
          <p className="mt-1 font-semibold text-neutral-950">Hotels remaining {inventory.hotels ?? "Unknown"}</p>
        </div>
      </div>
    </section>
  );
}

function MonopolyGroupsPanel({
  game,
  ownerships,
}: Readonly<{
  game: GameMetadata;
  ownerships: Map<string, PropertyOwnershipView>;
}>) {
  return (
    <section aria-label="Monopoly groups" className="rounded-md border border-neutral-200 bg-white p-3">
      <div className="flex items-center gap-2">
        <CircleDollarSign aria-hidden="true" className="size-4 text-teal-700" />
        <h3 className="text-sm font-semibold text-neutral-950">Monopoly groups</h3>
      </div>
      <ul className="mt-3 grid gap-2 text-sm md:grid-cols-2">
        {PROPERTY_GROUPS.map((group) => {
          const status = monopolyGroupStatus(game, group, ownerships);
          return (
            <li key={group.id} className="rounded border border-neutral-200 bg-neutral-50 px-3 py-2">
              <div className="flex items-center gap-2">
                <span
                  aria-hidden="true"
                  className="size-3 rounded-sm border border-neutral-300"
                  style={{ backgroundColor: group.color }}
                />
                <span className="font-semibold text-neutral-950">{group.name}</span>
              </div>
              <div className="mt-1 flex flex-wrap gap-1.5 text-[11px] font-medium text-neutral-600">
                <span>{status.completion}</span>
                <span>{status.mortgage}</span>
                <span>{status.improvements}</span>
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function PropertyDetailCard({
  game,
  property,
  ownership,
  actions,
  controlsDisabled,
  pendingActionType,
  onSubmit,
}: Readonly<{
  game: GameMetadata;
  property: StaticDataProperty;
  ownership: PropertyOwnershipView;
  actions: Record<ManagementActionType, LegalAction | null>;
  controlsDisabled: boolean;
  pendingActionType: string | null;
  onSubmit: (action: LegalAction) => void;
}>) {
  const facts = propertyFacts(property);
  const buyAction = actions.BUY_HOUSE;
  const sellAction = actions.SELL_HOUSE;
  const mortgageAction = actions.MORTGAGE_PROPERTY;
  const unmortgageAction = actions.UNMORTGAGE_PROPERTY;
  const hasAnyAction = Boolean(buyAction ?? sellAction ?? mortgageAction ?? unmortgageAction);

  return (
    <article
      aria-label={`Property detail: ${property.name}`}
      className="rounded-md border border-neutral-200 bg-white p-3"
      role="region"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[10px] font-semibold uppercase text-neutral-500">Property detail</p>
          <h4 className="mt-1 text-sm font-semibold text-neutral-950">{property.name}</h4>
          <p className="mt-1 text-xs font-medium text-neutral-600">{propertyGroupName(property)}</p>
        </div>
        <span
          aria-hidden="true"
          className="mt-1 size-4 shrink-0 rounded-sm border border-neutral-300"
          style={{ backgroundColor: groupById.get(property.group)?.color ?? "#d4d4d4" }}
        />
      </div>

      <div className="mt-3 grid gap-1.5 text-xs text-neutral-700">
        <p>Price {formatMoney(property.price)}</p>
        <p>Mortgage value {formatMoney(property.mortgage_value)}</p>
        <p>Owner {ownerName(game, ownership.owner_id)}</p>
        <p>{ownership.mortgaged ? "Mortgaged" : "Unmortgaged"}</p>
        <p>Houses: {ownership.houses}</p>
        <p>Hotels: {ownership.hotels}</p>
        {facts.map((fact) => (
          <p key={fact}>{fact}</p>
        ))}
        <p>{hotelConversionText(property, ownership, buyAction, sellAction)}</p>
      </div>

      {hasAnyAction ? (
        <div className="mt-3 flex flex-wrap gap-2">
          {mortgageAction ? (
            <ManagementActionButton
              action={mortgageAction}
              disabled={controlsDisabled}
              label="Mortgage"
              onSubmit={onSubmit}
              pendingActionType={pendingActionType}
            />
          ) : null}
          {unmortgageAction ? (
            <ManagementActionButton
              action={unmortgageAction}
              disabled={controlsDisabled}
              label="Unmortgage"
              onSubmit={onSubmit}
              pendingActionType={pendingActionType}
            />
          ) : null}
          {buyAction ? (
            <ManagementActionButton
              action={buyAction}
              disabled={controlsDisabled}
              label="Build house"
              onSubmit={onSubmit}
              pendingActionType={pendingActionType}
            />
          ) : null}
          {sellAction ? (
            <ManagementActionButton
              action={sellAction}
              disabled={controlsDisabled}
              label="Sell house"
              onSubmit={onSubmit}
              pendingActionType={pendingActionType}
            />
          ) : null}
        </div>
      ) : null}
    </article>
  );
}

export function PropertyManagementPanel({
  game,
  snapshot,
  legalActions,
  controlsDisabled,
  pendingActionType,
  onSubmit,
}: PropertyManagementPanelProps) {
  const ownerships = useMemo(() => ownershipByProperty(snapshot), [snapshot]);
  const inventory = useMemo(() => bankInventory(snapshot), [snapshot]);
  const ownerGroups = useMemo(() => buildOwnerGroups(game, ownerships), [game, ownerships]);
  const managementLegalActions = useMemo(() => legalActions.filter(isManagementAction), [legalActions]);

  return (
    <section aria-label="Property management" className="rounded-md border border-neutral-200 bg-white p-4 shadow-sm">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h2 className="text-sm font-semibold text-neutral-950">Property management</h2>
          <p className="mt-1 text-xs text-neutral-600">
            Mortgage, building, and sale controls appear only when /legal-actions returns them for a property.
          </p>
        </div>
        <span className="inline-flex w-fit items-center gap-1.5 rounded-full bg-neutral-100 px-2 py-1 text-xs font-medium text-neutral-600">
          {managementLegalActions.length} management actions
        </span>
      </div>

      <div className="mt-4 grid gap-4">
        <OwnerPropertyList groups={ownerGroups} ownerships={ownerships} />
        <div className="grid gap-4 lg:grid-cols-2">
          <BankInventoryPanel inventory={inventory} />
          <MonopolyGroupsPanel game={game} ownerships={ownerships} />
        </div>
        <section aria-label="Property detail cards" className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {PROPERTIES.map((propertyRef) => {
            const property = PROPERTIES_BY_ID[propertyRef.id];
            const ownership = ownerships.get(property.id) ?? defaultOwnership(property.id);
            const actions: Record<ManagementActionType, LegalAction | null> = {
              BUY_HOUSE: legalActionFor(managementLegalActions, "BUY_HOUSE", property.id),
              SELL_HOUSE: legalActionFor(managementLegalActions, "SELL_HOUSE", property.id),
              MORTGAGE_PROPERTY: legalActionFor(managementLegalActions, "MORTGAGE_PROPERTY", property.id),
              UNMORTGAGE_PROPERTY: legalActionFor(managementLegalActions, "UNMORTGAGE_PROPERTY", property.id),
            };
            return (
              <PropertyDetailCard
                key={property.id}
                actions={actions}
                controlsDisabled={controlsDisabled}
                game={game}
                onSubmit={onSubmit}
                ownership={ownership}
                pendingActionType={pendingActionType}
                property={property}
              />
            );
          })}
        </section>
      </div>
    </section>
  );
}
