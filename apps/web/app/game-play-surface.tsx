"use client";

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
  Hourglass,
  KeyRound,
  Loader2,
  LogOut,
  ShieldAlert,
  UserRound,
} from "lucide-react";
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
import { readGame, type GameMetadata } from "../lib/api/games";
import { readRejectedActions, type RejectedActionRecord } from "../lib/api/rejected-actions";
import { cn } from "../lib/ui";
import { AiAuditPanel } from "./ai-audit-panel";
import { AuctionPanel, isAuctionAction, readActiveAuction } from "./auction-panel";
import { ContractsPanel } from "./contracts-panel";
import { ClassicGameBoard, getPlayerColor } from "./game-board";
import { NegotiationPanel } from "./negotiation-panel";
import { PropertyManagementPanel } from "./property-management";

type GamePlaySurfaceProps = {
  gameId: string;
  initialGame: GameMetadata;
  apiBaseUrl?: string;
};

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

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
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

function playerCash(player: GameMetadata["players"][number]): string {
  return `$${readNumber(player.state.cash).toLocaleString("en-US")}`;
}

function playerPosition(player: GameMetadata["players"][number]): string {
  return String(readNumber(player.state.position));
}

function turnRecord(snapshot: GameStateResponse | undefined): Record<string, unknown> | null {
  const turn = snapshot?.state.turn;
  return isRecord(turn) ? turn : null;
}

function activePhase(game: GameMetadata, snapshot: GameStateResponse | undefined): string {
  const phase = turnRecord(snapshot)?.phase;
  return typeof phase === "string" && phase ? phase : (game.current_phase ?? "Unassigned");
}

function activePlayer(game: GameMetadata, snapshot: GameStateResponse | undefined): GameMetadata["players"][number] | null {
  const turn = turnRecord(snapshot);
  const playerId = typeof turn?.current_player_id === "string" ? turn.current_player_id : null;
  if (playerId) {
    const byId = game.players.find((player) => player.id === playerId);
    if (byId) {
      return byId;
    }
  }

  const playerIndex = typeof turn?.current_player_index === "number" ? turn.current_player_index : 0;
  return game.players.find((player) => player.seat_order === playerIndex) ?? game.players[0] ?? null;
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

function latestRejectedAction(records: RejectedActionRecord[]): RejectedActionRecord | null {
  if (records.length === 0) {
    return null;
  }
  return [...records].sort((left, right) => Date.parse(right.created_at) - Date.parse(left.created_at))[0] ?? null;
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
        model.variant === "danger" && "bg-rose-700 hover:bg-rose-800 focus-visible:outline-rose-700",
      )}
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
      className="min-h-9 justify-start bg-white px-2.5 py-1.5 text-xs text-neutral-500 ring-1 ring-inset ring-neutral-200"
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
          <p className="mt-1 text-xs text-neutral-600">Current backend turn context.</p>
        </div>
        <span className="inline-flex items-center gap-1.5 rounded-full bg-teal-50 px-2 py-1 text-xs font-medium text-teal-700 ring-1 ring-inset ring-teal-200">
          <span aria-hidden="true" className="size-1.5 rounded-full bg-teal-600" />
          {phase}
        </span>
      </div>

      {player ? (
        <dl className="mt-4 grid grid-cols-2 gap-3 text-sm">
          <div>
            <dt className="text-xs font-medium uppercase text-neutral-500">Name</dt>
            <dd className="mt-1 font-medium text-neutral-950">{player.name}</dd>
          </div>
          <div>
            <dt className="text-xs font-medium uppercase text-neutral-500">Type</dt>
            <dd className="mt-1 inline-flex items-center gap-1.5 text-neutral-800">
              {player.controller_type === "ai" ? (
                <Bot aria-hidden="true" className="size-3.5 text-purple-700" />
              ) : (
                <UserRound aria-hidden="true" className="size-3.5 text-teal-700" />
              )}
              {player.controller_type}
            </dd>
          </div>
          <div>
            <dt className="text-xs font-medium uppercase text-neutral-500">Cash</dt>
            <dd className="mt-1 font-medium text-neutral-950">{playerCash(player)}</dd>
          </div>
          <div>
            <dt className="text-xs font-medium uppercase text-neutral-500">Position</dt>
            <dd className="mt-1 font-medium text-neutral-950">{playerPosition(player)}</dd>
          </div>
        </dl>
      ) : (
        <p className="mt-4 text-sm text-neutral-600">No active player assigned.</p>
      )}
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
        <p className="mt-1 text-xs text-neutral-600">Seat order, type, color, position, and status.</p>
      </div>
      <div className="overflow-x-auto">
        <table className="min-w-full text-left text-xs">
          <thead className="bg-neutral-50 text-[10px] uppercase text-neutral-500">
            <tr>
              <th scope="col" className="px-3 py-2 font-semibold">
                Player
              </th>
              <th scope="col" className="px-3 py-2 font-semibold">
                Type
              </th>
              <th scope="col" className="px-3 py-2 font-semibold">
                Color
              </th>
              <th scope="col" className="px-3 py-2 font-semibold">
                Pos
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
                      {player.controller_type}
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
                      {player.status}
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
          Game details
        </h2>
        <dl className="mt-4 grid gap-3 text-sm">
          <div>
            <dt className="text-xs font-medium uppercase text-neutral-500">Status</dt>
            <dd className="mt-1 text-neutral-950">{game.status}</dd>
          </div>
          <div>
            <dt className="text-xs font-medium uppercase text-neutral-500">Phase</dt>
            <dd className="mt-1 text-neutral-950">{phase}</dd>
          </div>
          <div>
            <dt className="text-xs font-medium uppercase text-neutral-500">Seed</dt>
            <dd className="mt-1 break-all text-neutral-950">{game.seed ?? "Generated by backend"}</dd>
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
  const queryClient = useQueryClient();
  const baseUrl = backendBaseUrl(apiBaseUrl);
  const [localRejectedAction, setLocalRejectedAction] = useState<ActionRejectedResponse | null>(null);
  const [acceptedEvents, setAcceptedEvents] = useState<AcceptedEvent[]>([]);
  const [pendingActionType, setPendingActionType] = useState<string | null>(null);
  const [aiStepResult, setAiStepResult] = useState<AiStepResponse | null>(null);
  const [autoStepAi, setAutoStepAi] = useState(false);
  const [lastAutoStepKey, setLastAutoStepKey] = useState<string | null>(null);

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
  const currentPlayer = activePlayer(game, stateQuery.data);
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

    source.addEventListener("message", invalidate);
    source.addEventListener("game_event", invalidate);
    source.onerror = () => undefined;

    return () => {
      source.removeEventListener("message", invalidate);
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
    },
    onSuccess: (result) => {
      if (result.status === "accepted") {
        setAcceptedEvents(result.accepted_events);
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

  const aiStep = useMutation({
    mutationFn: (mode: "manual" | "auto") =>
      submitAiStep({
        gameId,
        baseUrl,
        input: {
          player_id: currentPlayer?.id ?? "",
          decision_type: "action_decision",
          mandatory: true,
          request_context: { mode },
        },
      }),
    onMutate: () => {
      setAiStepResult(null);
    },
    onSuccess: (result) => {
      setAiStepResult(result);
      if (result.accepted_events.length > 0) {
        setAcceptedEvents(result.accepted_events);
      }
      void invalidateGameplayData();
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
  const legalActionsLoading = stateQuery.isLoading || legalActionsQuery.isLoading || legalActionsQuery.isFetching;
  const controlsDisabled = legalActionsLoading || submitAction.isPending || aiStep.isPending;
  const activeAiPlayer = currentPlayer?.controller_type === "ai" ? currentPlayer : null;
  const autoStepKey = activeAiPlayer && stateQuery.data ? `${activeAiPlayer.id}:${stateHash}:${eventSequence}` : null;
  const auctionControlsDisabled =
    controlsDisabled || (Boolean(activeAuction) && auctionLegalActionsQueries.some((query) => query.isLoading || query.isFetching));

  function handleSubmit(action: LegalAction) {
    submitAction.mutate(action);
  }

  function handleAiStep(mode: "manual" | "auto") {
    if (!activeAiPlayer || aiStep.isPending) {
      return;
    }
    aiStep.mutate(mode);
  }

  useEffect(() => {
    if (!autoStepAi || !activeAiPlayer || !autoStepKey || aiStep.isPending || submitAction.isPending) {
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
    lastAutoStepKey,
    stateQuery.isFetching,
    submitAction.isPending,
  ]);

  return (
    <div className="mx-auto grid max-w-7xl gap-6 px-4 py-6 sm:px-6 lg:grid-cols-[minmax(0,1fr)_340px] lg:px-8">
      <div className="grid content-start gap-4">
        <ClassicGameBoard game={game} />
        <PropertyManagementPanel
          controlsDisabled={controlsDisabled}
          game={game}
          legalActions={legalActions}
          onSubmit={handleSubmit}
          pendingActionType={pendingActionType}
          snapshot={stateQuery.data}
        />
        <NegotiationPanel apiBaseUrl={baseUrl} game={game} gameId={gameId} />
        <AiAuditPanel apiBaseUrl={baseUrl} game={game} gameId={gameId} />
      </div>

      <aside className="grid content-start gap-4">
        <ActivePlayerPanel player={currentPlayer} phase={phase} />

        <AuctionPanel
          controlsDisabled={auctionControlsDisabled}
          events={visibleEvents}
          game={game}
          legalActions={auctionLegalActions}
          onSubmit={handleSubmit}
          pendingActionType={pendingActionType}
          snapshot={stateQuery.data}
        />

        <section aria-label="Turn controls" className="rounded-md border border-neutral-200 bg-white p-4">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold text-neutral-950">Turn controls</h2>
              <p className="mt-1 text-xs text-neutral-600">Only actions returned by /legal-actions are enabled.</p>
            </div>
            {legalActionsLoading ? (
              <span className="inline-flex items-center gap-1.5 rounded-full bg-neutral-100 px-2 py-1 text-xs font-medium text-neutral-600">
                <Hourglass aria-hidden="true" className="size-3" />
                Loading legal actions
              </span>
            ) : null}
          </div>

          {legalActionsQuery.isError ? (
            <div className="mt-3 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
              Legal actions unavailable.
            </div>
          ) : null}

          {activeAiPlayer ? (
            <div className="mt-4 grid gap-3 rounded-md border border-purple-200 bg-purple-50 p-3">
              <div className="flex flex-wrap items-center gap-2">
                <Button onClick={() => handleAiStep("manual")} disabled={aiStep.isPending}>
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
              disabled={controlsDisabled}
              pendingActionType={pendingActionType}
              onSubmit={handleSubmit}
            />
            <div className="rounded-md border border-neutral-200 bg-neutral-50 p-3">
              <h3 className="text-xs font-semibold uppercase text-neutral-500">End turn</h3>
              <div className="mt-2 flex flex-wrap gap-2">
                <EndTurnControl
                  endTurnAction={endTurnAction}
                  disabled={controlsDisabled}
                  pendingActionType={pendingActionType}
                  onSubmit={handleSubmit}
                />
                {!endTurnAction ? <span className="self-center text-xs text-neutral-500">Unavailable</span> : null}
              </div>
            </div>
            <ActionGroupPanel
              title={groupTitles.purchase}
              actions={actionsByGroup.purchase}
              disabled={controlsDisabled}
              pendingActionType={pendingActionType}
              onSubmit={handleSubmit}
            />
            <ActionGroupPanel
              title={groupTitles.payment}
              actions={actionsByGroup.payment}
              disabled={controlsDisabled}
              pendingActionType={pendingActionType}
              onSubmit={handleSubmit}
            />
            <ActionGroupPanel
              title={groupTitles.jail}
              actions={actionsByGroup.jail}
              disabled={controlsDisabled}
              pendingActionType={pendingActionType}
              onSubmit={handleSubmit}
            />
          </div>
        </section>

        {visibleRejection ? <RejectedActionAlert rejection={visibleRejection} /> : null}

        <ContractsPanel
          apiBaseUrl={baseUrl}
          events={visibleEvents}
          game={game}
          gameId={gameId}
          rejectedActions={rejectedActionsQuery.data ?? []}
        />
        <PlayerTable game={game} />
        <GameDetails game={game} phase={phase} />
      </aside>
    </div>
  );
}
