"use client";

import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  BadgeCheck,
  Blocks,
  Database,
  FlaskConical,
  Gamepad2,
  RefreshCw,
  Server,
  Settings2,
  ShieldAlert,
} from "lucide-react";

import { Button } from "../components/ui/button";
import { HealthSnapshotSchema, type HealthSnapshot } from "../lib/api/health";
import { cn } from "../lib/ui";

type DashboardShellProps = {
  initialHealth: HealthSnapshot;
  title?: string;
};

const navigation = [
  { name: "Overview", href: "#overview", icon: Activity },
  { name: "Tier health", href: "#tier-health", icon: BadgeCheck },
  { name: "Workspace", href: "#workspace", icon: Blocks },
  { name: "Setup filters", href: "#setup-filters", icon: Settings2 },
];

const workspaceRows = [
  {
    name: "Game table",
    status: "Planned",
    detail: "Reserved for the playable board and legal action controls after rules endpoints exist.",
    icon: Gamepad2,
  },
  {
    name: "Research audit",
    status: "Planned",
    detail: "Reserved for AI decisions, rejected actions, memory, and local audit trails in later phases.",
    icon: FlaskConical,
  },
  {
    name: "Rules authority",
    status: "Backend-owned",
    detail: "The frontend displays backend state only; legality remains outside the web tier.",
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
      tier: "FastAPI service",
      status: backendOnline ? "ok" : "unavailable",
      tone: backendOnline ? "success" : "danger",
      stage: backendOnline ? snapshot.health.stage : "health fetch failed",
      environment: backendOnline ? snapshot.health.environment : snapshot.error,
      record: backendOnline ? snapshot.health.database : "not verified",
      icon: Server,
    },
    {
      tier: "Next.js app",
      status: "ready",
      tone: "info",
      stage: "Stage 1.4 shell",
      environment: "local browser",
      record: "App Router",
      icon: Activity,
    },
    {
      tier: "Postgres",
      status: backendOnline ? "configured" : "pending verification",
      tone: backendOnline ? "success" : "warning",
      stage: "compose service",
      environment: "local stack",
      record: backendOnline ? snapshot.health.database : "awaiting API health",
      icon: Database,
    },
  ] as const;
}

export function DashboardShell({ initialHealth, title = "Local Game Research Console" }: DashboardShellProps) {
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
              <p className="text-sm font-semibold text-neutral-950">Monopoly AI</p>
              <p className="text-xs text-neutral-500">Local research stack</p>
            </div>
          </div>
          <nav aria-label="Stack navigation" className="flex flex-1 flex-col gap-1 px-4 py-5">
            {navigation.map((item) => (
              <a
                key={item.href}
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
                  <p className="text-xs font-semibold uppercase text-teal-700">Phase 1 Stage 1.4</p>
                  <h1 className="mt-1 text-2xl font-semibold tracking-normal text-neutral-950">
                    {title}
                  </h1>
                </div>
                <nav
                  aria-label="Stack navigation"
                  className="grid grid-cols-2 gap-2 text-sm sm:grid-cols-4 lg:hidden"
                >
                  {navigation.map((item) => (
                    <a
                      key={item.href}
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
                    Stack status
                  </h2>
                  <p className="mt-2 max-w-3xl text-sm leading-6 text-neutral-600">
                    Monitor the local frontend, FastAPI service, and database readiness before game
                    rules, AI runtime, negotiations, and generated API contracts are introduced.
                  </p>
                </div>

                <div
                  role="status"
                  aria-label="Backend health"
                  aria-live="polite"
                  className="rounded-md border border-neutral-200 bg-neutral-50 p-4"
                >
                  <div className="flex items-start justify-between gap-4">
                    <div>
                      <p className="text-sm font-medium text-neutral-950">Backend health</p>
                      <div className="mt-2 flex flex-wrap items-center gap-2">
                        <StatusBadge tone={backendOnline ? "success" : "danger"}>
                          {backendOnline ? snapshot.health.status : "unavailable"}
                        </StatusBadge>
                        <span className="text-sm text-neutral-600">
                          {backendOnline ? snapshot.health.service : "api"}
                        </span>
                      </div>
                    </div>
                    <Button
                      aria-label="Refresh backend health"
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
                      <dt className="text-xs font-medium uppercase text-neutral-500">Stage</dt>
                      <dd className="mt-1 text-neutral-950">
                        {backendOnline ? snapshot.health.stage : "unverified"}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-xs font-medium uppercase text-neutral-500">Environment</dt>
                      <dd className="mt-1 text-neutral-950">
                        {backendOnline ? snapshot.health.environment : "offline"}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-xs font-medium uppercase text-neutral-500">Database</dt>
                      <dd className="mt-1 text-neutral-950">
                        {backendOnline ? snapshot.health.database : "not verified"}
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

            <section id="tier-health" aria-labelledby="tier-health-title" className="bg-[var(--color-page)]">
              <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
                <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
                  <div>
                    <h2 id="tier-health-title" className="text-base font-semibold text-neutral-950">
                      Tier health records
                    </h2>
                    <p className="mt-2 text-sm text-neutral-600">
                      Compact records for comparing current local stack readiness.
                    </p>
                  </div>
                </div>

                <div className="mt-5 overflow-hidden border-y border-neutral-200 bg-white">
                  <div className="overflow-x-auto">
                    <table className="min-w-full text-left text-sm">
                      <thead className="bg-neutral-50 text-xs uppercase text-neutral-500">
                        <tr>
                          <th scope="col" className="px-4 py-3 font-semibold sm:px-6">
                            Tier
                          </th>
                          <th scope="col" className="px-4 py-3 font-semibold">
                            Status
                          </th>
                          <th scope="col" className="px-4 py-3 font-semibold">
                            Stage
                          </th>
                          <th scope="col" className="px-4 py-3 font-semibold">
                            Environment
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

            <section id="workspace" aria-labelledby="workspace-title" className="border-t border-neutral-200 bg-white">
              <div className="mx-auto grid max-w-7xl gap-8 px-4 py-8 sm:px-6 lg:grid-cols-[minmax(0,1fr)_320px] lg:px-8">
                <div>
                  <h2 id="workspace-title" className="text-base font-semibold text-neutral-950">
                    Future workspace regions
                  </h2>
                  <div className="mt-5 grid gap-3 md:grid-cols-3">
                    {workspaceRows.map((item) => (
                      <article key={item.name} className="rounded-md border border-neutral-200 bg-neutral-50 p-4">
                        <div className="flex items-start justify-between gap-3">
                          <item.icon aria-hidden="true" className="size-5 text-teal-700" />
                          <StatusBadge tone={item.status === "Backend-owned" ? "info" : "neutral"}>
                            {item.status}
                          </StatusBadge>
                        </div>
                        <h3 className="mt-4 text-sm font-semibold text-neutral-950">{item.name}</h3>
                        <p className="mt-2 text-sm leading-6 text-neutral-600">{item.detail}</p>
                      </article>
                    ))}
                  </div>
                </div>

                <form id="setup-filters" aria-labelledby="setup-filters-title" className="rounded-md border border-neutral-200 bg-neutral-50 p-4">
                  <fieldset disabled aria-describedby="setup-filters-note" className="space-y-4">
                    <div>
                      <legend id="setup-filters-title" className="text-sm font-semibold text-neutral-950">
                        Setup filters
                      </legend>
                      <p id="setup-filters-note" className="mt-2 text-sm leading-6 text-neutral-600">
                        Settings controls are intentionally disabled until setup and filter endpoints exist.
                      </p>
                    </div>
                    <label className="grid gap-1 text-sm font-medium text-neutral-700">
                      Player count
                      <select className="rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm text-neutral-500 disabled:cursor-not-allowed disabled:bg-neutral-100">
                        <option>2-5 players later</option>
                      </select>
                    </label>
                    <label className="grid gap-1 text-sm font-medium text-neutral-700">
                      Stack view
                      <select className="rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm text-neutral-500 disabled:cursor-not-allowed disabled:bg-neutral-100">
                        <option>Health records only</option>
                      </select>
                    </label>
                  </fieldset>
                </form>
              </div>
            </section>
          </main>
        </div>
      </div>
    </div>
  );
}
