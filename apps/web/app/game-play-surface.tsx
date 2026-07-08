"use client";

import {
  BOARD_SPACES,
  CHANCE_DECK,
  COMMUNITY_CHEST_DECK,
  PROPERTY_GROUPS,
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
  Gavel,
  HandCoins,
  KeyRound,
  Loader2,
  LogOut,
  ScrollText,
  ShieldAlert,
  UserRound,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

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
import { ClassicGameBoard, getPlayerColor, type BoardMotion, type DrawnCardView, type LastRollView } from "./game-board";
import { GameTableMenu } from "./game-table-menu";
import { NegotiationPanel } from "./negotiation-panel";
import { getPlayerIcon } from "./player-icons";
import { PropertyDeedCard } from "./property-deed-card";
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
  group: StaticDataProperty["group"];
  hotel: boolean;
  hotels: number;
  mortgaged: boolean;
  name: string;
  price: number;
  property: StaticDataProperty;
  houses: number;
};

type TurnResultSummary = {
  badge: string;
  detail: string;
};

type TableView = "properties" | "deals" | "contracts" | "ai-notebook";

const tableViews: Array<{ id: TableView; label: string }> = [
  { id: "properties", label: "Properties" },
  { id: "deals", label: "Deals" },
  { id: "contracts", label: "Contracts" },
  { id: "ai-notebook", label: "AI notebook" },
];

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
const propertyGroupById = new Map<string, (typeof PROPERTY_GROUPS)[number]>(PROPERTY_GROUPS.map((group) => [group.id, group]));
const diceRevealDelayMs = 700;
const tokenStepDelayMs = 440;
const tokenSettleDelayMs = 480;
const cardRevealDelayMs = 320;
const jailBoardPosition = 10;

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

function snapshotPlayerRecord(
  snapshot: GameStateResponse | undefined,
  playerId: string,
): Record<string, unknown> | null {
  const players = snapshot?.state.players;
  if (!Array.isArray(players)) {
    return null;
  }
  const player = players.find((entry) => isRecord(entry) && entry.id === playerId);
  return isRecord(player) ? player : null;
}

function playerCash(player: GameMetadata["players"][number], snapshot?: GameStateResponse): string {
  const snapshotPlayer = snapshotPlayerRecord(snapshot, player.id);
  return money(readNumber(snapshotPlayer?.cash, readNumber(player.state.cash)));
}

function playerPosition(player: GameMetadata["players"][number], snapshot?: GameStateResponse): string {
  const snapshotPlayer = snapshotPlayerRecord(snapshot, player.id);
  const position = readNumber(snapshotPlayer?.position, readNumber(player.state.position));
  const space = BOARD_SPACES[position];
  return space ? space.name : String(position);
}

function turnRecord(snapshot: GameStateResponse | undefined): Record<string, unknown> | null {
  const turn = snapshot?.state.turn;
  return isRecord(turn) ? turn : null;
}

function activePaymentRecord(snapshot: GameStateResponse | undefined): Record<string, unknown> | null {
  const payment = snapshot?.state.active_payment;
  return isRecord(payment) ? payment : null;
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

function legalActionDescription(action: LegalAction): string | null {
  if (action.type === "ROLL_DICE" || action.type === "END_TURN") {
    return null;
  }
  return action.description ?? null;
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

function paymentReasonLabel(reason: string | null): string {
  if (!reason) {
    return "Payment due";
  }
  if (reason.startsWith("rent:")) {
    return `Rent for ${propertyName(reason.slice("rent:".length))}`;
  }
  return formatTitleCase(reason);
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
    "ACTIVE_PAYMENT_SET",
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
      detail: "",
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

function latestRollFromEvents(
  events: AcceptedEvent[],
  playersById: Map<string, GameMetadata["players"][number]>,
): LastRollView | null {
  for (const event of [...events].reverse()) {
    if (event.event_type !== "DICE_ROLLED") {
      continue;
    }
    const dice = diceFromEvent(event);
    if (!dice || dice.length === 0) {
      continue;
    }
    const playerId = eventPlayerId(event);
    const total = eventPayloadNumber(event, "total") ?? dice.reduce((sum, value) => sum + value, 0);
    const nextDice = events.find(
      (candidate) => candidate.sequence > event.sequence && candidate.event_type === "DICE_ROLLED",
    );
    const moveEvent = boardDestinationEvents(
      events.filter((candidate) => candidate.sequence > event.sequence && (!nextDice || candidate.sequence < nextDice.sequence)),
      playerId,
    ).at(-1);
    const landedPosition = boardDestinationEventPosition(moveEvent);
    const landedSpaceName = landedPosition === null ? undefined : boardSpaceName(landedPosition);

    return {
      dice,
      eventId: event.id,
      isDoubles: dice.length >= 2 && dice.every((value) => value === dice[0]),
      landedSpaceName,
      playerName: playerId ? playersById.get(playerId)?.name : undefined,
      total,
    };
  }
  return null;
}

function diceFromEvent(event: AcceptedEvent | undefined): number[] | undefined {
  const payload = eventPayloadRecord(event);
  const dice = payload.dice;
  if (!Array.isArray(dice)) {
    const die1 = typeof payload.die_1 === "number" && Number.isFinite(payload.die_1) ? payload.die_1 : null;
    const die2 = typeof payload.die_2 === "number" && Number.isFinite(payload.die_2) ? payload.die_2 : null;
    return die1 !== null && die2 !== null ? [die1, die2] : undefined;
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

function normalizedBoardPosition(position: number): number {
  return ((position % BOARD_SPACES.length) + BOARD_SPACES.length) % BOARD_SPACES.length;
}

function boardSpaceName(position: number): string {
  return BOARD_SPACES[normalizedBoardPosition(position)]?.name ?? `position ${position}`;
}

function boardDestinationEventPosition(event: AcceptedEvent | undefined): number | null {
  if (!event) {
    return null;
  }
  if (event.event_type === "TOKEN_MOVED") {
    return eventPayloadNumber(event, "to_position");
  }
  if (event.event_type === "PLAYER_POSITION_SET") {
    return eventPayloadNumber(event, "position");
  }
  if (event.event_type === "PLAYER_JAIL_SET" && eventPayloadBoolean(event, "in_jail") === true) {
    return jailBoardPosition;
  }
  return null;
}

function isBoardDestinationEvent(event: AcceptedEvent): boolean {
  return boardDestinationEventPosition(event) !== null;
}

function eventPlayerId(event: AcceptedEvent | undefined): string | null {
  return eventPayloadString(event, "player_id") ?? event?.actor_player_id ?? null;
}

function boardDestinationEvents(events: AcceptedEvent[], playerId: string | null | undefined): AcceptedEvent[] {
  const destinations = events.filter((event) => {
    if (!isBoardDestinationEvent(event)) {
      return false;
    }
    const destinationPlayerId = eventPlayerId(event);
    return !playerId || !destinationPlayerId || destinationPlayerId === playerId;
  });

  return destinations.filter((event, index) => {
    if (event.event_type !== "PLAYER_POSITION_SET" || eventPayloadNumber(event, "position") !== jailBoardPosition) {
      return true;
    }
    return !destinations
      .slice(index + 1)
      .some((candidate) => candidate.event_type === "PLAYER_JAIL_SET" && eventPayloadBoolean(candidate, "in_jail") === true);
  });
}

function boardMotionPath(destinationEvents: AcceptedEvent[], fromPosition: number): number[] {
  const path = [normalizedBoardPosition(fromPosition)];
  let currentPosition = normalizedBoardPosition(fromPosition);

  for (const event of destinationEvents) {
    const nextPosition = boardDestinationEventPosition(event);
    if (nextPosition === null) {
      continue;
    }
    const normalizedNextPosition = normalizedBoardPosition(nextPosition);
    if (event.event_type === "PLAYER_JAIL_SET") {
      if (path[path.length - 1] !== normalizedNextPosition) {
        path.push(normalizedNextPosition);
      }
      currentPosition = normalizedNextPosition;
      continue;
    }
    const segment = boardPath(currentPosition, normalizedNextPosition);
    path.push(...segment.slice(1));
    currentPosition = normalizedNextPosition;
  }

  return path;
}

function playerNameForMotion(players: GameMetadata["players"], playerId: string): string | undefined {
  return players.find((player) => player.id === playerId)?.name;
}

function playerBoardPositionForMotion(
  players: GameMetadata["players"],
  snapshot: GameStateResponse | undefined,
  playerId: string,
): number {
  const snapshotPosition = snapshotPlayerRecord(snapshot, playerId)?.position;
  if (typeof snapshotPosition === "number" && Number.isInteger(snapshotPosition)) {
    return snapshotPosition;
  }
  return readInteger(players.find((player) => player.id === playerId)?.state.position, 0);
}

function boardMotionFromAcceptedEvents(
  events: AcceptedEvent[],
  fallbackPlayerId: string,
  players: GameMetadata["players"],
  snapshot?: GameStateResponse,
): BoardMotionState | null {
  const diceEvent = events.find((event) => event.event_type === "DICE_ROLLED");
  const dicePlayerId = eventPlayerId(diceEvent) ?? fallbackPlayerId;
  const destinationEvents = boardDestinationEvents(events, dicePlayerId);
  const moveEvent = destinationEvents.at(-1);
  const dice = diceFromEvent(diceEvent);
  const total = eventPayloadNumber(diceEvent, "total") ?? undefined;

  if (moveEvent) {
    const playerId = eventPlayerId(moveEvent) ?? dicePlayerId;
    const firstDestination = destinationEvents[0] ?? moveEvent;
    const fromPosition =
      eventPayloadNumber(firstDestination, "from_position") ?? playerBoardPositionForMotion(players, snapshot, playerId);
    const toPosition = boardDestinationEventPosition(moveEvent);
    if (fromPosition !== null && toPosition !== null) {
      const path = boardMotionPath(destinationEvents, fromPosition);
      return {
        dice,
        displayPosition: path[0] ?? fromPosition,
        fromPosition,
        landedSpaceName: boardSpaceName(toPosition),
        motionKey: `${moveEvent.id}:${moveEvent.sequence}`,
        path,
        playerId,
        playerName: playerNameForMotion(players, playerId),
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
      landedSpaceName: boardSpaceName(0),
      motionKey: `${diceEvent.id}:${diceEvent.sequence}`,
      path: [0],
      playerId: fallbackPlayerId,
      playerName: playerNameForMotion(players, fallbackPlayerId),
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
        group: property.group,
        hotel: readBoolean(entry.hotel) || readInteger(entry.hotels) > 0,
        hotels: Math.max(0, readInteger(entry.hotels)),
        mortgaged: readBoolean(entry.mortgaged),
        name: property.name,
        price: property.price,
        property,
        houses: Math.max(0, readInteger(entry.houses)),
      },
    ];
  });
}

function groupedPlayerProperties(properties: CurrentPlayerProperty[]): Array<{
  color: string;
  id: string;
  label: string;
  properties: CurrentPlayerProperty[];
}> {
  const order = new Map<string, number>(PROPERTY_GROUPS.map((group, index) => [group.id, index]));
  const groups = new Map<string, { color: string; id: string; label: string; properties: CurrentPlayerProperty[] }>();
  for (const property of [...properties].sort((left, right) => left.property.board_position - right.property.board_position)) {
    const group = propertyGroupById.get(property.group);
    const key = group?.id ?? property.group;
    const existing =
      groups.get(key) ??
      {
        color: group?.color ?? "#d4d4d4",
        id: key,
        label: group?.name ?? property.group,
        properties: [],
      };
    existing.properties.push(property);
    groups.set(key, existing);
  }
  return [...groups.values()].sort((left, right) => (order.get(left.id) ?? 999) - (order.get(right.id) ?? 999));
}

function propertyOwnershipForTray(property: CurrentPlayerProperty, ownerId: string) {
  return {
    property_id: property.id,
    owner_id: ownerId,
    mortgaged: property.mortgaged,
    houses: property.houses,
    hotels: property.hotels,
    hotel: property.hotel,
  };
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

type GameLogCategory = "turn" | "roll" | "movement" | "card" | "cash" | "property" | "deal" | "ai" | "system";

type GameLogEntry = {
  badge: string;
  category: GameLogCategory;
  detail: string;
  eventId: string;
  playerId: string | null;
  playerName: string;
  sequence: number;
  time: string;
};

const gameLogCategories: Array<{ id: GameLogCategory; label: string }> = [
  { id: "turn", label: "Turn order" },
  { id: "roll", label: "Dice rolls" },
  { id: "movement", label: "Movement" },
  { id: "card", label: "Cards" },
  { id: "cash", label: "Cash" },
  { id: "property", label: "Property" },
  { id: "deal", label: "Deals & contracts" },
  { id: "ai", label: "AI decisions" },
  { id: "system", label: "Game state" },
];

function gameLogTime(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit",
  });
}

function gameLogEntryFromEvent(event: AcceptedEvent, events: AcceptedEvent[], game: GameMetadata): GameLogEntry | null {
  const playerId = eventPayloadString(event, "player_id") ?? event.actor_player_id ?? null;
  const actor = playerName(game, playerId);
  const base = {
    eventId: event.id,
    playerId,
    playerName: actor,
    sequence: event.sequence,
    time: gameLogTime(event.created_at),
  };

  if (event.event_type === "TURN_STATE_SET") {
    const phase = eventPayloadString(event, "phase");
    const currentPlayerId = eventPayloadString(event, "current_player_id");
    if (phase !== "START_TURN" && phase !== "ROLL_PENDING") {
      return null;
    }
    return {
      ...base,
      badge: "Turn",
      category: "turn",
      detail: `${playerName(game, currentPlayerId)} is up.`,
      playerId: currentPlayerId,
      playerName: playerName(game, currentPlayerId),
    };
  }

  if (event.event_type === "DICE_ROLLED") {
    const dice = diceFromEvent(event) ?? [];
    const total = eventPayloadNumber(event, "total") ?? dice.reduce((sum, value) => sum + value, 0);
    const doubles = dice.length >= 2 && dice.every((value) => value === dice[0]);
    return {
      ...base,
      badge: "Roll",
      category: "roll",
      detail: `${actor} rolled ${dice.join(" + ")} = ${total}${doubles ? " (doubles)" : ""}.`,
    };
  }

  if (event.event_type === "TOKEN_MOVED" || event.event_type === "PLAYER_POSITION_SET") {
    const position = eventPayloadNumber(event, event.event_type === "TOKEN_MOVED" ? "to_position" : "position");
    return {
      ...base,
      badge: "Move",
      category: "movement",
      detail: `${actor} moved to ${position === null ? "an unknown space" : boardSpaceName(position)}.`,
    };
  }

  if (event.event_type === "CARD_DRAWN") {
    const card = cardsById.get(eventPayloadString(event, "card_id") ?? "");
    const deck = deckLabel(eventPayloadString(event, "deck") ?? card?.deck ?? null);
    return {
      ...base,
      badge: deck,
      category: "card",
      detail: `${actor} drew ${card?.title ?? "a card"}.`,
    };
  }

  if (event.event_type === "PLAYER_CASH_DELTA") {
    return {
      ...base,
      badge: "Cash",
      category: "cash",
      detail: cashTransferSummary(game, events, event),
    };
  }

  if (event.event_type === "PROPERTY_OWNER_SET") {
    const ownerId = eventPayloadString(event, "owner_id");
    const propertyId = eventPayloadString(event, "property_id");
    return {
      ...base,
      badge: "Deed",
      category: "property",
      detail: ownerId
        ? `${playerName(game, ownerId)} now owns ${propertyName(propertyId)}.`
        : `${propertyName(propertyId)} returned to the bank.`,
      playerId: ownerId,
      playerName: playerName(game, ownerId),
    };
  }

  if (event.event_type === "PROPERTY_MORTGAGE_SET") {
    return {
      ...base,
      badge: "Mortgage",
      category: "property",
      detail: `${propertyName(eventPayloadString(event, "property_id"))} is ${eventPayloadBoolean(event, "mortgaged") ? "mortgaged" : "unmortgaged"}.`,
    };
  }

  if (event.event_type === "PROPERTY_IMPROVEMENTS_SET") {
    const houses = eventPayloadNumber(event, "houses") ?? 0;
    const hotel = eventPayloadBoolean(event, "hotel") ?? false;
    return {
      ...base,
      badge: "Build",
      category: "property",
      detail: hotel
        ? `${propertyName(eventPayloadString(event, "property_id"))} now has a hotel.`
        : `${propertyName(eventPayloadString(event, "property_id"))} now has ${houses} houses.`,
    };
  }

  if (event.event_type === "AUCTION_RESULT") {
    const winnerId = eventPayloadString(event, "winner_id");
    const winningBid = eventPayloadNumber(event, "winning_bid");
    return {
      ...base,
      badge: "Auction",
      category: "property",
      detail: `${playerName(game, winnerId)} won ${propertyName(eventPayloadString(event, "property_id"))} for ${
        winningBid === null ? "an unknown amount" : money(winningBid)
      }.`,
      playerId: winnerId,
      playerName: playerName(game, winnerId),
    };
  }

  if (/(DEAL|CONTRACT|OBLIGATION|NEGOTIATION)/.test(event.event_type)) {
    return {
      ...base,
      badge: "Deal",
      category: "deal",
      detail: eventPayloadString(event, "summary") ?? `${formatTitleCase(event.event_type)} was recorded.`,
    };
  }

  if (/AI|DECISION|MEMORY|DIALOGUE/.test(event.event_type)) {
    return {
      ...base,
      badge: "AI",
      category: "ai",
      detail: eventPayloadString(event, "summary") ?? `${formatTitleCase(event.event_type)} was recorded.`,
    };
  }

  if (/(BANKRUPT|GAME_OVER|WINNER)/.test(event.event_type)) {
    return {
      ...base,
      badge: "Game",
      category: "system",
      detail: eventPayloadString(event, "summary") ?? `${formatTitleCase(event.event_type)} was recorded.`,
    };
  }

  return null;
}

function GameLogChatPanel({
  events,
  game,
}: Readonly<{
  events: AcceptedEvent[];
  game: GameMetadata;
}>) {
  const [filtersOpen, setFiltersOpen] = useState(false);
  const logEndRef = useRef<HTMLLIElement | null>(null);
  const [enabledCategories, setEnabledCategories] = useState<ReadonlySet<GameLogCategory>>(
    () => new Set(gameLogCategories.map((category) => category.id)),
  );
  const entries = useMemo(
    () =>
      events
        .map((event) => gameLogEntryFromEvent(event, events, game))
        .filter((entry): entry is GameLogEntry => entry !== null && enabledCategories.has(entry.category))
        .slice(-120),
    [enabledCategories, events, game],
  );
  const latestEntryKey =
    entries.length > 0 ? `${entries.at(-1)?.sequence}:${entries.at(-1)?.eventId}` : "empty";

  useEffect(() => {
    logEndRef.current?.scrollIntoView?.({ block: "end" });
  }, [latestEntryKey]);

  function toggleCategory(category: GameLogCategory) {
    setEnabledCategories((current) => {
      const next = new Set(current);
      if (next.has(category)) {
        next.delete(category);
      } else {
        next.add(category);
      }
      return next;
    });
  }

  return (
    <section aria-label="Game log" className="grid min-h-0 gap-3" data-game-log-chat="" id="game-log">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-black uppercase text-[#2f2418]">Game log</h2>
        </div>
        <div className="relative">
          <button
            aria-expanded={filtersOpen}
            className="inline-flex items-center gap-1.5 rounded border border-[#2f2418]/25 bg-white px-2.5 py-1.5 text-xs font-black text-[#2f2418] hover:bg-[#fffbea] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#0f766e]"
            onClick={() => setFiltersOpen((current) => !current)}
            type="button"
          >
            <ScrollText aria-hidden="true" className="size-3.5" />
            Filters
          </button>
          {filtersOpen ? (
            <div className="absolute right-0 top-full z-30 mt-2 w-64 rounded-md border-2 border-[#2f2418]/35 bg-[#fff8e8] p-3 shadow-[0_18px_40px_rgba(47,36,24,0.2)]">
              <div className="grid gap-2">
                {gameLogCategories.map((category) => (
                  <label key={category.id} className="flex items-center gap-2 text-xs font-semibold text-[#2f2418]">
                    <input
                      checked={enabledCategories.has(category.id)}
                      className="size-4 accent-[#0f766e]"
                      onChange={() => toggleCategory(category.id)}
                      type="checkbox"
                    />
                    {category.label}
                  </label>
                ))}
              </div>
            </div>
          ) : null}
        </div>
      </div>

      <ol
        aria-live="polite"
        className="flex max-h-[min(58vh,38rem)] min-h-[22rem] flex-col gap-2 overflow-y-auto rounded-md border border-[#2f2418]/20 bg-white/65 p-3"
        data-game-log-scroll-region=""
      >
        {entries.length > 0
          ? (
          entries.map((entry) => {
            const player = entry.playerId ? game.players.find((candidate) => candidate.id === entry.playerId) : null;
            const playerColor = player ? getPlayerColor(game, player.seat_order) : "#173c45";
            const playerIcon = player ? getPlayerIcon(game, player.seat_order) : null;
            return (
              <li key={`${entry.sequence}:${entry.eventId}`} className="grid gap-1 rounded-md border border-[#2f2418]/10 bg-[#fffbea] px-3 py-2">
                <div className="flex items-center justify-between gap-2">
                  <span className="inline-flex min-w-0 items-center gap-1.5">
                    {playerIcon ? (
                      <span
                        aria-hidden="true"
                        className="grid size-6 shrink-0 place-items-center rounded-sm border border-[#2f2418]/40 text-xs"
                        style={{ backgroundColor: playerColor, color: "#fff" }}
                      >
                        {playerIcon}
                      </span>
                    ) : null}
                    <span className="truncate text-xs font-black text-[#2f2418]">{entry.playerName}</span>
                  </span>
                  <span className="shrink-0 text-[10px] font-bold uppercase text-[#6f604c]">{entry.time}</span>
                </div>
                <div className="text-sm font-semibold leading-5 text-[#2f2418]">{entry.detail}</div>
                <span className="w-fit rounded-sm bg-[#173c45] px-1.5 py-0.5 text-[9px] font-black uppercase text-[#f7d977]">
                  {entry.badge}
                </span>
              </li>
            );
          })
        )
          : null}
        <li ref={logEndRef} aria-hidden="true" className="h-px shrink-0" role="presentation" />
      </ol>
    </section>
  );
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
          <div className="mt-1 font-medium">{rejection.reason_code}</div>
          {messages.length > 0 ? (
            <ul className="mt-2 list-disc space-y-1 pl-4">
              {messages.map((message) => (
                <li key={message}>{message}</li>
              ))}
            </ul>
          ) : null}
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
          <div className="font-semibold">{label}</div>
          {result?.reason_code ? <div className="mt-1 text-xs">{result.reason_code}</div> : null}
          {result?.validation_errors?.[0]?.message ? (
            <div className="mt-1 text-xs">{result.validation_errors[0].message}</div>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function ActivePaymentPanel({
  game,
  snapshot,
}: Readonly<{
  game: GameMetadata;
  snapshot: GameStateResponse | undefined;
}>) {
  const payment = activePaymentRecord(snapshot);
  if (!payment) {
    return null;
  }

  const debtorId = typeof payment.debtor_id === "string" ? payment.debtor_id : null;
  const creditorId = typeof payment.creditor_id === "string" ? payment.creditor_id : null;
  const amountOwed = readNumber(payment.amount_owed);
  const amountPaid = readNumber(payment.amount_paid);
  const amountDue = Math.max(0, amountOwed - amountPaid);
  const reason = typeof payment.reason === "string" ? payment.reason : null;
  const creditorName = creditorId ? playerName(game, creditorId) : "the bank";

  return (
    <div
      aria-label="Active payment"
      role="status"
      className="mt-4 rounded-md border-2 border-[#2f2418] bg-[#fff8e8] p-3 text-[#2f2418] shadow-[0_6px_0_rgba(47,36,24,0.16)]"
    >
      <div className="flex items-start gap-3">
        <span className="grid size-9 shrink-0 place-items-center rounded-sm bg-[#173c45] text-[#f7d977]">
          <Banknote aria-hidden="true" className="size-5" />
        </span>
        <div className="min-w-0">
          <h3 className="text-xs font-black uppercase">Payment due</h3>
          <div className="mt-1 text-sm font-black">
            {playerName(game, debtorId)} owes {creditorName} {money(amountDue)}
          </div>
          <div className="mt-1 text-xs font-semibold text-[#6f604c]">{paymentReasonLabel(reason)}</div>
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
  const description = legalActionDescription(action);
  const Icon = model.icon;
  return (
    <div className="grid max-w-full gap-1">
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
      {description ? (
        <div className="max-w-52 text-xs font-medium leading-5 text-neutral-600" data-legal-action-description="">
          {description}
        </div>
      ) : null}
    </div>
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
  if (!player) {
    return null;
  }

  return (
    <section aria-label="Active player" className="rounded-md border border-neutral-200 bg-white p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-neutral-950">Active player</h2>
        </div>
        <span className="inline-flex items-center gap-1.5 rounded-full bg-teal-50 px-2 py-1 text-xs font-medium text-teal-700 ring-1 ring-inset ring-teal-200">
          <span aria-hidden="true" className="size-1.5 rounded-full bg-teal-600" />
          {formatTurnPhase(phase)}
        </span>
      </div>

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
    </section>
  );
}

function PlayerTrayRail({
  currentPlayerId,
  events,
  game,
  snapshot,
}: Readonly<{
  currentPlayerId: string | null;
  events: AcceptedEvent[];
  game: GameMetadata;
  snapshot: GameStateResponse | undefined;
}>) {
  const [selectedPlayerId, setSelectedPlayerId] = useState(currentPlayerId ?? game.players[0]?.id ?? null);

  useEffect(() => {
    if (!selectedPlayerId || !game.players.some((player) => player.id === selectedPlayerId)) {
      setSelectedPlayerId(currentPlayerId ?? game.players[0]?.id ?? null);
    }
  }, [currentPlayerId, game.players, selectedPlayerId]);

  const selectedPlayer = game.players.find((player) => player.id === selectedPlayerId) ?? game.players[0] ?? null;
  const selectedProperties = currentPlayerProperties(snapshot, selectedPlayer?.id);
  const propertyGroups = groupedPlayerProperties(selectedProperties);
  const commitmentEvents = playerRelatedCommitmentEvents(events, selectedPlayer?.id);

  return (
    <section
      id="player-trays"
      aria-label="Player trays"
      className="rounded-md border-2 border-[#2f2418]/30 bg-[#fff8e8] p-3 shadow-[0_14px_30px_rgba(47,36,24,0.14)]"
    >
      <div
        aria-label="Player tray tabs"
        className="flex flex-wrap items-end overflow-hidden rounded-t-md border border-[#2f2418]/20 border-b-0 bg-[#d8caa8] px-1 pt-1"
        role="tablist"
      >
        {game.players.map((player) => {
          const isCurrent = player.id === currentPlayerId;
          const isSelected = player.id === selectedPlayer?.id;
          const color = getPlayerColor(game, player.seat_order);
          const icon = getPlayerIcon(game, player.seat_order);
          return (
            <button
              key={player.id}
              aria-controls="active-player-tray-panel"
              aria-selected={isSelected}
              className={cn(
                "min-h-12 min-w-0 basis-1/2 border border-[#2f2418]/20 border-b-0 px-2.5 py-2 text-left transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#0f766e] sm:basis-1/4 lg:flex-none lg:basis-auto lg:min-w-[8.5rem] lg:max-w-[12rem]",
                "flex items-center gap-2 rounded-t-md",
                isSelected
                  ? "relative z-10 -mb-px bg-white text-[#2f2418] shadow-[inset_0_3px_0_#d7a84c]"
                  : "bg-[#efe6cf] text-[#2f2418] hover:bg-[#fff8e8]",
              )}
              data-current-player={isCurrent ? "true" : undefined}
              id={`${player.id}-tray-tab`}
              onClick={() => setSelectedPlayerId(player.id)}
              role="tab"
              type="button"
            >
              <span
                aria-label={`${player.name} token`}
                className="grid size-8 shrink-0 place-items-center rounded-sm border-2 border-[#2f2418] text-base font-black"
                data-token-icon={icon}
                role="img"
                style={{ backgroundColor: color, color: "#fff" }}
              >
                <span aria-hidden="true" className="leading-none" data-player-token-icon="">
                  {icon}
                </span>
              </span>
              <span className="min-w-0">
                <span className="block truncate text-xs font-black">{player.name}</span>
                <span className={cn("mt-0.5 block truncate text-[10px] font-bold", isSelected ? "text-[#f7d977]" : "text-[#6f604c]")}>
                  {isCurrent ? "Current turn" : playerCash(player, snapshot)}
                </span>
              </span>
            </button>
          );
        })}
      </div>

      {selectedPlayer ? (
        <article
          aria-label={`${selectedPlayer.name} active player tray${selectedPlayer.id === currentPlayerId ? " current turn" : ""}`}
          className="rounded-b-md rounded-tr-md border border-[#b99768]/70 bg-white/80 p-3 text-[#2f2418]"
          data-active-player-tray=""
          data-current-player={selectedPlayer.id === currentPlayerId ? "true" : undefined}
          id="active-player-tray-panel"
          role="tabpanel"
        >
          <div className="grid gap-3 lg:grid-cols-[minmax(0,0.65fr)_minmax(0,1.35fr)]">
            <div className="rounded-md border border-[#2f2418]/15 bg-[#fffbea] p-3">
              <div className="flex items-start gap-3">
                <span
                  aria-label={`${selectedPlayer.name} token`}
                  className="grid size-12 shrink-0 place-items-center rounded-md border-2 border-[#2f2418] text-2xl font-black shadow-[0_3px_0_rgba(47,36,24,0.25)]"
                  data-token-icon={getPlayerIcon(game, selectedPlayer.seat_order)}
                  role="img"
                  style={{ backgroundColor: getPlayerColor(game, selectedPlayer.seat_order), color: "#fff" }}
                >
                  <span aria-hidden="true" className="leading-none" data-player-token-icon="">
                    {getPlayerIcon(game, selectedPlayer.seat_order)}
                  </span>
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="truncate text-lg font-black">{selectedPlayer.name}</h3>
                    {selectedPlayer.id === currentPlayerId ? (
                      <span className="rounded-sm bg-[#173c45] px-1.5 py-0.5 text-[10px] font-black uppercase text-[#f7d977]">
                        Current turn
                      </span>
                    ) : null}
                  </div>
                  <div className="mt-1 text-2xl font-black leading-none text-[#173c45]">{playerCash(selectedPlayer, snapshot)}</div>
                  <div className="mt-1 text-sm font-semibold text-[#6f604c]">{playerPosition(selectedPlayer, snapshot)}</div>
                  <div className="mt-2 text-xs font-black uppercase text-[#6f604c]">{formatControllerType(selectedPlayer.controller_type)}</div>
                </div>
              </div>

            </div>

            <div className="grid gap-3">
              {propertyGroups.length > 0 ? (
                propertyGroups.map((group) => (
                  <section key={group.id} aria-label={`${selectedPlayer.name} ${group.label} deeds`} className="grid gap-2">
                    <div className="flex items-center gap-2">
                      <span aria-hidden="true" className="h-3 w-10 rounded-sm border border-[#2f2418]/20" style={{ backgroundColor: group.color }} />
                      <h4 className="text-xs font-black uppercase text-[#6f604c]">{group.label}</h4>
                    </div>
                    <div className="grid gap-2 sm:grid-cols-2 2xl:grid-cols-3">
                      {group.properties.map((property) => (
                        <PropertyDeedCard
                          key={property.id}
                          game={game}
                          ownership={propertyOwnershipForTray(property, selectedPlayer.id)}
                          property={property.property}
                          variant="compact"
                        />
                      ))}
                    </div>
                  </section>
                ))
              ) : null}

              <section aria-label={`${selectedPlayer.name} contracts and obligations`} className="rounded border border-[#2f2418]/15 bg-[#fffbea] p-3">
                <h4 className="text-xs font-black uppercase text-[#6f604c]">Contracts & obligations</h4>
                {commitmentEvents.length > 0 ? (
                  <ul className="mt-2 grid gap-2">
                    {commitmentEvents.map((event) => (
                      <li key={event.id} className="rounded bg-white/70 px-2 py-1.5 text-xs font-semibold text-[#2f2418]">
                        <span className="block font-black">{formatTitleCase(event.event_type)}</span>
                        <span className="mt-0.5 block text-[#6f604c]">
                          {eventPayloadString(event, "summary") ?? `Recorded sequence ${event.sequence}.`}
                        </span>
                      </li>
                    ))}
                  </ul>
                ) : null}
              </section>
            </div>
          </div>
        </article>
      ) : null}
    </section>
  );
}

function TableViewTabs({
  activeView,
  onChange,
}: Readonly<{
  activeView: TableView;
  onChange: (view: TableView) => void;
}>) {
  return (
    <div
      aria-label="Table views"
      className="flex flex-wrap gap-1 rounded-md border-2 border-[#2f2418]/30 bg-[#fff8e8] p-1"
      role="tablist"
    >
      {tableViews.map((view) => (
        <button
          key={view.id}
          aria-controls={`${view.id}-panel`}
          aria-selected={activeView === view.id}
          className={cn(
            "rounded-sm px-3 py-2 text-xs font-black uppercase transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#0f766e]",
            activeView === view.id
              ? "bg-[#173c45] text-[#f7d977] shadow-[0_2px_0_rgba(47,36,24,0.2)]"
              : "text-[#2f2418] hover:bg-white/80",
          )}
          id={`${view.id}-tab`}
          onClick={() => onChange(view.id)}
          role="tab"
          type="button"
        >
          {view.label}
        </button>
      ))}
    </div>
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
                    <div className="font-semibold text-neutral-950">{property.name}</div>
                    <span className="text-xs font-medium text-neutral-600">{money(property.price)}</span>
                  </div>
                  <div className="mt-1 text-xs text-neutral-600">
                    {property.mortgaged ? "Mortgaged" : "Active"}
                    {property.hotel ? " / Hotel" : property.houses > 0 ? ` / ${property.houses} houses` : ""}
                  </div>
                </li>
              ))}
            </ul>
          ) : null}
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
          ) : null}
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
          {summary.detail ? <div className="mt-1 text-sm text-neutral-700">{summary.detail}</div> : null}
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
          <h2 className="text-sm font-semibold text-neutral-950">{player ? `${player.name} trade request` : "Trade request"}</h2>
        </div>
        <span className="inline-flex w-fit shrink-0 items-center rounded-full bg-purple-50 px-2 py-1 text-xs font-medium text-purple-700 ring-1 ring-inset ring-purple-200">
          Trade
        </span>
      </div>
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
          <div>Max rounds: {cutoffs.max_rounds ?? "not set"}</div>
          <div>Proposal limit/player: {cutoffs.max_proposals_per_player ?? "not set"}</div>
        </div>
      </section>
    </>
  );
}

export function GamePlaySurface({ gameId, initialGame, apiBaseUrl }: GamePlaySurfaceProps) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const baseUrl = backendBaseUrl(apiBaseUrl);
  const [activeTableView, setActiveTableView] = useState<TableView>("properties");
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

  const playersById = useMemo(() => new Map(game.players.map((player) => [player.id, player])), [game.players]);
  const visibleEvents = mergeEvents(eventsQuery.data ?? [], acceptedEvents);
  const latestDrawnCard = useMemo(
    () => latestDrawnCardFromEvents(visibleEvents, playersById),
    [playersById, visibleEvents],
  );
  const latestRoll = useMemo(
    () => latestRollFromEvents(visibleEvents, playersById),
    [playersById, visibleEvents],
  );
  const displayedLastRoll = phase === "START_TURN" && !boardMotion ? null : latestRoll;
  const latestDrawnCardEventId = latestDrawnCard?.eventId ?? null;

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
        landedSpaceName: nextMotion.landedSpaceName,
        motionKey: `${nextMotion.motionKey}:dice-reveal`,
        playerId: nextMotion.playerId,
        playerName: nextMotion.playerName,
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

    return undefined;
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
      setAiStepResult(null);
      setDismissedCardEventId(latestDrawnCardEventId ?? revealedCardEventId);
      setRevealedCardEventId(null);
      setPendingActionType(action.type);
      setQueuedBoardMotion(null);
      setBoardMotion(
        action.type === "ROLL_DICE"
          ? {
              motionKey: `${action.actor_id}:${action.expected_event_sequence}:rolling`,
              playerId: action.actor_id,
              playerName: playerNameForMotion(game.players, action.actor_id),
              status: "rolling",
            }
          : null,
      );
    },
    onSuccess: (result, action) => {
      if (result.status === "accepted") {
        setAcceptedEvents((current) => mergeEvents(current, result.accepted_events));
        playBoardMotion(boardMotionFromAcceptedEvents(result.accepted_events, action.actor_id, game.players, stateQuery.data));
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
      setDismissedCardEventId(latestDrawnCardEventId ?? revealedCardEventId);
      setRevealedCardEventId(null);
      setQueuedBoardMotion(null);
      setBoardMotion(null);
    },
    onSuccess: (result) => {
      setAiStepResult(result);
      if (result.accepted_events.length > 0) {
        setAcceptedEvents((current) => mergeEvents(current, result.accepted_events));
        playBoardMotion(boardMotionFromAcceptedEvents(result.accepted_events, result.player_id, game.players, stateQuery.data));
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
  const actionsByGroup = useMemo(() => {
    const grouped: Record<ActionGroup, LegalAction[]> = {
      turn: [],
      purchase: [],
      payment: [],
      jail: [],
    };
    for (const action of legalActions) {
      const model = actionModels[action.type];
      if (!model || action.type === "END_TURN" || action.type === "DECLARE_BANKRUPTCY" || isAuctionAction(action)) {
        continue;
      }
      grouped[model.group].push(action);
    }
    return grouped;
  }, [legalActions]);
  const endTurnAction = legalActions.find((action) => action.type === "END_TURN") ?? null;
  const gameAiBlocked = game.status === "AI_BLOCKED";
  const gameEnded = game.status.toLowerCase() === "ended";
  const listedBankruptcyAction = legalActions.find((action) => action.type === "DECLARE_BANKRUPTCY") ?? null;
  const voluntaryBankruptcyAction: LegalAction | null =
    !listedBankruptcyAction &&
    stateQuery.data &&
    currentPlayer?.controller_type === "human" &&
    !gameEnded &&
    !gameAiBlocked
      ? {
          actor_id: currentPlayer.id,
          type: "DECLARE_BANKRUPTCY",
          payload: { creditor_id: null },
          expected_state_hash: stateQuery.data.state_hash,
          expected_event_sequence: stateQuery.data.event_sequence,
          description: "Give up and leave the game.",
          schema: {},
        }
      : null;
  const bankruptcyAction = listedBankruptcyAction ?? voluntaryBankruptcyAction;
  const latestAuditRejection = latestRejectedAction(rejectedActionsQuery.data ?? []);
  const visibleRejection = localRejectedAction ?? latestAuditRejection;
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
  const controlsDisabled = gameEnded || gameAiBlocked || legalActionsLoading || submitAction.isPending || aiStep.isPending;
  const turnAiPlayer = stateActivePlayer?.controller_type === "ai" ? stateActivePlayer : null;
  const auctionAiStepPlayer =
    game.players.find(
      (player) =>
        player.controller_type === "ai" &&
        auctionLegalActions.some((action) => action.actor_id === player.id && isAuctionAction(action)),
    ) ?? null;
  const activeAiPlayer = activeAuction ? auctionAiStepPlayer : turnAiPlayer;
  const manualAiStepMode: AiStepMode = auctionAiStepPlayer ? "auction_ai_bidder" : "manual";
  const autoAiStepMode: AiStepMode = auctionAiStepPlayer ? "auction_ai_bidder" : "auto";
  const directActionControlsDisabled = controlsDisabled || Boolean(turnAiPlayer);
  const aiStepStateBlocked = gameEnded || gameAiBlocked || !stateQuery.data || stateQuery.isFetching || aiStep.isPending;
  const aiStepBlocked = !activeAiPlayer || aiStepStateBlocked;
  const manualAiStepDisabled = controlsDisabled || aiStepBlocked;
  const autoStepKey = activeAiPlayer && stateQuery.data ? `${autoAiStepMode}:${activeAiPlayer.id}:${stateHash}:${eventSequence}` : null;
  const boardMotionInProgress = boardMotion?.status === "rolling" || boardMotion?.status === "moving";
  const auctionActionsLoading =
    Boolean(activeAuction) && auctionLegalActionsQueries.some((query) => query.isLoading);
  const auctionControlsDisabled = controlsDisabled || auctionActionsLoading;

  function isAiControlledActor(action: LegalAction): boolean {
    return playersById.get(action.actor_id)?.controller_type === "ai";
  }

  function isAuctionActionDisabled(action: LegalAction): boolean {
    if (action.type === "START_AUCTION") {
      return Boolean(turnAiPlayer) || isAiControlledActor(action);
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
    return !turnAiPlayer;
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
    if (
      gameAiBlocked ||
      !autoStepAi ||
      !activeAiPlayer ||
      !autoStepKey ||
      aiStep.isPending ||
      submitAction.isPending ||
      boardMotionInProgress
    ) {
      return;
    }
    if (stateQuery.isFetching || gameQuery.isFetching || lastAutoStepKey === autoStepKey) {
      return;
    }
    setLastAutoStepKey(autoStepKey);
    handleAiStep(autoAiStepMode);
  }, [
    activeAiPlayer,
    aiStep.isPending,
    autoAiStepMode,
    autoStepAi,
    autoStepKey,
    boardMotionInProgress,
    gameQuery.isFetching,
    gameAiBlocked,
    lastAutoStepKey,
    stateQuery.isFetching,
    submitAction.isPending,
  ]);

  const hasAuctionContext = Boolean(activeAuction) || auctionLegalActions.length > 0;
  const turnResultSummary = lastTurnResultFromEvents(visibleEvents, game);
  const runningLogPanel = (
    <section
      aria-label="Running log panel"
      className="grid min-h-0 gap-3 rounded-md border-2 border-[#2f2418]/30 bg-[#fff8e8] p-3 shadow-[0_14px_30px_rgba(47,36,24,0.12)]"
      data-testid="running-log-panel"
    >
      <GameLogChatPanel events={visibleEvents} game={game} />
    </section>
  );
  const tableViewPanel = (
    <section
      aria-label="Table workspace"
      className="grid min-h-0 gap-3 rounded-md border-2 border-[#2f2418]/30 bg-[#fff8e8] p-3 shadow-[0_14px_30px_rgba(47,36,24,0.12)]"
      data-testid="secondary-table-panel"
    >
      <TableViewTabs activeView={activeTableView} onChange={setActiveTableView} />
      <div
        aria-labelledby={`${activeTableView}-tab`}
        className="min-h-0"
        id={`${activeTableView}-panel`}
        role="tabpanel"
      >
        {activeTableView === "properties" ? (
          <div id="properties">
            <PropertyManagementPanel
              controlsDisabled={directActionControlsDisabled}
              game={game}
              legalActions={legalActions}
              onSubmit={handleSubmit}
              pendingActionType={pendingActionType}
              snapshot={stateQuery.data}
            />
          </div>
        ) : null}
        {activeTableView === "deals" ? (
          <div id="deals">
            <NegotiationPanel apiBaseUrl={baseUrl} game={game} gameId={gameId} />
          </div>
        ) : null}
        {activeTableView === "contracts" ? (
          <div id="contracts">
            <ContractsPanel
              apiBaseUrl={baseUrl}
              events={visibleEvents}
              game={game}
              gameId={gameId}
              rejectedActions={rejectedActionsQuery.data ?? []}
            />
          </div>
        ) : null}
        {activeTableView === "ai-notebook" ? (
          <div id="ai-notebook">
            <AiAuditPanel apiBaseUrl={baseUrl} game={game} gameId={gameId} />
          </div>
        ) : null}
      </div>
    </section>
  );
  const turnControlsPanel = (
    <section id="current-turn" aria-label="Turn controls" className="rounded-md border border-neutral-200 bg-white p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-neutral-950">Turn controls</h2>
        </div>
      </div>

      {legalActionsQuery.isError ? (
        <div className="mt-3 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
          Available moves unavailable.
        </div>
      ) : null}

      {activeAiPlayer && !gameAiBlocked ? (
        <div className="mt-4 grid gap-3 rounded-md border border-purple-200 bg-purple-50 p-3">
          <div className="flex flex-wrap items-center gap-2">
            <Button onClick={() => handleAiStep(manualAiStepMode)} disabled={manualAiStepDisabled} variant="ai">
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

      {(!activeAiPlayer || gameAiBlocked) && aiStepResult ? (
        <div className="mt-4">
          <AiStepStatusPanel isThinking={aiStep.isPending} result={aiStepResult} />
        </div>
      ) : null}

      <ActivePaymentPanel game={game} snapshot={stateQuery.data} />

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
    <div className="mx-auto grid max-w-[1600px] gap-4 px-4 py-4 sm:px-6 lg:px-8">
      <GameTableMenu
        bankruptcyAction={bankruptcyAction}
        bankruptcyDisabled={bankruptcyAction ? !canSubmitDirectAction(bankruptcyAction) : true}
        currentPlayerName={currentPlayer?.name ?? null}
        isEnding={endGameMutation.isPending}
        message={sessionMessage}
        onDeclareBankruptcy={handleSubmit}
        onEndGame={handleEndGame}
        onLoadGame={handleLoadGame}
        onSaveGame={handleSaveGame}
        onSelectTableView={setActiveTableView}
        onToggleLoadGames={() => setShowLoadGames((current) => !current)}
        phase={formatTurnPhase(phase)}
        savedGames={savedGames}
        showLoadGames={showLoadGames}
        status={formatGameStatus(game.status)}
      />
      <div className="grid gap-4 xl:grid-cols-[minmax(420px,500px)_minmax(0,1fr)] xl:items-start">
        <aside className="order-4 min-h-0 xl:sticky xl:top-4 xl:order-1 xl:row-span-2 xl:max-h-[calc(100vh-2rem)] xl:overflow-y-auto">
          {runningLogPanel}
        </aside>

        <main className="order-1 grid content-start gap-3 xl:order-2">
          <div id="game-board">
            <ClassicGameBoard
              drawnCard={visibleDrawnCard}
              game={game}
              lastRoll={displayedLastRoll}
              motion={boardMotion ?? undefined}
              onDismissDrawnCard={() => setDismissedCardEventId(visibleDrawnCard?.eventId ?? null)}
              snapshot={stateQuery.data}
            />
          </div>
        </main>

        <div className="order-2 grid gap-4 xl:order-3 xl:col-start-2">
          <PlayerTrayRail
            currentPlayerId={currentPlayer?.id ?? null}
            events={visibleEvents}
            game={game}
            snapshot={stateQuery.data}
          />
          <div className="grid gap-4">
            {turnControlsPanel}
            <div className="grid gap-4 lg:grid-cols-2">
              <ActivePlayerPanel player={currentPlayer} phase={phase} />
              <section aria-label="Turn context" className="grid content-start gap-4">
                {hasAuctionContext ? (
                  <AuctionPanel
                    controlsDisabled={auctionControlsDisabled}
                    events={visibleEvents}
                    game={game}
                    activeAiPlayerId={turnAiPlayer?.id ?? null}
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
            {tableViewPanel}
          </div>
        </div>
      </div>

      {visibleRejection ? <RejectedActionAlert rejection={visibleRejection} /> : null}
    </div>
  );
}
