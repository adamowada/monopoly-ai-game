"use client";

import { Menu, X } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

type GameTableMenuProps = {
  gameId: string;
  status: string;
};

export function GameTableMenu({ gameId, status }: GameTableMenuProps) {
  const [open, setOpen] = useState(false);

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
          className="absolute right-0 mt-2 w-64 overflow-hidden rounded-md border border-neutral-200 bg-white text-sm text-neutral-900 shadow-[0_18px_40px_rgba(47,36,24,0.18)]"
          role="menu"
        >
          <div className="border-b border-neutral-200 px-3 py-2">
            <p className="text-xs font-semibold uppercase text-neutral-500">Game table</p>
            <p className="mt-1 truncate font-semibold text-neutral-950">{gameId}</p>
            <p className="mt-0.5 text-xs text-neutral-600">{status}</p>
          </div>
          <Link
            className="flex items-center px-3 py-2 font-medium text-neutral-800 transition hover:bg-neutral-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-inset focus-visible:outline-teal-700"
            href="/"
            role="menuitem"
          >
            Setup
          </Link>
        </div>
      ) : null}
    </div>
  );
}
