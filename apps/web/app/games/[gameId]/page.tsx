import { ArrowLeft } from "lucide-react";
import Link from "next/link";

import { Button } from "../../../components/ui/button";
import { readGame } from "../../../lib/api/games";
import { GameTableMenu } from "../../game-table-menu";
import { GamePlaySurface } from "../../game-play-surface";

export const dynamic = "force-dynamic";
export const revalidate = 0;

type GamePageProps = {
  params: Promise<{
    gameId: string;
  }>;
};

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

  return (
    <main className="min-h-screen bg-[var(--color-page)] text-neutral-950">
      <GameTableMenu gameId={game.id} status={game.status} />

      <GamePlaySurface gameId={game.id} initialGame={game} />
    </main>
  );
}
