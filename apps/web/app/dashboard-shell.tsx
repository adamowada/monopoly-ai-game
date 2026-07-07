"use client";

import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  BadgeCheck,
  Brain,
  Blocks,
  Database,
  Gamepad2,
  RefreshCw,
  Server,
  Settings2,
  ShieldAlert,
} from "lucide-react";

import { GameSetupPanel } from "./game-setup";
import { RejectedActionAuditView } from "./rejected-action-audit";
import { Button } from "../components/ui/button";
import { HealthSnapshotSchema, type HealthSnapshot } from "../lib/api/health";
import type { RejectedActionRecord } from "../lib/api/rejected-actions";
import { cn } from "../lib/ui";

type DashboardShellProps = {
  initialHealth: HealthSnapshot;
  initialRejectedActions?: RejectedActionRecord[];
  title?: string;
};

const navigation = [
  { name: "Overview", href: "#overview", icon: Activity },
  { name: "Game setup", href: "#game-setup", icon: Gamepad2 },
  { name: "Table check", href: "#table-check", icon: BadgeCheck },
  { name: "Rulings", href: "#rulings", icon: ShieldAlert },
  { name: "Table areas", href: "#table-areas", icon: Blocks },
  { name: "House rules", href: "#game-setup", icon: Settings2 },
];

const workspaceRows = [
  {
    name: "Game table",
    status: "Ready",
    detail: "Created games open into the illustrated board with player tokens, legal actions, and live table state.",
    icon: Gamepad2,
  },
  {
    name: "AI notebook",
    status: "Active",
    detail: "AI players keep a private notebook for decisions, memory, dialogue, and rejected moves.",
    icon: Brain,
  },
  {
    name: "Rules authority",
    status: "Referee-checked",
    detail: "The local referee checks every move before the table state changes.",
    icon: ShieldAlert,
  },
];

async function fetchHealthSnapshot(): Promise<HealthSnapshot> {
  const response = await fetch("/api/backend-health", {
    cache: "no-store",
    headers: { accept: "application/json" },
  });
  const payload: unknown = await response.json();
  return HealthSnapshotSchema.parse(payload);
}

function formatCheckedAt(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleTimeString("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    timeZone: "UTC",
    timeZoneName: "short",
  });
}

function StatusBadge({
  tone,
  children,
}: Readonly<{
  tone: "neutral" | "success" | "warning" | "danger" | "info";
  children: React.ReactNode;
}>) {
  const tones = {
    neutral: "bg-neutral-100 text-neutral-700 ring-neutral-300 [&>svg]:fill-neutral-400",
    success: "bg-green-50 text-green-700 ring-green-200 [&>svg]:fill-green-500",
    warning: "bg-amber-50 text-amber-800 ring-amber-200 [&>svg]:fill-amber-500",
    danger: "bg-rose-50 text-rose-700 ring-rose-200 [&>svg]:fill-rose-500",
    info: "bg-sky-50 text-sky-700 ring-sky-200 [&>svg]:fill-sky-500",
  };

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset",
        tones[tone],
      )}
    >
      <svg viewBox="0 0 6 6" aria-hidden="true" className="size-1.5">
        <circle r={3} cx={3} cy={3} />
      </svg>
      {children}
    </span>
  );
}

function getTierRows(snapshot: HealthSnapshot) {
  const backendOnline = snapshot.state === "online";

  return [
    {
      tier: "Rules referee",
      status: backendOnline ? "ready" : "offline",
      tone: backendOnline ? "success" : "danger",
      stage: backendOnline ? "Move validation" : "Connection failed",
      environment: backendOnline ? "Local table" : snapshot.error,
      record: backendOnline ? "Ready for play" : "Not verified",
      icon: Server,
    },
    {
      tier: "Game board",
      status: "ready",
      tone: "info",
      stage: "Board and controls",
      environment: "local browser",
      record: "Ready for play",
      icon: Activity,
    },
    {
      tier: "Save data",
      status: backendOnline ? "ready" : "pending",
      tone: backendOnline ? "success" : "warning",
      stage: "Game persistence",
      environment: "local database",
      record: backendOnline ? snapshot.health.database : "awaiting referee",
      icon: Database,
    },
  ] as const;
}

export function DashboardShell({
  initialHealth,
  initialRejectedActions = [],
  title = "Monopoly 2.0 Game Table",
}: DashboardShellProps) {
  const healthQuery = useQuery({
    queryKey: ["backend-health"],
    queryFn: fetchHealthSnapshot,
    initialData: initialHealth,
    staleTime: 10_000,
  });
  const snapshot = healthQuery.data;
  const backendOnline = snapshot.state === "online";
  const tierRows = getTierRows(snapshot);

  return (
    <div className="min-h-screen bg-[var(--color-page)] text-neutral-950">
      <div className="lg:flex">
        <aside className="hidden border-r border-neutral-200 bg-white lg:fixed lg:inset-y-0 lg:flex lg:w-72 lg:flex-col">
          <div className="flex h-16 items-center gap-3 border-b border-neutral-200 px-6">
            <div className="flex size-9 items-center justify-center rounded-md bg-teal-700 text-white">
              <Gamepad2 aria-hidden="true" className="size-5" />
            </div>
            <div>
              <p className="text-sm font-semibold text-neutral-950">Monopoly 2.0</p>
              <p className="text-xs text-neutral-500">Local tabletop game</p>
            </div>
          </div>
          <nav aria-label="Table navigation" className="flex flex-1 flex-col gap-1 px-4 py-5">
            {navigation.map((item) => (
              <a
                key={`${item.name}-${item.href}`}
                href={item.href}
                className="group flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium text-neutral-700 hover:bg-neutral-100 hover:text-neutral-950 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal-700"
              >
                <item.icon aria-hidden="true" className="size-4 text-neutral-500 group-hover:text-teal-700" />
                {item.name}
              </a>
            ))}
          </nav>
        </aside>

        <div className="min-w-0 flex-1 lg:pl-72">
          <header className="border-b border-neutral-200 bg-white">
            <div className="mx-auto max-w-7xl px-4 py-4 sm:px-6 lg:px-8">
              <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
                <div>
                  <p className="text-xs font-semibold uppercase text-teal-700">
                    Local tabletop build
                  </p>
                  <h1 className="mt-1 text-2xl font-semibold tracking-normal text-neutral-950">
                    {title}
                  </h1>
                </div>
                <nav
                  aria-label="Table navigation"
                  className="grid grid-cols-2 gap-2 text-sm sm:grid-cols-3 lg:hidden"
                >
                  {navigation.map((item) => (
                    <a
                      key={`${item.name}-${item.href}`}
                      href={item.href}
                      className="flex items-center gap-2 rounded-md border border-neutral-200 bg-neutral-50 px-3 py-2 font-medium text-neutral-700 hover:border-teal-200 hover:bg-teal-50 hover:text-teal-800 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal-700"
                    >
                      <item.icon aria-hidden="true" className="size-4" />
                      {item.name}
                    </a>
                  ))}
                </nav>
              </div>
            </div>
          </header>

          <main>
            <section id="overview" aria-labelledby="overview-title" className="border-b border-neutral-200 bg-white">
              <div className="mx-auto grid max-w-7xl gap-6 px-4 py-6 sm:px-6 lg:grid-cols-[minmax(0,1fr)_minmax(280px,360px)] lg:px-8">
                <div>
                  <h2 id="overview-title" className="text-base font-semibold text-neutral-950">
                    Table status
                  </h2>
                  <p className="mt-2 max-w-3xl text-sm leading-6 text-neutral-600">
                    Start a local table, choose human or AI seats, then play from the illustrated
                    board with referee-checked moves, negotiations, contracts, and the AI notebook.
                  </p>
                </div>

                <div
                  role="status"
                  aria-label="Table connection"
                  aria-live="polite"
                  className="rounded-md border border-neutral-200 bg-neutral-50 p-4"
                >
                  <div className="flex items-start justify-between gap-4">
                    <div>
                      <p className="text-sm font-medium text-neutral-950">Table connection</p>
                      <div className="mt-2 flex flex-wrap items-center gap-2">
                        <StatusBadge tone={backendOnline ? "success" : "danger"}>
                          {backendOnline ? "Ready" : "Unavailable"}
                        </StatusBadge>
                        <span className="text-sm text-neutral-600">Rules referee</span>
                      </div>
                    </div>
                    <Button
                      aria-label="Refresh table connection"
                      disabled={healthQuery.isFetching}
                      onClick={() => {
                        void healthQuery.refetch();
                      }}
                    >
                      <RefreshCw
                        aria-hidden="true"
                        className={cn("size-4", healthQuery.isFetching && "animate-spin")}
                      />
                      {healthQuery.isFetching ? "Refreshing" : "Refresh"}
                    </Button>
                  </div>

                  <dl className="mt-4 grid grid-cols-1 gap-3 text-sm sm:grid-cols-2">
                    <div>
                      <dt className="text-xs font-medium uppercase text-neutral-500">Rules</dt>
                      <dd className="mt-1 text-neutral-950">
                        {backendOnline ? "Move validation ready" : "Unverified"}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-xs font-medium uppercase text-neutral-500">Mode</dt>
                      <dd className="mt-1 text-neutral-950">
                        {backendOnline ? "Local table" : "Offline"}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-xs font-medium uppercase text-neutral-500">Save data</dt>
                      <dd className="mt-1 text-neutral-950">
                        {backendOnline ? snapshot.health.database : "Not verified"}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-xs font-medium uppercase text-neutral-500">Checked</dt>
                      <dd className="mt-1 text-neutral-950">{formatCheckedAt(snapshot.checkedAt)}</dd>
                    </div>
                  </dl>
                </div>
              </div>
            </section>

            <GameSetupPanel />

            <section id="table-check" aria-labelledby="table-check-title" className="bg-[var(--color-page)]">
              <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
                <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
                  <div>
                    <h2 id="table-check-title" className="text-base font-semibold text-neutral-950">
                      Table check
                    </h2>
                    <p className="mt-2 text-sm text-neutral-600">
                      Fast local checks for the board, rules referee, and saved game state.
                    </p>
                  </div>
                </div>

                <div className="mt-5 overflow-hidden border-y border-neutral-200 bg-white">
                  <div className="overflow-x-auto">
                    <table className="min-w-full text-left text-sm">
                      <thead className="bg-neutral-50 text-xs uppercase text-neutral-500">
                        <tr>
                          <th scope="col" className="px-4 py-3 font-semibold sm:px-6">
                            Area
                          </th>
                          <th scope="col" className="px-4 py-3 font-semibold">
                            Status
                          </th>
                          <th scope="col" className="px-4 py-3 font-semibold">
                            Role
                          </th>
                          <th scope="col" className="px-4 py-3 font-semibold">
                            Mode
                          </th>
                          <th scope="col" className="px-4 py-3 font-semibold">
                            Record
                          </th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-neutral-200">
                        {tierRows.map((row) => (
                          <tr key={row.tier}>
                            <th scope="row" className="whitespace-nowrap px-4 py-4 font-medium text-neutral-950 sm:px-6">
                              <span className="flex items-center gap-2">
                                <row.icon aria-hidden="true" className="size-4 text-neutral-500" />
                                {row.tier}
                              </span>
                            </th>
                            <td className="px-4 py-4">
                              <StatusBadge tone={row.tone}>{row.status}</StatusBadge>
                            </td>
                            <td className="whitespace-nowrap px-4 py-4 text-neutral-700">{row.stage}</td>
                            <td className="max-w-xs px-4 py-4 text-neutral-700">{row.environment}</td>
                            <td className="whitespace-nowrap px-4 py-4 text-neutral-700">{row.record}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              </div>
            </section>

            <section
              id="rulings"
              aria-labelledby="rejected-actions-title"
              className="border-t border-neutral-200 bg-[var(--color-page)]"
            >
              <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
                <RejectedActionAuditView records={initialRejectedActions} />
              </div>
            </section>

            <section id="table-areas" aria-labelledby="workspace-title" className="border-t border-neutral-200 bg-white">
              <div className="mx-auto grid max-w-7xl gap-8 px-4 py-8 sm:px-6 lg:grid-cols-[minmax(0,1fr)_320px] lg:px-8">
                <div>
                  <h2 id="workspace-title" className="text-base font-semibold text-neutral-950">
                    Table areas
                  </h2>
                  <div className="mt-5 grid gap-3 md:grid-cols-3">
                    {workspaceRows.map((item) => (
                      <article key={item.name} className="rounded-md border border-neutral-200 bg-neutral-50 p-4">
                        <div className="flex items-start justify-between gap-3">
                          <item.icon aria-hidden="true" className="size-5 text-teal-700" />
                          <StatusBadge tone={item.status === "Referee-checked" ? "info" : "neutral"}>
                            {item.status}
                          </StatusBadge>
                        </div>
                        <h3 className="mt-4 text-sm font-semibold text-neutral-950">{item.name}</h3>
                        <p className="mt-2 text-sm leading-6 text-neutral-600">{item.detail}</p>
                      </article>
                    ))}
                  </div>
                </div>

                <aside className="rounded-md border border-neutral-200 bg-neutral-50 p-4">
                  <h3 className="text-sm font-semibold text-neutral-950">Local table rules</h3>
                  <p className="mt-2 text-sm leading-6 text-neutral-600">
                    Games run locally only. The rules referee checks each move while the board,
                    action controls, negotiations, contracts, and AI notebook stay visible.
                  </p>
                </aside>
              </div>
            </section>
          </main>
        </div>
      </div>
    </div>
  );
}
