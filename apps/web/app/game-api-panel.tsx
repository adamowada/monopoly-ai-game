"use client";

import { useState } from "react";
import { Loader2, Plus, Search } from "lucide-react";

import { Button } from "../components/ui/button";
import { createGame, readGame, type GameSnapshot } from "../lib/api/games";
import { cn } from "../lib/ui";

const demoPlayers = [
  { name: "Ada", kind: "human" },
  { name: "Grace", kind: "ai" },
] as const;

export function GameApiPanel() {
  const [gameId, setGameId] = useState("");
  const [snapshot, setSnapshot] = useState<GameSnapshot | null>(null);
  const [busyAction, setBusyAction] = useState<"create" | "load" | null>(null);

  async function handleCreateGame() {
    setBusyAction("create");
    const result = await createGame({
      seed: `web-${Date.now()}`,
      players: [...demoPlayers],
    });
    setSnapshot(result);
    if (result.state === "loaded") {
      setGameId(result.game.id);
    }
    setBusyAction(null);
  }

  async function handleLoadGame() {
    if (!gameId.trim()) {
      setSnapshot({ state: "error", error: "Game ID is required" });
      return;
    }

    setBusyAction("load");
    setSnapshot(await readGame({ gameId: gameId.trim() }));
    setBusyAction(null);
  }

  const isBusy = busyAction !== null;

  return (
    <section id="game-api" aria-labelledby="game-api-title" className="border-b border-neutral-200 bg-white">
      <div className="mx-auto grid max-w-7xl gap-5 px-4 py-6 sm:px-6 lg:grid-cols-[minmax(0,1fr)_minmax(320px,420px)] lg:px-8">
        <div>
          <h2 id="game-api-title" className="text-base font-semibold text-neutral-950">
            Game API
          </h2>
          <dl className="mt-4 grid gap-3 text-sm sm:grid-cols-3">
            <div className="border-y border-neutral-200 py-3">
              <dt className="text-xs font-medium uppercase text-neutral-500">Status</dt>
              <dd className="mt-1 font-medium text-neutral-950">
                {snapshot?.state === "loaded" ? snapshot.game.status : "No game loaded"}
              </dd>
            </div>
            <div className="border-y border-neutral-200 py-3">
              <dt className="text-xs font-medium uppercase text-neutral-500">Phase</dt>
              <dd className="mt-1 font-medium text-neutral-950">
                {snapshot?.state === "loaded" ? snapshot.game.current_phase : "Unverified"}
              </dd>
            </div>
            <div className="border-y border-neutral-200 py-3">
              <dt className="text-xs font-medium uppercase text-neutral-500">Players</dt>
              <dd className="mt-1 font-medium text-neutral-950">
                {snapshot?.state === "loaded" ? snapshot.game.players.length : 0}
              </dd>
            </div>
          </dl>
        </div>

        <div className="rounded-md border border-neutral-200 bg-neutral-50 p-4">
          <div className="flex flex-col gap-3 sm:flex-row">
            <Button onClick={handleCreateGame} disabled={isBusy} className="justify-center">
              {busyAction === "create" ? (
                <Loader2 aria-hidden="true" className="size-4 animate-spin" />
              ) : (
                <Plus aria-hidden="true" className="size-4" />
              )}
              Create game
            </Button>
            <div className="grid min-w-0 flex-1 gap-1">
              <label htmlFor="load-game-id" className="text-xs font-medium uppercase text-neutral-500">
                Game ID
              </label>
              <input
                id="load-game-id"
                value={gameId}
                onChange={(event) => setGameId(event.target.value)}
                className="min-w-0 rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm text-neutral-950 outline-none focus:border-teal-700 focus:ring-2 focus:ring-teal-700/20"
              />
            </div>
            <Button onClick={handleLoadGame} disabled={isBusy} className="justify-center self-end">
              {busyAction === "load" ? (
                <Loader2 aria-hidden="true" className="size-4 animate-spin" />
              ) : (
                <Search aria-hidden="true" className="size-4" />
              )}
              Load game
            </Button>
          </div>

          <div
            role="status"
            aria-live="polite"
            className={cn(
              "mt-4 rounded-md border px-3 py-2 text-sm",
              snapshot?.state === "error"
                ? "border-rose-200 bg-rose-50 text-rose-700"
                : "border-neutral-200 bg-white text-neutral-700",
            )}
          >
            {snapshot?.state === "loaded" ? (
              <span className="font-medium text-neutral-950">{snapshot.game.id}</span>
            ) : snapshot?.state === "error" ? (
              snapshot.error
            ) : (
              "Ready"
            )}
          </div>
        </div>
      </div>
    </section>
  );
}
