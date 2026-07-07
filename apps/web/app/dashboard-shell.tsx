"use client";

import { useQuery } from "@tanstack/react-query";
import { BadgeCheck, Gamepad2, RefreshCw, ShieldAlert, UsersRound } from "lucide-react";
import { useState } from "react";

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

const prepNavigation = [
  { name: "Setup", href: "#game-setup", icon: UsersRound },
  { name: "Connection details", href: "#connection-details", icon: BadgeCheck },
] as const;

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

function readinessLabel(snapshot: HealthSnapshot): string {
  return snapshot.state === "online" ? "Ready" : "Unavailable";
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
  const [showConnectionDetails, setShowConnectionDetails] = useState(false);
  const [showRuleRulings, setShowRuleRulings] = useState(false);

  return (
    <div className="min-h-screen bg-[#173c45] text-[#2f2418]">
      <header className="border-b-4 border-[#2f2418] bg-[#fff8e8]">
        <div className="mx-auto flex max-w-7xl flex-col gap-4 px-4 py-5 sm:px-6 lg:flex-row lg:items-center lg:justify-between lg:px-8">
          <div className="flex items-center gap-3">
            <span className="grid size-11 place-items-center rounded-md border-2 border-[#2f2418] bg-[#d7a84c] text-[#173c45] shadow-[0_3px_0_rgba(47,36,24,0.25)]">
              <Gamepad2 aria-hidden="true" className="size-6" />
            </span>
            <div>
              <p className="text-xs font-black uppercase text-[#6f604c]">Local tabletop build</p>
              <h1 className="text-2xl font-black tracking-normal text-[#2f2418]">{title}</h1>
            </div>
          </div>
          <nav aria-label="Game prep navigation" className="flex flex-wrap gap-2 text-sm">
            {prepNavigation.map((item) => (
              <a
                key={item.name}
                className="inline-flex items-center gap-2 rounded-sm border-2 border-[#2f2418]/25 bg-white/70 px-3 py-2 font-black text-[#2f2418] hover:bg-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#0f766e]"
                href={item.href}
              >
                <item.icon aria-hidden="true" className="size-4 text-[#0f766e]" />
                {item.name}
              </a>
            ))}
          </nav>
        </div>
      </header>

      <main>
        <section className="bg-[#173c45] text-[#fff8e8]" aria-labelledby="table-prep-title">
          <div className="mx-auto grid max-w-7xl gap-5 px-4 py-6 sm:px-6 lg:grid-cols-[minmax(0,1fr)_320px] lg:px-8">
            <div>
              <h2 id="table-prep-title" className="text-xl font-black tracking-normal">
                Set the seats, then open the board.
              </h2>
              <p className="mt-2 max-w-3xl text-sm font-semibold leading-6 text-[#f7e6ad]">
                Pick tokens, colors, human or AI seats, and the local negotiation limits before the
                game table opens.
              </p>
            </div>

            <div
              role="status"
              aria-label="Referee readiness"
              aria-live="polite"
              className="rounded-md border-2 border-[#f7d977] bg-[#fff8e8] p-3 text-[#2f2418] shadow-[0_5px_0_rgba(0,0,0,0.2)]"
            >
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="text-xs font-black uppercase text-[#6f604c]">Referee readiness</p>
                  <p className="mt-1 text-lg font-black">{readinessLabel(snapshot)}</p>
                  <p className="mt-1 text-xs font-semibold text-[#6f604c]">
                    {backendOnline ? "Local referee" : "Connection unavailable"}
                  </p>
                </div>
                <Button
                  aria-label="Refresh referee readiness"
                  disabled={healthQuery.isFetching}
                  onClick={() => {
                    void healthQuery.refetch();
                  }}
                  variant="secondary"
                >
                  <RefreshCw
                    aria-hidden="true"
                    className={cn("size-4", healthQuery.isFetching && "animate-spin")}
                  />
                </Button>
              </div>
            </div>
          </div>
        </section>

        <GameSetupPanel />

        <section className="bg-[#eaf3d7]" aria-label="Troubleshooting">
          <div className="mx-auto grid max-w-7xl gap-3 px-4 py-5 sm:px-6 lg:px-8">
            <div className="rounded-md border-2 border-[#2f2418]/25 bg-[#fff8e8] p-3">
              <button
                id="connection-details"
                aria-expanded={showConnectionDetails}
                className="text-sm font-black text-[#2f2418]"
                onClick={() => setShowConnectionDetails((current) => !current)}
                type="button"
              >
                Connection details
              </button>
              {showConnectionDetails ? (
                <div className="mt-4 overflow-x-auto">
                  <table className="min-w-full text-left text-sm">
                    <thead className="text-xs uppercase text-[#6f604c]">
                      <tr>
                        <th scope="col" className="px-3 py-2 font-black">
                          Area
                        </th>
                        <th scope="col" className="px-3 py-2 font-black">
                          Status
                        </th>
                        <th scope="col" className="px-3 py-2 font-black">
                          Role
                        </th>
                        <th scope="col" className="px-3 py-2 font-black">
                          Mode
                        </th>
                        <th scope="col" className="px-3 py-2 font-black">
                          Checked
                        </th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-[#b99768]/40">
                      <tr>
                        <th scope="row" className="px-3 py-3 font-black">
                          Rules referee
                        </th>
                        <td className="px-3 py-3">{backendOnline ? "ready" : "offline"}</td>
                        <td className="px-3 py-3">{backendOnline ? "Move validation" : "Connection failed"}</td>
                        <td className="px-3 py-3">{backendOnline ? "Local table" : snapshot.error}</td>
                        <td className="px-3 py-3">{formatCheckedAt(snapshot.checkedAt)}</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              ) : null}
            </div>

            {initialRejectedActions.length > 0 ? (
              <div className="rounded-md border-2 border-[#2f2418]/25 bg-[#fff8e8] p-3">
                <button
                  aria-expanded={showRuleRulings}
                  className="flex items-center gap-2 text-sm font-black text-[#2f2418]"
                  onClick={() => setShowRuleRulings((current) => !current)}
                  type="button"
                >
                  <ShieldAlert aria-hidden="true" className="size-4 text-[#9b2f18]" />
                  Rule rulings
                </button>
                {showRuleRulings ? (
                  <div className="mt-4">
                    <RejectedActionAuditView records={initialRejectedActions} />
                  </div>
                ) : null}
              </div>
            ) : null}
          </div>
        </section>
      </main>
    </div>
  );
}
