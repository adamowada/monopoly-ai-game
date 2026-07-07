"use client";

import {
  AlertTriangle,
  Brain,
  ClipboardList,
  FileText,
  FolderOpen,
  Handshake,
  Home,
  Landmark,
  Loader2,
  LogOut,
  Menu,
  Save,
  ScrollText,
  TableProperties,
  UsersRound,
  X,
} from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Button } from "../components/ui/button";
import type { LegalAction } from "../lib/api/gameplay";

type SavedGameRecord = {
  id: string;
  label: string;
  status: string;
  updatedAt: string;
  savedAt: string;
};

type TableViewTarget = "game-log" | "properties" | "deals" | "contracts" | "ai-notebook";

type GameTableMenuProps = {
  bankruptcyAction?: LegalAction | null;
  bankruptcyDisabled?: boolean;
  isEnding?: boolean;
  message?: string | null;
  onDeclareBankruptcy?: (action: LegalAction) => void;
  onEndGame?: () => void;
  onLoadGame?: (gameId: string) => void;
  onSaveGame?: () => void;
  onSelectTableView?: (view: TableViewTarget) => void;
  onToggleLoadGames?: () => void;
  phase?: string;
  currentPlayerName?: string | null;
  savedGames?: SavedGameRecord[];
  showLoadGames?: boolean;
  status: string;
};

const navigationItems = [
  { label: "Board", href: "#game-board", icon: TableProperties },
  { label: "Current turn", href: "#current-turn", icon: ClipboardList },
  { label: "Player trays", href: "#player-trays", icon: UsersRound },
  { label: "Properties", href: "#properties", icon: Landmark, tableView: "properties" },
  { label: "Deals", href: "#deals", icon: Handshake, tableView: "deals" },
  { label: "Contracts", href: "#contracts", icon: FileText, tableView: "contracts" },
  { label: "AI notebook", href: "#ai-notebook", icon: Brain, tableView: "ai-notebook" },
  { label: "Game log", href: "#game-log", icon: ScrollText, tableView: "game-log" },
] as const;

function formatSavedStatus(status: string): string {
  return status.toLowerCase().replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function GameTableMenu({
  bankruptcyAction = null,
  bankruptcyDisabled = false,
  currentPlayerName,
  isEnding = false,
  message = null,
  onDeclareBankruptcy,
  onEndGame,
  onLoadGame,
  onSaveGame,
  onSelectTableView,
  onToggleLoadGames,
  phase,
  savedGames = [],
  showLoadGames = false,
  status,
}: GameTableMenuProps) {
  const [open, setOpen] = useState(false);
  const [confirmingBankruptcy, setConfirmingBankruptcy] = useState(false);
  const canDeclareBankruptcy = Boolean(bankruptcyAction && onDeclareBankruptcy);
  const bankruptcyPlayerName = currentPlayerName ?? "Current player";

  return (
    <div className="fixed right-4 top-4 z-[80]">
      <button
        aria-expanded={open}
        aria-label={open ? "Close game menu" : "Open game menu"}
        className="grid size-10 place-items-center rounded-md border border-neutral-300 bg-white text-neutral-900 shadow-md transition hover:bg-neutral-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal-700"
        onClick={() => setOpen((current) => !current)}
        type="button"
      >
        {open ? <X aria-hidden="true" className="size-5" /> : <Menu aria-hidden="true" className="size-5" />}
      </button>

      {open ? (
        <div
          aria-label="Game menu"
          className="absolute right-0 mt-2 max-h-[calc(100vh-5rem)] w-80 overflow-y-auto rounded-md border-2 border-[#2f2418]/50 bg-[#fff8e8] p-3 text-sm text-[#2f2418] shadow-[0_18px_40px_rgba(47,36,24,0.22)]"
          role="menu"
        >
          <div className="rounded border border-[#2f2418]/20 bg-white/70 px-3 py-2">
            <div className="flex flex-wrap gap-2 text-xs font-semibold text-[#6f604c]">
              <span>{status}</span>
              {phase ? <span>{phase}</span> : null}
              {currentPlayerName ? <span>{currentPlayerName}</span> : null}
            </div>
          </div>

          <nav aria-label="Game drawer navigation" className="mt-3 grid gap-1">
            {navigationItems.map((item) => (
              <a
                key={item.href}
                className="flex items-center gap-2 rounded border border-transparent px-2.5 py-2 font-semibold text-[#2f2418] transition hover:border-[#b99768] hover:bg-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#0f766e]"
                href={item.href}
                onClick={() => {
                  if ("tableView" in item) {
                    onSelectTableView?.(item.tableView);
                  }
                  setOpen(false);
                }}
                role="menuitem"
              >
                <item.icon aria-hidden="true" className="size-4 text-[#0f766e]" />
                {item.label}
              </a>
            ))}
          </nav>

          {onSaveGame && onToggleLoadGames && onEndGame ? (
            <div className="mt-3 grid gap-2 border-t border-[#2f2418]/15 pt-3">
              <Button onClick={onSaveGame} className="justify-start" role="menuitem" variant="secondary">
                <Save aria-hidden="true" className="size-4" />
                Save game
              </Button>
              <Button onClick={onToggleLoadGames} className="justify-start" role="menuitem" variant="secondary">
                <FolderOpen aria-hidden="true" className="size-4" />
                Load game
              </Button>
              <Button onClick={onEndGame} disabled={isEnding} className="justify-start" role="menuitem" variant="danger">
                {isEnding ? <Loader2 aria-hidden="true" className="size-4 animate-spin" /> : <LogOut aria-hidden="true" className="size-4" />}
                {isEnding ? "Ending..." : "End game"}
              </Button>
            </div>
          ) : null}

          {canDeclareBankruptcy ? (
            <div className="mt-3 grid gap-2 border-t border-[#2f2418]/15 pt-3">
              <Button
                aria-label="Declare bankruptcy"
                className="justify-start text-rose-800 hover:bg-rose-50"
                disabled={bankruptcyDisabled}
                onClick={() => {
                  setOpen(false);
                  setConfirmingBankruptcy(true);
                }}
                role="menuitem"
                variant="secondary"
              >
                <AlertTriangle aria-hidden="true" className="size-4" />
                Declare bankruptcy
              </Button>
            </div>
          ) : null}

          {message ? (
            <p aria-live="polite" className="mt-3 rounded border border-teal-200 bg-teal-50 px-3 py-2 text-xs font-semibold text-teal-800">
              {message}
            </p>
          ) : null}

          {showLoadGames ? (
            <div aria-label="Saved games" className="mt-3 grid gap-2" role="group">
              {savedGames.length > 0 ? (
                savedGames.map((savedGame) => (
                  <button
                    key={savedGame.id}
                    aria-label={`Open ${savedGame.label}`}
                    className="rounded border border-[#b99768]/60 bg-white px-3 py-2 text-left text-xs text-[#6f604c] transition hover:bg-[#fffbea] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#0f766e]"
                    onClick={() => onLoadGame?.(savedGame.id)}
                    role="menuitem"
                    type="button"
                  >
                    <span className="block font-black text-[#2f2418]">Open {savedGame.label}</span>
                    <span className="mt-0.5 block uppercase">{formatSavedStatus(savedGame.status)}</span>
                  </button>
                ))
              ) : (
                <p className="rounded border border-[#b99768]/60 bg-white px-3 py-2 text-xs text-[#6f604c]">
                  No saved games yet.
                </p>
              )}
            </div>
          ) : null}

          <Link
            className="mt-3 flex items-center gap-2 rounded border border-[#2f2418]/15 bg-white/70 px-2.5 py-2 font-semibold text-[#2f2418] transition hover:bg-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-inset focus-visible:outline-teal-700"
            href="/"
            role="menuitem"
          >
            <Home aria-hidden="true" className="size-4 text-[#0f766e]" />
            Setup
          </Link>
        </div>
      ) : null}

      {confirmingBankruptcy && bankruptcyAction ? (
        <div className="fixed inset-0 z-[90] grid place-items-center bg-neutral-950/55 px-4">
          <div
            aria-labelledby="confirm-bankruptcy-title"
            aria-modal="true"
            className="w-full max-w-md rounded-md border-2 border-rose-900 bg-[#fff8e8] p-4 text-[#2f2418] shadow-[0_24px_70px_rgba(0,0,0,0.35)]"
            role="dialog"
          >
            <div className="flex items-start gap-3">
              <span className="grid size-10 shrink-0 place-items-center rounded-full bg-rose-100 text-rose-800">
                <AlertTriangle aria-hidden="true" className="size-5" />
              </span>
              <div>
                <h2 id="confirm-bankruptcy-title" className="text-lg font-black">
                  Confirm bankruptcy
                </h2>
                <p className="mt-2 text-sm font-semibold leading-6 text-[#6f604c]">
                  {bankruptcyPlayerName} will give up and lose. The table will continue with the remaining players.
                </p>
              </div>
            </div>
            <div className="mt-4 flex flex-wrap justify-end gap-2">
              <Button onClick={() => setConfirmingBankruptcy(false)} variant="secondary">
                Cancel
              </Button>
              <Button
                disabled={bankruptcyDisabled}
                onClick={() => {
                  setConfirmingBankruptcy(false);
                  onDeclareBankruptcy?.(bankruptcyAction);
                }}
                variant="danger"
              >
                Confirm bankruptcy
              </Button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
