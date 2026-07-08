"use client";

import { Bot, Gavel, HandCoins, Loader2, LogOut, Trophy } from "lucide-react";
import { useMemo, useState } from "react";
import { PROPERTIES, PROPERTIES_BY_ID, type StaticDataProperty } from "@monopoly-ai-game/schemas";

import { Button } from "../components/ui/button";
import type { AcceptedEvent, GameStateResponse, LegalAction } from "../lib/api/gameplay";
import type { GameMetadata } from "../lib/api/games";

export const AUCTION_ACTION_TYPES = new Set(["START_AUCTION", "BID_AUCTION", "PASS_AUCTION"]);

type AuctionStateView = {
  property_id: string;
  high_bidder_id: string | null;
  high_bid_amount: number | null;
  passed_player_ids: string[];
};

type AuctionPanelProps = {
  game: GameMetadata;
  snapshot: GameStateResponse | undefined;
  legalActions: LegalAction[];
  events: AcceptedEvent[];
  controlsDisabled: boolean;
  isActionDisabled?: (action: LegalAction) => boolean;
  activeAiPlayerId?: string | null;
  aiStepDisabled?: boolean;
  aiStepPending?: boolean;
  onStepAiBidder?: (playerId: string) => void;
  pendingActionType: string | null;
  onSubmit: (action: LegalAction) => void;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function readString(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function readNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function readStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.filter((entry): entry is string => typeof entry === "string" && entry.length > 0);
}

function formatMoney(value: number | null): string {
  return value === null ? "No bids yet" : `$${value.toLocaleString("en-US")}`;
}

function playerName(game: GameMetadata, playerId: string | null): string {
  if (!playerId) {
    return "None";
  }
  return game.players.find((player) => player.id === playerId)?.name ?? playerId;
}

function propertyById(propertyId: string | null): StaticDataProperty | null {
  if (!propertyId) {
    return null;
  }
  const byId = (PROPERTIES_BY_ID as Readonly<Record<string, StaticDataProperty | undefined>>)[propertyId];
  return byId ?? PROPERTIES.find((property) => property.id === propertyId) ?? null;
}

function propertyName(propertyId: string | null): string {
  return propertyById(propertyId)?.name ?? propertyId ?? "No property selected";
}

export function readActiveAuction(snapshot: GameStateResponse | undefined): AuctionStateView | null {
  const activeAuction = snapshot?.state.active_auction;
  if (!isRecord(activeAuction)) {
    return null;
  }

  const propertyId = readString(activeAuction.property_id);
  if (!propertyId) {
    return null;
  }

  return {
    property_id: propertyId,
    high_bidder_id: readString(activeAuction.high_bidder_id),
    high_bid_amount: readNumber(activeAuction.high_bid_amount),
    passed_player_ids: readStringArray(activeAuction.passed_player_ids),
  };
}

export function isAuctionAction(action: LegalAction): boolean {
  return AUCTION_ACTION_TYPES.has(action.type);
}

function payloadPropertyId(action: LegalAction): string | null {
  return isRecord(action.payload) ? readString(action.payload.property_id) : null;
}

function legalActionFor(
  legalActions: LegalAction[],
  type: "START_AUCTION" | "BID_AUCTION" | "PASS_AUCTION",
  propertyId: string | null,
  actorId?: string,
): LegalAction | null {
  return (
    legalActions.find((action) => {
      if (action.type !== type) {
        return false;
      }
      if (actorId && action.actor_id !== actorId) {
        return false;
      }
      const actionPropertyId = payloadPropertyId(action);
      return !propertyId || !actionPropertyId || actionPropertyId === propertyId;
    }) ?? null
  );
}

function bidMinimumFromSchema(action: LegalAction): number | null {
  const properties = isRecord(action.schema.properties) ? action.schema.properties : null;
  const amount = properties && isRecord(properties.amount) ? properties.amount : null;
  return amount ? readNumber(amount.minimum) : null;
}

function minimumBidAmount(action: LegalAction, auction: AuctionStateView): number {
  const schemaMinimum = bidMinimumFromSchema(action);
  if (schemaMinimum !== null) {
    return schemaMinimum;
  }
  return auction.high_bid_amount === null ? 1 : auction.high_bid_amount + 1;
}

function defaultBidAmount(action: LegalAction, auction: AuctionStateView): number {
  const payloadAmount = isRecord(action.payload) ? readNumber(action.payload.amount) : null;
  return payloadAmount ?? minimumBidAmount(action, auction);
}

function concreteBidAction(action: LegalAction, auction: AuctionStateView, amount = defaultBidAmount(action, auction)): LegalAction {
  return {
    ...action,
    payload: {
      ...action.payload,
      property_id: auction.property_id,
      amount,
    },
  };
}

function latestAuctionResultEvent(events: AcceptedEvent[]): AcceptedEvent | null {
  const resultEvents = events.filter((event) => event.event_type === "AUCTION_RESULT");
  return [...resultEvents].sort((left, right) => right.sequence - left.sequence)[0] ?? null;
}

function auctionResultText(game: GameMetadata, event: AcceptedEvent): string {
  if (event.event_type === "AUCTION_RESULT") {
    const propertyId = readString(event.payload.property_id);
    const winnerId = readString(event.payload.winner_id);
    const winningBid = readNumber(event.payload.winning_bid);
    const winner = playerName(game, winnerId);
    const bidText = winningBid === null ? "an undisclosed bid" : formatMoney(winningBid);
    return `Winner ${winner}. ${winner} won ${propertyName(propertyId)} for ${bidText}.`;
  }

  return "";
}

function playerAuctionStatus(playerId: string, auction: AuctionStateView): string {
  if (auction.passed_player_ids.includes(playerId)) {
    return "Passed";
  }
  if (auction.high_bidder_id === playerId) {
    return "High bidder";
  }
  return "Remaining bidder";
}

function AuctionActionButton({
  action,
  label,
  icon: Icon,
  disabled,
  pendingActionType,
  onSubmit,
}: Readonly<{
  action: LegalAction;
  label: "Start auction" | "Bid" | "Pass";
  icon: typeof Gavel;
  disabled: boolean;
  pendingActionType: string | null;
  onSubmit: (action: LegalAction) => void;
}>) {
  const isSubmitting = pendingActionType === action.type;
  return (
    <Button
      onClick={() => onSubmit(action)}
      disabled={disabled}
      className="min-h-9 justify-start px-2.5 py-1.5 text-xs"
      variant={label === "Pass" ? "dark" : label === "Bid" ? "warning" : "primary"}
    >
      {isSubmitting ? (
        <Loader2 aria-hidden="true" className="size-3.5 animate-spin" />
      ) : (
        <Icon aria-hidden="true" className="size-3.5" />
      )}
      {isSubmitting ? "Submitting..." : label}
    </Button>
  );
}

function AuctionAiStepButton({
  disabled,
  isPending,
  onStep,
}: Readonly<{
  disabled: boolean;
  isPending: boolean;
  onStep: () => void;
}>) {
  return (
    <Button
      onClick={onStep}
      disabled={disabled}
      className="min-h-9 justify-start px-2.5 py-1.5 text-xs"
      variant="ai"
    >
      {isPending ? (
        <Loader2 aria-hidden="true" className="size-3.5 animate-spin" />
      ) : (
        <Bot aria-hidden="true" className="size-3.5" />
      )}
      Step AI
    </Button>
  );
}

function AuctionBidControl({
  action,
  auction,
  controlsDisabled,
  disabled,
  pendingActionType,
  playerName,
  onSubmit,
}: Readonly<{
  action: LegalAction;
  auction: AuctionStateView;
  controlsDisabled: boolean;
  disabled: boolean;
  pendingActionType: string | null;
  playerName: string;
  onSubmit: (action: LegalAction) => void;
}>) {
  const minimumBid = minimumBidAmount(action, auction);
  const initialBid = defaultBidAmount(action, auction);
  const [bidAmount, setBidAmount] = useState(String(initialBid));
  const parsedBid = Number.parseInt(bidAmount, 10);
  const submittedBid = Number.isFinite(parsedBid) ? Math.max(parsedBid, minimumBid) : minimumBid;
  const concreteBid = concreteBidAction(action, auction, submittedBid);

  return (
    <div className="flex flex-wrap items-center gap-2">
      <label className="grid gap-1 text-xs font-medium text-neutral-700">
        <span>{playerName} bid amount</span>
        <input
          aria-label={`${playerName} bid amount`}
          className="h-9 w-24 rounded-md border border-neutral-300 bg-white px-2 text-sm font-semibold text-neutral-950 outline-none focus:border-amber-700 focus:ring-2 focus:ring-amber-700/20 disabled:bg-neutral-100 disabled:text-neutral-500"
          disabled={controlsDisabled || disabled}
          min={minimumBid}
          onChange={(event) => setBidAmount(event.target.value)}
          step={1}
          type="number"
          value={bidAmount}
        />
      </label>
      <AuctionActionButton
        action={concreteBid}
        disabled={controlsDisabled || disabled}
        icon={HandCoins}
        label="Bid"
        onSubmit={onSubmit}
        pendingActionType={pendingActionType}
      />
    </div>
  );
}

export function AuctionPanel({
  game,
  snapshot,
  legalActions,
  events,
  controlsDisabled,
  isActionDisabled = () => false,
  activeAiPlayerId = null,
  aiStepDisabled = false,
  aiStepPending = false,
  onStepAiBidder,
  pendingActionType,
  onSubmit,
}: AuctionPanelProps) {
  const auction = useMemo(() => readActiveAuction(snapshot), [snapshot]);
  const startAuctionAction = legalActionFor(legalActions, "START_AUCTION", null);
  const firstAuctionAction = startAuctionAction ?? legalActions.find(isAuctionAction) ?? null;
  const displayedPropertyId = auction?.property_id ?? (firstAuctionAction ? payloadPropertyId(firstAuctionAction) : null);
  const resultEvent = useMemo(() => latestAuctionResultEvent(events), [events]);
  const passedPlayerIds = new Set(auction?.passed_player_ids ?? []);
  const remainingBidders = auction ? game.players.filter((player) => !passedPlayerIds.has(player.id)) : [];

  return (
    <section aria-label="Auction" className="rounded-md border border-neutral-200 bg-white p-4 shadow-sm">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <div className="flex items-center gap-2">
            <Gavel aria-hidden="true" className="size-4 text-teal-700" />
            <h2 className="text-sm font-semibold text-neutral-950">Auction</h2>
          </div>
        </div>
        <span className="inline-flex w-fit items-center gap-1.5 rounded-full bg-neutral-100 px-2 py-1 text-xs font-medium text-neutral-600">
          {legalActions.filter(isAuctionAction).length} auction actions
        </span>
      </div>

      <div className="mt-4 grid gap-3">
        <div className="rounded-md border border-neutral-200 bg-neutral-50 p-3">
          <dl className="grid gap-3 text-sm sm:grid-cols-2">
            <div>
              <dt className="text-xs font-medium uppercase text-neutral-500">Auction state</dt>
              <dd className="mt-1 font-semibold text-neutral-950">{auction ? "Active" : "No active auction"}</dd>
            </div>
            <div>
              <dt className="text-xs font-medium uppercase text-neutral-500">Property</dt>
              <dd className="mt-1 font-semibold text-neutral-950">{propertyName(displayedPropertyId)}</dd>
            </div>
            <div>
              <dt className="text-xs font-medium uppercase text-neutral-500">Current high bid</dt>
              <dd className="mt-1 font-semibold text-neutral-950">{formatMoney(auction?.high_bid_amount ?? null)}</dd>
            </div>
            <div>
              <dt className="text-xs font-medium uppercase text-neutral-500">Current high bidder</dt>
              <dd className="mt-1 font-semibold text-neutral-950">{playerName(game, auction?.high_bidder_id ?? null)}</dd>
            </div>
            <div>
              <dt className="text-xs font-medium uppercase text-neutral-500">Remaining bidders</dt>
              <dd className="mt-1 text-neutral-800">
                {auction
                  ? remainingBidders.map((player) => player.name).join(", ") || "None"
                  : "Auction not started"}
              </dd>
            </div>
            <div>
              <dt className="text-xs font-medium uppercase text-neutral-500">Passed players</dt>
              <dd className="mt-1 text-neutral-800">
                {auction?.passed_player_ids.length
                  ? auction.passed_player_ids.map((playerId) => playerName(game, playerId)).join(", ")
                  : "None"}
              </dd>
            </div>
          </dl>

          {!auction && startAuctionAction ? (
            <div className="mt-3">
              <AuctionActionButton
                action={startAuctionAction}
                disabled={controlsDisabled || isActionDisabled(startAuctionAction)}
                icon={Gavel}
                label="Start auction"
                onSubmit={onSubmit}
                pendingActionType={pendingActionType}
              />
            </div>
          ) : null}
        </div>

        {auction ? (
          <section aria-label="Auction bidder controls" className="rounded-md border border-neutral-200 bg-white p-3">
            <div className="flex items-center gap-2">
              <HandCoins aria-hidden="true" className="size-4 text-amber-700" />
              <h3 className="text-sm font-semibold text-neutral-950">Bidder controls</h3>
            </div>
            <ul className="mt-3 grid gap-2">
              {game.players.map((player) => {
                const bidAction = legalActionFor(legalActions, "BID_AUCTION", auction.property_id, player.id);
                const passAction = legalActionFor(legalActions, "PASS_AUCTION", auction.property_id, player.id);
                const hasControls = Boolean(bidAction ?? passAction);
                const canStepAiBidder =
                  player.controller_type === "ai" &&
                  player.id !== activeAiPlayerId &&
                  hasControls &&
                  Boolean(onStepAiBidder);
                return (
                  <li
                    key={player.id}
                    role="group"
                    aria-label={`${player.name} auction controls`}
                    className="flex flex-col gap-2 rounded border border-neutral-200 bg-neutral-50 px-3 py-2 sm:flex-row sm:items-center sm:justify-between"
                  >
                    <div className="min-w-0">
                      <div className="font-medium text-neutral-950">{player.name}</div>
                      <div className="mt-0.5 text-xs text-neutral-600">{playerAuctionStatus(player.id, auction)}</div>
                    </div>
                    {hasControls ? (
                      <div className="flex flex-wrap gap-2">
                        {bidAction ? (
                          <AuctionBidControl
                            action={bidAction}
                            auction={auction}
                            controlsDisabled={controlsDisabled}
                            disabled={isActionDisabled(bidAction)}
                            onSubmit={onSubmit}
                            pendingActionType={pendingActionType}
                            playerName={player.name}
                          />
                        ) : null}
                        {passAction ? (
                          <AuctionActionButton
                            action={passAction}
                            disabled={controlsDisabled || isActionDisabled(passAction)}
                            icon={LogOut}
                            label="Pass"
                            onSubmit={onSubmit}
                            pendingActionType={pendingActionType}
                          />
                        ) : null}
                        {canStepAiBidder ? (
                          <AuctionAiStepButton
                            disabled={controlsDisabled || aiStepDisabled}
                            isPending={aiStepPending}
                            onStep={() => onStepAiBidder?.(player.id)}
                          />
                        ) : null}
                      </div>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          </section>
        ) : null}

        {resultEvent ? (
          <section aria-label="Auction result" className="rounded-md border border-neutral-200 bg-neutral-50 p-3">
            <div className="flex items-center gap-2">
              <Trophy aria-hidden="true" className="size-4 text-teal-700" />
              <h3 className="text-sm font-semibold text-neutral-950">Auction result</h3>
            </div>
            <div className="mt-2 text-sm text-neutral-700">{auctionResultText(game, resultEvent)}</div>
          </section>
        ) : null}
      </div>
    </section>
  );
}
