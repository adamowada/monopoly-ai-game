"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BadgeDollarSign,
  Bot,
  CheckCircle2,
  Clock3,
  FileText,
  Loader2,
  MessageSquareText,
  RefreshCw,
  Send,
  ShieldAlert,
  Split,
  XCircle,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { Button } from "../components/ui/button";
import { submitAiStep, type AiDecisionType, type AiStepResponse } from "../lib/api/gameplay";
import {
  acceptDeal,
  createDeal,
  createNegotiation,
  createNegotiationMessage,
  expireNegotiation,
  readDeals,
  readNegotiationMessages,
  readNegotiations,
  rejectDeal,
  type Deal,
  type DealMutationResponse,
  type DealTerm,
  type DealTermKind,
  type Negotiation,
  type NegotiationMutationResponse,
  type ValidationError,
} from "../lib/api/negotiations";
import type { GameMetadata } from "../lib/api/games";
import { cn } from "../lib/ui";

type NegotiationPanelProps = {
  gameId: string;
  game: GameMetadata;
  apiBaseUrl?: string;
};

type TermDraft = {
  kind: DealTermKind;
  from_player_id: string;
  to_player_id: string;
  amount: string;
  property_id: string;
  due_round: string;
  percentage: string;
  summary: string;
};

const termKinds: DealTermKind[] = [
  "cash_transfer",
  "property_transfer",
  "loan",
  "option",
  "rent_share",
  "risk_transfer",
];

function playerName(game: GameMetadata, playerId: string | null | undefined): string {
  if (!playerId) {
    return "Unassigned";
  }
  return game.players.find((player) => player.id === playerId)?.name ?? playerId;
}

function playerNames(game: GameMetadata, playerIds: string[]): string {
  return playerIds.map((playerId) => playerName(game, playerId)).join(", ");
}

function statusLabel(status: string): string {
  return status.charAt(0).toUpperCase() + status.slice(1);
}

function readString(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function readNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function formatMoney(value: number | null): string {
  return value === null ? "unspecified cash" : `$${value.toLocaleString("en-US")}`;
}

function termSummary(game: GameMetadata, term: DealTerm): string {
  const summary = readString(term.summary);
  if (summary) {
    return summary;
  }

  const from = playerName(game, readString(term.from_player_id));
  const to = playerName(game, readString(term.to_player_id));
  const amount = formatMoney(readNumber(term.amount));
  const propertyId = readString(term.property_id) ?? "unspecified property";
  const percentage = readNumber(term.percentage);
  if (term.kind === "cash_transfer") {
    return `${from} transfers ${amount} to ${to}`;
  }
  if (term.kind === "property_transfer") {
    return `${from} transfers ${propertyId} to ${to}`;
  }
  if (term.kind === "loan") {
    return `${from} lends ${amount} to ${to}`;
  }
  if (term.kind === "option") {
    return `${to} receives an option on ${propertyId}`;
  }
  if (term.kind === "rent_share") {
    return `${from} shares ${percentage ?? 0}% rent with ${to}`;
  }
  return `${to} receives risk coverage from ${from}`;
}

function defaultTermDraft(game: GameMetadata, participants: string[]): TermDraft {
  const first = participants[0] ?? game.players[0]?.id ?? "";
  const second = participants.find((playerId) => playerId !== first) ?? game.players[1]?.id ?? first;
  return {
    kind: "cash_transfer",
    from_player_id: first,
    to_player_id: second,
    amount: "100",
    property_id: "property_reading_railroad",
    due_round: "2",
    percentage: "25",
    summary: "",
  };
}

function numberFromDraft(value: string, fallback: number): number {
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function termFromDraft(draft: TermDraft): DealTerm {
  const amount = numberFromDraft(draft.amount, 0);
  const dueRound = numberFromDraft(draft.due_round, 1);
  const percentage = numberFromDraft(draft.percentage, 0);
  const base = {
    kind: draft.kind,
    from_player_id: draft.from_player_id,
    to_player_id: draft.to_player_id,
    summary: draft.summary.trim() || undefined,
  };

  if (draft.kind === "cash_transfer") {
    return { ...base, amount };
  }
  if (draft.kind === "property_transfer") {
    return { ...base, property_id: draft.property_id };
  }
  if (draft.kind === "loan") {
    return { ...base, principal: amount, due_round: dueRound, interest_rate_percent: 10 };
  }
  if (draft.kind === "option") {
    return { ...base, property_id: draft.property_id, strike_price: amount, expires_round: dueRound };
  }
  if (draft.kind === "rent_share") {
    return { ...base, property_id: draft.property_id, percentage, expires_round: dueRound };
  }
  return { ...base, covered_event: "rent_due", payout_amount: amount, expires_round: dueRound };
}

function sampleComplexTerms(game: GameMetadata, participants: string[]): DealTerm[] {
  const first = participants[0] ?? game.players[0]?.id ?? "";
  const second = participants.find((playerId) => playerId !== first) ?? game.players[1]?.id ?? first;
  return [
    {
      kind: "cash_transfer",
      from_player_id: first,
      to_player_id: second,
      amount: 120,
      summary: `${playerName(game, first)} pays ${playerName(game, second)} $120 immediately`,
    },
    {
      kind: "property_transfer",
      from_player_id: second,
      to_player_id: first,
      property_id: "property_reading_railroad",
      summary: `${playerName(game, second)} transfers Reading Railroad`,
    },
    {
      kind: "loan",
      from_player_id: first,
      to_player_id: second,
      principal: 200,
      due_round: 3,
      interest_rate_percent: 10,
      summary: `${playerName(game, first)} finances a $200 loan due in round 3`,
    },
    {
      kind: "option",
      from_player_id: second,
      to_player_id: first,
      property_id: "property_oriental_avenue",
      strike_price: 140,
      expires_round: 4,
      summary: `${playerName(game, first)} receives a purchase option on Oriental Avenue`,
    },
    {
      kind: "rent_share",
      from_player_id: first,
      to_player_id: second,
      property_id: "property_reading_railroad",
      percentage: 25,
      expires_round: 5,
      summary: `${playerName(game, second)} receives 25% of Reading Railroad rent`,
    },
    {
      kind: "risk_transfer",
      from_player_id: second,
      to_player_id: first,
      covered_event: "large_rent_payment",
      payout_amount: 75,
      expires_round: 2,
      summary: `${playerName(game, second)} covers $75 of ${playerName(game, first)}'s next large rent loss`,
    },
  ];
}

function sortNegotiations(negotiations: Negotiation[]): Negotiation[] {
  return [...negotiations].sort((left, right) => Date.parse(right.updated_at) - Date.parse(left.updated_at));
}

function sortDeals(deals: Deal[]): Deal[] {
  return [...deals].sort((left, right) => left.version - right.version);
}

function ValidationAlert({ errors }: Readonly<{ errors: ValidationError[] }>) {
  if (errors.length === 0) {
    return null;
  }
  return (
    <div role="alert" className="rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-800">
      <div className="flex gap-2">
        <ShieldAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-rose-700" />
        <div>
          <p className="font-semibold text-rose-950">Validation errors</p>
          <ul className="mt-1 list-disc space-y-1 pl-4">
            {errors.map((error) => (
              <li key={`${error.code}-${error.field ?? "field"}-${error.message}`}>
                {error.field ? `${error.field}: ` : null}
                {error.message}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}

function isRejectedMutation(
  result: NegotiationMutationResponse | DealMutationResponse,
): result is Extract<NegotiationMutationResponse | DealMutationResponse, { status: "rejected" }> {
  return result.status === "rejected";
}

export function NegotiationPanel({ gameId, game, apiBaseUrl }: NegotiationPanelProps) {
  const queryClient = useQueryClient();
  const [selectedNegotiationId, setSelectedNegotiationId] = useState<string | null>(null);
  const [selectedDealId, setSelectedDealId] = useState<string | null>(null);
  const [openedByPlayerId, setOpenedByPlayerId] = useState(game.players[0]?.id ?? "");
  const [participantPlayerIds, setParticipantPlayerIds] = useState(() =>
    game.players.slice(0, Math.min(2, game.players.length)).map((player) => player.id),
  );
  const [topic, setTopic] = useState("");
  const [context, setContext] = useState("");
  const [messageAuthorId, setMessageAuthorId] = useState(game.players[0]?.id ?? "");
  const [messageBody, setMessageBody] = useState("");
  const [proposerPlayerId, setProposerPlayerId] = useState(game.players[0]?.id ?? "");
  const [parentDealId, setParentDealId] = useState<string | null>(null);
  const [draftTerms, setDraftTerms] = useState<DealTerm[]>([]);
  const [termDraft, setTermDraft] = useState(() => defaultTermDraft(game, participantPlayerIds));
  const [validationErrors, setValidationErrors] = useState<ValidationError[]>([]);
  const [selectedAiPlayerId, setSelectedAiPlayerId] = useState("");
  const [aiNegotiationResult, setAiNegotiationResult] = useState<AiStepResponse | null>(null);

  const negotiationsQuery = useQuery({
    queryKey: ["negotiations", gameId],
    queryFn: () => readNegotiations({ gameId, baseUrl: apiBaseUrl }),
  });
  const dealsQuery = useQuery({
    queryKey: ["deals", gameId],
    queryFn: () => readDeals({ gameId, baseUrl: apiBaseUrl }),
  });

  const negotiations = useMemo(() => sortNegotiations(negotiationsQuery.data ?? []), [negotiationsQuery.data]);
  const selectedNegotiation = selectedNegotiationId
    ? negotiations.find((negotiation) => negotiation.id === selectedNegotiationId) ?? null
    : negotiations[0] ?? null;

  useEffect(() => {
    if (!selectedNegotiationId && negotiations[0]) {
      setSelectedNegotiationId(negotiations[0].id);
      setSelectedDealId(null);
      setParentDealId(null);
    }
    if (
      selectedNegotiationId &&
      negotiations.length > 0 &&
      !negotiationsQuery.isFetching &&
      !negotiations.some((negotiation) => negotiation.id === selectedNegotiationId)
    ) {
      setSelectedNegotiationId(negotiations[0]?.id ?? null);
      setSelectedDealId(null);
      setParentDealId(null);
    }
  }, [negotiations, negotiationsQuery.isFetching, selectedNegotiationId]);

  useEffect(() => {
    const nextParticipants = selectedNegotiation?.participant_player_ids ?? participantPlayerIds;
    setTermDraft((current) => ({
      ...current,
      from_player_id: nextParticipants.includes(current.from_player_id)
        ? current.from_player_id
        : nextParticipants[0] ?? current.from_player_id,
      to_player_id: nextParticipants.includes(current.to_player_id)
        ? current.to_player_id
        : nextParticipants.find((playerId) => playerId !== current.from_player_id) ?? nextParticipants[0] ?? current.to_player_id,
    }));
  }, [participantPlayerIds, selectedNegotiation]);

  const messagesQuery = useQuery({
    queryKey: ["negotiation-messages", gameId, selectedNegotiation?.id],
    queryFn: () =>
      readNegotiationMessages({
        gameId,
        negotiationId: selectedNegotiation?.id ?? "",
        baseUrl: apiBaseUrl,
      }),
    enabled: Boolean(selectedNegotiation?.id),
  });

  const selectedDeals = useMemo(
    () => sortDeals((dealsQuery.data ?? []).filter((deal) => deal.negotiation_id === selectedNegotiation?.id)),
    [dealsQuery.data, selectedNegotiation?.id],
  );
  const selectedDeal = selectedDeals.find((deal) => deal.id === selectedDealId) ?? selectedDeals.at(-1) ?? null;
  const aiParticipants = useMemo(
    () =>
      (selectedNegotiation?.participant_player_ids ?? []).filter(
        (playerId) => game.players.find((player) => player.id === playerId)?.controller_type === "ai",
      ),
    [game.players, selectedNegotiation?.participant_player_ids],
  );
  useEffect(() => {
    if (aiParticipants.length === 0) {
      setSelectedAiPlayerId("");
      return;
    }
    if (!aiParticipants.includes(selectedAiPlayerId)) {
      setSelectedAiPlayerId(aiParticipants[0] ?? "");
    }
  }, [aiParticipants, selectedAiPlayerId]);
  const previewTerms = draftTerms.length > 0 ? draftTerms : selectedDeal?.terms ?? [];
  const previewParticipants =
    selectedNegotiation?.participant_player_ids ?? selectedDeal?.participant_player_ids ?? participantPlayerIds;
  const isNegotiationOpen = selectedNegotiation?.status === "open";
  const hasDraftReady = Boolean(selectedNegotiation && draftTerms.length > 0 && proposerPlayerId);

  async function invalidateNegotiationData(negotiationId = selectedNegotiation?.id) {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["negotiations", gameId] }),
      queryClient.invalidateQueries({ queryKey: ["deals", gameId] }),
      negotiationId
        ? queryClient.invalidateQueries({ queryKey: ["negotiation-messages", gameId, negotiationId] })
        : Promise.resolve(),
    ]);
  }

  const startNegotiation = useMutation({
    mutationFn: () =>
      createNegotiation({
        gameId,
        baseUrl: apiBaseUrl,
        input: {
          opened_by_player_id: openedByPlayerId,
          participant_player_ids: participantPlayerIds,
          topic: topic.trim(),
          context: context.trim(),
        },
    }),
    onSuccess: async (result) => {
      if (isRejectedMutation(result)) {
        setValidationErrors(result.validation_errors);
        return;
      }
      setValidationErrors([]);
      setSelectedNegotiationId(result.negotiation.id);
      setSelectedDealId(null);
      setParentDealId(null);
      setTopic("");
      setContext("");
      await invalidateNegotiationData(result.negotiation.id);
    },
  });

  const sendMessage = useMutation({
    mutationFn: () =>
      createNegotiationMessage({
        gameId,
        baseUrl: apiBaseUrl,
        input: {
          negotiationId: selectedNegotiation?.id ?? "",
          author_player_id: messageAuthorId,
          body: messageBody.trim(),
        },
      }),
    onSuccess: async (result) => {
      if (result.status === "rejected") {
        setValidationErrors(result.validation_errors);
        return;
      }
      setValidationErrors([]);
      setMessageBody("");
      await invalidateNegotiationData(result.message.negotiation_id);
    },
  });

  const proposeDeal = useMutation({
    mutationFn: () =>
      createDeal({
        gameId,
        baseUrl: apiBaseUrl,
        input: {
          negotiation_id: selectedNegotiation?.id ?? "",
          proposer_player_id: proposerPlayerId,
          participant_player_ids: selectedNegotiation?.participant_player_ids ?? participantPlayerIds,
          parent_deal_id: parentDealId,
          terms: draftTerms,
        },
    }),
    onSuccess: async (result) => {
      if (isRejectedMutation(result)) {
        setValidationErrors(result.validation_errors);
        return;
      }
      setValidationErrors([]);
      setSelectedDealId(result.deal.id);
      setParentDealId(null);
      setDraftTerms([]);
      await invalidateNegotiationData(result.deal.negotiation_id);
    },
  });

  const acceptDealMutation = useMutation({
    mutationFn: (dealId: string) => acceptDeal({ gameId, dealId, baseUrl: apiBaseUrl }),
    onSuccess: async (result) => {
      if (isRejectedMutation(result)) {
        setValidationErrors(result.validation_errors);
        return;
      }
      setValidationErrors([]);
      setSelectedDealId(result.deal.id);
      await invalidateNegotiationData(result.deal.negotiation_id);
    },
  });

  const rejectDealMutation = useMutation({
    mutationFn: (dealId: string) => rejectDeal({ gameId, dealId, baseUrl: apiBaseUrl }),
    onSuccess: async (result) => {
      if (isRejectedMutation(result)) {
        setValidationErrors(result.validation_errors);
        return;
      }
      setValidationErrors([]);
      setSelectedDealId(result.deal.id);
      await invalidateNegotiationData(result.deal.negotiation_id);
    },
  });

  const expireNegotiationMutation = useMutation({
    mutationFn: (negotiationId: string) => expireNegotiation({ gameId, negotiationId, baseUrl: apiBaseUrl }),
    onSuccess: async (result) => {
      if (isRejectedMutation(result)) {
        setValidationErrors(result.validation_errors);
        return;
      }
      setValidationErrors([]);
      setSelectedNegotiationId(result.negotiation.id);
      await invalidateNegotiationData(result.negotiation.id);
    },
  });

  const requestAiNegotiationStep = useMutation({
    mutationFn: (decisionType: AiDecisionType) =>
      submitAiStep({
        gameId,
        baseUrl: apiBaseUrl,
        input: {
          player_id: selectedAiPlayerId || aiParticipants[0] || "",
          decision_type: decisionType,
          negotiation_id: selectedNegotiation?.id ?? null,
          mandatory: false,
          request_context: {
            mode: "negotiation",
            selected_deal_id: selectedDeal?.id ?? null,
          },
        },
      }),
    onSuccess: async (result) => {
      setAiNegotiationResult(result);
      if (result.status === "rejected" || result.status === "blocked") {
        setValidationErrors(result.validation_errors);
      } else {
        setValidationErrors([]);
      }
      await invalidateNegotiationData(result.negotiation_id ?? selectedNegotiation?.id);
    },
  });

  function toggleParticipant(playerId: string) {
    setParticipantPlayerIds((current) => {
      if (current.includes(playerId)) {
        return current.filter((id) => id !== playerId);
      }
      return [...current, playerId];
    });
  }

  function addDraftTerm() {
    setDraftTerms((current) => [...current, termFromDraft(termDraft)]);
  }

  function addSampleTerms() {
    const participants = selectedNegotiation?.participant_player_ids ?? participantPlayerIds;
    setDraftTerms(sampleComplexTerms(game, participants));
  }

  function startCounteroffer(deal: Deal) {
    setParentDealId(deal.id);
    setSelectedDealId(deal.id);
    setDraftTerms([]);
    setProposerPlayerId(
      deal.participant_player_ids.find((playerId) => playerId !== deal.proposer_player_id) ?? deal.proposer_player_id,
    );
  }

  function updateTermDraft(patch: Partial<TermDraft>) {
    setTermDraft((current) => ({ ...current, ...patch }));
  }

  const busy =
    startNegotiation.isPending ||
    sendMessage.isPending ||
    proposeDeal.isPending ||
    acceptDealMutation.isPending ||
    rejectDealMutation.isPending ||
    expireNegotiationMutation.isPending ||
    requestAiNegotiationStep.isPending;

  return (
    <section aria-label="Negotiation inbox" className="rounded-md border border-neutral-200 bg-white p-4 shadow-sm">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h2 className="text-sm font-semibold text-neutral-950">Negotiation inbox</h2>
          <p className="mt-1 text-xs text-neutral-600">Deals, messages, and accept/reject outcomes are loaded from the API.</p>
        </div>
        <span className="inline-flex w-fit items-center gap-1.5 rounded-full bg-neutral-100 px-2 py-1 text-xs font-medium text-neutral-600">
          <MessageSquareText aria-hidden="true" className="size-3" />
          {negotiations.length} threads
        </span>
      </div>

      <div className="mt-4 grid gap-4">
        <ValidationAlert errors={validationErrors} />

        <form
          className="grid gap-3 rounded-md border border-neutral-200 bg-neutral-50 p-3"
          onSubmit={(event) => {
            event.preventDefault();
            startNegotiation.mutate();
          }}
        >
          <div className="grid gap-3 md:grid-cols-2">
            <label className="grid gap-1 text-sm font-medium text-neutral-700">
              Opened by
              <select
                value={openedByPlayerId}
                onChange={(event) => setOpenedByPlayerId(event.target.value)}
                className="rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm text-neutral-950 outline-none focus:border-teal-700 focus:ring-2 focus:ring-teal-700/20"
              >
                {game.players.map((player) => (
                  <option key={player.id} value={player.id}>
                    {player.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="grid gap-1 text-sm font-medium text-neutral-700">
              Negotiation topic
              <input
                value={topic}
                onChange={(event) => setTopic(event.target.value)}
                className="rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm text-neutral-950 outline-none focus:border-teal-700 focus:ring-2 focus:ring-teal-700/20"
              />
            </label>
          </div>

          <fieldset className="grid gap-2">
            <legend className="text-sm font-semibold text-neutral-950">Selected participants</legend>
            <div className="flex flex-wrap gap-2">
              {game.players.map((player) => (
                <label
                  key={player.id}
                  className="inline-flex items-center gap-2 rounded-md border border-neutral-200 bg-white px-3 py-2 text-sm font-medium text-neutral-700"
                >
                  <input
                    type="checkbox"
                    checked={participantPlayerIds.includes(player.id)}
                    onChange={() => toggleParticipant(player.id)}
                    className="size-4 accent-teal-700"
                  />
                  {player.name}
                </label>
              ))}
            </div>
          </fieldset>

          <label className="grid gap-1 text-sm font-medium text-neutral-700">
            Negotiation context
            <textarea
              value={context}
              onChange={(event) => setContext(event.target.value)}
              className="min-h-20 rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm text-neutral-950 outline-none focus:border-teal-700 focus:ring-2 focus:ring-teal-700/20"
            />
          </label>

          <Button type="submit" disabled={busy || participantPlayerIds.length < 2 || topic.trim().length === 0} className="w-fit">
            {startNegotiation.isPending ? <Loader2 aria-hidden="true" className="size-4 animate-spin" /> : <Split aria-hidden="true" className="size-4" />}
            Start negotiation
          </Button>
        </form>

        <div className="grid gap-4 xl:grid-cols-[280px_minmax(0,1fr)]">
          <div className="rounded-md border border-neutral-200 bg-neutral-50 p-3">
            <h3 className="text-sm font-semibold text-neutral-950">Threads</h3>
            {negotiationsQuery.isLoading ? (
              <p className="mt-2 text-sm text-neutral-600">Loading negotiations.</p>
            ) : negotiations.length === 0 ? (
              <p className="mt-2 text-sm text-neutral-600">No negotiations yet.</p>
            ) : (
              <div className="mt-3 grid gap-2">
                {negotiations.map((negotiation) => (
                  <button
                    key={negotiation.id}
                    type="button"
                    aria-label={`${negotiation.topic} ${statusLabel(negotiation.status)}`}
                    onClick={() => {
                      setSelectedNegotiationId(negotiation.id);
                      setSelectedDealId(null);
                      setParentDealId(null);
                    }}
                    className={cn(
                      "rounded-md border px-3 py-2 text-left text-sm",
                      negotiation.id === selectedNegotiation?.id
                        ? "border-teal-300 bg-teal-50 text-teal-950"
                        : "border-neutral-200 bg-white text-neutral-700",
                    )}
                  >
                    <span className="block font-semibold">{negotiation.topic}</span>
                    <span className="mt-1 block text-xs">
                      {statusLabel(negotiation.status)} · round_number {negotiation.round_number}
                    </span>
                  </button>
                ))}
              </div>
            )}
          </div>

          <section aria-label="Negotiation thread" className="rounded-md border border-neutral-200 bg-white p-3">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
              <div>
                <h3 className="text-sm font-semibold text-neutral-950">Negotiation thread</h3>
                {selectedNegotiation ? (
                  <p className="mt-1 text-xs text-neutral-600">
                    {selectedNegotiation.topic} · {statusLabel(selectedNegotiation.status)} · round_number{" "}
                    {selectedNegotiation.round_number}
                  </p>
                ) : (
                  <p className="mt-1 text-xs text-neutral-600">No negotiation selected.</p>
                )}
              </div>
              {selectedNegotiation ? (
                <span
                  className={cn(
                    "inline-flex w-fit items-center gap-1.5 rounded-full px-2 py-1 text-xs font-medium ring-1 ring-inset",
                    selectedNegotiation.status === "open"
                      ? "bg-emerald-50 text-emerald-700 ring-emerald-200"
                      : "bg-neutral-100 text-neutral-700 ring-neutral-200",
                  )}
                >
                  {selectedNegotiation.status === "expired" ? (
                    <Clock3 aria-hidden="true" className="size-3" />
                  ) : (
                    <CheckCircle2 aria-hidden="true" className="size-3" />
                  )}
                  {statusLabel(selectedNegotiation.status)}
                </span>
              ) : null}
            </div>

            {selectedNegotiation ? (
              <div className="mt-3 grid gap-3">
                <dl className="grid gap-2 text-sm text-neutral-700 md:grid-cols-2">
                  <div>
                    <dt className="text-xs font-medium uppercase text-neutral-500">Opened by</dt>
                    <dd className="mt-1 text-neutral-950">{playerName(game, selectedNegotiation.opened_by_player_id)}</dd>
                  </div>
                  <div>
                    <dt className="text-xs font-medium uppercase text-neutral-500">Participants</dt>
                    <dd className="mt-1 text-neutral-950">
                      Participants {playerNames(game, selectedNegotiation.participant_player_ids)}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-xs font-medium uppercase text-neutral-500">status</dt>
                    <dd className="mt-1 text-neutral-950">{selectedNegotiation.status}</dd>
                  </div>
                  <div>
                    <dt className="text-xs font-medium uppercase text-neutral-500">participant_player_ids</dt>
                    <dd className="mt-1 break-all text-neutral-950">{selectedNegotiation.participant_player_ids.join(", ")}</dd>
                  </div>
                </dl>
                {selectedNegotiation.status === "expired" ? (
                  <p className="rounded-md border border-neutral-200 bg-neutral-50 px-3 py-2 text-sm font-medium text-neutral-700">
                    Expired negotiation is visibly closed and cannot execute accept controls.
                  </p>
                ) : null}

                {aiParticipants.length > 0 ? (
                  <section aria-label="AI negotiation controls" className="rounded-md border border-purple-200 bg-purple-50 p-3">
                    <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
                      <label className="grid gap-1 text-sm font-medium text-purple-950">
                        AI participant
                        <select
                          value={selectedAiPlayerId}
                          onChange={(event) => setSelectedAiPlayerId(event.target.value)}
                          className="rounded-md border border-purple-200 bg-white px-3 py-2 text-sm text-neutral-950 outline-none focus:border-purple-700 focus:ring-2 focus:ring-purple-700/20"
                        >
                          {aiParticipants.map((playerId) => (
                            <option key={playerId} value={playerId}>
                              {playerName(game, playerId)}
                            </option>
                          ))}
                        </select>
                      </label>
                      <div className="flex flex-wrap gap-2">
                        <Button
                          onClick={() => requestAiNegotiationStep.mutate("negotiation_message")}
                          disabled={busy || !isNegotiationOpen || !selectedAiPlayerId}
                        >
                          {requestAiNegotiationStep.isPending ? (
                            <Loader2 aria-hidden="true" className="size-4 animate-spin" />
                          ) : (
                            <Bot aria-hidden="true" className="size-4" />
                          )}
                          Ask AI message
                        </Button>
                        <Button
                          onClick={() => requestAiNegotiationStep.mutate("deal_proposal")}
                          disabled={busy || !isNegotiationOpen || !selectedAiPlayerId}
                          className="bg-white text-purple-900 ring-1 ring-inset ring-purple-200 hover:bg-purple-100"
                        >
                          <BadgeDollarSign aria-hidden="true" className="size-4" />
                          Ask AI offer
                        </Button>
                        <Button
                          onClick={() => requestAiNegotiationStep.mutate("counteroffer")}
                          disabled={busy || !isNegotiationOpen || !selectedAiPlayerId || !selectedDeal}
                          className="bg-white text-purple-900 ring-1 ring-inset ring-purple-200 hover:bg-purple-100"
                        >
                          <RefreshCw aria-hidden="true" className="size-4" />
                          Ask AI counteroffer
                        </Button>
                        <Button
                          onClick={() => requestAiNegotiationStep.mutate("accept_reject")}
                          disabled={busy || !isNegotiationOpen || !selectedAiPlayerId || !selectedDeal}
                          className="bg-white text-purple-900 ring-1 ring-inset ring-purple-200 hover:bg-purple-100"
                        >
                          <CheckCircle2 aria-hidden="true" className="size-4" />
                          Ask AI accept/reject
                        </Button>
                      </div>
                    </div>
                    {aiNegotiationResult ? (
                      <p className="mt-3 rounded-md border border-purple-200 bg-white px-3 py-2 text-sm font-medium text-purple-950">
                        AI response {aiNegotiationResult.status}
                      </p>
                    ) : null}
                  </section>
                ) : null}

                <div className="rounded-md border border-neutral-200 bg-neutral-50 p-3">
                  <h4 className="text-sm font-semibold text-neutral-950">Messages</h4>
                  {messagesQuery.isLoading ? (
                    <p className="mt-2 text-sm text-neutral-600">Loading messages.</p>
                  ) : (messagesQuery.data ?? []).length === 0 ? (
                    <p className="mt-2 text-sm text-neutral-600">No messages yet.</p>
                  ) : (
                    <ol className="mt-2 divide-y divide-neutral-200 text-sm">
                      {(messagesQuery.data ?? []).map((message) => (
                        <li key={message.id} className="py-2">
                          <p className="font-medium text-neutral-950">{playerName(game, message.author_player_id)}</p>
                          <p className="mt-1 text-neutral-700">{message.body}</p>
                        </li>
                      ))}
                    </ol>
                  )}
                </div>

                <form
                  className="grid gap-2 rounded-md border border-neutral-200 bg-neutral-50 p-3"
                  onSubmit={(event) => {
                    event.preventDefault();
                    sendMessage.mutate();
                  }}
                >
                  <div className="grid gap-2 md:grid-cols-[180px_minmax(0,1fr)]">
                    <label className="grid gap-1 text-sm font-medium text-neutral-700">
                      Message author
                      <select
                        value={messageAuthorId}
                        onChange={(event) => setMessageAuthorId(event.target.value)}
                        className="rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm text-neutral-950 outline-none focus:border-teal-700 focus:ring-2 focus:ring-teal-700/20"
                      >
                        {selectedNegotiation.participant_player_ids.map((playerId) => (
                          <option key={playerId} value={playerId}>
                            {playerName(game, playerId)}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="grid gap-1 text-sm font-medium text-neutral-700">
                      Freeform message
                      <input
                        value={messageBody}
                        onChange={(event) => setMessageBody(event.target.value)}
                        className="rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm text-neutral-950 outline-none focus:border-teal-700 focus:ring-2 focus:ring-teal-700/20"
                      />
                    </label>
                  </div>
                  <Button type="submit" disabled={busy || !isNegotiationOpen || messageBody.trim().length === 0} className="w-fit">
                    {sendMessage.isPending ? <Loader2 aria-hidden="true" className="size-4 animate-spin" /> : <Send aria-hidden="true" className="size-4" />}
                    Send message
                  </Button>
                </form>

                <section aria-label="Selected deal versions" className="grid gap-2">
                  <h4 className="text-sm font-semibold text-neutral-950">Selected deal versions</h4>
                  {selectedDeals.length === 0 ? (
                    <p className="rounded-md border border-dashed border-neutral-200 bg-neutral-50 px-3 py-4 text-sm text-neutral-600">
                      No deal versions proposed.
                    </p>
                  ) : (
                    selectedDeals.map((deal) => {
                      const canExecute = deal.status === "proposed" && isNegotiationOpen;
                      return (
                        <article
                          key={deal.id}
                          aria-label={`Deal v${deal.version}`}
                          className="rounded-md border border-neutral-200 bg-neutral-50 p-3"
                          role="region"
                        >
                          <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                            <div>
                              <h5 className="text-sm font-semibold text-neutral-950">Deal v{deal.version}</h5>
                              <p className="mt-1 text-xs text-neutral-600">
                                {deal.parent_deal_id ? `Counteroffer · Parent deal ${deal.parent_deal_id}` : "Original proposal"}
                              </p>
                            </div>
                            <span
                              className={cn(
                                "inline-flex w-fit items-center gap-1.5 rounded-full px-2 py-1 text-xs font-medium ring-1 ring-inset",
                                deal.status === "accepted"
                                  ? "bg-emerald-50 text-emerald-700 ring-emerald-200"
                                  : deal.status === "rejected" || deal.status === "expired"
                                    ? "bg-neutral-100 text-neutral-700 ring-neutral-200"
                                    : "bg-amber-50 text-amber-700 ring-amber-200",
                              )}
                            >
                              {deal.status === "rejected" ? <XCircle aria-hidden="true" className="size-3" /> : null}
                              {statusLabel(deal.status)}
                            </span>
                          </div>

                          <dl className="mt-3 grid gap-2 text-xs text-neutral-700 md:grid-cols-2">
                            <div>
                              <dt className="font-medium uppercase text-neutral-500">Proposer</dt>
                              <dd className="mt-1 text-neutral-950">{playerName(game, deal.proposer_player_id)}</dd>
                            </div>
                            <div>
                              <dt className="font-medium uppercase text-neutral-500">accepted_at</dt>
                              <dd className="mt-1 text-neutral-950">{deal.accepted_at ?? "not accepted"}</dd>
                            </div>
                            <div>
                              <dt className="font-medium uppercase text-neutral-500">validation_errors</dt>
                              <dd className="mt-1 text-neutral-950">{deal.validation_errors.length}</dd>
                            </div>
                            <div>
                              <dt className="font-medium uppercase text-neutral-500">parent_deal_id</dt>
                              <dd className="mt-1 text-neutral-950">{deal.parent_deal_id ?? "none"}</dd>
                            </div>
                          </dl>

                          <ul className="mt-3 grid gap-1.5 text-sm text-neutral-700">
                            {deal.terms.map((term, index) => (
                              <li key={`${deal.id}-${term.kind}-${index}`} className="rounded border border-neutral-200 bg-white px-2 py-1.5">
                                <span className="font-semibold text-neutral-950">{term.kind}</span> · {termSummary(game, term)}
                              </li>
                            ))}
                          </ul>

                          {deal.status === "rejected" ? (
                            <p className="mt-3 rounded-md border border-neutral-200 bg-white px-3 py-2 text-sm font-medium text-neutral-700">
                              Rejected deal is visibly closed and cannot execute accept controls.
                            </p>
                          ) : null}
                          {deal.status === "expired" ? (
                            <p className="mt-3 rounded-md border border-neutral-200 bg-white px-3 py-2 text-sm font-medium text-neutral-700">
                              Expired deal is visibly closed and cannot execute accept controls.
                            </p>
                          ) : null}

                          {canExecute ? (
                            <div className="mt-3 flex flex-wrap gap-2">
                              <Button
                                onClick={() => startCounteroffer(deal)}
                                disabled={busy}
                                className="bg-white text-neutral-700 ring-1 ring-inset ring-neutral-300 hover:bg-neutral-100"
                              >
                                <RefreshCw aria-hidden="true" className="size-4" />
                                Counteroffer
                              </Button>
                              <Button onClick={() => acceptDealMutation.mutate(deal.id)} disabled={busy}>
                                {acceptDealMutation.isPending ? (
                                  <Loader2 aria-hidden="true" className="size-4 animate-spin" />
                                ) : (
                                  <CheckCircle2 aria-hidden="true" className="size-4" />
                                )}
                                Accept
                              </Button>
                              <Button
                                onClick={() => rejectDealMutation.mutate(deal.id)}
                                disabled={busy}
                                className="bg-rose-700 hover:bg-rose-800 focus-visible:outline-rose-700"
                              >
                                {rejectDealMutation.isPending ? (
                                  <Loader2 aria-hidden="true" className="size-4 animate-spin" />
                                ) : (
                                  <XCircle aria-hidden="true" className="size-4" />
                                )}
                                Reject
                              </Button>
                            </div>
                          ) : null}
                        </article>
                      );
                    })
                  )}
                </section>

                {isNegotiationOpen ? (
                  <Button
                    onClick={() => expireNegotiationMutation.mutate(selectedNegotiation.id)}
                    disabled={busy}
                    className="w-fit bg-white text-neutral-700 ring-1 ring-inset ring-neutral-300 hover:bg-neutral-100"
                  >
                    {expireNegotiationMutation.isPending ? (
                      <Loader2 aria-hidden="true" className="size-4 animate-spin" />
                    ) : (
                      <Clock3 aria-hidden="true" className="size-4" />
                    )}
                    Expire negotiation
                  </Button>
                ) : null}
              </div>
            ) : null}
          </section>
        </div>

        <section aria-label="Structured deal builder" className="rounded-md border border-neutral-200 bg-neutral-50 p-3">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <h3 className="text-sm font-semibold text-neutral-950">Structured deal builder</h3>
              <p className="mt-1 text-xs text-neutral-600">
                Add executable terms as a draft, then submit them to the API for a proposed deal version.
              </p>
            </div>
            <span className="inline-flex w-fit items-center gap-1.5 rounded-full bg-white px-2 py-1 text-xs font-medium text-neutral-600 ring-1 ring-inset ring-neutral-200">
              <BadgeDollarSign aria-hidden="true" className="size-3" />
              Complex instruments
            </span>
          </div>

          <div className="mt-3 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            <label className="grid gap-1 text-sm font-medium text-neutral-700">
              Proposer
              <select
                value={proposerPlayerId}
                onChange={(event) => setProposerPlayerId(event.target.value)}
                className="rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm text-neutral-950 outline-none focus:border-teal-700 focus:ring-2 focus:ring-teal-700/20"
              >
                {(selectedNegotiation?.participant_player_ids ?? participantPlayerIds).map((playerId) => (
                  <option key={playerId} value={playerId}>
                    {playerName(game, playerId)}
                  </option>
                ))}
              </select>
            </label>
            <label className="grid gap-1 text-sm font-medium text-neutral-700">
              Term kind
              <select
                value={termDraft.kind}
                onChange={(event) => updateTermDraft({ kind: event.target.value as DealTermKind })}
                className="rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm text-neutral-950 outline-none focus:border-teal-700 focus:ring-2 focus:ring-teal-700/20"
              >
                {termKinds.map((kind) => (
                  <option key={kind} value={kind}>
                    {kind}
                  </option>
                ))}
              </select>
            </label>
            <label className="grid gap-1 text-sm font-medium text-neutral-700">
              Term from player
              <select
                value={termDraft.from_player_id}
                onChange={(event) => updateTermDraft({ from_player_id: event.target.value })}
                className="rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm text-neutral-950 outline-none focus:border-teal-700 focus:ring-2 focus:ring-teal-700/20"
              >
                {game.players.map((player) => (
                  <option key={player.id} value={player.id}>
                    {player.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="grid gap-1 text-sm font-medium text-neutral-700">
              Term to player
              <select
                value={termDraft.to_player_id}
                onChange={(event) => updateTermDraft({ to_player_id: event.target.value })}
                className="rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm text-neutral-950 outline-none focus:border-teal-700 focus:ring-2 focus:ring-teal-700/20"
              >
                {game.players.map((player) => (
                  <option key={player.id} value={player.id}>
                    {player.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="grid gap-1 text-sm font-medium text-neutral-700">
              Term amount
              <input
                type="number"
                min={0}
                value={termDraft.amount}
                onChange={(event) => updateTermDraft({ amount: event.target.value })}
                className="rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm text-neutral-950 outline-none focus:border-teal-700 focus:ring-2 focus:ring-teal-700/20"
              />
            </label>
            <label className="grid gap-1 text-sm font-medium text-neutral-700">
              Term property id
              <input
                value={termDraft.property_id}
                onChange={(event) => updateTermDraft({ property_id: event.target.value })}
                className="rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm text-neutral-950 outline-none focus:border-teal-700 focus:ring-2 focus:ring-teal-700/20"
              />
            </label>
            <label className="grid gap-1 text-sm font-medium text-neutral-700">
              Term due round
              <input
                type="number"
                min={1}
                value={termDraft.due_round}
                onChange={(event) => updateTermDraft({ due_round: event.target.value })}
                className="rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm text-neutral-950 outline-none focus:border-teal-700 focus:ring-2 focus:ring-teal-700/20"
              />
            </label>
            <label className="grid gap-1 text-sm font-medium text-neutral-700">
              Term percentage
              <input
                type="number"
                min={0}
                max={100}
                value={termDraft.percentage}
                onChange={(event) => updateTermDraft({ percentage: event.target.value })}
                className="rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm text-neutral-950 outline-none focus:border-teal-700 focus:ring-2 focus:ring-teal-700/20"
              />
            </label>
            <label className="grid gap-1 text-sm font-medium text-neutral-700 xl:col-span-3">
              Term summary
              <input
                value={termDraft.summary}
                onChange={(event) => updateTermDraft({ summary: event.target.value })}
                className="rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm text-neutral-950 outline-none focus:border-teal-700 focus:ring-2 focus:ring-teal-700/20"
              />
            </label>
          </div>

          <div className="mt-3 flex flex-wrap gap-2">
            <Button onClick={addDraftTerm} disabled={!selectedNegotiation || !isNegotiationOpen}>
              <BadgeDollarSign aria-hidden="true" className="size-4" />
              Add term
            </Button>
            <Button
              onClick={addSampleTerms}
              disabled={!selectedNegotiation || !isNegotiationOpen}
              className="bg-white text-neutral-700 ring-1 ring-inset ring-neutral-300 hover:bg-neutral-100"
            >
              <FileText aria-hidden="true" className="size-4" />
              Add sample complex instruments
            </Button>
            <Button onClick={() => proposeDeal.mutate()} disabled={busy || !hasDraftReady}>
              {proposeDeal.isPending ? <Loader2 aria-hidden="true" className="size-4 animate-spin" /> : <Send aria-hidden="true" className="size-4" />}
              Propose deal
            </Button>
          </div>

          <div className="mt-3 rounded-md border border-neutral-200 bg-white p-3 text-sm text-neutral-700">
            <p className="font-semibold text-neutral-950">Counteroffer</p>
            <p className="mt-1">Parent deal {parentDealId ?? "none"}</p>
          </div>
        </section>

        <section aria-label="Contract preview" className="rounded-md border border-neutral-200 bg-white p-3">
          <div className="flex items-center gap-2">
            <FileText aria-hidden="true" className="size-4 text-teal-700" />
            <h3 className="text-sm font-semibold text-neutral-950">Contract preview</h3>
          </div>
          <dl className="mt-3 grid gap-2 text-sm text-neutral-700 md:grid-cols-2">
            <div>
              <dt className="text-xs font-medium uppercase text-neutral-500">Parties</dt>
              <dd className="mt-1 text-neutral-950">{previewParticipants.length > 0 ? playerNames(game, previewParticipants) : "No parties"}</dd>
            </div>
            <div>
              <dt className="text-xs font-medium uppercase text-neutral-500">Deal version</dt>
              <dd className="mt-1 text-neutral-950">{draftTerms.length > 0 ? "Draft" : selectedDeal ? `v${selectedDeal.version}` : "No deal"}</dd>
            </div>
            <div>
              <dt className="text-xs font-medium uppercase text-neutral-500">Complex instruments</dt>
              <dd className="mt-1 text-neutral-950">{previewTerms.length} terms</dd>
            </div>
            <div>
              <dt className="text-xs font-medium uppercase text-neutral-500">Obligations</dt>
              <dd className="mt-1 text-neutral-950">Phase 6 would create obligations from accepted structured terms.</dd>
            </div>
          </dl>
          {previewTerms.length === 0 ? (
            <p className="mt-3 rounded-md border border-dashed border-neutral-200 bg-neutral-50 px-3 py-4 text-sm text-neutral-600">
              No terms selected for preview.
            </p>
          ) : (
            <ul className="mt-3 grid gap-2 text-sm text-neutral-700">
              {previewTerms.map((term, index) => (
                <li key={`${term.kind}-${index}`} className="rounded-md border border-neutral-200 bg-neutral-50 px-3 py-2">
                  <span className="font-semibold text-neutral-950">{term.kind}</span> · {termSummary(game, term)}
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </section>
  );
}
