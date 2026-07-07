"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRightLeft, Bot, CalendarClock, FileText, History, Info, ListFilter, Loader2, ShieldAlert } from "lucide-react";
import { useMemo, useState } from "react";

import { Button } from "../components/ui/button";
import {
  readContractOutcomes,
  readContracts,
  readObligations,
  settleContract,
  type ContractEnforcementResult,
  type ContractOutcomeExplanation,
  type ContractRecord,
  type ObligationRecord,
} from "../lib/api/contracts";
import type { AcceptedEvent } from "../lib/api/gameplay";
import type { GameMetadata } from "../lib/api/games";
import { readDeals, type Deal } from "../lib/api/negotiations";
import type { RejectedActionRecord } from "../lib/api/rejected-actions";
import { cn } from "../lib/ui";

type ContractsPanelProps = {
  game: GameMetadata;
  gameId: string;
  apiBaseUrl?: string;
  events: AcceptedEvent[];
  rejectedActions: RejectedActionRecord[];
};

type LogFilter = "actions" | "deals" | "ai" | "rejections";

type GameLogEntry = {
  id: string;
  kind: LogFilter;
  title: string;
  timestamp: string;
  detail: string;
  badge: string;
  sequence?: number;
  sourceAgreementId?: string | null;
  dealId?: string | null;
  contractId?: string | null;
};

const filterLabels: Record<LogFilter, string> = {
  actions: "Actions",
  deals: "Deals",
  ai: "AI decisions",
  rejections: "Rejections",
};

const maxRenderedLogEntries = 200;
const upcomingStatuses = new Set<ObligationRecord["status"]>(["pending", "due", "scheduled"]);

function formatDate(value: string | null | undefined): string {
  if (!value) {
    return "not set";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleString("en-US", {
    month: "short",
    day: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: true,
    timeZone: "UTC",
  });
}

function payloadString(payload: Record<string, unknown>, key: string): string | null {
  const value = payload[key];
  return typeof value === "string" && value.trim() ? value : null;
}

function payloadNumber(payload: Record<string, unknown>, key: string): number | null {
  const value = payload[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function playerName(game: GameMetadata, playerId: string | null | undefined): string {
  if (!playerId) {
    return "Unknown";
  }
  return game.players.find((player) => player.id === playerId)?.name ?? playerId;
}

function playerNames(game: GameMetadata, playerIds: string[]): string {
  return playerIds.map((playerId) => playerName(game, playerId)).join(", ");
}

function money(value: number | null | undefined): string | null {
  return typeof value === "number" && Number.isFinite(value) ? `$${value.toLocaleString("en-US")}` : null;
}

function contractTermText(contract: ContractRecord): string {
  if (contract.term_summary) {
    return contract.term_summary;
  }
  const summaries = contract.terms
    .map((term) => (typeof term.summary === "string" && term.summary.trim() ? term.summary : term.kind))
    .filter(Boolean);
  return summaries.join("; ") || "No term summary supplied.";
}

function obligationAssetText(obligation: ObligationRecord): string {
  if (obligation.asset_summary) {
    return obligation.asset_summary;
  }
  const amount = money(obligation.amount);
  return amount ? `${amount} transfer` : "No amount or asset summary supplied.";
}

function dueText(obligation: ObligationRecord): string {
  const parts = [];
  if (obligation.due_turn !== null) {
    parts.push(`due_turn ${obligation.due_turn}`);
  }
  if (obligation.due_condition) {
    parts.push(obligation.due_condition);
  }
  return parts.join(" / ") || "due condition not set";
}

export function canSettleObligation(obligation: ObligationRecord): boolean {
  if (obligation.status === "due") {
    return true;
  }
  return obligation.status === "pending" && obligation.due_turn === null && obligation.due_condition === null;
}

function eventKind(event: AcceptedEvent): LogFilter {
  if (event.event_type.includes("AI")) {
    return "ai";
  }
  if (event.event_type.includes("DEAL")) {
    return "deals";
  }
  return "actions";
}

function eventDetail(game: GameMetadata, event: AcceptedEvent): string {
  const payload = event.payload;
  if (event.event_type === "DICE_ROLLED") {
    const dice = payload.dice;
    const total = payloadNumber(payload, "total");
    const diceText = Array.isArray(dice) ? `dice ${dice.join(" + ")}` : "";
    const totalText = total !== null ? `total ${total}` : "";
    return `${diceText} ${totalText}`.trim();
  }
  if (event.event_type === "TOKEN_MOVED") {
    const playerId = payloadString(payload, "player_id");
    const toPosition = payloadNumber(payload, "to_position");
    return `${playerName(game, playerId)} moved to position ${toPosition ?? "unknown"}`;
  }
  if (event.event_type === "CONTRACT_TRIGGERED_TRANSFER") {
    const fromPlayer = playerName(game, payloadString(payload, "from_player_id"));
    const toPlayer = playerName(game, payloadString(payload, "to_player_id"));
    const amount = money(payloadNumber(payload, "amount")) ?? "an asset";
    const summary = payloadString(payload, "summary");
    return `Contract-triggered transfer ${fromPlayer} to ${toPlayer} ${amount}${summary ? `: ${summary}` : ""}`;
  }
  if (event.event_type.includes("AI")) {
    return payloadString(payload, "decision") ?? "AI decision event recorded.";
  }
  if (event.event_type.includes("DEAL")) {
    const dealId = payloadString(payload, "deal_id");
    const sourceAgreementId = payloadString(payload, "source_agreement_id");
    return [dealId ? `deal ${dealId}` : null, sourceAgreementId ? `Source agreement ${sourceAgreementId}` : null]
      .filter(Boolean)
      .join(" / ");
  }
  return payloadString(payload, "summary") ?? "";
}

function dealSummary(game: GameMetadata, deal: Deal): string {
  const proposer = playerName(game, deal.proposer_player_id);
  const participants = playerNames(game, deal.participant_player_ids);
  const terms = deal.terms.map((term) => term.summary ?? term.kind).join("; ");
  return `${proposer} proposed terms with ${participants}. ${terms}`;
}

function rejectionSummary(game: GameMetadata, record: RejectedActionRecord): string {
  const actor = playerName(game, record.actor_player_id);
  const validation = record.validation_errors
    .map((error) => {
      const field = error.field ? `${error.field}: ` : "";
      return `${field}${error.message}`;
    })
    .join(" ");
  return `${actor} submitted ${record.action_type}. ${record.reason_code}${validation ? `: ${validation}` : ""}`;
}

function buildLogEntries({
  deals,
  events,
  game,
  rejectedActions,
}: {
  deals: Deal[];
  events: AcceptedEvent[];
  game: GameMetadata;
  rejectedActions: RejectedActionRecord[];
}): GameLogEntry[] {
  const eventEntries = events.map((event): GameLogEntry => ({
    id: `event-${event.id}`,
    kind: eventKind(event),
    title: event.event_type,
    timestamp: event.created_at,
    detail: eventDetail(game, event),
    badge: event.sequence.toString(),
    sequence: event.sequence,
    sourceAgreementId: payloadString(event.payload, "source_agreement_id"),
    dealId: payloadString(event.payload, "deal_id"),
    contractId: payloadString(event.payload, "contract_id"),
  }));

  const dealEntries = deals.map((deal): GameLogEntry => ({
    id: `deal-${deal.id}`,
    kind: "deals",
    title: `Deal ${deal.id}`,
    timestamp: deal.updated_at,
    detail: dealSummary(game, deal),
    badge: deal.status,
    dealId: deal.id,
  }));

  const rejectionEntries = rejectedActions.map((record): GameLogEntry => ({
    id: `rejection-${record.id}`,
    kind: "rejections",
    title: "Rejected action",
    timestamp: record.created_at,
    detail: rejectionSummary(game, record),
    badge: record.reason_code,
  }));

  return [...eventEntries, ...dealEntries, ...rejectionEntries].sort((left, right) => {
    const leftTime = Date.parse(left.timestamp);
    const rightTime = Date.parse(right.timestamp);
    if (Number.isNaN(leftTime) || Number.isNaN(rightTime) || leftTime === rightTime) {
      return (left.sequence ?? 10_000) - (right.sequence ?? 10_000);
    }
    return leftTime - rightTime;
  });
}

function EmptyState({ text }: Readonly<{ text: string }>) {
  return (
    <div className="rounded-md border border-dashed border-neutral-200 bg-neutral-50 px-3 py-4 text-sm text-neutral-600">
      {text}
    </div>
  );
}

function ErrorNote({ text }: Readonly<{ text: string }>) {
  return <div className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">{text}</div>;
}

function pluralize(count: number, singular: string): string {
  return `${count} ${singular}${count === 1 ? "" : "s"}`;
}

function ContractEnforcementStatus({ result }: Readonly<{ result: ContractEnforcementResult }>) {
  const text =
    result.status === "rejected"
      ? `Contract enforcement rejected: ${result.reason_code}`
      : `Contract enforcement settled ${pluralize(
          result.settled_obligation_ids.length,
          "obligation",
        )} and defaulted ${pluralize(result.defaulted_obligation_ids.length, "obligation")}.`;
  return (
    <div
      aria-label="Contract enforcement status"
      role="status"
      className={cn(
        "rounded-md border px-3 py-2 text-sm",
        result.status === "rejected"
          ? "border-rose-200 bg-rose-50 text-rose-700"
          : "border-emerald-200 bg-emerald-50 text-emerald-700",
      )}
    >
      {text}
    </div>
  );
}

function ActiveContracts({
  contracts,
  game,
  isLoading,
}: Readonly<{
  contracts: ContractRecord[];
  game: GameMetadata;
  isLoading: boolean;
}>) {
  const activeContracts = contracts.filter((contract) => contract.status === "active");
  return (
    <section className="rounded-md border border-neutral-200 bg-white p-4" aria-labelledby="active-contracts-title">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 id="active-contracts-title" className="text-sm font-semibold text-neutral-950">
            Active contracts
          </h2>
          <p className="mt-1 text-xs text-neutral-600">Server-owned agreements that can create future obligations.</p>
        </div>
        <FileText aria-hidden="true" className="size-4 text-teal-700" />
      </div>

      <div className="mt-3 grid gap-3">
        {isLoading ? (
          <EmptyState text="Loading active contracts from the API." />
        ) : activeContracts.length === 0 ? (
          <EmptyState text="No active contracts returned by the API." />
        ) : (
          activeContracts.map((contract) => (
            <article
              key={contract.id}
              aria-label={`Contract ${contract.id}`}
              className="rounded-md border border-neutral-200 bg-neutral-50 p-3 text-sm"
            >
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div>
                  <h3 className="font-semibold text-neutral-950">Contract {contract.id}</h3>
                  <p className="mt-1 text-neutral-700">Parties {playerNames(game, contract.party_player_ids)}</p>
                </div>
                <span className="rounded-full bg-emerald-50 px-2 py-1 text-xs font-medium text-emerald-700 ring-1 ring-inset ring-emerald-200">
                  Status {contract.status}
                </span>
              </div>
              <div className="mt-3 grid gap-1 text-xs text-neutral-700">
                <p>deal_id {contract.deal_id ?? "not linked"}</p>
                <p>source_agreement_id {contract.source_agreement_id ?? "not linked"}</p>
                <p>effective_event_id {contract.effective_event_id ?? "not linked"}</p>
                <p>Created {formatDate(contract.created_at)}</p>
                <p>Effective {formatDate(contract.effective_at)}</p>
              </div>
              <p className="mt-3 rounded-md bg-white px-3 py-2 text-xs leading-5 text-neutral-700">
                {contractTermText(contract)}
              </p>
            </article>
          ))
        )}
      </div>
    </section>
  );
}

function UpcomingObligations({
  game,
  enforcingObligationId,
  isEnforcing,
  isLoading,
  onEnforce,
  obligations,
}: Readonly<{
  game: GameMetadata;
  enforcingObligationId: string | null;
  isEnforcing: boolean;
  isLoading: boolean;
  onEnforce: (obligation: ObligationRecord) => void;
  obligations: ObligationRecord[];
}>) {
  const upcoming = obligations.filter((obligation) => upcomingStatuses.has(obligation.status));
  return (
    <section className="rounded-md border border-neutral-200 bg-white p-4" aria-labelledby="upcoming-obligations-title">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 id="upcoming-obligations-title" className="text-sm font-semibold text-neutral-950">
            Upcoming obligations
          </h2>
          <p className="mt-1 text-xs text-neutral-600">Scheduled and pending obligations returned by the API.</p>
        </div>
        <CalendarClock aria-hidden="true" className="size-4 text-teal-700" />
      </div>

      <div className="mt-3 grid gap-3">
        {isLoading ? (
          <EmptyState text="Loading upcoming obligations from the API." />
        ) : upcoming.length === 0 ? (
          <EmptyState text="No upcoming obligations returned by the API." />
        ) : (
          upcoming.map((obligation) => {
            const canSettle = canSettleObligation(obligation);
            const isCurrentEnforcement = enforcingObligationId === obligation.id;
            const unavailableDescriptionId = `obligation-${obligation.id}-settlement-unavailable`;

            return (
              <article
                key={obligation.id}
                aria-label={`Obligation ${obligation.id}`}
                className="rounded-md border border-neutral-200 bg-neutral-50 p-3 text-sm"
              >
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <h3 className="font-semibold text-neutral-950">obligation_id {obligation.id}</h3>
                  <span className="rounded-full bg-amber-50 px-2 py-1 text-xs font-medium text-amber-700 ring-1 ring-inset ring-amber-200">
                    {obligation.status}
                  </span>
                </div>
                <div className="mt-3 grid gap-1 text-xs text-neutral-700">
                  <p>contract_id {obligation.contract_id}</p>
                  <p>{dueText(obligation)}</p>
                  <p>{obligationAssetText(obligation)}</p>
                  <p>Counterparty {playerName(game, obligation.counterparty_player_id)}</p>
                </div>
                <div className="mt-3 grid gap-2">
                  <Button
                    aria-describedby={!canSettle ? unavailableDescriptionId : undefined}
                    onClick={() => {
                      if (canSettle) {
                        onEnforce(obligation);
                      }
                    }}
                    disabled={isLoading || isEnforcing || !canSettle}
                    className="min-h-9 justify-start px-2.5 py-1.5 text-xs"
                  >
                    {isCurrentEnforcement ? (
                      <Loader2 aria-hidden="true" className="size-3.5 animate-spin" />
                    ) : canSettle ? (
                      <ArrowRightLeft aria-hidden="true" className="size-3.5" />
                    ) : (
                      <ShieldAlert aria-hidden="true" className="size-3.5" />
                    )}
                    {isCurrentEnforcement ? "Enforcing..." : canSettle ? "Enforce obligation" : "Unavailable until due"}
                  </Button>
                  {!canSettle ? (
                    <p id={unavailableDescriptionId} className="text-xs text-neutral-600">
                      Settlement unavailable until this obligation is due.
                    </p>
                  ) : null}
                </div>
              </article>
            );
          })
        )}
      </div>
    </section>
  );
}

function SettlementHistory({
  isLoading,
  obligations,
}: Readonly<{
  isLoading: boolean;
  obligations: ObligationRecord[];
}>) {
  const settled = obligations.filter((obligation) => obligation.status === "settled");
  return (
    <section className="rounded-md border border-neutral-200 bg-white p-4" aria-labelledby="settlement-history-title">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 id="settlement-history-title" className="text-sm font-semibold text-neutral-950">
            Obligation settlement history
          </h2>
          <p className="mt-1 text-xs text-neutral-600">Past settlements and their triggering events.</p>
        </div>
        <History aria-hidden="true" className="size-4 text-teal-700" />
      </div>

      <div className="mt-3 grid gap-2">
        {isLoading ? (
          <EmptyState text="Loading obligation settlement history from the API." />
        ) : settled.length === 0 ? (
          <EmptyState text="No settled obligations returned by the API." />
        ) : (
          settled.map((obligation) => (
            <article key={obligation.id} className="rounded-md border border-neutral-200 bg-neutral-50 p-3 text-xs text-neutral-700">
              <p className="font-semibold text-neutral-950">settled_at {formatDate(obligation.settled_at)}</p>
              <p className="mt-1">triggering event {obligation.triggering_event_id ?? "not linked"}</p>
              <p className="mt-1">linked contract_id {obligation.contract_id}</p>
              <p className="mt-2 rounded-md bg-white px-3 py-2 leading-5">
                {obligation.transfer_summary ?? obligationAssetText(obligation)}
              </p>
            </article>
          ))
        )}
      </div>
    </section>
  );
}

function ContractOutcomeExplanations({
  isLoading,
  outcomes,
}: Readonly<{
  isLoading: boolean;
  outcomes: ContractOutcomeExplanation[];
}>) {
  return (
    <section
      className="rounded-md border border-neutral-200 bg-white p-4"
      aria-labelledby="contract-outcome-explanation-title"
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 id="contract-outcome-explanation-title" className="text-sm font-semibold text-neutral-950">
            Contract outcome explanation
          </h2>
          <p className="mt-1 text-xs text-neutral-600">Classic-rule effects returned by the API.</p>
        </div>
        <Info aria-hidden="true" className="size-4 text-teal-700" />
      </div>

      <div className="mt-3 grid gap-2">
        {isLoading ? (
          <EmptyState text="Loading contract outcome explanations from the API." />
        ) : outcomes.length === 0 ? (
          <EmptyState text="No contract outcome explanations returned by the API." />
        ) : (
          outcomes.map((outcome) => (
            <article key={outcome.id} className="rounded-md border border-neutral-200 bg-neutral-50 p-3 text-xs">
              <div className="flex flex-wrap items-center gap-2 text-neutral-700">
                <span className="font-semibold text-neutral-950">contract_id {outcome.contract_id}</span>
                <span className="rounded bg-white px-1.5 py-0.5 font-medium text-neutral-600">
                  {String(outcome.decision.status ?? "recorded")}
                </span>
              </div>
              <p className="mt-1 text-neutral-600">obligation_id {outcome.obligation_id ?? "contract"}</p>
              <p className="mt-1 text-neutral-600">source_deal_id {outcome.source_deal_id ?? "not linked"}</p>
              <p className="mt-2 rounded-md bg-white px-3 py-2 leading-5 text-neutral-700">
                {outcome.explanation_text}
              </p>
            </article>
          ))
        )}
      </div>
    </section>
  );
}

function FilterToggle({
  checked,
  filter,
  onChange,
}: Readonly<{
  checked: boolean;
  filter: LogFilter;
  onChange: (filter: LogFilter) => void;
}>) {
  return (
    <label className="inline-flex min-h-8 items-center gap-2 rounded-md border border-neutral-200 bg-neutral-50 px-2.5 py-1 text-xs font-medium text-neutral-700">
      <input
        type="checkbox"
        checked={checked}
        onChange={() => onChange(filter)}
        className="size-3.5 accent-teal-700"
      />
      {filterLabels[filter]}
    </label>
  );
}

function LogKindIcon({ kind }: Readonly<{ kind: LogFilter }>) {
  if (kind === "ai") {
    return <Bot aria-hidden="true" className="mt-0.5 size-3.5 shrink-0 text-purple-700" />;
  }
  if (kind === "rejections") {
    return <ShieldAlert aria-hidden="true" className="mt-0.5 size-3.5 shrink-0 text-rose-700" />;
  }
  if (kind === "deals") {
    return <FileText aria-hidden="true" className="mt-0.5 size-3.5 shrink-0 text-teal-700" />;
  }
  return <ArrowRightLeft aria-hidden="true" className="mt-0.5 size-3.5 shrink-0 text-neutral-700" />;
}

function FullGameLog({ entries }: Readonly<{ entries: GameLogEntry[] }>) {
  const [filters, setFilters] = useState<Record<LogFilter, boolean>>({
    actions: true,
    deals: true,
    ai: true,
    rejections: true,
  });

  const visibleEntries = entries.filter((entry) => filters[entry.kind]);
  const renderedEntries = visibleEntries.slice(-maxRenderedLogEntries);
  const shownCountText =
    renderedEntries.length < visibleEntries.length
      ? `${renderedEntries.length} of ${visibleEntries.length} shown`
      : `${visibleEntries.length} shown`;

  function toggleFilter(filter: LogFilter) {
    setFilters((current) => ({ ...current, [filter]: !current[filter] }));
  }

  return (
    <section aria-label="Game log" className="rounded-md border border-neutral-200 bg-white p-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h2 className="text-sm font-semibold text-neutral-950">Full game log</h2>
          <p className="mt-1 text-xs text-neutral-600">Accepted events, deals, AI decisions, and rejections.</p>
        </div>
        <div className="flex items-center gap-2 text-xs text-neutral-500">
          <ListFilter aria-hidden="true" className="size-3.5" />
          <span>{shownCountText}</span>
        </div>
      </div>

      <div className="mt-3 flex flex-wrap gap-2">
        {(Object.keys(filterLabels) as LogFilter[]).map((filter) => (
          <FilterToggle key={filter} checked={filters[filter]} filter={filter} onChange={toggleFilter} />
        ))}
      </div>

      {visibleEntries.length === 0 ? (
        <div className="mt-3">
          <EmptyState text="No log entries match the selected filters." />
        </div>
      ) : (
        <ol className="mt-3 divide-y divide-neutral-200 text-sm">
          {renderedEntries.map((entry) => (
            <li key={entry.id} className="py-2">
              <div className="flex items-start gap-2">
                <LogKindIcon kind={entry.kind} />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="font-semibold text-neutral-950">{entry.title}</p>
                    <span
                      className={cn(
                        "rounded bg-neutral-100 px-1.5 py-0.5 text-[10px] font-semibold text-neutral-600",
                        entry.kind === "rejections" && "bg-rose-50 text-rose-700",
                        entry.kind === "ai" && "bg-purple-50 text-purple-700",
                      )}
                    >
                      {entry.badge}
                    </span>
                  </div>
                  <p className="mt-1 text-xs leading-5 text-neutral-700">{entry.detail}</p>
                  {entry.sourceAgreementId || entry.dealId || entry.contractId ? (
                    <p className="mt-1 text-xs text-neutral-500">
                      {entry.sourceAgreementId ? `Source agreement ${entry.sourceAgreementId}` : null}
                      {entry.sourceAgreementId && entry.dealId ? " / " : null}
                      {entry.dealId ? `deal ${entry.dealId}` : null}
                      {(entry.sourceAgreementId || entry.dealId) && entry.contractId ? " / " : null}
                      {entry.contractId ? `contract ${entry.contractId}` : null}
                    </p>
                  ) : null}
                  <p className="mt-1 text-[11px] text-neutral-500">{formatDate(entry.timestamp)}</p>
                </div>
              </div>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

export function ContractsPanel({
  apiBaseUrl,
  events,
  game,
  gameId,
  rejectedActions,
}: ContractsPanelProps) {
  const queryClient = useQueryClient();
  const [enforcementResult, setEnforcementResult] = useState<ContractEnforcementResult | null>(null);
  const contractsQuery = useQuery({
    queryKey: ["contracts", gameId],
    queryFn: () => readContracts({ gameId, baseUrl: apiBaseUrl }),
  });

  const obligationsQuery = useQuery({
    queryKey: ["obligations", gameId],
    queryFn: () => readObligations({ gameId, baseUrl: apiBaseUrl }),
  });

  const outcomesQuery = useQuery({
    queryKey: ["contract-outcomes", gameId],
    queryFn: () => readContractOutcomes({ gameId, baseUrl: apiBaseUrl }),
  });

  const dealsQuery = useQuery({
    queryKey: ["deals", gameId],
    queryFn: () => readDeals({ gameId, baseUrl: apiBaseUrl }),
  });

  const contracts = contractsQuery.data ?? [];
  const obligations = obligationsQuery.data ?? [];
  const outcomes = outcomesQuery.data ?? [];
  const deals = dealsQuery.data ?? [];
  const logEntries = useMemo(
    () => buildLogEntries({ deals, events, game, rejectedActions }),
    [deals, events, game, rejectedActions],
  );
  const enforceContractsMutation = useMutation({
    mutationFn: (obligation: ObligationRecord) =>
      settleContract({
        gameId,
        baseUrl: apiBaseUrl,
        contractId: obligation.contract_id,
        obligationId: obligation.id,
      }),
    onSuccess: async (result) => {
      setEnforcementResult(result);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["game", gameId] }),
        queryClient.invalidateQueries({ queryKey: ["game-state", gameId] }),
        queryClient.invalidateQueries({ queryKey: ["legal-actions", gameId] }),
        queryClient.invalidateQueries({ queryKey: ["events", gameId] }),
        queryClient.invalidateQueries({ queryKey: ["rejected-actions", gameId] }),
        queryClient.invalidateQueries({ queryKey: ["contracts", gameId] }),
        queryClient.invalidateQueries({ queryKey: ["obligations", gameId] }),
        queryClient.invalidateQueries({ queryKey: ["contract-outcomes", gameId] }),
        queryClient.invalidateQueries({ queryKey: ["deals", gameId] }),
      ]);
    },
  });
  const enforceDueObligation = (obligation: ObligationRecord) => {
    if (canSettleObligation(obligation)) {
      enforceContractsMutation.mutate(obligation);
    }
  };

  return (
    <section aria-label="Contracts obligations panel" className="grid content-start gap-4">
      {contractsQuery.isError || obligationsQuery.isError || outcomesQuery.isError || dealsQuery.isError ? (
        <ErrorNote text="Contracts, obligations, outcomes, or deal records are unavailable from the API." />
      ) : null}
      {enforceContractsMutation.isError ? <ErrorNote text="Contract enforcement request failed." /> : null}
      {enforcementResult ? <ContractEnforcementStatus result={enforcementResult} /> : null}

      <ActiveContracts contracts={contracts} game={game} isLoading={contractsQuery.isLoading} />
      <UpcomingObligations
        enforcingObligationId={enforceContractsMutation.isPending ? (enforceContractsMutation.variables?.id ?? null) : null}
        game={game}
        isEnforcing={enforceContractsMutation.isPending}
        isLoading={obligationsQuery.isLoading}
        obligations={obligations}
        onEnforce={enforceDueObligation}
      />
      <SettlementHistory isLoading={obligationsQuery.isLoading} obligations={obligations} />
      <ContractOutcomeExplanations isLoading={outcomesQuery.isLoading} outcomes={outcomes} />
      <FullGameLog entries={logEntries} />
    </section>
  );
}
