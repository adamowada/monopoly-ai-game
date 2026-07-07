"use client";

import {
  BOARD_SPACES,
  CHANCE_DECK,
  COMMUNITY_CHEST_DECK,
  PROPERTIES_BY_ID,
  type StaticDataCard,
  type StaticDataProperty,
} from "@monopoly-ai-game/schemas";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Banknote,
  Bot,
  CheckCircle2,
  CircleDollarSign,
  Dice5,
  FolderOpen,
  Gavel,
  HandCoins,
  KeyRound,
  Loader2,
  LogOut,
  Save,
  ShieldAlert,
  UserRound,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { Button } from "../components/ui/button";
import {
  backendBaseUrl,
  eventsStreamUrl,
  readEvents,
  readGameState,
  readLegalActions,
  submitGameAction,
  submitAiStep,
  type AcceptedEvent,
  type ActionRejectedResponse,
  type AiStepResponse,
  type GameStateResponse,
  type LegalAction,
} from "../lib/api/gameplay";
import { endGame, readGame, type GameMetadata } from "../lib/api/games";
import { readRejectedActions, type RejectedActionRecord } from "../lib/api/rejected-actions";
import { cn } from "../lib/ui";
import { AiAuditPanel } from "./ai-audit-panel";
import { AuctionPanel, isAuctionAction, readActiveAuction } from "./auction-panel";
import { ContractsPanel } from "./contracts-panel";
import { ClassicGameBoard, getPlayerColor, type BoardMotion, type DrawnCardView } from "./game-board";
import { NegotiationPanel } from "./negotiation-panel";
import { PropertyManagementPanel } from "./property-management";

type GamePlaySurfaceProps = {
  gameId: string;
  initialGame: GameMetadata;
  apiBaseUrl?: string;
};

type AiStepMode = "manual" | "auto" | "auction_ai_bidder";

type AiStepRequest = {
  mode: AiStepMode;
  playerId: string;
};

type BoardMotionState =
  | (Extract<BoardMotion, { status: "rolling" }> & { motionKey: string })
  | (Extract<BoardMotion, { status: "moving" | "settled" }> & {
      motionKey: string;
      path: number[];
      stepIndex: number;
    });

type NegotiationCutoffs = {
  max_rounds?: number;
  max_proposals_per_player?: number;
};

type ActionGroup = "turn" | "purchase" | "payment" | "jail";

type ActionModel = {
  label: string;
  group: ActionGroup;
  icon: typeof Dice5;
  variant?: "danger";
};

type SavedGameRecord = {
  id: string;
  label: string;
  status: string;
  updatedAt: string;
  savedAt: string;
};

type CurrentPlayerProperty = {
  id: string;
  name: string;
  price: number;
  mortgaged: boolean;
  houses: number;
  hotel: boolean;
};

type TurnResultSummary = {
  badge: string;
  detail: string;
};

const actionModels: Record<string, ActionModel> = {
  ROLL_DICE: { label: "Roll dice", group: "turn", icon: Dice5 },
  BUY_PROPERTY: { label: "Buy property", group: "purchase", icon: CircleDollarSign },
  START_AUCTION: { label: "Start auction", group: "purchase", icon: Gavel },
  BID_AUCTION: { label: "Bid auction", group: "purchase", icon: HandCoins },
  PASS_AUCTION: { label: "Pass auction", group: "purchase", icon: LogOut },
  SETTLE_DEBT: { label: "Settle debt", group: "payment", icon: Banknote },
  PAY_JAIL_FINE: { label: "Pay jail fine", group: "jail", icon: KeyRound },
  USE_GET_OUT_OF_JAIL_CARD: { label: "Use get out of jail card", group: "jail", icon: KeyRound },
  DECLARE_BANKRUPTCY: { label: "Declare bankruptcy", group: "payment", icon: AlertTriangle, variant: "danger" },
  END_TURN: { label: "End turn", group: "turn", icon: CheckCircle2 },
};

const groupTitles: Record<ActionGroup, string> = {
  turn: "Turn",
  purchase: "Buy, pass, and auction",
  payment: "Rent, tax, and payment",
  jail: "Jail",
};

const savedGamesStorageKey = "monopoly-ai-game.saved-games";
const cardsById = new Map<string, StaticDataCard>(
  [...CHANCE_DECK, ...COMMUNITY_CHEST_DECK].map((card) => [card.id, card]),
);
const diceRevealDelayMs = 700;
const tokenStepDelayMs = 170;
const tokenSettleDelayMs = 160;
const motionClearDelayMs = 1200;
const cardRevealDelayMs = 320;

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function isSavedGameRecord(value: unknown): value is SavedGameRecord {
  if (!isRecord(value)) {
    return false;
  }
  return (
    typeof value.id === "string" &&
    typeof value.label === "string" &&
    typeof value.status === "string" &&
    typeof value.updatedAt === "string" &&
    typeof value.savedAt === "string"
  );
}

function readSavedGames(): SavedGameRecord[] {
  if (typeof window === "undefined") {
    return [];
  }
  try {
    const rawValue = window.localStorage.getItem(savedGamesStorageKey);
    const parsed: unknown = rawValue ? JSON.parse(rawValue) : [];
    return Array.isArray(parsed) ? parsed.filter(isSavedGameRecord) : [];
  } catch {
    return [];
  }
}

function writeSavedGames(savedGames: SavedGameRecord[]) {
  if (typeof window === "undefined") {
    return;
  }
  window.localStorage.setItem(savedGamesStorageKey, JSON.stringify(savedGames));
}

function savedGameRecord(game: GameMetadata): SavedGameRecord {
  return {
    id: game.id,
    label: game.id,
    status: game.status,
    updatedAt: game.updated_at,
    savedAt: new Date().toISOString(),
  };
}

function getNegotiationCutoffs(game: GameMetadata): NegotiationCutoffs {
  const cutoffs = game.settings.negotiation_cutoffs;
  if (!isRecord(cutoffs)) {
    return {};
  }
  return cutoffs as NegotiationCutoffs;
}

function readNumber(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function readInteger(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isInteger(value) ? value : fallback;
}

function readBoolean(value: unknown, fallback = false): boolean {
  return typeof value === "boolean" ? value : fallback;
}

function money(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value) ? `$${value.toLocaleString("en-US")}` : "$0";
}

function playerCash(player: GameMetadata["players"][number]): string {
  return money(readNumber(player.state.cash));
}

function playerPosition(player: GameMetadata["players"][number]): string {
  const position = readNumber(player.state.position);
  const space = BOARD_SPACES[position];
  return space ? `${space.name} (${position})` : String(position);
}

function turnRecord(snapshot: GameStateResponse | undefined): Record<string, unknown> | null {
  const turn = snapshot?.state.turn;
  return isRecord(turn) ? turn : null;
}

function activePhase(game: GameMetadata, snapshot: GameStateResponse | undefined): string {
  const phase = turnRecord(snapshot)?.phase;
  return typeof phase === "string" && phase ? phase : (game.current_phase ?? "Unassigned");
}

const phaseLabels: Record<string, string> = {
  START_TURN: "Start turn",
  PRE_ROLL_MANAGEMENT: "Manage properties",
  ROLLING: "Rolling dice",
  MOVING: "Moving token",
  RESOLVE_SPACE: "Resolve space",
  PURCHASE_OR_AUCTION: "Buy or auction",
  AUCTION: "Auction",
  NEGOTIATION_WINDOW: "Negotiation",
  END_TURN: "End turn",
};

function formatTitleCase(value: string): string {
  return value.toLowerCase().replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function formatTurnPhase(phase: string): string {
  return phaseLabels[phase] ?? formatTitleCase(phase);
}

function formatGameStatus(status: string): string {
  return formatTitleCase(status);
}

function formatControllerType(type: string): string {
  return type === "ai" ? "AI" : formatTitleCase(type);
}

function activePlayerFromState(
  game: GameMetadata,
  snapshot: GameStateResponse | undefined,
): GameMetadata["players"][number] | null {
  const turn = turnRecord(snapshot);
  if (!turn) {
    return null;
  }

  const playerId = typeof turn.current_player_id === "string" ? turn.current_player_id : null;
  if (playerId) {
    const byId = game.players.find((player) => player.id === playerId);
    if (byId) {
      return byId;
    }
  }

  const playerIndex = typeof turn.current_player_index === "number" ? turn.current_player_index : null;
  if (playerIndex !== null) {
    return game.players.find((player) => player.seat_order === playerIndex) ?? null;
  }

  return null;
}

function activePlayer(game: GameMetadata, snapshot: GameStateResponse | undefined): GameMetadata["players"][number] | null {
  const statePlayer = activePlayerFromState(game, snapshot);
  if (statePlayer) {
    return statePlayer;
  }

  const turn = turnRecord(snapshot);
  const playerIndex = typeof turn?.current_player_index === "number" ? turn.current_player_index : 0;
  return game.players.find((player) => player.seat_order === playerIndex) ?? game.players[0] ?? null;
}

function playerName(game: GameMetadata, playerId: string | null | undefined): string {
  if (!playerId) {
    return "Unknown";
  }
  return game.players.find((player) => player.id === playerId)?.name ?? playerId;
}

function propertyById(propertyId: string | null): StaticDataProperty | null {
  if (!propertyId) {
    return null;
  }
  return (PROPERTIES_BY_ID as Readonly<Record<string, StaticDataProperty | undefined>>)[propertyId] ?? null;
}

function propertyName(propertyId: string | null): string {
  return propertyById(propertyId)?.name ?? propertyId ?? "Unknown property";
}

async function loadGame(gameId: string, apiBaseUrl?: string): Promise<GameMetadata> {
  const snapshot = await readGame({ gameId, baseUrl: apiBaseUrl });
  if (snapshot.state === "error") {
    throw new Error(snapshot.error);
  }
  return snapshot.game;
}

function createIdempotencyKey(action: LegalAction): string {
  const randomValue =
    typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
      ? crypto.randomUUID()
      : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  return `${action.actor_id}:${action.type}:${randomValue}`;
}

function mergeEvents(events: AcceptedEvent[], optimisticEvents: AcceptedEvent[]): AcceptedEvent[] {
  const byKey = new Map<string, AcceptedEvent>();
  for (const event of [...events, ...optimisticEvents]) {
    byKey.set(`${event.sequence}:${event.id}`, event);
  }
  return [...byKey.values()].sort((left, right) => left.sequence - right.sequence);
}

function eventPayloadRecord(event: AcceptedEvent | undefined): Record<string, unknown> {
  return isRecord(event?.payload) ? event.payload : {};
}

function eventPayloadNumber(event: AcceptedEvent | undefined, key: string): number | null {
  const value = eventPayloadRecord(event)[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function eventPayloadString(event: AcceptedEvent | undefined, key: string): string | null {
  const value = eventPayloadRecord(event)[key];
  return typeof value === "string" && value ? value : null;
}

function eventPayloadBoolean(event: AcceptedEvent | undefined, key: string): boolean | null {
  const value = eventPayloadRecord(event)[key];
  return typeof value === "boolean" ? value : null;
}

function cashTransferSummary(game: GameMetadata, events: AcceptedEvent[], event: AcceptedEvent): string {
  const playerId = eventPayloadString(event, "player_id") ?? event.actor_player_id;
  const amount = eventPayloadNumber(event, "amount");
  if (!playerId || amount === null) {
    return "Cash changed hands.";
  }

  const pairedEvent = [...events]
    .filter((candidate) => candidate.id !== event.id && candidate.event_type === "PLAYER_CASH_DELTA")
    .sort((left, right) => Math.abs(left.sequence - event.sequence) - Math.abs(right.sequence - event.sequence))
    .find((candidate) => eventPayloadNumber(candidate, "amount") === -amount);
  const pairedPlayerId = pairedEvent ? (eventPayloadString(pairedEvent, "player_id") ?? pairedEvent.actor_player_id) : null;

  if (pairedPlayerId && amount > 0) {
    return `${playerName(game, pairedPlayerId)} paid ${playerName(game, playerId)} ${money(amount)}.`;
  }
  if (pairedPlayerId && amount < 0) {
    return `${playerName(game, playerId)} paid ${playerName(game, pairedPlayerId)} ${money(Math.abs(amount))}.`;
  }
  return amount > 0
    ? `${playerName(game, playerId)} received ${money(amount)}.`
    : `${playerName(game, playerId)} paid ${money(Math.abs(amount))}.`;
}

function latestSignificantEvent(events: AcceptedEvent[]): AcceptedEvent | null {
  const passiveEvents = new Set([
    "DECK_STATE_SET",
    "BANK_INVENTORY_SET",
    "TURN_STATE_SET",
    "ACTIVE_AUCTION_SET",
    "ACTIVE_NEGOTIATION_SET",
    "ACTIVE_ATOMIC_RESOLUTION_SET",
  ]);
  return [...events].reverse().find((event) => !passiveEvents.has(event.event_type)) ?? events.at(-1) ?? null;
}

function lastTurnResultFromEvents(events: AcceptedEvent[], game: GameMetadata): TurnResultSummary {
  const event = latestSignificantEvent(events);
  if (!event) {
    return {
      badge: "Waiting",
      detail: "No completed turn result yet.",
    };
  }

  if (event.event_type === "PROPERTY_OWNER_SET") {
    const ownerId = eventPayloadString(event, "owner_id");
    const propertyId = eventPayloadString(event, "property_id");
    return {
      badge: "Property",
      detail: ownerId
        ? `${playerName(game, ownerId)} owns ${propertyName(propertyId)}.`
        : `${propertyName(propertyId)} returned to the bank.`,
    };
  }

  if (event.event_type === "AUCTION_RESULT") {
    const winnerId = eventPayloadString(event, "winner_id");
    const propertyId = eventPayloadString(event, "property_id");
    const winningBid = eventPayloadNumber(event, "winning_bid");
    const bidText = winningBid === null ? "an unknown amount" : money(winningBid);
    return {
      badge: "Auction",
      detail: `${playerName(game, winnerId)} won ${propertyName(propertyId)} for ${bidText}.`,
    };
  }

  if (event.event_type === "CARD_DRAWN") {
    const card = cardsById.get(eventPayloadString(event, "card_id") ?? "");
    const deck = deckLabel(eventPayloadString(event, "deck") ?? card?.deck ?? null);
    const actor = playerName(game, event.actor_player_id);
    return {
      badge: deck,
      detail: `${actor} drew ${card?.title ?? "a card"}.`,
    };
  }

  if (event.event_type === "PLAYER_CASH_DELTA") {
    return {
      badge: "Cash",
      detail: cashTransferSummary(game, events, event),
    };
  }

  if (event.event_type === "TOKEN_MOVED" || event.event_type === "PLAYER_POSITION_SET") {
    const playerId = eventPayloadString(event, "player_id") ?? event.actor_player_id;
    const position = eventPayloadNumber(event, event.event_type === "TOKEN_MOVED" ? "to_position" : "position");
    const space = position === null ? null : BOARD_SPACES[position];
    return {
      badge: "Move",
      detail: `${playerName(game, playerId)} moved to ${space?.name ?? "an unknown space"}.`,
    };
  }

  if (event.event_type === "PROPERTY_MORTGAGE_SET") {
    const propertyId = eventPayloadString(event, "property_id");
    const mortgaged = eventPayloadBoolean(event, "mortgaged");
    return {
      badge: "Mortgage",
      detail: `${propertyName(propertyId)} is now ${mortgaged ? "mortgaged" : "unmortgaged"}.`,
    };
  }

  if (event.event_type === "PROPERTY_IMPROVEMENTS_SET") {
    const propertyId = eventPayloadString(event, "property_id");
    const houses = eventPayloadNumber(event, "houses") ?? 0;
    const hotel = eventPayloadBoolean(event, "hotel") ?? false;
    return {
      badge: "Build",
      detail: hotel ? `${propertyName(propertyId)} now has a hotel.` : `${propertyName(propertyId)} now has ${houses} houses.`,
    };
  }

  if (event.event_type === "ACTIVE_PAYMENT_SET" && eventPayloadBoolean(event, "active")) {
    const debtorId = eventPayloadString(event, "debtor_id");
    const creditorId = eventPayloadString(event, "creditor_id");
    const amountOwed = eventPayloadNumber(event, "amount_owed");
    const reason = eventPayloadString(event, "reason");
    return {
      badge: "Payment",
      detail: `${playerName(game, debtorId)} owes ${creditorId ? playerName(game, creditorId) : "the bank"} ${money(amountOwed)}${reason ? ` for ${reason}` : ""}.`,
    };
  }

  return {
    badge: formatTitleCase(event.event_type),
    detail: eventPayloadString(event, "summary") ?? `${formatTitleCase(event.event_type)} was recorded.`,
  };
}

function deckLabel(deck: string | null): string {
  if (deck === "chance") {
    return "Chance";
  }
  if (deck === "community_chest") {
    return "Community Chest";
  }
  return "Card";
}

function latestDrawnCardFromEvents(
  events: AcceptedEvent[],
  playersById: Map<string, GameMetadata["players"][number]>,
): DrawnCardView | null {
  for (const event of [...events].reverse()) {
    if (event.event_type !== "CARD_DRAWN") {
      continue;
    }
    const cardId = eventPayloadString(event, "card_id");
    if (!cardId) {
      continue;
    }
    const card = cardsById.get(cardId);
    if (!card) {
      continue;
    }
    return {
      eventId: event.id,
      deckLabel: deckLabel(eventPayloadString(event, "deck") ?? card.deck),
      title: card.title,
      description: card.description,
      playerName: event.actor_player_id ? (playersById.get(event.actor_player_id)?.name ?? null) : null,
    };
  }
  return null;
}

function diceFromEvent(event: AcceptedEvent | undefined): number[] | undefined {
  const dice = eventPayloadRecord(event).dice;
  if (!Array.isArray(dice)) {
    return undefined;
  }
  const values = dice.filter((value): value is number => typeof value === "number" && Number.isFinite(value));
  return values.length > 0 ? values : undefined;
}

function boardPath(fromPosition: number, toPosition: number): number[] {
  const path = [fromPosition];
  let position = fromPosition;
  while (position !== toPosition && path.length <= BOARD_SPACES.length) {
    position = (position + 1) % BOARD_SPACES.length;
    path.push(position);
  }
  return path;
}

function boardMotionFromAcceptedEvents(events: AcceptedEvent[], fallbackPlayerId: string): BoardMotionState | null {
  const diceEvent = events.find((event) => event.event_type === "DICE_ROLLED");
  const moveEvent = events.find((event) => event.event_type === "TOKEN_MOVED");
  const dice = diceFromEvent(diceEvent);
  const total = eventPayloadNumber(diceEvent, "total") ?? undefined;

  if (moveEvent) {
    const playerId = eventPayloadString(moveEvent, "player_id") ?? fallbackPlayerId;
    const fromPosition = eventPayloadNumber(moveEvent, "from_position");
    const toPosition = eventPayloadNumber(moveEvent, "to_position");
    if (fromPosition !== null && toPosition !== null) {
      const path = boardPath(fromPosition, toPosition);
      return {
        dice,
        displayPosition: path[0] ?? fromPosition,
        fromPosition,
        motionKey: `${moveEvent.id}:${moveEvent.sequence}`,
        path,
        playerId,
        status: "moving",
        stepIndex: 0,
        toPosition,
        total,
      };
    }
  }

  if (diceEvent) {
    return {
      dice,
      displayPosition: 0,
      fromPosition: 0,
      motionKey: `${diceEvent.id}:${diceEvent.sequence}`,
      path: [0],
      playerId: fallbackPlayerId,
      status: "settled",
      stepIndex: 0,
      toPosition: 0,
      total,
    };
  }

  return null;
}

function latestRejectedAction(records: RejectedActionRecord[]): RejectedActionRecord | null {
  if (records.length === 0) {
    return null;
  }
  return [...records].sort((left, right) => Date.parse(right.created_at) - Date.parse(left.created_at))[0] ?? null;
}

function currentPlayerProperties(
  snapshot: GameStateResponse | undefined,
  playerId: string | null | undefined,
): CurrentPlayerProperty[] {
  if (!playerId) {
    return [];
  }
  const ownership = snapshot?.state.property_ownership;
  if (!Array.isArray(ownership)) {
    return [];
  }

  return ownership.flatMap((entry) => {
    if (!isRecord(entry) || entry.owner_id !== playerId) {
      return [];
    }
    const propertyId = typeof entry.property_id === "string" ? entry.property_id : null;
    const property = propertyById(propertyId);
    if (!property) {
      return [];
    }
    return [
      {
        id: property.id,
        name: property.name,
        price: property.price,
        mortgaged: readBoolean(entry.mortgaged),
        houses: Math.max(0, readInteger(entry.houses)),
        hotel: readBoolean(entry.hotel) || readInteger(entry.hotels) > 0,
      },
    ];
  });
}

function playerRelatedCommitmentEvents(events: AcceptedEvent[], playerId: string | null | undefined): AcceptedEvent[] {
  if (!playerId) {
    return [];
  }
  return [...events]
    .reverse()
    .filter((event) => {
      if (event.actor_player_id === playerId) {
        return /(CONTRACT|OBLIGATION|DEAL|NEGOTIATION)/.test(event.event_type);
      }
      const payload = eventPayloadRecord(event);
      return Object.entries(payload).some(
        ([key, value]) => key.endsWith("player_id") && value === playerId && /(CONTRACT|OBLIGATION|DEAL|NEGOTIATION|PAYMENT)/.test(event.event_type),
      );
    })
    .slice(0, 3);
}

function uniqueLegalActions(actions: LegalAction[]): LegalAction[] {
  const seen = new Set<string>();
  const unique: LegalAction[] = [];
  for (const action of actions) {
    const key = `${action.actor_id}:${action.type}:${JSON.stringify(action.payload)}`;
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    unique.push(action);
  }
  return unique;
}

function rejectionMessages(rejection: ActionRejectedResponse | RejectedActionRecord): string[] {
  return rejection.validation_errors.map((error) => {
    const field = error.field ? `${error.field}: ` : "";
    return `${field}${error.message}`;
  });
}

function RejectedActionAlert({
  rejection,
}: Readonly<{
  rejection: ActionRejectedResponse | RejectedActionRecord;
}>) {
  const messages = rejectionMessages(rejection);
  return (
    <section
      aria-label="Rejected action"
      role="alert"
      className="rounded-md border border-rose-200 bg-rose-50 p-4 text-sm text-rose-800"
    >
      <div className="flex items-start gap-3">
        <ShieldAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-rose-700" />
        <div className="min-w-0">
          <h2 className="font-semibold text-rose-950">Rejected action</h2>
          <p className="mt-1 font-medium">{rejection.reason_code}</p>
          {messages.length > 0 ? (
            <ul className="mt-2 list-disc space-y-1 pl-4">
              {messages.map((message) => (
                <li key={message}>{message}</li>
              ))}
            </ul>
          ) : (
            <p className="mt-2">No validation details supplied.</p>
          )}
        </div>
      </div>
    </section>
  );
}

function aiStepStatusLabel(result: AiStepResponse | null, isThinking: boolean): string {
  if (isThinking) {
    return "AI thinking";
  }
  if (!result) {
    return "AI idle";
  }
  if (result.status === "blocked") {
    return "AI blocked";
  }
  if (result.status === "rejected") {
    return "AI rejected";
  }
  return "AI done";
}

function AiStepStatusPanel({
  result,
  isThinking,
}: Readonly<{
  result: AiStepResponse | null;
  isThinking: boolean;
}>) {
  const label = aiStepStatusLabel(result, isThinking);
  const isProblem = label === "AI blocked" || label === "AI rejected";
  return (
    <div
      aria-label="AI step status"
      role="status"
      className={cn(
        "rounded-md border px-3 py-2 text-sm",
        isProblem
          ? "border-rose-200 bg-rose-50 text-rose-800"
          : "border-neutral-200 bg-neutral-50 text-neutral-700",
      )}
    >
      <div className="flex items-start gap-2">
        {isThinking ? (
          <Loader2 aria-hidden="true" className="mt-0.5 size-4 animate-spin text-neutral-600" />
        ) : isProblem ? (
          <ShieldAlert aria-hidden="true" className="mt-0.5 size-4 text-rose-700" />
        ) : (
          <Bot aria-hidden="true" className="mt-0.5 size-4 text-purple-700" />
        )}
        <div>
          <p className="font-semibold">{label}</p>
          {result?.reason_code ? <p className="mt-1 text-xs">{result.reason_code}</p> : null}
          {result?.validation_errors?.[0]?.message ? (
            <p className="mt-1 text-xs">{result.validation_errors[0].message}</p>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function ActionButton({
  action,
  disabled,
  isSubmitting,
  onSubmit,
}: Readonly<{
  action: LegalAction;
  disabled: boolean;
  isSubmitting: boolean;
  onSubmit: (action: LegalAction) => void;
}>) {
  const model = actionModels[action.type];
  if (!model) {
    return null;
  }
  const Icon = model.icon;
  return (
    <Button
      onClick={() => onSubmit(action)}
      disabled={disabled}
      className={cn(
        "min-h-9 justify-start px-2.5 py-1.5 text-xs",
      )}
      variant={model.variant === "danger" ? "danger" : "primary"}
    >
      {isSubmitting ? (
        <Loader2 aria-hidden="true" className="size-3.5 animate-spin" />
      ) : (
        <Icon aria-hidden="true" className="size-3.5" />
      )}
      {isSubmitting ? "Submitting..." : model.label}
    </Button>
  );
}

function ActionGroupPanel({
  title,
  actions,
  disabled,
  pendingActionType,
  onSubmit,
}: Readonly<{
  title: string;
  actions: LegalAction[];
  disabled: boolean;
  pendingActionType: string | null;
  onSubmit: (action: LegalAction) => void;
}>) {
  if (actions.length === 0) {
    return null;
  }
  return (
    <div className="rounded-md border border-neutral-200 bg-neutral-50 p-3">
      <h3 className="text-xs font-semibold uppercase text-neutral-500">{title}</h3>
      <div className="mt-2 flex flex-wrap gap-2">
        {actions.map((action) => (
          <ActionButton
            key={`${action.type}-${JSON.stringify(action.payload)}`}
            action={action}
            disabled={disabled}
            isSubmitting={pendingActionType === action.type}
            onSubmit={onSubmit}
          />
        ))}
      </div>
    </div>
  );
}

function EndTurnControl({
  endTurnAction,
  disabled,
  pendingActionType,
  onSubmit,
}: Readonly<{
  endTurnAction: LegalAction | null;
  disabled: boolean;
  pendingActionType: string | null;
  onSubmit: (action: LegalAction) => void;
}>) {
  if (endTurnAction) {
    return (
      <ActionButton
        action={endTurnAction}
        disabled={disabled}
        isSubmitting={pendingActionType === endTurnAction.type}
        onSubmit={onSubmit}
      />
    );
  }

  return (
    <Button
      disabled
      className="min-h-9 justify-start px-2.5 py-1.5 text-xs"
      variant="secondary"
    >
      <CheckCircle2 aria-hidden="true" className="size-3.5" />
      End turn
    </Button>
  );
}

function ActivePlayerPanel({
  player,
  phase,
}: Readonly<{
  player: GameMetadata["players"][number] | null;
  phase: string;
}>) {
  return (
    <section aria-label="Active player" className="rounded-md border border-neutral-200 bg-white p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-neutral-950">Active player</h2>
          <p className="mt-1 text-xs text-neutral-600">Current turn at the table.</p>
        </div>
        <span className="inline-flex items-center gap-1.5 rounded-full bg-teal-50 px-2 py-1 text-xs font-medium text-teal-700 ring-1 ring-inset ring-teal-200">
          <span aria-hidden="true" className="size-1.5 rounded-full bg-teal-600" />
          {formatTurnPhase(phase)}
        </span>
      </div>

      {player ? (
        <dl className="mt-4 grid grid-cols-2 gap-3 text-sm">
          <div>
            <dt className="text-xs font-medium uppercase text-neutral-500">Name</dt>
            <dd className="mt-1 font-medium text-neutral-950">{player.name}</dd>
          </div>
          <div>
            <dt className="text-xs font-medium uppercase text-neutral-500">Seat</dt>
            <dd className="mt-1 inline-flex items-center gap-1.5 text-neutral-800">
              {player.controller_type === "ai" ? (
                <Bot aria-hidden="true" className="size-3.5 text-purple-700" />
              ) : (
                <UserRound aria-hidden="true" className="size-3.5 text-teal-700" />
              )}
              {formatControllerType(player.controller_type)}
            </dd>
          </div>
          <div>
            <dt className="text-xs font-medium uppercase text-neutral-500">Cash</dt>
            <dd className="mt-1 font-medium text-neutral-950">{playerCash(player)}</dd>
          </div>
          <div>
            <dt className="text-xs font-medium uppercase text-neutral-500">Space</dt>
            <dd className="mt-1 font-medium text-neutral-950">{playerPosition(player)}</dd>
          </div>
        </dl>
      ) : (
        <p className="mt-4 text-sm text-neutral-600">No active player assigned.</p>
      )}
    </section>
  );
}

function CurrentPlayerHoldingsPanel({
  player,
  snapshot,
  events,
}: Readonly<{
  player: GameMetadata["players"][number] | null;
  snapshot: GameStateResponse | undefined;
  events: AcceptedEvent[];
}>) {
  const properties = currentPlayerProperties(snapshot, player?.id);
  const commitmentEvents = playerRelatedCommitmentEvents(events, player?.id);

  return (
    <section aria-label="Current player holdings" className="rounded-md border border-neutral-200 bg-white p-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h2 className="text-sm font-semibold text-neutral-950">
            {player ? `${player.name} holdings` : "Current player holdings"}
          </h2>
          <p className="mt-1 text-xs text-neutral-600">Owned property, contracts, obligations, and active commitments for the current turn.</p>
        </div>
        <span className="inline-flex w-fit items-center rounded-full bg-teal-50 px-2 py-1 text-xs font-medium text-teal-700 ring-1 ring-inset ring-teal-200">
          {properties.length} properties
        </span>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-[minmax(0,1.15fr)_minmax(220px,0.85fr)]">
        <div>
          <h3 className="text-xs font-semibold uppercase text-neutral-500">Owned properties</h3>
          {properties.length > 0 ? (
            <ul className="mt-2 grid gap-2 sm:grid-cols-2">
              {properties.map((property) => (
                <li key={property.id} className="rounded border border-neutral-200 bg-neutral-50 px-3 py-2 text-sm">
                  <div className="flex items-start justify-between gap-3">
                    <p className="font-semibold text-neutral-950">{property.name}</p>
                    <span className="text-xs font-medium text-neutral-600">{money(property.price)}</span>
                  </div>
                  <p className="mt-1 text-xs text-neutral-600">
                    {property.mortgaged ? "Mortgaged" : "Active"}
                    {property.hotel ? " / Hotel" : property.houses > 0 ? ` / ${property.houses} houses` : ""}
                  </p>
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-2 rounded border border-neutral-200 bg-neutral-50 px-3 py-2 text-sm text-neutral-600">
              No properties owned yet.
            </p>
          )}
        </div>

        <div>
          <h3 className="text-xs font-semibold uppercase text-neutral-500">Contracts & obligations</h3>
          {commitmentEvents.length > 0 ? (
            <ul className="mt-2 grid gap-2">
              {commitmentEvents.map((event) => (
                <li key={event.id} className="rounded border border-neutral-200 bg-neutral-50 px-3 py-2 text-xs text-neutral-700">
                  <span className="block font-semibold text-neutral-950">{formatTitleCase(event.event_type)}</span>
                  <span className="mt-1 block">{eventPayloadString(event, "summary") ?? `Recorded sequence ${event.sequence}.`}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-2 rounded border border-neutral-200 bg-neutral-50 px-3 py-2 text-sm text-neutral-600">
              No current contracts or obligations for {player?.name ?? "this player"}.
            </p>
          )}
        </div>
      </div>
    </section>
  );
}

function LastTurnResultPanel({ summary }: Readonly<{ summary: TurnResultSummary }>) {
  return (
    <section className="rounded-md border border-neutral-200 bg-white p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-neutral-950">Last turn result</h2>
          <p className="mt-1 text-sm text-neutral-700">{summary.detail}</p>
        </div>
        <span className="inline-flex w-fit shrink-0 items-center rounded-full bg-neutral-100 px-2 py-1 text-xs font-medium text-neutral-700">
          {summary.badge}
        </span>
      </div>
    </section>
  );
}

function TradeContextPanel({ player }: Readonly<{ player: GameMetadata["players"][number] | null }>) {
  return (
    <section className="rounded-md border border-neutral-200 bg-white p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-neutral-950">Trade request</h2>
          <p className="mt-1 text-sm text-neutral-700">
            {player ? `${player.name} has an open negotiation window.` : "The table has an open negotiation window."}
          </p>
        </div>
        <span className="inline-flex w-fit shrink-0 items-center rounded-full bg-purple-50 px-2 py-1 text-xs font-medium text-purple-700 ring-1 ring-inset ring-purple-200">
          Trade
        </span>
      </div>
    </section>
  );
}

function GameSessionPanel({
  isEnding,
  message,
  onEndGame,
  onLoadGame,
  onSaveGame,
  onToggleLoadGames,
  savedGames,
  showLoadGames,
}: Readonly<{
  isEnding: boolean;
  message: string | null;
  onEndGame: () => void;
  onLoadGame: (gameId: string) => void;
  onSaveGame: () => void;
  onToggleLoadGames: () => void;
  savedGames: SavedGameRecord[];
  showLoadGames: boolean;
}>) {
  return (
    <section aria-label="Game session" className="rounded-md border border-neutral-200 bg-white p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-neutral-950">Game session</h2>
        </div>
      </div>

      <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-3 lg:grid-cols-1">
        <Button onClick={onSaveGame} className="justify-start" variant="secondary">
          <Save aria-hidden="true" className="size-4" />
          Save game
        </Button>
        <Button onClick={onToggleLoadGames} className="justify-start" variant="secondary">
          <FolderOpen aria-hidden="true" className="size-4" />
          Load game
        </Button>
        <Button
          onClick={onEndGame}
          disabled={isEnding}
          className="justify-start"
          variant="danger"
        >
          {isEnding ? <Loader2 aria-hidden="true" className="size-4 animate-spin" /> : <LogOut aria-hidden="true" className="size-4" />}
          {isEnding ? "Ending..." : "End game"}
        </Button>
      </div>

      {message ? (
        <p aria-live="polite" className="mt-3 rounded-md border border-teal-200 bg-teal-50 px-3 py-2 text-xs font-medium text-teal-800">
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
                className="rounded-md border border-neutral-200 bg-neutral-50 px-3 py-2 text-left text-xs text-neutral-700 transition hover:bg-neutral-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal-700"
                onClick={() => onLoadGame(savedGame.id)}
                type="button"
              >
                <span className="block font-semibold text-neutral-950">Open {savedGame.label}</span>
                <span className="mt-0.5 block uppercase text-neutral-500">{formatGameStatus(savedGame.status)}</span>
              </button>
            ))
          ) : (
            <p className="rounded-md border border-neutral-200 bg-neutral-50 px-3 py-2 text-xs text-neutral-600">
              No saved games yet.
            </p>
          )}
        </div>
      ) : null}
    </section>
  );
}

function PlayerTable({ game }: Readonly<{ game: GameMetadata }>) {
  return (
    <section aria-labelledby="players-title" className="overflow-hidden rounded-md border border-neutral-200 bg-white">
      <div className="border-b border-neutral-200 px-4 py-3">
        <h2 id="players-title" className="text-sm font-semibold text-neutral-950">
          Players
        </h2>
        <p className="mt-1 text-xs text-neutral-600">Seat order, controller, token color, board space, and status.</p>
      </div>
      <div className="overflow-x-auto">
        <table className="min-w-full text-left text-xs">
          <thead className="bg-neutral-50 text-[10px] uppercase text-neutral-500">
            <tr>
              <th scope="col" className="px-3 py-2 font-semibold">
                Player
              </th>
              <th scope="col" className="px-3 py-2 font-semibold">
                Seat
              </th>
              <th scope="col" className="px-3 py-2 font-semibold">
                Color
              </th>
              <th scope="col" className="px-3 py-2 font-semibold">
                Space
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
                      {formatControllerType(player.controller_type)}
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
                  <td className="whitespace-nowrap px-3 py-3 text-neutral-700">{playerPosition(player)}</td>
                  <td className="whitespace-nowrap px-3 py-3 text-neutral-700">
                    <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-50 px-2 py-1 font-medium text-emerald-700 ring-1 ring-inset ring-emerald-200">
                      <span aria-hidden="true" className="size-1.5 rounded-full bg-emerald-600" />
                      {formatGameStatus(player.status)}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function GameDetails({ game, phase }: Readonly<{ game: GameMetadata; phase: string }>) {
  const cutoffs = getNegotiationCutoffs(game);
  return (
    <>
      <section aria-labelledby="game-details-title" className="rounded-md border border-neutral-200 bg-white p-4">
        <h2 id="game-details-title" className="text-sm font-semibold text-neutral-950">
          Table details
        </h2>
        <dl className="mt-4 grid gap-3 text-sm">
          <div>
            <dt className="text-xs font-medium uppercase text-neutral-500">Status</dt>
            <dd className="mt-1 text-neutral-950">{formatGameStatus(game.status)}</dd>
          </div>
          <div>
            <dt className="text-xs font-medium uppercase text-neutral-500">Turn step</dt>
            <dd className="mt-1 text-neutral-950">{formatTurnPhase(phase)}</dd>
          </div>
          <div>
            <dt className="text-xs font-medium uppercase text-neutral-500">Setup seed</dt>
            <dd className="mt-1 break-all text-neutral-950">{game.seed ?? "Generated locally"}</dd>
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
    </>
  );
}

export function GamePlaySurface({ gameId, initialGame, apiBaseUrl }: GamePlaySurfaceProps) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const baseUrl = backendBaseUrl(apiBaseUrl);
  const [localRejectedAction, setLocalRejectedAction] = useState<ActionRejectedResponse | null>(null);
  const [acceptedEvents, setAcceptedEvents] = useState<AcceptedEvent[]>([]);
  const [pendingActionType, setPendingActionType] = useState<string | null>(null);
  const [aiStepResult, setAiStepResult] = useState<AiStepResponse | null>(null);
  const [autoStepAi, setAutoStepAi] = useState(false);
  const [boardMotion, setBoardMotion] = useState<BoardMotionState | null>(null);
  const [queuedBoardMotion, setQueuedBoardMotion] = useState<BoardMotionState | null>(null);
  const [dismissedCardEventId, setDismissedCardEventId] = useState<string | null>(null);
  const [revealedCardEventId, setRevealedCardEventId] = useState<string | null>(null);
  const [lastAutoStepKey, setLastAutoStepKey] = useState<string | null>(null);
  const [savedGames, setSavedGames] = useState<SavedGameRecord[]>(() => readSavedGames());
  const [sessionMessage, setSessionMessage] = useState<string | null>(null);
  const [showLoadGames, setShowLoadGames] = useState(false);

  const gameQuery = useQuery({
    queryKey: ["game", gameId],
    queryFn: () => loadGame(gameId, baseUrl),
    initialData: initialGame,
  });

  const stateQuery = useQuery({
    queryKey: ["game-state", gameId],
    queryFn: () => readGameState({ gameId, baseUrl }),
  });

  const game = gameQuery.data;
  const stateActivePlayer = activePlayerFromState(game, stateQuery.data);
  const currentPlayer = stateActivePlayer ?? activePlayer(game, stateQuery.data);
  const phase = activePhase(game, stateQuery.data);
  const stateHash = stateQuery.data?.state_hash ?? "pending-state";
  const eventSequence = stateQuery.data?.event_sequence ?? "pending-sequence";

  const legalActionsQuery = useQuery({
    queryKey: ["legal-actions", gameId, currentPlayer?.id, stateHash, eventSequence],
    queryFn: () => readLegalActions({ gameId, actorPlayerId: currentPlayer?.id ?? "", baseUrl }),
    enabled: Boolean(currentPlayer?.id),
  });

  const activeAuction = readActiveAuction(stateQuery.data);
  const auctionLegalActionsQueries = useQueries({
    queries: activeAuction
      ? game.players.map((player) => ({
          queryKey: ["legal-actions", gameId, player.id, "auction", stateHash, eventSequence],
          queryFn: () => readLegalActions({ gameId, actorPlayerId: player.id, baseUrl }),
          enabled: Boolean(player.id),
        }))
      : [],
  });

  const eventsQuery = useQuery({
    queryKey: ["events", gameId],
    queryFn: () => readEvents({ gameId, baseUrl }),
  });

  const rejectedActionsQuery = useQuery({
    queryKey: ["rejected-actions", gameId],
    queryFn: async () => {
      const snapshot = await readRejectedActions({ gameId, baseUrl });
      if (snapshot.state === "error") {
        throw new Error(snapshot.error);
      }
      return snapshot.rejectedActions;
    },
  });

  const endGameMutation = useMutation({
    mutationFn: () => endGame({ gameId, baseUrl }),
    onSuccess: (snapshot) => {
      if (snapshot.state === "loaded") {
        queryClient.setQueryData<GameMetadata>(["game", gameId], snapshot.game);
        setSessionMessage(`Ended ${snapshot.game.id}`);
        router.push("/");
        return;
      }
      setSessionMessage(snapshot.error);
    },
    onError: (error) => {
      setSessionMessage(error instanceof Error ? error.message : String(error));
    },
  });

  function playBoardMotion(nextMotion: BoardMotionState | null) {
    setQueuedBoardMotion(null);
    if (!nextMotion) {
      setBoardMotion(null);
      return;
    }

    if (nextMotion.dice && nextMotion.dice.length > 0) {
      setQueuedBoardMotion(nextMotion);
      setBoardMotion({
        dice: nextMotion.dice,
        displayPosition: nextMotion.fromPosition,
        fromPosition: nextMotion.fromPosition,
        motionKey: `${nextMotion.motionKey}:dice-reveal`,
        playerId: nextMotion.playerId,
        status: "rolling",
        toPosition: nextMotion.toPosition,
        total: nextMotion.total,
      });
      return;
    }

    setBoardMotion(nextMotion);
  }

  useEffect(() => {
    if (!boardMotion) {
      return;
    }

    if (boardMotion.status === "rolling" && queuedBoardMotion) {
      const revealTimer = window.setTimeout(() => {
        setBoardMotion((current) =>
          current?.motionKey === boardMotion.motionKey && current.status === "rolling" ? queuedBoardMotion : current,
        );
        setQueuedBoardMotion((current) => (current?.motionKey === queuedBoardMotion.motionKey ? null : current));
      }, diceRevealDelayMs);
      return () => window.clearTimeout(revealTimer);
    }

    if (boardMotion.status === "moving") {
      if (boardMotion.stepIndex >= boardMotion.path.length - 1) {
        const settleTimer = window.setTimeout(() => {
          setBoardMotion((current) =>
            current?.motionKey === boardMotion.motionKey && current.status === "moving"
              ? { ...current, status: "settled" }
              : current,
          );
        }, tokenSettleDelayMs);
        return () => window.clearTimeout(settleTimer);
      }

      const moveTimer = window.setTimeout(() => {
        setBoardMotion((current) => {
          if (!current || current.motionKey !== boardMotion.motionKey || current.status !== "moving") {
            return current;
          }
          const stepIndex = Math.min(current.stepIndex + 1, current.path.length - 1);
          return {
            ...current,
            displayPosition: current.path[stepIndex] ?? current.toPosition,
            stepIndex,
          };
        });
      }, tokenStepDelayMs);
      return () => window.clearTimeout(moveTimer);
    }

    if (boardMotion.status === "settled") {
      const clearTimer = window.setTimeout(() => {
        setBoardMotion((current) => (current?.motionKey === boardMotion.motionKey ? null : current));
      }, motionClearDelayMs);
      return () => window.clearTimeout(clearTimer);
    }
  }, [boardMotion, queuedBoardMotion]);

  useEffect(() => {
    if (typeof EventSource === "undefined") {
      return;
    }

    const source = new EventSource(eventsStreamUrl(gameId, baseUrl));
    const invalidate = () => {
      void queryClient.invalidateQueries({ queryKey: ["game", gameId] });
      void queryClient.invalidateQueries({ queryKey: ["game-state", gameId] });
      void queryClient.invalidateQueries({ queryKey: ["legal-actions", gameId] });
      void queryClient.invalidateQueries({ queryKey: ["events", gameId] });
      void queryClient.invalidateQueries({ queryKey: ["rejected-actions", gameId] });
      void queryClient.invalidateQueries({ queryKey: ["contracts", gameId] });
      void queryClient.invalidateQueries({ queryKey: ["obligations", gameId] });
      void queryClient.invalidateQueries({ queryKey: ["deals", gameId] });
    };

    source.addEventListener("game_event", invalidate);
    source.onerror = () => undefined;

    return () => {
      source.removeEventListener("game_event", invalidate);
      source.close();
    };
  }, [baseUrl, gameId, queryClient]);

  const submitAction = useMutation({
    mutationFn: (action: LegalAction) =>
      submitGameAction({
        gameId,
        action,
        baseUrl,
        idempotencyKey: createIdempotencyKey(action),
      }),
    onMutate: (action) => {
      setLocalRejectedAction(null);
      setPendingActionType(action.type);
      setQueuedBoardMotion(null);
      setBoardMotion(
        action.type === "ROLL_DICE"
          ? {
              motionKey: `${action.actor_id}:${action.expected_event_sequence}:rolling`,
              playerId: action.actor_id,
              status: "rolling",
            }
          : null,
      );
    },
    onSuccess: (result, action) => {
      if (result.status === "accepted") {
        setAcceptedEvents(result.accepted_events);
        playBoardMotion(boardMotionFromAcceptedEvents(result.accepted_events, action.actor_id));
        queryClient.setQueryData<GameStateResponse>(["game-state", gameId], {
          game_id: gameId,
          state: result.state,
          state_hash: result.state_hash,
          event_sequence: result.event_sequence,
        });
        void Promise.all([
          queryClient.invalidateQueries({ queryKey: ["game", gameId] }),
          queryClient.invalidateQueries({ queryKey: ["game-state", gameId] }),
          queryClient.invalidateQueries({ queryKey: ["legal-actions", gameId] }),
          queryClient.invalidateQueries({ queryKey: ["events", gameId] }),
          queryClient.invalidateQueries({ queryKey: ["rejected-actions", gameId] }),
          queryClient.invalidateQueries({ queryKey: ["contracts", gameId] }),
          queryClient.invalidateQueries({ queryKey: ["obligations", gameId] }),
          queryClient.invalidateQueries({ queryKey: ["deals", gameId] }),
        ]);
        return;
      }

      setQueuedBoardMotion(null);
      setBoardMotion(null);
      setLocalRejectedAction(result);
      void Promise.all([
        queryClient.invalidateQueries({ queryKey: ["legal-actions", gameId] }),
        queryClient.invalidateQueries({ queryKey: ["events", gameId] }),
        queryClient.invalidateQueries({ queryKey: ["rejected-actions", gameId] }),
        queryClient.invalidateQueries({ queryKey: ["contracts", gameId] }),
        queryClient.invalidateQueries({ queryKey: ["obligations", gameId] }),
        queryClient.invalidateQueries({ queryKey: ["deals", gameId] }),
      ]);
    },
    onSettled: () => {
      setPendingActionType(null);
    },
    onError: () => {
      setQueuedBoardMotion(null);
      setBoardMotion(null);
    },
  });

  const invalidateGameplayData = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: ["game", gameId] }),
      queryClient.invalidateQueries({ queryKey: ["game-state", gameId] }),
      queryClient.invalidateQueries({ queryKey: ["legal-actions", gameId] }),
      queryClient.invalidateQueries({ queryKey: ["events", gameId] }),
      queryClient.invalidateQueries({ queryKey: ["rejected-actions", gameId] }),
      queryClient.invalidateQueries({ queryKey: ["contracts", gameId] }),
      queryClient.invalidateQueries({ queryKey: ["obligations", gameId] }),
      queryClient.invalidateQueries({ queryKey: ["negotiations", gameId] }),
      queryClient.invalidateQueries({ queryKey: ["negotiation-messages", gameId] }),
      queryClient.invalidateQueries({ queryKey: ["deals", gameId] }),
    ]);

  const invalidateAiAuditData = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: ["ai-profiles", gameId] }),
      queryClient.invalidateQueries({ queryKey: ["ai-decisions", gameId] }),
      queryClient.invalidateQueries({ queryKey: ["ai-self-dialogue", gameId] }),
      queryClient.invalidateQueries({ queryKey: ["ai-memory", gameId] }),
      queryClient.invalidateQueries({ queryKey: ["ai-retrieval-records", gameId] }),
      queryClient.invalidateQueries({ queryKey: ["ai-rejected-outputs", gameId] }),
    ]);

  const aiStep = useMutation({
    mutationFn: ({ mode, playerId }: AiStepRequest) =>
      submitAiStep({
        gameId,
        baseUrl,
        input: {
          player_id: playerId,
          decision_type: "action_decision",
          mandatory: true,
          request_context: { mode },
        },
      }),
    onMutate: () => {
      setAiStepResult(null);
      setQueuedBoardMotion(null);
      setBoardMotion(null);
    },
    onSuccess: (result) => {
      setAiStepResult(result);
      if (result.accepted_events.length > 0) {
        setAcceptedEvents(result.accepted_events);
        playBoardMotion(boardMotionFromAcceptedEvents(result.accepted_events, result.player_id));
      }
      void Promise.all([invalidateGameplayData(), invalidateAiAuditData()]);
    },
  });

  const legalActions = legalActionsQuery.data?.legal_actions ?? [];
  const auctionLegalActions = useMemo(
    () =>
      uniqueLegalActions([
        ...legalActions.filter(isAuctionAction),
        ...auctionLegalActionsQueries.flatMap((query) => query.data?.legal_actions.filter(isAuctionAction) ?? []),
      ]),
    [auctionLegalActionsQueries, legalActions],
  );
  const playersById = useMemo(() => new Map(game.players.map((player) => [player.id, player])), [game.players]);
  const actionsByGroup = useMemo(() => {
    const grouped: Record<ActionGroup, LegalAction[]> = {
      turn: [],
      purchase: [],
      payment: [],
      jail: [],
    };
    for (const action of legalActions) {
      const model = actionModels[action.type];
      if (!model || action.type === "END_TURN" || isAuctionAction(action)) {
        continue;
      }
      grouped[model.group].push(action);
    }
    return grouped;
  }, [legalActions]);
  const endTurnAction = legalActions.find((action) => action.type === "END_TURN") ?? null;
  const latestAuditRejection = latestRejectedAction(rejectedActionsQuery.data ?? []);
  const visibleRejection = localRejectedAction ?? latestAuditRejection;
  const visibleEvents = mergeEvents(eventsQuery.data ?? [], acceptedEvents);
  const latestDrawnCard = useMemo(
    () => latestDrawnCardFromEvents(visibleEvents, playersById),
    [playersById, visibleEvents],
  );
  const latestDrawnCardEventId = latestDrawnCard?.eventId ?? null;
  useEffect(() => {
    if (!latestDrawnCardEventId || latestDrawnCardEventId === dismissedCardEventId || latestDrawnCardEventId === revealedCardEventId) {
      return;
    }
    if (boardMotion?.status === "rolling" || boardMotion?.status === "moving") {
      return;
    }

    const revealTimer = window.setTimeout(
      () => setRevealedCardEventId(latestDrawnCardEventId),
      boardMotion?.status === "settled" ? cardRevealDelayMs : 0,
    );
    return () => window.clearTimeout(revealTimer);
  }, [boardMotion?.motionKey, boardMotion?.status, dismissedCardEventId, latestDrawnCardEventId, revealedCardEventId]);
  const visibleDrawnCard =
    latestDrawnCard && latestDrawnCard.eventId !== dismissedCardEventId && latestDrawnCard.eventId === revealedCardEventId
      ? latestDrawnCard
      : null;
  const legalActionsLoading = stateQuery.isLoading || legalActionsQuery.isLoading;
  const gameAiBlocked = game.status === "AI_BLOCKED";
  const gameEnded = game.status.toLowerCase() === "ended";
  const controlsDisabled = gameEnded || gameAiBlocked || legalActionsLoading || submitAction.isPending || aiStep.isPending;
  const activeAiPlayer = stateActivePlayer?.controller_type === "ai" ? stateActivePlayer : null;
  const directActionControlsDisabled = controlsDisabled || Boolean(activeAiPlayer);
  const aiStepStateBlocked = gameEnded || gameAiBlocked || !stateQuery.data || stateQuery.isFetching || aiStep.isPending;
  const aiStepBlocked = !activeAiPlayer || aiStepStateBlocked;
  const manualAiStepDisabled = controlsDisabled || aiStepBlocked;
  const autoStepKey = activeAiPlayer && stateQuery.data ? `${activeAiPlayer.id}:${stateHash}:${eventSequence}` : null;
  const auctionActionsLoading =
    Boolean(activeAuction) && auctionLegalActionsQueries.some((query) => query.isLoading);
  const auctionControlsDisabled = controlsDisabled || auctionActionsLoading;

  function isAiControlledActor(action: LegalAction): boolean {
    return playersById.get(action.actor_id)?.controller_type === "ai";
  }

  function isAuctionActionDisabled(action: LegalAction): boolean {
    if (action.type === "START_AUCTION") {
      return Boolean(activeAiPlayer) || isAiControlledActor(action);
    }
    return isAiControlledActor(action);
  }

  function canSubmitDirectAction(action: LegalAction): boolean {
    if (controlsDisabled || isAiControlledActor(action)) {
      return false;
    }
    if (isAuctionAction(action) && action.type !== "START_AUCTION") {
      return true;
    }
    return !activeAiPlayer;
  }

  function handleSubmit(action: LegalAction) {
    if (!canSubmitDirectAction(action)) {
      return;
    }
    submitAction.mutate(action);
  }

  function handleAiStep(mode: AiStepMode, playerId = activeAiPlayer?.id) {
    if (!playerId || aiStepStateBlocked || gameAiBlocked) {
      return;
    }
    aiStep.mutate({ mode, playerId });
  }

  function handleSaveGame() {
    const record = savedGameRecord(game);
    const nextSavedGames = [record, ...savedGames.filter((savedGame) => savedGame.id !== record.id)];
    setSavedGames(nextSavedGames);
    writeSavedGames(nextSavedGames);
    setSessionMessage(`Saved ${record.id}`);
  }

  function handleLoadGame(savedGameId: string) {
    router.push(`/games/${encodeURIComponent(savedGameId)}`);
  }

  function handleEndGame() {
    const confirmed =
      typeof window === "undefined" ||
      typeof window.confirm !== "function" ||
      window.confirm("End this game and return to setup?");
    if (!confirmed) {
      return;
    }
    endGameMutation.mutate();
  }

  useEffect(() => {
    if (gameAiBlocked || !autoStepAi || !activeAiPlayer || !autoStepKey || aiStep.isPending || submitAction.isPending) {
      return;
    }
    if (stateQuery.isFetching || gameQuery.isFetching || lastAutoStepKey === autoStepKey) {
      return;
    }
    setLastAutoStepKey(autoStepKey);
    handleAiStep("auto");
  }, [
    activeAiPlayer,
    aiStep.isPending,
    autoStepAi,
    autoStepKey,
    gameQuery.isFetching,
    gameAiBlocked,
    lastAutoStepKey,
    stateQuery.isFetching,
    submitAction.isPending,
  ]);

  const hasAuctionContext = Boolean(activeAuction) || auctionLegalActions.length > 0;
  const turnResultSummary = lastTurnResultFromEvents(visibleEvents, game);
  const turnControlsPanel = (
    <section aria-label="Turn controls" className="rounded-md border border-neutral-200 bg-white p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-neutral-950">Turn controls</h2>
          <p className="mt-1 text-xs text-neutral-600">Available moves update from the local rules referee.</p>
        </div>
      </div>

      {legalActionsQuery.isError ? (
        <div className="mt-3 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
          Available moves unavailable.
        </div>
      ) : null}

      {activeAiPlayer ? (
        <div className="mt-4 grid gap-3 rounded-md border border-purple-200 bg-purple-50 p-3">
          <div className="flex flex-wrap items-center gap-2">
            <Button onClick={() => handleAiStep("manual")} disabled={manualAiStepDisabled} variant="ai">
              {aiStep.isPending ? (
                <Loader2 aria-hidden="true" className="size-4 animate-spin" />
              ) : (
                <Bot aria-hidden="true" className="size-4" />
              )}
              Step AI
            </Button>
            <label className="inline-flex items-center gap-2 rounded-md border border-purple-200 bg-white px-3 py-2 text-sm font-medium text-purple-900">
              <input
                type="checkbox"
                checked={autoStepAi}
                disabled={gameAiBlocked}
                onChange={(event) => setAutoStepAi(event.target.checked)}
                className="size-4 accent-purple-700"
              />
              Auto-step AI
            </label>
          </div>
          <AiStepStatusPanel isThinking={aiStep.isPending} result={aiStepResult} />
        </div>
      ) : null}

      <div className="mt-4 grid gap-3">
        <ActionGroupPanel
          title={groupTitles.turn}
          actions={actionsByGroup.turn}
          disabled={directActionControlsDisabled}
          pendingActionType={pendingActionType}
          onSubmit={handleSubmit}
        />
        <div className="rounded-md border border-neutral-200 bg-neutral-50 p-3">
          <h3 className="text-xs font-semibold uppercase text-neutral-500">End turn</h3>
          <div className="mt-2 flex flex-wrap gap-2">
            <EndTurnControl
              endTurnAction={endTurnAction}
              disabled={directActionControlsDisabled}
              pendingActionType={pendingActionType}
              onSubmit={handleSubmit}
            />
            {!endTurnAction ? <span className="self-center text-xs text-neutral-500">Unavailable</span> : null}
          </div>
        </div>
        <ActionGroupPanel
          title={groupTitles.purchase}
          actions={actionsByGroup.purchase}
          disabled={directActionControlsDisabled}
          pendingActionType={pendingActionType}
          onSubmit={handleSubmit}
        />
        <ActionGroupPanel
          title={groupTitles.payment}
          actions={actionsByGroup.payment}
          disabled={directActionControlsDisabled}
          pendingActionType={pendingActionType}
          onSubmit={handleSubmit}
        />
        <ActionGroupPanel
          title={groupTitles.jail}
          actions={actionsByGroup.jail}
          disabled={directActionControlsDisabled}
          pendingActionType={pendingActionType}
          onSubmit={handleSubmit}
        />
      </div>
    </section>
  );

  return (
    <div className="mx-auto grid max-w-7xl gap-4 px-4 py-4 sm:px-6 lg:px-8">
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
        <ClassicGameBoard
          drawnCard={visibleDrawnCard}
          game={game}
          motion={boardMotion ?? undefined}
          onDismissDrawnCard={() => setDismissedCardEventId(visibleDrawnCard?.eventId ?? null)}
          snapshot={stateQuery.data}
        />
        <aside className="grid content-start gap-4">
          <ActivePlayerPanel player={currentPlayer} phase={phase} />
          {turnControlsPanel}

          <GameSessionPanel
            isEnding={endGameMutation.isPending}
            message={sessionMessage}
            onEndGame={handleEndGame}
            onLoadGame={handleLoadGame}
            onSaveGame={handleSaveGame}
            onToggleLoadGames={() => setShowLoadGames((current) => !current)}
            savedGames={savedGames}
            showLoadGames={showLoadGames}
          />
        </aside>
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
        <CurrentPlayerHoldingsPanel events={visibleEvents} player={currentPlayer} snapshot={stateQuery.data} />
        <section aria-label="Turn context" className="grid content-start gap-4">
          {hasAuctionContext ? (
            <AuctionPanel
              controlsDisabled={auctionControlsDisabled}
              events={visibleEvents}
              game={game}
              activeAiPlayerId={activeAiPlayer?.id ?? null}
              aiStepDisabled={aiStepStateBlocked}
              aiStepPending={aiStep.isPending}
              isActionDisabled={isAuctionActionDisabled}
              legalActions={auctionLegalActions}
              onStepAiBidder={(playerId) => handleAiStep("auction_ai_bidder", playerId)}
              onSubmit={handleSubmit}
              pendingActionType={pendingActionType}
              snapshot={stateQuery.data}
            />
          ) : phase === "NEGOTIATION_WINDOW" ? (
            <TradeContextPanel player={currentPlayer} />
          ) : (
            <LastTurnResultPanel summary={turnResultSummary} />
          )}
        </section>
      </div>

      {visibleRejection ? <RejectedActionAlert rejection={visibleRejection} /> : null}

      <section aria-label="Supporting table details" className="grid gap-4">
        <PropertyManagementPanel
          controlsDisabled={directActionControlsDisabled}
          game={game}
          legalActions={legalActions}
          onSubmit={handleSubmit}
          pendingActionType={pendingActionType}
          snapshot={stateQuery.data}
        />
        <NegotiationPanel apiBaseUrl={baseUrl} game={game} gameId={gameId} />
        <ContractsPanel
          apiBaseUrl={baseUrl}
          events={visibleEvents}
          game={game}
          gameId={gameId}
          rejectedActions={rejectedActionsQuery.data ?? []}
        />
        <AiAuditPanel apiBaseUrl={baseUrl} game={game} gameId={gameId} />
        <div className="grid gap-4 lg:grid-cols-2">
          <PlayerTable game={game} />
          <GameDetails game={game} phase={phase} />
        </div>
      </section>
    </div>
  );
}
