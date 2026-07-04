import { ArrowLeft, Bot, UserRound } from "lucide-react";
import Link from "next/link";

import { Button } from "../../../components/ui/button";
import { readGame, type GameMetadata } from "../../../lib/api/games";
import { ClassicGameBoard, getPlayerColor } from "../../game-board";

export const dynamic = "force-dynamic";
export const revalidate = 0;

type GamePageProps = {
  params: Promise<{
    gameId: string;
  }>;
};

type NegotiationCutoffs = {
  max_rounds?: number;
  max_proposals_per_player?: number;
};

function getNegotiationCutoffs(game: GameMetadata): NegotiationCutoffs {
  const cutoffs = game.settings.negotiation_cutoffs;
  if (cutoffs === null || typeof cutoffs !== "object" || Array.isArray(cutoffs)) {
    return {};
  }
  return cutoffs as NegotiationCutoffs;
}

function getPlayerPositionLabel(player: GameMetadata["players"][number]): string {
  const rawPosition = player.state.position;
  return typeof rawPosition === "number" && Number.isInteger(rawPosition) ? String(rawPosition) : "0";
}

export default async function GameBoardPage({ params }: GamePageProps) {
  const { gameId } = await params;
  const snapshot = await readGame({ gameId });

  if (snapshot.state === "error") {
    return (
      <main className="min-h-screen bg-[var(--color-page)] px-4 py-8 text-neutral-950 sm:px-6 lg:px-8">
        <div className="mx-auto max-w-5xl rounded-md border border-rose-200 bg-rose-50 p-5">
          <h1 className="text-xl font-semibold">Game board unavailable</h1>
          <p className="mt-2 text-sm text-rose-700">{snapshot.error}</p>
          <Button asChild className="mt-4">
            <Link href="/">
              <ArrowLeft aria-hidden="true" className="size-4" />
              Back to setup
            </Link>
          </Button>
        </div>
      </main>
    );
  }

  const { game } = snapshot;
  const cutoffs = getNegotiationCutoffs(game);

  return (
    <main className="min-h-screen bg-[var(--color-page)] text-neutral-950">
      <header className="border-b border-neutral-200 bg-white">
        <div className="mx-auto flex max-w-7xl flex-col gap-4 px-4 py-5 sm:px-6 lg:flex-row lg:items-center lg:justify-between lg:px-8">
          <div>
            <p className="text-xs font-semibold uppercase text-teal-700">Phase 5 Stage 5.2</p>
            <h1 className="mt-1 text-2xl font-semibold tracking-normal">Game board {game.id}</h1>
            <p className="mt-2 text-sm text-neutral-600">
              Original vector board surface with state-derived player token positions.
            </p>
          </div>
          <Button asChild className="w-fit bg-white text-neutral-700 ring-1 ring-inset ring-neutral-300 hover:bg-neutral-100">
            <Link href="/">
              <ArrowLeft aria-hidden="true" className="size-4" />
              Setup
            </Link>
          </Button>
        </div>
      </header>

      <div className="mx-auto grid max-w-7xl gap-6 px-4 py-6 sm:px-6 lg:grid-cols-[minmax(0,1fr)_320px] lg:px-8">
        <ClassicGameBoard game={game} />

        <aside className="grid content-start gap-4">
          <section aria-labelledby="players-title" className="overflow-hidden rounded-md border border-neutral-200 bg-white">
            <div className="border-b border-neutral-200 px-4 py-3">
              <h2 id="players-title" className="text-sm font-semibold text-neutral-950">
                Players
              </h2>
              <p className="mt-1 text-xs text-neutral-600">Seat order, type, color, position, and status.</p>
            </div>
            <div className="overflow-x-auto">
              <table className="min-w-full text-left text-xs">
                <thead className="bg-neutral-50 text-[10px] uppercase text-neutral-500">
                  <tr>
                    <th scope="col" className="px-3 py-2 font-semibold">
                      Player
                    </th>
                    <th scope="col" className="px-3 py-2 font-semibold">
                      Type
                    </th>
                    <th scope="col" className="px-3 py-2 font-semibold">
                      Color
                    </th>
                    <th scope="col" className="px-3 py-2 font-semibold">
                      Pos
                    </th>
                    <th scope="col" className="px-3 py-2 font-semibold">
                      Status
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-neutral-200">
                  {game.players.map((player) => {
                    const color = getPlayerColor(game, player.seat_order);
                    return (
                      <tr key={player.id}>
                        <td className="whitespace-nowrap px-3 py-3 font-medium text-neutral-950">{player.name}</td>
                        <td className="whitespace-nowrap px-3 py-3 text-neutral-700">
                          <span className="inline-flex items-center gap-1.5">
                            {player.controller_type === "ai" ? (
                              <Bot aria-hidden="true" className="size-3.5 text-purple-700" />
                            ) : (
                              <UserRound aria-hidden="true" className="size-3.5 text-teal-700" />
                            )}
                            {player.controller_type}
                          </span>
                        </td>
                        <td className="whitespace-nowrap px-3 py-3 text-neutral-700">
                          <span className="inline-flex items-center gap-1.5">
                            <span
                              aria-hidden="true"
                              className="size-3.5 rounded-full border border-neutral-300"
                              style={{ backgroundColor: color }}
                            />
                            {color}
                          </span>
                        </td>
                        <td className="whitespace-nowrap px-3 py-3 text-neutral-700">{getPlayerPositionLabel(player)}</td>
                        <td className="whitespace-nowrap px-3 py-3 text-neutral-700">
                          <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-50 px-2 py-1 font-medium text-emerald-700 ring-1 ring-inset ring-emerald-200">
                            <span aria-hidden="true" className="size-1.5 rounded-full bg-emerald-600" />
                            {player.status}
                          </span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </section>

          <section aria-labelledby="game-details-title" className="rounded-md border border-neutral-200 bg-white p-4">
            <h2 id="game-details-title" className="text-sm font-semibold text-neutral-950">
              Game details
            </h2>
            <dl className="mt-4 grid gap-3 text-sm">
              <div>
                <dt className="text-xs font-medium uppercase text-neutral-500">Status</dt>
                <dd className="mt-1 text-neutral-950">{game.status}</dd>
              </div>
              <div>
                <dt className="text-xs font-medium uppercase text-neutral-500">Phase</dt>
                <dd className="mt-1 text-neutral-950">{game.current_phase ?? "Unassigned"}</dd>
              </div>
              <div>
                <dt className="text-xs font-medium uppercase text-neutral-500">Seed</dt>
                <dd className="mt-1 break-all text-neutral-950">{game.seed ?? "Generated by backend"}</dd>
              </div>
            </dl>
          </section>

          <section aria-labelledby="cutoffs-title" className="rounded-md border border-neutral-200 bg-white p-4">
            <h2 id="cutoffs-title" className="text-sm font-semibold text-neutral-950">
              Negotiation cutoffs
            </h2>
            <div className="mt-3 space-y-2 text-sm text-neutral-700">
              <p>Max rounds: {cutoffs.max_rounds ?? "not set"}</p>
              <p>Proposal limit/player: {cutoffs.max_proposals_per_player ?? "not set"}</p>
            </div>
          </section>
        </aside>
      </div>
    </main>
  );
}
