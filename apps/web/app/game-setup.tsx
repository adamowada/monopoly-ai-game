"use client";

import { BOARD_SPACES, PROPERTIES_BY_ID, PROPERTY_GROUPS, type StaticDataProperty } from "@monopoly-ai-game/schemas";
import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2, Plus, RefreshCw, Trash2 } from "lucide-react";

import { Button } from "../components/ui/button";
import { createGame, type CreateGamePlayer } from "../lib/api/games";
import { cn } from "../lib/ui";
import { PLAYER_ICON_OPTIONS, defaultPlayerIcon, isPlayerIconOption } from "./player-icons";

type PlayerKind = CreateGamePlayer["kind"];

type SetupPlayer = {
  id: string;
  name: string;
  kind: PlayerKind;
  color: string;
  icon: string;
};

type DebugPropertyImprovementValue = "" | "1" | "2" | "3" | "4" | "hotel";

const playerColors = ["#0f766e", "#2563eb", "#7c3aed", "#dc2626", "#ca8a04"];
const hexColorPattern = /^#[0-9a-fA-F]{6}$/;
const defaultStartingCash = "1500";
const maxDebugStartingCash = 100_000;
const debugPropertyImprovementValues = new Set(["", "1", "2", "3", "4", "hotel"]);
const debugPropertyImprovementOptions: Array<{ value: DebugPropertyImprovementValue; label: string }> = [
  { value: "", label: "No buildings" },
  { value: "1", label: "1 house" },
  { value: "2", label: "2 houses" },
  { value: "3", label: "3 houses" },
  { value: "4", label: "4 houses" },
  { value: "hotel", label: "Hotel" },
];
const debugPropertiesById = PROPERTIES_BY_ID as Readonly<Record<string, StaticDataProperty | undefined>>;
const debugPropertyOptions = BOARD_SPACES.flatMap((space) => {
  if (!space.property_id) {
    return [];
  }
  const property = PROPERTIES_BY_ID[space.property_id];
  return property ? [{ id: property.id, kind: property.kind, name: property.name }] : [];
});
const debugPropertySetOptions = PROPERTY_GROUPS.map((group) => ({
  id: group.id,
  name: group.name,
  propertyIds: group.property_ids.filter((propertyId) => Boolean(PROPERTIES_BY_ID[propertyId])),
})).filter((group) => group.propertyIds.length > 0);

export const AI_PLAYER_NAMES = [
  "Emma",
  "Noah",
  "Olivia",
  "Liam",
  "Ava",
  "Ethan",
  "Sophia",
  "Mason",
  "Mia",
  "Lucas",
  "Amelia",
  "Logan",
  "Harper",
  "James",
  "Evelyn",
  "Benjamin",
  "Abigail",
  "Henry",
  "Charlotte",
  "Daniel",
  "Ella",
  "Michael",
  "Grace",
  "Alexander",
  "Lily",
  "Jacob",
  "Nora",
  "William",
  "Chloe",
  "Samuel",
];

function debugPropertyById(propertyId: string): StaticDataProperty | null {
  return debugPropertiesById[propertyId] ?? null;
}

function generateSeed(): string {
  return `setup-${Date.now().toString(36)}-${Math.floor(Math.random() * 100_000)
    .toString(36)
    .padStart(4, "0")}`;
}

function defaultPlayer(index: number): SetupPlayer {
  return {
    id: `player-${index + 1}`,
    name: `Player ${index + 1}`,
    kind: "human",
    color: playerColors[index] ?? "#525252",
    icon: defaultPlayerIcon(index),
  };
}

function parsePositiveInteger(value: string): number | null {
  if (!/^\d+$/.test(value.trim())) {
    return null;
  }
  const parsed = Number.parseInt(value, 10);
  return Number.isSafeInteger(parsed) ? parsed : null;
}

function parseNonNegativeInteger(value: string): number | null {
  if (!/^\d+$/.test(value.trim())) {
    return null;
  }
  const parsed = Number.parseInt(value, 10);
  return Number.isSafeInteger(parsed) ? parsed : null;
}

function normalizeColor(value: string): string {
  return value.trim().toLowerCase();
}

function colorInputValue(value: string): string {
  return hexColorPattern.test(value) ? value : "#000000";
}

function isGenericPlayerName(name: string): boolean {
  return /^Player \d+$/.test(name.trim());
}

function generatedAiName(players: SetupPlayer[], targetIndex: number): string {
  const usedNames = new Set(
    players
      .filter((_, index) => index !== targetIndex)
      .map((player) => player.name.trim().toLowerCase())
      .filter(Boolean),
  );

  for (let offset = 0; offset < AI_PLAYER_NAMES.length; offset += 1) {
    const name = AI_PLAYER_NAMES[(targetIndex + offset) % AI_PLAYER_NAMES.length];
    if (!usedNames.has(name.toLowerCase())) {
      return name;
    }
  }

  return `AI ${targetIndex + 1}`;
}

function validateSetup(
  players: SetupPlayer[],
  maxRounds: string,
  proposalLimit: string,
  debugEnabled: boolean,
  debugCash: Record<string, string>,
  debugPropertyOwners: Record<string, string>,
  debugPropertyImprovements: Record<string, string>,
  debugPropertyMortgages: Record<string, boolean>,
): string[] {
  const messages: string[] = [];
  const names = players.map((player) => player.name.trim());
  const lowerNames = names.map((name) => name.toLowerCase());
  const maxRoundsValue = parsePositiveInteger(maxRounds);
  const proposalLimitValue = parsePositiveInteger(proposalLimit);

  if (players.length < 2 || players.length > 5) {
    messages.push("Game setup requires 2 to 5 players");
  }
  if (names.some((name) => name.length === 0)) {
    messages.push("Player names are required");
  }
  if (new Set(lowerNames).size !== lowerNames.length) {
    messages.push("Player names must be unique");
  }
  if (players.some((player) => !hexColorPattern.test(player.color.trim()))) {
    messages.push("Player colors must be valid hex colors");
  }
  if (new Set(players.map((player) => normalizeColor(player.color))).size !== players.length) {
    messages.push("Player colors must be unique");
  }
  if (players.some((player) => !isPlayerIconOption(player.icon))) {
    messages.push("Player token icons must use the setup choices");
  }
  if (new Set(players.map((player) => player.icon)).size !== players.length) {
    messages.push("Player token icons must be unique");
  }
  if (maxRoundsValue === null || maxRoundsValue < 1) {
    messages.push("Max negotiation rounds must be at least 1");
  } else if (maxRoundsValue > 20) {
    messages.push("Max negotiation rounds must be 20 or less");
  }
  if (proposalLimitValue === null || proposalLimitValue < 1) {
    messages.push("Proposal limit per player must be at least 1");
  } else if (proposalLimitValue > 50) {
    messages.push("Proposal limit per player must be 50 or less");
  }
  if (debugEnabled) {
    for (const player of players) {
      const cash = parseNonNegativeInteger(debugCash[player.id] ?? defaultStartingCash);
      if (cash === null || cash > maxDebugStartingCash) {
        messages.push(`${player.name.trim() || "Player"} starting cash must be between 0 and ${maxDebugStartingCash}`);
      }
    }
    const validSeatValues = new Set(players.map((_, index) => String(index)));
    for (const ownerValue of Object.values(debugPropertyOwners)) {
      if (ownerValue !== "" && !validSeatValues.has(ownerValue)) {
        messages.push("Debug property owners must reference a configured seat");
        break;
      }
    }
    for (const [propertyId, improvementValue] of Object.entries(debugPropertyImprovements)) {
      if (improvementValue === "") {
        continue;
      }
      const property = debugPropertyById(propertyId);
      if (!property || property.kind !== "street" || !debugPropertyImprovementValues.has(improvementValue)) {
        messages.push("Debug property improvements must be houses or hotel on street properties");
        break;
      }
      if (!validSeatValues.has(debugPropertyOwners[propertyId] ?? "")) {
        messages.push("Debug property improvements require a configured owner");
        break;
      }
    }
    for (const [propertyId, mortgaged] of Object.entries(debugPropertyMortgages)) {
      if (!mortgaged) {
        continue;
      }
      const property = debugPropertyById(propertyId);
      if (!property) {
        messages.push("Debug property mortgages must reference board properties");
        break;
      }
      if (!validSeatValues.has(debugPropertyOwners[propertyId] ?? "")) {
        messages.push("Debug property mortgages require a configured owner");
        break;
      }
      if (property.kind === "street" && (debugPropertyImprovements[propertyId] ?? "") !== "") {
        messages.push("Debug mortgaged properties cannot start with buildings");
        break;
      }
    }
  }

  return messages;
}

export function GameSetupPanel() {
  const router = useRouter();
  const [seed, setSeed] = useState(generateSeed);
  const [players, setPlayers] = useState<SetupPlayer[]>(() => [defaultPlayer(0), defaultPlayer(1)]);
  const [maxRounds, setMaxRounds] = useState("3");
  const [proposalLimit, setProposalLimit] = useState("4");
  const [debugEnabled, setDebugEnabled] = useState(false);
  const [debugCash, setDebugCash] = useState<Record<string, string>>({});
  const [debugPropertyOwners, setDebugPropertyOwners] = useState<Record<string, string>>({});
  const [debugPropertyImprovements, setDebugPropertyImprovements] = useState<Record<string, string>>({});
  const [debugPropertyMortgages, setDebugPropertyMortgages] = useState<Record<string, boolean>>({});
  const [messages, setMessages] = useState<string[]>([]);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const canAddPlayer = players.length < 5;
  const canRemovePlayer = players.length > 2;
  const maxRoundsValue = useMemo(() => parsePositiveInteger(maxRounds) ?? 0, [maxRounds]);
  const proposalLimitValue = useMemo(() => parsePositiveInteger(proposalLimit) ?? 0, [proposalLimit]);

  function updatePlayer(index: number, patch: Partial<SetupPlayer>) {
    setPlayers((current) =>
      current.map((player, playerIndex) => (playerIndex === index ? { ...player, ...patch } : player)),
    );
  }

  function addPlayer() {
    if (!canAddPlayer) {
      return;
    }
    setPlayers((current) => [...current, defaultPlayer(current.length)]);
  }

  function setPlayerKind(index: number, kind: PlayerKind) {
    setPlayers((current) =>
      current.map((player, playerIndex) => {
        if (playerIndex !== index) {
          return player;
        }
        const shouldGenerateAiName = kind === "ai" && player.kind !== "ai" && isGenericPlayerName(player.name);
        return {
          ...player,
          kind,
          name: shouldGenerateAiName ? generatedAiName(current, index) : player.name,
        };
      }),
    );
  }

  function removePlayer(index: number) {
    if (!canRemovePlayer) {
      return;
    }
    setPlayers((current) => current.filter((_, playerIndex) => playerIndex !== index));
  }

  function debugStartingCash(player: SetupPlayer): string {
    return debugCash[player.id] ?? defaultStartingCash;
  }

  function setDebugStartingCash(playerId: string, cash: string) {
    setDebugCash((current) => ({ ...current, [playerId]: cash }));
  }

  function setDebugPropertyOwner(propertyId: string, seatOrder: string) {
    setDebugPropertyOwners((current) => ({ ...current, [propertyId]: seatOrder }));
    if (seatOrder === "") {
      setDebugPropertyImprovements((current) => {
        const next = { ...current };
        delete next[propertyId];
        return next;
      });
      setDebugPropertyMortgages((current) => {
        const next = { ...current };
        delete next[propertyId];
        return next;
      });
    }
  }

  function debugPropertyImprovement(propertyId: string): string {
    return debugPropertyImprovements[propertyId] ?? "";
  }

  function setDebugPropertyImprovement(propertyId: string, improvement: string) {
    setDebugPropertyImprovements((current) => ({ ...current, [propertyId]: improvement }));
    if (improvement !== "") {
      setDebugPropertyMortgages((current) => {
        const next = { ...current };
        delete next[propertyId];
        return next;
      });
    }
  }

  function debugPropertyMortgaged(propertyId: string): boolean {
    return debugPropertyMortgages[propertyId] === true;
  }

  function setDebugPropertyMortgage(propertyId: string, mortgaged: boolean) {
    if (mortgaged) {
      setDebugPropertyImprovements((current) => {
        const next = { ...current };
        delete next[propertyId];
        return next;
      });
    }
    setDebugPropertyMortgages((current) => {
      const next = { ...current };
      if (mortgaged) {
        next[propertyId] = true;
      } else {
        delete next[propertyId];
      }
      return next;
    });
  }

  function debugPropertySetOwnerValue(propertyIds: readonly string[]): string {
    const ownerValues = propertyIds.map((propertyId) => debugPropertyOwners[propertyId] ?? "");
    const firstOwnerValue = ownerValues[0] ?? "";
    return ownerValues.every((ownerValue) => ownerValue === firstOwnerValue) ? firstOwnerValue : "__mixed";
  }

  function setDebugPropertySetOwner(propertyIds: readonly string[], seatOrder: string) {
    setDebugPropertyOwners((current) => {
      const next = { ...current };
      for (const propertyId of propertyIds) {
        next[propertyId] = seatOrder;
      }
      return next;
    });
    if (seatOrder === "") {
      setDebugPropertyImprovements((current) => {
        const next = { ...current };
        for (const propertyId of propertyIds) {
          delete next[propertyId];
        }
        return next;
      });
      setDebugPropertyMortgages((current) => {
        const next = { ...current };
        for (const propertyId of propertyIds) {
          delete next[propertyId];
        }
        return next;
      });
    }
  }

  function debugAllocationSettings() {
    if (!debugEnabled) {
      return {};
    }
    const validSeatValues = new Set(players.map((_, index) => String(index)));
    const propertyImprovements = Object.entries(debugPropertyImprovements)
      .filter(([propertyId, improvement]) => {
        const property = debugPropertyById(propertyId);
        return (
          improvement !== "" &&
          property?.kind === "street" &&
          debugPropertyImprovementValues.has(improvement) &&
          validSeatValues.has(debugPropertyOwners[propertyId] ?? "")
        );
      })
      .map(([propertyId, improvement]) => ({
        property_id: propertyId,
        houses: improvement === "hotel" ? 0 : Number.parseInt(improvement, 10),
        hotel: improvement === "hotel",
      }));
    const propertyMortgages = Object.entries(debugPropertyMortgages)
      .filter(([propertyId, mortgaged]) => {
        const property = debugPropertyById(propertyId);
        return (
          mortgaged &&
          Boolean(property) &&
          validSeatValues.has(debugPropertyOwners[propertyId] ?? "") &&
          !(property?.kind === "street" && (debugPropertyImprovements[propertyId] ?? "") !== "")
        );
      })
      .map(([propertyId]) => ({
        property_id: propertyId,
        mortgaged: true,
      }));
    return {
      debug_allocations: {
        player_cash: players.map((player, seatOrder) => ({
          seat_order: seatOrder,
          cash: parseNonNegativeInteger(debugStartingCash(player)) ?? Number(defaultStartingCash),
        })),
        property_owners: Object.entries(debugPropertyOwners)
          .filter(([, seatOrder]) => seatOrder !== "" && validSeatValues.has(seatOrder))
          .map(([propertyId, seatOrder]) => ({
            property_id: propertyId,
            seat_order: Number.parseInt(seatOrder, 10),
          })),
        ...(propertyImprovements.length > 0 ? { property_improvements: propertyImprovements } : {}),
        ...(propertyMortgages.length > 0 ? { property_mortgages: propertyMortgages } : {}),
      },
    };
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const validationMessages = validateSetup(
      players,
      maxRounds,
      proposalLimit,
      debugEnabled,
      debugCash,
      debugPropertyOwners,
      debugPropertyImprovements,
      debugPropertyMortgages,
    );
    if (validationMessages.length > 0) {
      setMessages(validationMessages);
      return;
    }

    setIsSubmitting(true);
    setMessages([]);

    const result = await createGame({
      seed: seed.trim(),
      players: players.map((player) => ({
        name: player.name.trim(),
        kind: player.kind,
      })),
      settings: {
        player_colors: players.map((player, seatOrder) => ({
          seat_order: seatOrder,
          color: normalizeColor(player.color),
        })),
        player_icons: players.map((player, seatOrder) => ({
          seat_order: seatOrder,
          icon: player.icon,
        })),
        ...debugAllocationSettings(),
        negotiation_cutoffs: {
          max_rounds: maxRoundsValue,
          max_proposals_per_player: proposalLimitValue,
        },
      },
    });

    setIsSubmitting(false);
    if (result.state === "loaded") {
      router.push(`/games/${encodeURIComponent(result.game.id)}`, { scroll: true });
      return;
    }
    setMessages([result.error]);
  }

  return (
    <section id="game-setup" aria-label="Choose seats" className="bg-[#eaf3d7]">
      <div className="mx-auto max-w-7xl px-4 py-4 sm:px-6 lg:px-8">
        <form noValidate onSubmit={handleSubmit} className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_320px]">
          <div
            className="min-w-0 rounded-md border-2 border-[#2f2418]/30 bg-[#fff8e8] p-3 shadow-[0_10px_25px_rgba(47,36,24,0.14)]"
          >
            <div className="flex flex-col gap-3 border-b border-[#b99768]/50 pb-3 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <h3 className="text-sm font-black text-[#2f2418]">Seat cards</h3>
              </div>
              <Button onClick={addPlayer} disabled={!canAddPlayer || isSubmitting} className="justify-center">
                <Plus aria-hidden="true" className="size-4" />
                Add player
              </Button>
            </div>

            <div className="mt-3 grid gap-3 md:grid-cols-2">
              {players.map((player, index) => {
                const playerNumber = index + 1;
                return (
                  <article
                    key={player.id}
                    aria-label={`Seat ${playerNumber} token setup`}
                    className="grid gap-3 rounded-md border-2 border-[#b99768]/70 bg-white/85 p-3 text-[#2f2418] shadow-sm"
                    role="group"
                  >
                    <div className="flex items-start gap-3">
                      <span
                        aria-hidden="true"
                        className="grid size-11 shrink-0 place-items-center rounded-[0.35rem] border-2 border-[#2f2418] text-xl font-black text-white shadow-[0_3px_0_rgba(47,36,24,0.25)]"
                        style={{ backgroundColor: colorInputValue(player.color) }}
                      >
                        {player.icon}
                      </span>
                      <div className="min-w-0 flex-1" />
                      <Button
                        aria-label={`Remove Player ${playerNumber}`}
                        onClick={() => removePlayer(index)}
                        disabled={!canRemovePlayer || isSubmitting}
                        variant="secondary"
                      >
                        <Trash2 aria-hidden="true" className="size-4" />
                      </Button>
                    </div>

                    <label className="grid gap-1 text-sm font-bold text-[#2f2418]">
                      Name
                      <input
                        aria-label={`Player ${playerNumber} name`}
                        value={player.name}
                        onChange={(event) => updatePlayer(index, { name: event.target.value })}
                        className="w-full rounded-md border border-[#b99768] bg-white px-3 py-2 text-sm text-[#2f2418] outline-none focus:border-teal-700 focus:ring-2 focus:ring-teal-700/20"
                      />
                    </label>

                    <fieldset className="grid gap-1">
                      <legend className="text-sm font-bold text-[#2f2418]">Token icon</legend>
                      <div className="flex flex-wrap gap-1.5">
                        {PLAYER_ICON_OPTIONS.map((option) => {
                          const selected = player.icon === option.icon;
                          const unavailable = players.some(
                            (otherPlayer, otherIndex) => otherIndex !== index && otherPlayer.icon === option.icon,
                          );
                          return (
                            <button
                              key={option.icon}
                              aria-label={`Player ${playerNumber} token icon ${option.label}`}
                              aria-pressed={selected}
                              className={cn(
                                "grid size-9 place-items-center rounded-md border-2 text-lg shadow-sm transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#0f766e]",
                                selected
                                  ? "border-[#173c45] bg-[#173c45] shadow-[0_3px_0_rgba(47,36,24,0.24)]"
                                  : "border-[#b99768] bg-white hover:bg-[#fffbea]",
                                unavailable && "cursor-not-allowed opacity-45 hover:bg-white",
                              )}
                              disabled={isSubmitting || unavailable}
                              onClick={() => updatePlayer(index, { icon: option.icon })}
                              title={option.label}
                              type="button"
                            >
                              <span aria-hidden="true" className="leading-none">
                                {option.icon}
                              </span>
                            </button>
                          );
                        })}
                      </div>
                    </fieldset>

                    <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto]">
                      <label className="grid gap-1 text-sm font-bold text-[#2f2418]">
                        Seat type
                        <select
                          aria-label={`Player ${playerNumber} type`}
                          value={player.kind}
                          onChange={(event) => setPlayerKind(index, event.target.value as PlayerKind)}
                          className="w-full rounded-md border border-[#b99768] bg-white px-3 py-2 text-sm text-[#2f2418] outline-none focus:border-teal-700 focus:ring-2 focus:ring-teal-700/20"
                        >
                          <option value="human">Human</option>
                          <option value="ai">AI</option>
                        </select>
                      </label>

                      <label className="grid gap-1 text-sm font-bold text-[#2f2418]">
                        Token color
                        <span className="flex items-center gap-2">
                          <input
                            aria-label={`Player ${playerNumber} color picker`}
                            type="color"
                            value={colorInputValue(player.color)}
                            onChange={(event) => updatePlayer(index, { color: event.target.value })}
                            className="size-10 rounded-md border border-[#b99768] bg-white p-1"
                          />
                          <input
                            aria-label={`Player ${playerNumber} color hex`}
                            value={player.color}
                            onChange={(event) => updatePlayer(index, { color: event.target.value })}
                            className="w-28 rounded-md border border-[#b99768] bg-white px-3 py-2 text-sm text-[#2f2418] outline-none focus:border-teal-700 focus:ring-2 focus:ring-teal-700/20"
                          />
                        </span>
                      </label>
                    </div>
                  </article>
                );
              })}
            </div>
          </div>

          <div className="grid gap-4 rounded-md border-2 border-[#2f2418]/30 bg-[#fff8e8] p-4 shadow-[0_8px_20px_rgba(47,36,24,0.12)]">
            <label className="grid gap-1 text-sm font-black text-[#2f2418]">
              Seed
              <span className="flex gap-2">
                <input
                  value={seed}
                  onChange={(event) => setSeed(event.target.value)}
                  className="min-w-0 flex-1 rounded-md border border-[#b99768] bg-white px-3 py-2 text-sm text-[#2f2418] outline-none focus:border-teal-700 focus:ring-2 focus:ring-teal-700/20"
                />
                <Button
                  aria-label="Generate seed"
                  onClick={() => setSeed(generateSeed())}
                  disabled={isSubmitting}
                  className="shrink-0"
                  variant="secondary"
                >
                  <RefreshCw aria-hidden="true" className="size-4" />
                </Button>
              </span>
            </label>

            <fieldset className="grid gap-3">
              <legend className="text-sm font-black text-[#2f2418]">Negotiation cutoffs</legend>
              <label className="grid gap-1 text-sm font-bold text-[#2f2418]">
                Max negotiation rounds
                <input
                  type="number"
                  min={1}
                  max={20}
                  value={maxRounds}
                  onChange={(event) => setMaxRounds(event.target.value)}
                  className="rounded-md border border-[#b99768] bg-white px-3 py-2 text-sm text-[#2f2418] outline-none focus:border-teal-700 focus:ring-2 focus:ring-teal-700/20"
                />
              </label>
              <label className="grid gap-1 text-sm font-bold text-[#2f2418]">
                Proposal limit per player
                <input
                  type="number"
                  min={1}
                  max={50}
                  value={proposalLimit}
                  onChange={(event) => setProposalLimit(event.target.value)}
                  className="rounded-md border border-[#b99768] bg-white px-3 py-2 text-sm text-[#2f2418] outline-none focus:border-teal-700 focus:ring-2 focus:ring-teal-700/20"
                />
              </label>
            </fieldset>

            <fieldset className="grid gap-3 rounded-md border border-[#b99768]/70 bg-white/60 p-3">
              <legend className="px-1 text-sm font-black text-[#2f2418]">Debug setup</legend>
              <label className="flex items-center gap-2 text-sm font-bold text-[#2f2418]">
                <input
                  aria-label="Enable debug setup"
                  checked={debugEnabled}
                  className="size-4 accent-[#0f766e]"
                  disabled={isSubmitting}
                  onChange={(event) => setDebugEnabled(event.target.checked)}
                  type="checkbox"
                />
                Enable debug setup
              </label>

              {debugEnabled ? (
                <div className="grid gap-4">
                  <div className="grid gap-2">
                    <div className="text-xs font-black uppercase text-[#6f604c]">Starting cash</div>
                    {players.map((player, index) => (
                      <label key={player.id} className="grid gap-1 text-sm font-bold text-[#2f2418]">
                        {player.name.trim() || `Player ${index + 1}`}
                        <input
                          aria-label={`Player ${index + 1} starting cash`}
                          max={maxDebugStartingCash}
                          min={0}
                          onChange={(event) => setDebugStartingCash(player.id, event.target.value)}
                          type="number"
                          value={debugStartingCash(player)}
                          className="rounded-md border border-[#b99768] bg-white px-3 py-2 text-sm text-[#2f2418] outline-none focus:border-teal-700 focus:ring-2 focus:ring-teal-700/20"
                        />
                      </label>
                    ))}
                  </div>

                  <div className="grid gap-2">
                    <div className="text-xs font-black uppercase text-[#6f604c]">Property sets</div>
                    <div className="grid gap-2">
                      {debugPropertySetOptions.map((group) => {
                        const value = debugPropertySetOwnerValue(group.propertyIds);
                        return (
                          <label key={group.id} className="grid gap-1 text-sm font-bold text-[#2f2418]">
                            {group.name} set
                            <select
                              aria-label={`${group.name} set owner`}
                              onChange={(event) => setDebugPropertySetOwner(group.propertyIds, event.target.value)}
                              value={value}
                              className="rounded-md border border-[#b99768] bg-white px-3 py-2 text-sm text-[#2f2418] outline-none focus:border-teal-700 focus:ring-2 focus:ring-teal-700/20"
                            >
                              {value === "__mixed" ? (
                                <option disabled value="__mixed">
                                  Mixed
                                </option>
                              ) : null}
                              <option value="">Bank</option>
                              {players.map((player, seatOrder) => (
                                <option key={player.id} value={seatOrder}>
                                  {player.name.trim() || `Player ${seatOrder + 1}`}
                                </option>
                              ))}
                            </select>
                          </label>
                        );
                      })}
                    </div>
                  </div>

                  <div className="grid gap-2">
                    <div className="text-xs font-black uppercase text-[#6f604c]">Property owners</div>
                    <div className="grid max-h-80 gap-2 overflow-y-auto pr-1">
                      {debugPropertyOptions.map((property) => {
                        const ownerValue = debugPropertyOwners[property.id] ?? "";
                        const improvementValue = debugPropertyImprovement(property.id);
                        const mortgageDisabled =
                          ownerValue === "" || (property.kind === "street" && improvementValue !== "");
                        return (
                          <div key={property.id} className="grid gap-2 rounded border border-[#b99768]/50 bg-white/55 p-2">
                            <label className="grid gap-1 text-sm font-bold text-[#2f2418]">
                              {property.name}
                              <select
                                aria-label={`${property.name} owner`}
                                onChange={(event) => setDebugPropertyOwner(property.id, event.target.value)}
                                value={ownerValue}
                                className="rounded-md border border-[#b99768] bg-white px-3 py-2 text-sm text-[#2f2418] outline-none focus:border-teal-700 focus:ring-2 focus:ring-teal-700/20"
                              >
                                <option value="">Bank</option>
                                {players.map((player, seatOrder) => (
                                  <option key={player.id} value={seatOrder}>
                                    {player.name.trim() || `Player ${seatOrder + 1}`}
                                  </option>
                                ))}
                              </select>
                            </label>
                            {property.kind === "street" ? (
                              <label className="grid gap-1 text-sm font-bold text-[#2f2418]">
                                Improvements
                                <select
                                  aria-label={`${property.name} improvements`}
                                  disabled={ownerValue === ""}
                                  onChange={(event) => setDebugPropertyImprovement(property.id, event.target.value)}
                                  value={improvementValue}
                                  className="rounded-md border border-[#b99768] bg-white px-3 py-2 text-sm text-[#2f2418] outline-none focus:border-teal-700 focus:ring-2 focus:ring-teal-700/20 disabled:bg-[#e8dfcd] disabled:text-[#6f604c]"
                                >
                                  {debugPropertyImprovementOptions.map((option) => (
                                    <option key={option.value} value={option.value}>
                                      {option.label}
                                    </option>
                                  ))}
                                </select>
                              </label>
                            ) : null}
                            <label className="inline-flex items-center gap-2 text-sm font-bold text-[#2f2418]">
                              <input
                                aria-label={`${property.name} mortgaged`}
                                checked={debugPropertyMortgaged(property.id)}
                                disabled={mortgageDisabled}
                                onChange={(event) => setDebugPropertyMortgage(property.id, event.target.checked)}
                                type="checkbox"
                                className="size-4 rounded border-[#b99768] text-teal-700 focus:ring-teal-700 disabled:opacity-50"
                              />
                              Mortgaged
                            </label>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                </div>
              ) : null}
            </fieldset>

            {messages.length > 0 ? (
              <div
                role="alert"
                className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700"
              >
                <ul className="list-disc space-y-1 pl-4">
                  {messages.map((message) => (
                    <li key={message}>{message}</li>
                  ))}
                </ul>
              </div>
            ) : null}

            <Button type="submit" disabled={isSubmitting} className={cn("w-full justify-center", isSubmitting && "gap-2")}>
              {isSubmitting ? <Loader2 aria-hidden="true" className="size-4 animate-spin" /> : <Plus aria-hidden="true" className="size-4" />}
              Create game
            </Button>
          </div>
        </form>
      </div>
    </section>
  );
}
