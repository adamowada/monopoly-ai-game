"use client";

import { Gamepad2, ShieldAlert } from "lucide-react";
import { useState } from "react";

import { GameSetupPanel } from "./game-setup";
import { RejectedActionAuditView } from "./rejected-action-audit";
import type { RejectedActionRecord } from "../lib/api/rejected-actions";

type DashboardShellProps = {
  initialRejectedActions?: RejectedActionRecord[];
  title?: string;
};

export function DashboardShell({
  initialRejectedActions = [],
  title = "Monopoly 2.0 Game Table",
}: DashboardShellProps) {
  const [showRuleRulings, setShowRuleRulings] = useState(false);

  return (
    <div className="min-h-screen bg-[#eaf3d7] text-[#2f2418]">
      <header className="border-b-4 border-[#2f2418] bg-[#fff8e8]">
        <div className="mx-auto flex max-w-7xl flex-col gap-4 px-4 py-5 sm:px-6 lg:flex-row lg:items-center lg:justify-between lg:px-8">
          <div className="flex items-center gap-3">
            <span className="grid size-11 place-items-center rounded-md border-2 border-[#2f2418] bg-[#d7a84c] text-[#173c45] shadow-[0_3px_0_rgba(47,36,24,0.25)]">
              <Gamepad2 aria-hidden="true" className="size-6" />
            </span>
            <div>
              <h1 className="text-2xl font-black tracking-normal text-[#2f2418]">{title}</h1>
            </div>
          </div>
        </div>
      </header>

      <main className="bg-[#eaf3d7]">
        <GameSetupPanel />

        {initialRejectedActions.length > 0 ? (
          <section className="bg-[#eaf3d7]" aria-label="Troubleshooting">
            <div className="mx-auto grid max-w-7xl gap-3 px-4 py-5 sm:px-6 lg:px-8">
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
            </div>
          </section>
        ) : null}
      </main>
    </div>
  );
}
