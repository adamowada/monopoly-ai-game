import { ArrowLeft, Bot, UserRound } from "lucide-react";
import Link from "next/link";

import { Button } from "../../../components/ui/button";
import { readGame, type GameMetadata } from "../../../lib/api/games";

export const dynamic = "force-dynamic";
export const revalidate = 0;

type GamePageProps = {
  params: Promise<{
    gameId: string;
  }>;
};

type PlayerColorSetting = {
  seat_order: number;
  color: string;
};

type NegotiationCutoffs = {
  max_rounds?: number;
  max_proposals_per_player?: number;
};

function getPlayerColor(game: GameMetadata, seatOrder: number): string {
  const settings = game.settings;
  const colors = settings.player_colors;
  if (!Array.isArray(colors)) {
    return "#525252";
  }
  const match = colors.find((entry): entry is PlayerColorSetting => {
    if (entry === null || typeof entry !== "object") {
      return false;
    }
    const candidate = entry as Partial<PlayerColorSetting>;
    return candidate.seat_order === seatOrder && typeof candidate.color === "string";
  });
  return match?.color ?? "#525252";
}

function getNegotiationCutoffs(game: GameMetadata): NegotiationCutoffs {
  const cutoffs = game.settings.negotiation_cutoffs;
  if (cutoffs === null || typeof cutoffs !== "object" || Array.isArray(cutoffs)) {
    return {};
  }
  return cutoffs as NegotiationCutoffs;
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
            <p className="text-xs font-semibold uppercase text-teal-700">Board shell</p>
            <h1 className="mt-1 text-2xl font-semibold tracking-normal">Game board {game.id}</h1>
            <p className="mt-2 text-sm text-neutral-600">
              Stage 5.1 placeholder table view. SVG board art and turn controls start in later stages.
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
        <section aria-labelledby="players-title" className="overflow-hidden border-y border-neutral-200 bg-white">
          <div className="border-b border-neutral-200 px-4 py-3 sm:px-6">
            <h2 id="players-title" className="text-base font-semibold text-neutral-950">
              Players
            </h2>
            <p className="mt-1 text-sm text-neutral-600">Seat order and setup colors persisted in game settings.</p>
          </div>
          <div className="overflow-x-auto">
            <table className="min-w-full text-left text-sm">
              <thead className="bg-neutral-50 text-xs uppercase text-neutral-500">
                <tr>
                  <th scope="col" className="px-4 py-3 font-semibold sm:px-6">
                    Seat
                  </th>
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Player
                  </th>
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Type
                  </th>
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Color
                  </th>
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Status
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-neutral-200">
                {game.players.map((player) => {
                  const color = getPlayerColor(game, player.seat_order);
                  return (
                    <tr key={player.id}>
                      <td className="whitespace-nowrap px-4 py-4 font-medium text-neutral-950 sm:px-6">
                        {player.seat_order + 1}
                      </td>
                      <td className="whitespace-nowrap px-4 py-4 font-medium text-neutral-950">{player.name}</td>
                      <td className="whitespace-nowrap px-4 py-4 text-neutral-700">
                        <span className="inline-flex items-center gap-2">
                          {player.controller_type === "ai" ? (
                            <Bot aria-hidden="true" className="size-4 text-purple-700" />
                          ) : (
                            <UserRound aria-hidden="true" className="size-4 text-teal-700" />
                          )}
                          {player.controller_type}
                        </span>
                      </td>
                      <td className="whitespace-nowrap px-4 py-4 text-neutral-700">
                        <span className="inline-flex items-center gap-2">
                          <span
                            aria-hidden="true"
                            className="size-4 rounded-full border border-neutral-300"
                            style={{ backgroundColor: color }}
                          />
                          {color}
                        </span>
                      </td>
                      <td className="whitespace-nowrap px-4 py-4 text-neutral-700">{player.status}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>

        <aside className="grid content-start gap-4">
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
