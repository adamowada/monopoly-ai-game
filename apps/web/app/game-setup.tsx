"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { Bot, Loader2, Plus, RefreshCw, Trash2, UserRound } from "lucide-react";

import { Button } from "../components/ui/button";
import { createGame, type CreateGamePlayer } from "../lib/api/games";
import { cn } from "../lib/ui";

type PlayerKind = CreateGamePlayer["kind"];

type SetupPlayer = {
  id: string;
  name: string;
  kind: PlayerKind;
  color: string;
};

const playerColors = ["#0f766e", "#2563eb", "#7c3aed", "#dc2626", "#ca8a04"];
const hexColorPattern = /^#[0-9a-fA-F]{6}$/;

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
  };
}

function parsePositiveInteger(value: string): number | null {
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

function validateSetup(players: SetupPlayer[], maxRounds: string, proposalLimit: string): string[] {
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

  return messages;
}

export function GameSetupPanel() {
  const router = useRouter();
  const [seed, setSeed] = useState(generateSeed);
  const [players, setPlayers] = useState<SetupPlayer[]>(() => [defaultPlayer(0), defaultPlayer(1)]);
  const [maxRounds, setMaxRounds] = useState("3");
  const [proposalLimit, setProposalLimit] = useState("4");
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

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const validationMessages = validateSetup(players, maxRounds, proposalLimit);
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
        negotiation_cutoffs: {
          max_rounds: maxRoundsValue,
          max_proposals_per_player: proposalLimitValue,
        },
      },
    });

    setIsSubmitting(false);
    if (result.state === "loaded") {
      router.push(`/games/${encodeURIComponent(result.game.id)}`);
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
                <p className="mt-1 text-sm font-semibold text-[#6f604c]">Each card becomes one token at the board.</p>
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
                        className="grid size-11 shrink-0 place-items-center rounded-[0.35rem] border-2 border-[#2f2418] text-sm font-black text-white shadow-[0_3px_0_rgba(47,36,24,0.25)]"
                        style={{ backgroundColor: colorInputValue(player.color) }}
                      >
                        {playerNumber}
                      </span>
                      <div className="min-w-0 flex-1">
                        <p className="text-xs font-black uppercase text-[#6f604c]">Seat {playerNumber}</p>
                        <p className="mt-1 flex items-center gap-1.5 text-sm font-black">
                          {player.kind === "ai" ? (
                            <Bot aria-hidden="true" className="size-4 text-purple-700" />
                          ) : (
                            <UserRound aria-hidden="true" className="size-4 text-teal-700" />
                          )}
                          {player.kind === "ai" ? "AI token" : "Human token"}
                        </p>
                      </div>
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
