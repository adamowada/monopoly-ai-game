"use client";

import { useQuery } from "@tanstack/react-query";
import {
  Bot,
  Brain,
  CheckCircle2,
  Database,
  FileJson2,
  GitBranch,
  MessageSquareText,
  Search,
  ShieldAlert,
} from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";

import {
  readAiDecisions,
  readAiMemoryEntries,
  readAiProfiles,
  readAiRejectedOutputs,
  readAiRetrievalRecords,
  readAiSelfDialogue,
  type AiDecision,
  type AiMemoryEntry,
  type AiProfile,
  type AiRejectedOutput,
  type AiRetrievalRecord,
  type AiSelfDialogueRecord,
  type AiValidationError,
} from "../lib/api/ai-audit";
import type { GameMetadata } from "../lib/api/games";
import { cn } from "../lib/ui";
import { PropertyReference, propertyIdsFromText } from "./property-deed-card";

type AiAuditPanelProps = {
  game: GameMetadata;
  gameId: string;
  apiBaseUrl?: string;
};

type DecisionContext = {
  dialogue: AiSelfDialogueRecord[];
  memory: AiMemoryEntry[];
  retrieval: AiRetrievalRecord[];
  rejectedOutputs: AiRejectedOutput[];
};

type AiNotebookFeedItem = {
  badge: string;
  content: string;
  createdAt: string;
  id: string;
  playerId: string;
  tone: "dialogue" | "memory";
};

type AiNotebookView = "decisions" | "stream" | "profiles" | "memory" | "retrieval";

function formatDate(value: string): string {
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

function jsonBlock(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

function playerName(game: GameMetadata, playerId: string | null | undefined): string {
  if (!playerId) {
    return "Unknown player";
  }
  return game.players.find((player) => player.id === playerId)?.name ?? playerId;
}

function validationText(errors: AiValidationError[]): string {
  if (errors.length === 0) {
    return "";
  }
  return errors
    .map((error) => {
      const field = error.field ? `${error.field}: ` : "";
      return `${field}${error.message}`;
    })
    .join(" ");
}

function formatTitleCase(value: string): string {
  return value
    .toLowerCase()
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function decisionLabel(decision: AiDecision, game: GameMetadata): string {
  return `${playerName(game, decision.player_id)} ${formatTitleCase(decision.decision_type).toLowerCase()}`;
}

function metadataRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function compactionMetadata(entry: AiMemoryEntry): Record<string, unknown> | null {
  return metadataRecord(metadataRecord(entry.metadata)?.compaction);
}

function isCompactedSummary(entry: AiMemoryEntry): boolean {
  return compactionMetadata(entry)?.is_summary === true;
}

function compactionSourceIds(entry: AiMemoryEntry): string[] {
  const sourceIds = compactionMetadata(entry)?.source_memory_ids;
  return Array.isArray(sourceIds) ? sourceIds.filter((item): item is string => typeof item === "string") : [];
}

function visibleDialogueRecords(dialogue: AiSelfDialogueRecord[]): AiSelfDialogueRecord[] {
  return dialogue.filter((entry) => entry.status !== "empty" && entry.content.trim() !== "");
}

function legalActionDescription(action: AiDecision["legal_actions"][number]): string | null {
  if (action.type === "ROLL_DICE" || action.type === "END_TURN") {
    return null;
  }
  return action.description ?? null;
}

function EmptyState({ text }: Readonly<{ text: string }>) {
  return <p className="rounded-md border border-dashed border-neutral-300 bg-neutral-50 p-3 text-sm text-neutral-600">{text}</p>;
}

function ErrorNote({ text }: Readonly<{ text: string }>) {
  return (
    <div className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700" role="alert">
      {text}
    </div>
  );
}

function TechnicalRecord({
  buttonLabel,
  children,
}: Readonly<{
  buttonLabel: string;
  children: ReactNode;
}>) {
  const [isOpen, setIsOpen] = useState(false);

  return (
    <div className="mt-3">
      <button
        aria-expanded={isOpen}
        className="rounded-md border border-neutral-200 bg-white px-2.5 py-1.5 text-xs font-medium text-neutral-700 transition hover:bg-neutral-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal-700"
        onClick={() => setIsOpen((current) => !current)}
        type="button"
      >
        {isOpen ? "Hide technical record" : buttonLabel}
      </button>
      {isOpen ? <div className="mt-2 grid gap-2">{children}</div> : null}
    </div>
  );
}

function InlineMeta({ label, value }: Readonly<{ label: string; value: string | null | undefined }>) {
  const displayValue = value ?? "n/a";
  return (
    <span className="inline-flex items-center gap-1 rounded-md bg-neutral-100 px-2 py-1 text-[11px] font-medium text-neutral-700">
      <span className="text-neutral-500">{label}</span>
      {" "}
      <span className="break-all text-neutral-950">{displayValue}</span>
    </span>
  );
}

function StatusBadge({ status }: Readonly<{ status: AiDecision["status"] }>) {
  const accepted = status === "accepted" || status === "validated";
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2 py-1 text-xs font-medium ring-1 ring-inset",
        accepted
          ? "bg-emerald-50 text-emerald-700 ring-emerald-200"
          : "bg-rose-50 text-rose-700 ring-rose-200",
      )}
    >
      {accepted ? (
        <CheckCircle2 aria-hidden="true" className="size-3" />
      ) : (
        <ShieldAlert aria-hidden="true" className="size-3" />
      )}
      {formatTitleCase(status)}
    </span>
  );
}

function AiNotebookStream({
  dialogue,
  game,
  isLoading,
  memory,
}: Readonly<{
  dialogue: AiSelfDialogueRecord[];
  game: GameMetadata;
  isLoading: boolean;
  memory: AiMemoryEntry[];
}>) {
  const items = useMemo<AiNotebookFeedItem[]>(
    () =>
      [
        ...visibleDialogueRecords(dialogue).map((entry) => ({
          badge: `${formatTitleCase(entry.role)} thought`,
          content: entry.content,
          createdAt: entry.created_at,
          id: entry.self_dialogue_id,
          playerId: entry.player_id,
          tone: "dialogue" as const,
        })),
        ...memory.map((entry) => ({
          badge: `${formatTitleCase(entry.category)} memory`,
          content: entry.content,
          createdAt: entry.created_at,
          id: entry.memory_entry_id,
          playerId: entry.player_id,
          tone: "memory" as const,
        })),
      ]
        .sort((left, right) => Date.parse(left.createdAt) - Date.parse(right.createdAt))
        .slice(-140),
    [dialogue, memory],
  );

  return (
    <section aria-label="AI notebook stream" className="rounded-md border border-neutral-200 bg-white p-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-neutral-950">Notebook stream</h3>
        </div>
        <MessageSquareText aria-hidden="true" className="size-4 text-violet-700" />
      </div>

      <ol className="mt-3 flex max-h-[min(54vh,36rem)] min-h-64 flex-col gap-2 overflow-y-auto rounded-md border border-neutral-200 bg-neutral-50 p-3">
        {isLoading ? <EmptyState text="Loading notebook stream." /> : null}
        {!isLoading && items.length === 0 ? <EmptyState text="No AI thoughts or memories." /> : null}
        {items.map((item) => (
          <li
            key={item.id}
            className={cn(
              "rounded-md border px-3 py-2 text-sm",
              item.tone === "dialogue" ? "border-violet-200 bg-white text-neutral-800" : "border-emerald-200 bg-emerald-50 text-emerald-950",
            )}
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="font-semibold text-neutral-950">{playerName(game, item.playerId)}</span>
              <span className="text-[11px] font-medium text-neutral-500">{formatDate(item.createdAt)}</span>
            </div>
            <p className="mt-1 leading-6">{item.content}</p>
            {propertyIdsFromText(item.content).length > 0 ? (
              <span className="mt-2 flex flex-wrap gap-1.5" data-ai-notebook-property-references="">
                {propertyIdsFromText(item.content).map((propertyId) => (
                  <PropertyReference key={propertyId} game={game} propertyId={propertyId} />
                ))}
              </span>
            ) : null}
            <span
              className={cn(
                "mt-2 inline-flex rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase ring-1 ring-inset",
                item.tone === "dialogue"
                  ? "bg-violet-50 text-violet-700 ring-violet-200"
                  : "bg-white text-emerald-700 ring-emerald-200",
              )}
            >
              {item.badge}
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
}

function ProfilesView({
  game,
  isLoading,
  profiles,
}: Readonly<{
  game: GameMetadata;
  profiles: AiProfile[];
  isLoading: boolean;
}>) {
  return (
    <section className="rounded-md border border-neutral-200 bg-white p-3" aria-labelledby="ai-profiles-title">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 id="ai-profiles-title" className="text-sm font-semibold text-neutral-950">
            AI profile
          </h3>
        </div>
        <Bot aria-hidden="true" className="size-4 text-violet-700" />
      </div>

      <div className="mt-3 grid max-h-[min(58vh,38rem)] gap-3 overflow-y-auto pr-1">
        {isLoading ? <EmptyState text="Loading AI profiles." /> : null}
        {!isLoading && profiles.length === 0 ? <EmptyState text="No AI profiles." /> : null}
        {profiles.map((profile) => (
          <article key={profile.ai_profile_id} className="rounded-md border border-neutral-200 bg-neutral-50 p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <h4 className="text-sm font-semibold text-neutral-950">{profile.display_name}</h4>
                <p className="text-xs text-neutral-600">{playerName(game, profile.player_id)}</p>
              </div>
              <span className="text-xs text-neutral-500">{formatDate(profile.created_at)}</span>
            </div>
            <dl className="mt-3 grid gap-2 text-sm text-neutral-700 md:grid-cols-3">
              <div>
                <dt className="text-[11px] font-semibold uppercase text-neutral-500">Traits</dt>
                <dd className="mt-1 text-neutral-950">{profile.traits.join(", ")}</dd>
              </div>
              <div>
                <dt className="text-[11px] font-semibold uppercase text-neutral-500">Personality</dt>
                <dd className="mt-1 text-neutral-950">{profile.personality}</dd>
              </div>
              <div>
                <dt className="text-[11px] font-semibold uppercase text-neutral-500">Play style</dt>
                <dd className="mt-1 text-neutral-950">{profile.play_style}</dd>
              </div>
            </dl>
            <div className="mt-3 rounded-md border border-neutral-200 bg-white p-3 text-sm text-neutral-700">
              <h5 className="text-[11px] font-semibold uppercase text-neutral-500">Persona summary</h5>
              <div className="mt-1 text-neutral-950">{profile.persona_summary}</div>
            </div>
            <TechnicalRecord buttonLabel="Show profile technical record">
              <div className="flex flex-wrap gap-2">
                <InlineMeta label="ai_profile_id" value={profile.ai_profile_id} />
                <InlineMeta label="player_id" value={profile.player_id} />
              </div>
            </TechnicalRecord>
          </article>
        ))}
      </div>
    </section>
  );
}

function LinkedMemory({
  decision,
  memory,
}: Readonly<{
  decision: AiDecision;
  memory: AiMemoryEntry[];
}>) {
  const linked = memory.filter((entry) => decision.memory_entry_ids.includes(entry.memory_entry_id));
  if (linked.length === 0) {
    return null;
  }

  return (
    <div className="rounded-md border border-neutral-200 bg-white p-3">
      <h5 className="inline-flex items-center gap-1.5 text-xs font-semibold uppercase text-neutral-500">
        <Database aria-hidden="true" className="size-3.5" />
        Memory entries
      </h5>
      <ul className="mt-2 grid gap-2">
        {linked.map((entry) => (
          <li key={entry.memory_entry_id} className="text-sm text-neutral-700">
            <span className="font-medium text-neutral-950">{entry.content}</span>
            <span className="block">
              {entry.category} - {entry.visibility} - importance {entry.importance}
            </span>
            <span className="block">
              {isCompactedSummary(entry)
                ? `compacted summary of ${compactionSourceIds(entry).length} source memories`
                : "active memory note"}
            </span>
            <TechnicalRecord buttonLabel="Show memory technical record">
              <div className="flex flex-wrap gap-2">
                <InlineMeta label="memory_entry_id" value={entry.memory_entry_id} />
                <InlineMeta label="ai_decision_id" value={decision.ai_decision_id} />
                <InlineMeta label="superseded_by_memory_id" value={entry.superseded_by_memory_id} />
              </div>
            </TechnicalRecord>
          </li>
        ))}
      </ul>
    </div>
  );
}

function LinkedRetrievals({
  decision,
  retrieval,
}: Readonly<{
  decision: AiDecision;
  retrieval: AiRetrievalRecord[];
}>) {
  const linked = retrieval.filter((record) => decision.retrieval_record_ids.includes(record.retrieval_record_id));
  if (linked.length === 0) {
    return null;
  }

  return (
    <div className="rounded-md border border-neutral-200 bg-white p-3">
      <h5 className="inline-flex items-center gap-1.5 text-xs font-semibold uppercase text-neutral-500">
        <Search aria-hidden="true" className="size-3.5" />
        Retrieved context records
      </h5>
      <ul className="mt-2 grid gap-2">
        {linked.map((record) => (
          <li key={record.retrieval_record_id} className="text-sm text-neutral-700">
            <span className="font-medium text-neutral-950">{record.content}</span>
            <span className="block">
              {record.source_type} context{record.score === null ? "" : ` - score ${record.score.toFixed(2)}`}
            </span>
            <TechnicalRecord buttonLabel="Show retrieval technical record">
              <div className="flex flex-wrap gap-2">
                <InlineMeta label="retrieval_record_id" value={record.retrieval_record_id} />
                <InlineMeta label="ai_decision_id" value={record.ai_decision_id} />
                <InlineMeta label="source_id" value={record.source_id} />
              </div>
            </TechnicalRecord>
          </li>
        ))}
      </ul>
    </div>
  );
}

function LinkedDialogue({
  dialogue,
}: Readonly<{
  dialogue: AiSelfDialogueRecord[];
}>) {
  const visibleDialogue = visibleDialogueRecords(dialogue);
  if (visibleDialogue.length === 0) {
    return null;
  }

  return (
    <div className="rounded-md border border-neutral-200 bg-white p-3">
      <h5 className="inline-flex items-center gap-1.5 text-xs font-semibold uppercase text-neutral-500">
        <MessageSquareText aria-hidden="true" className="size-3.5" />
        Self-dialogue timeline
      </h5>
      <ol className="mt-2 grid gap-2">
        {visibleDialogue.map((entry) => (
          <li key={entry.self_dialogue_id} className="border-l-2 border-violet-200 pl-3 text-sm text-neutral-700">
            <span className="font-medium text-neutral-950">
              #{entry.sequence} {entry.role} - {formatTitleCase(entry.status)}
            </span>
            <span className="block">{entry.phase ? `Phase ${formatTitleCase(entry.phase)}` : "Phase unavailable"}</span>
            <span className="block">{entry.content}</span>
            <TechnicalRecord buttonLabel="Show dialogue technical record">
              <div className="flex flex-wrap gap-2">
                <InlineMeta label="self_dialogue_id" value={entry.self_dialogue_id} />
                <InlineMeta label="ai_decision_id" value={entry.ai_decision_id} />
                <InlineMeta label="ai_profile_id" value={entry.ai_profile_id} />
                <InlineMeta label="state_hash" value={entry.state_hash} />
              </div>
              <pre className="overflow-x-auto rounded-md bg-neutral-100 p-2 text-xs text-neutral-800">
                {jsonBlock(entry.payload)}
              </pre>
            </TechnicalRecord>
          </li>
        ))}
      </ol>
    </div>
  );
}

function RejectedOutputList({ records }: Readonly<{ records: AiRejectedOutput[] }>) {
  if (records.length === 0) {
    return null;
  }

  return (
    <div className="rounded-md border border-rose-200 bg-rose-50 p-3">
      <h5 className="inline-flex items-center gap-1.5 text-xs font-semibold uppercase text-rose-700">
        <ShieldAlert aria-hidden="true" className="size-3.5" />
        Rejected AI outputs
      </h5>
      <ul className="mt-2 grid gap-3">
        {records.map((record) => (
          <li key={record.rejected_output_id} className="rounded-md bg-white p-3 text-sm text-neutral-700">
            <h6 className="font-medium text-neutral-950">{formatTitleCase(record.status)} AI output</h6>
            {record.validation_errors.length > 0 ? (
              <div className="mt-2">
                <h6 className="font-medium text-neutral-950">Validation errors</h6>
                <div>{validationText(record.validation_errors)}</div>
              </div>
            ) : null}
            <TechnicalRecord buttonLabel="Show rejected output technical record">
              <div className="flex flex-wrap gap-2">
                <InlineMeta label="rejected_output_id" value={record.rejected_output_id} />
                <InlineMeta label="state_hash" value={record.state_hash} />
                <InlineMeta label="status" value={record.status} />
                <InlineMeta label="rejected_action_id" value={record.rejected_action_id} />
              </div>
              <div className="grid gap-2 md:grid-cols-2">
                <div>
                  <h6 className="text-[11px] font-semibold uppercase text-neutral-500">Raw output</h6>
                  <pre className="mt-1 overflow-x-auto rounded-md bg-neutral-950 p-2 text-xs text-white">{record.raw_output}</pre>
                </div>
                <div>
                  <h6 className="text-[11px] font-semibold uppercase text-neutral-500">Parsed output</h6>
                  <pre className="mt-1 overflow-x-auto rounded-md bg-neutral-950 p-2 text-xs text-white">
                    {jsonBlock(record.parsed_output)}
                  </pre>
                </div>
              </div>
            </TechnicalRecord>
          </li>
        ))}
      </ul>
    </div>
  );
}

function DecisionCard({
  context,
  decision,
  game,
}: Readonly<{
  context: DecisionContext;
  decision: AiDecision;
  game: GameMetadata;
}>) {
  const label = decisionLabel(decision, game);

  return (
    <article
      aria-label={`AI decision: ${label} ${formatTitleCase(decision.status)}`}
      className="rounded-md border border-neutral-200 bg-neutral-50 p-4"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h4 className="text-sm font-semibold text-neutral-950">
            {label}
          </h4>
          <span className="mt-1 block text-xs text-neutral-600">
            {formatTitleCase(decision.phase ?? "phase unavailable")} - {formatDate(decision.created_at)}
          </span>
        </div>
        <StatusBadge status={decision.status} />
      </div>

      <div className="mt-3 grid gap-3">
        {decision.legal_actions.length > 0 ? (
          <div className="rounded-md border border-neutral-200 bg-white p-3">
            <h5 className="inline-flex items-center gap-1.5 text-xs font-semibold uppercase text-neutral-500">
              <GitBranch aria-hidden="true" className="size-3.5" />
              Legal actions snapshot
            </h5>
            <ul className="mt-2 grid gap-2 text-sm text-neutral-700">
              {decision.legal_actions.map((action) => (
                <li key={`${action.actor_id}-${action.type}-${JSON.stringify(action.payload)}`}>
                  <span className="font-medium text-neutral-950">{action.type}</span>
                  {legalActionDescription(action) ? <span className="block">{legalActionDescription(action)}</span> : null}
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        <div className="grid gap-3 xl:grid-cols-2">
          <LinkedDialogue dialogue={context.dialogue} />
          <LinkedMemory decision={decision} memory={context.memory} />
        </div>

        <div className="grid gap-3 xl:grid-cols-2">
          <LinkedRetrievals decision={decision} retrieval={context.retrieval} />
          <RejectedOutputList records={context.rejectedOutputs} />
        </div>

        {decision.validation_errors.length > 0 ? (
          <div className="text-sm text-neutral-700">
            <span className="font-medium text-neutral-950">Validation errors</span>{" "}
            {validationText(decision.validation_errors)}
          </div>
        ) : null}

        <TechnicalRecord buttonLabel="Show AI technical trace">
          <div className="flex flex-wrap gap-2">
            <InlineMeta label="ai_decision_id" value={decision.ai_decision_id} />
            <InlineMeta label="ai_profile_id" value={decision.ai_profile_id} />
            <InlineMeta label="state_hash" value={decision.state_hash} />
            <InlineMeta label="prompt_context_hash" value={decision.prompt_context_hash} />
            <InlineMeta label="accepted_event_id" value={decision.accepted_event_id} />
            <InlineMeta label="rejected_action_id" value={decision.rejected_action_id} />
          </div>
          <div className="rounded-md border border-neutral-200 bg-white p-3">
            <h5 className="inline-flex items-center gap-1.5 text-xs font-semibold uppercase text-neutral-500">
              <Brain aria-hidden="true" className="size-3.5" />
              Prompt context
            </h5>
            <pre className="mt-2 overflow-x-auto rounded-md bg-neutral-950 p-3 text-xs text-white">
              {jsonBlock(decision.prompt_context)}
            </pre>
          </div>
          <div className="grid gap-3 xl:grid-cols-2">
            <div className="rounded-md border border-neutral-200 bg-white p-3">
              <h5 className="inline-flex items-center gap-1.5 text-xs font-semibold uppercase text-neutral-500">
                <FileJson2 aria-hidden="true" className="size-3.5" />
                Raw output
              </h5>
              <pre className="mt-2 overflow-x-auto rounded-md bg-neutral-950 p-3 text-xs text-white">{decision.raw_output}</pre>
            </div>
            <div className="rounded-md border border-neutral-200 bg-white p-3">
              <h5 className="inline-flex items-center gap-1.5 text-xs font-semibold uppercase text-neutral-500">
                <FileJson2 aria-hidden="true" className="size-3.5" />
                Parsed output
              </h5>
              <pre className="mt-2 overflow-x-auto rounded-md bg-neutral-950 p-3 text-xs text-white">
                {jsonBlock(decision.parsed_output)}
              </pre>
            </div>
          </div>
        </TechnicalRecord>
      </div>
    </article>
  );
}

export function AiAuditPanel({ apiBaseUrl, game, gameId }: AiAuditPanelProps) {
  const [activeView, setActiveView] = useState<AiNotebookView>("decisions");
  const profilesQuery = useQuery({
    queryKey: ["ai-profiles", gameId],
    queryFn: () => readAiProfiles({ gameId, baseUrl: apiBaseUrl }),
  });
  const decisionsQuery = useQuery({
    queryKey: ["ai-decisions", gameId],
    queryFn: () => readAiDecisions({ gameId, baseUrl: apiBaseUrl }),
  });
  const selfDialogueQuery = useQuery({
    queryKey: ["ai-self-dialogue", gameId],
    queryFn: () => readAiSelfDialogue({ gameId, baseUrl: apiBaseUrl }),
  });
  const memoryQuery = useQuery({
    queryKey: ["ai-memory", gameId],
    queryFn: () => readAiMemoryEntries({ gameId, baseUrl: apiBaseUrl }),
  });
  const retrievalQuery = useQuery({
    queryKey: ["ai-retrieval-records", gameId],
    queryFn: () => readAiRetrievalRecords({ gameId, baseUrl: apiBaseUrl }),
  });
  const rejectedOutputsQuery = useQuery({
    queryKey: ["ai-rejected-outputs", gameId],
    queryFn: () => readAiRejectedOutputs({ gameId, baseUrl: apiBaseUrl }),
  });

  const profiles = profilesQuery.data ?? [];
  const decisions = decisionsQuery.data ?? [];
  const selfDialogue = selfDialogueQuery.data ?? [];
  const memory = memoryQuery.data ?? [];
  const retrieval = retrievalQuery.data ?? [];
  const rejectedOutputs = rejectedOutputsQuery.data ?? [];
  const hasError =
    profilesQuery.isError ||
    decisionsQuery.isError ||
    selfDialogueQuery.isError ||
    memoryQuery.isError ||
    retrievalQuery.isError ||
    rejectedOutputsQuery.isError;

  const decisionContexts = useMemo(() => {
    const byDecisionId = new Map<string, DecisionContext>();
    for (const decision of decisions) {
      byDecisionId.set(decision.ai_decision_id, {
        dialogue: selfDialogue
          .filter((record) => record.ai_decision_id === decision.ai_decision_id)
          .sort((left, right) => left.sequence - right.sequence),
        memory: memory.filter((entry) => decision.memory_entry_ids.includes(entry.memory_entry_id)),
        retrieval: retrieval.filter((record) => decision.retrieval_record_ids.includes(record.retrieval_record_id)),
        rejectedOutputs: rejectedOutputs.filter((record) => record.ai_decision_id === decision.ai_decision_id),
      });
    }
    return byDecisionId;
  }, [decisions, memory, rejectedOutputs, retrieval, selfDialogue]);

  const tabs = [
    { id: "decisions" as const, label: "Decisions", count: decisions.length, icon: GitBranch },
    { id: "stream" as const, label: "Stream", count: selfDialogue.length + memory.length, icon: MessageSquareText },
    { id: "profiles" as const, label: "Profiles", count: profiles.length, icon: Bot },
    { id: "memory" as const, label: "Memory", count: memory.length, icon: Database },
    { id: "retrieval" as const, label: "Retrieval", count: retrieval.length, icon: Search },
  ];

  return (
    <section aria-label="AI audit" className="grid content-start gap-3">
      <div className="rounded-md border border-neutral-200 bg-white p-3">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <h2 id="ai-audit-title" className="text-base font-semibold text-neutral-950">
              AI notebook
            </h2>
          </div>
          <div className="flex flex-wrap gap-2">
            <span className="inline-flex w-fit items-center gap-1.5 rounded-full bg-violet-50 px-2 py-1 text-xs font-medium text-violet-700 ring-1 ring-inset ring-violet-200">
              <Brain aria-hidden="true" className="size-3" />
              {profiles.length} profiles
            </span>
            <span className="inline-flex w-fit items-center gap-1.5 rounded-full bg-neutral-100 px-2 py-1 text-xs font-medium text-neutral-700 ring-1 ring-inset ring-neutral-200">
              {decisions.length} decisions
            </span>
            {rejectedOutputs.length > 0 ? (
              <span className="inline-flex w-fit items-center gap-1.5 rounded-full bg-rose-50 px-2 py-1 text-xs font-medium text-rose-700 ring-1 ring-inset ring-rose-200">
                <ShieldAlert aria-hidden="true" className="size-3" />
                {rejectedOutputs.length} rejected
              </span>
            ) : null}
          </div>
        </div>
        <div
          aria-label="AI notebook sections"
          className="mt-3 flex gap-1 overflow-x-auto rounded-md border border-neutral-200 bg-neutral-50 p-1"
          role="tablist"
        >
          {tabs.map((tab) => {
            const Icon = tab.icon;
            const selected = activeView === tab.id;
            return (
              <button
                key={tab.id}
                aria-controls={`ai-notebook-${tab.id}-panel`}
                aria-selected={selected}
                className={cn(
                  "inline-flex shrink-0 items-center gap-1.5 rounded px-2.5 py-1.5 text-xs font-semibold transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal-700",
                  selected ? "bg-white text-neutral-950 shadow-sm" : "text-neutral-600 hover:bg-white/70",
                )}
                id={`ai-notebook-${tab.id}-tab`}
                onClick={() => setActiveView(tab.id)}
                role="tab"
                type="button"
              >
                <Icon aria-hidden="true" className="size-3.5" />
                {tab.label}
                <span className="rounded-full bg-neutral-200 px-1.5 py-0.5 text-[10px] text-neutral-700">{tab.count}</span>
              </button>
            );
          })}
        </div>
      </div>

      {hasError ? <ErrorNote text="AI notebook records are unavailable." /> : null}

      {activeView === "stream" ? (
        <div aria-labelledby="ai-notebook-stream-tab" id="ai-notebook-stream-panel" role="tabpanel">
          <AiNotebookStream
            dialogue={selfDialogue}
            game={game}
            isLoading={selfDialogueQuery.isLoading || memoryQuery.isLoading}
            memory={memory}
          />
        </div>
      ) : null}

      {activeView === "profiles" ? (
        <div aria-labelledby="ai-notebook-profiles-tab" id="ai-notebook-profiles-panel" role="tabpanel">
          <ProfilesView game={game} isLoading={profilesQuery.isLoading} profiles={profiles} />
        </div>
      ) : null}

      {activeView === "decisions" ? (
        <section
          aria-labelledby="ai-notebook-decisions-tab"
          id="ai-notebook-decisions-panel"
          role="tabpanel"
          className="rounded-md border border-neutral-200 bg-white p-3"
        >
          <div className="flex items-start justify-between gap-3">
            <h3 id="ai-decisions-title" className="text-sm font-semibold text-neutral-950">
              Decision history
            </h3>
            <GitBranch aria-hidden="true" className="size-4 text-violet-700" />
          </div>

          <div className="mt-3 grid max-h-[min(62vh,42rem)] gap-3 overflow-y-auto pr-1">
            {decisionsQuery.isLoading ? <EmptyState text="Loading decision history." /> : null}
            {!decisionsQuery.isLoading && decisions.length === 0 ? (
              <EmptyState text="No AI decisions." />
            ) : null}
            {decisions.map((decision) => (
              <DecisionCard
                key={decision.ai_decision_id}
                context={
                  decisionContexts.get(decision.ai_decision_id) ?? {
                    dialogue: [],
                    memory: [],
                    retrieval: [],
                    rejectedOutputs: [],
                  }
                }
                decision={decision}
                game={game}
              />
            ))}
          </div>
        </section>
      ) : null}

      {activeView === "memory" ? (
        <section
          aria-labelledby="ai-notebook-memory-tab"
          id="ai-notebook-memory-panel"
          role="tabpanel"
          className="rounded-md border border-neutral-200 bg-white p-3"
        >
          <div className="flex items-start justify-between gap-3">
            <h3 id="ai-memory-title" className="text-sm font-semibold text-neutral-950">
              Memory entries
            </h3>
            <Database aria-hidden="true" className="size-4 text-violet-700" />
          </div>
          {memoryQuery.isLoading ? <EmptyState text="Loading memory entries." /> : null}
          {!memoryQuery.isLoading && memory.length === 0 ? <EmptyState text="No memory records." /> : null}
          {memory.length > 0 ? (
            <ul className="mt-3 grid max-h-[min(62vh,42rem)] gap-2 overflow-y-auto pr-1 text-sm text-neutral-700">
              {memory.map((entry) => (
                <li key={entry.memory_entry_id} className="rounded-md border border-neutral-200 bg-neutral-50 p-3">
                  <span className="font-medium text-neutral-950">{entry.content}</span>
                  <span className="block">
                    {playerName(game, entry.player_id)} - {entry.category} - {entry.visibility} - importance {entry.importance}
                  </span>
                  {isCompactedSummary(entry) ? (
                    <span className="block">Compacted from {compactionSourceIds(entry).length} source memories.</span>
                  ) : null}
                  <TechnicalRecord buttonLabel="Show memory technical record">
                    <div className="flex flex-wrap gap-2">
                      <InlineMeta label="memory_entry_id" value={entry.memory_entry_id} />
                      <InlineMeta label="ai_profile_id" value={entry.ai_profile_id} />
                      <InlineMeta label="source_decision_id" value={entry.source_decision_id} />
                      <InlineMeta label="source_event_id" value={entry.source_event_id} />
                      <InlineMeta label="superseded_by_memory_id" value={entry.superseded_by_memory_id} />
                    </div>
                  </TechnicalRecord>
                </li>
              ))}
            </ul>
          ) : null}
        </section>
      ) : null}

      {activeView === "retrieval" ? (
        <section
          aria-labelledby="ai-notebook-retrieval-tab"
          id="ai-notebook-retrieval-panel"
          role="tabpanel"
          className="rounded-md border border-neutral-200 bg-white p-3"
        >
          <div className="flex items-start justify-between gap-3">
            <h3 id="ai-retrieval-title" className="text-sm font-semibold text-neutral-950">
              Retrieved context records
            </h3>
            <Search aria-hidden="true" className="size-4 text-violet-700" />
          </div>
          {retrievalQuery.isLoading ? <EmptyState text="Loading retrieved context." /> : null}
          {!retrievalQuery.isLoading && retrieval.length === 0 ? (
            <EmptyState text="No retrieved context." />
          ) : null}
          {retrieval.length > 0 ? (
            <ul className="mt-3 grid max-h-[min(62vh,42rem)] gap-2 overflow-y-auto pr-1 text-sm text-neutral-700">
              {retrieval.map((record) => (
                <li key={record.retrieval_record_id} className="rounded-md border border-neutral-200 bg-neutral-50 p-3">
                  <span className="font-medium text-neutral-950">{record.content}</span>
                  <span className="block">
                    {record.source_type} context{record.score === null ? "" : ` - score ${record.score.toFixed(2)}`}
                  </span>
                  <TechnicalRecord buttonLabel="Show retrieval technical record">
                    <div className="flex flex-wrap gap-2">
                      <InlineMeta label="retrieval_record_id" value={record.retrieval_record_id} />
                      <InlineMeta label="ai_decision_id" value={record.ai_decision_id} />
                      <InlineMeta label="source_id" value={record.source_id} />
                    </div>
                  </TechnicalRecord>
                </li>
              ))}
            </ul>
          ) : null}
        </section>
      ) : null}
    </section>
  );
}
