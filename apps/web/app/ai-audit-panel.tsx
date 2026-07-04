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
import { useMemo } from "react";

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
    return "No validation errors.";
  }
  return errors
    .map((error) => {
      const field = error.field ? `${error.field}: ` : "";
      return `${field}${error.message}`;
    })
    .join(" ");
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

function InlineMeta({ label, value }: Readonly<{ label: string; value: string }>) {
  return (
    <span className="inline-flex items-center gap-1 rounded-md bg-neutral-100 px-2 py-1 text-[11px] font-medium text-neutral-700">
      <span className="text-neutral-500">{label}</span>
      {" "}
      <span className="break-all text-neutral-950">{value}</span>
    </span>
  );
}

function StatusBadge({ status }: Readonly<{ status: AiDecision["status"] }>) {
  const accepted = status === "accepted";
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
      {status}
    </span>
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
    <section className="rounded-md border border-neutral-200 bg-white p-4" aria-labelledby="ai-profiles-title">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 id="ai-profiles-title" className="text-sm font-semibold text-neutral-950">
            AI profile
          </h3>
          <p className="mt-1 text-xs text-neutral-600">Profile traits, personality, and play-style for each AI player.</p>
        </div>
        <Bot aria-hidden="true" className="size-4 text-violet-700" />
      </div>

      <div className="mt-3 grid gap-3">
        {isLoading ? <EmptyState text="Loading AI profile records from the API." /> : null}
        {!isLoading && profiles.length === 0 ? <EmptyState text="No AI profile records returned by the API." /> : null}
        {profiles.map((profile) => (
          <article key={profile.ai_profile_id} className="rounded-md border border-neutral-200 bg-neutral-50 p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <h4 className="text-sm font-semibold text-neutral-950">{profile.display_name}</h4>
                <p className="text-xs text-neutral-600">
                  {playerName(game, profile.player_id)} · ai_profile_id {profile.ai_profile_id}
                </p>
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
  return (
    <div className="rounded-md border border-neutral-200 bg-white p-3">
      <h5 className="inline-flex items-center gap-1.5 text-xs font-semibold uppercase text-neutral-500">
        <Database aria-hidden="true" className="size-3.5" />
        Memory entries
      </h5>
      {linked.length === 0 ? (
        <p className="mt-2 text-sm text-neutral-600">No memory_entry_ids were linked to this decision.</p>
      ) : (
        <ul className="mt-2 grid gap-2">
          {linked.map((entry) => (
            <li key={entry.memory_entry_id} className="text-sm text-neutral-700">
              <span className="font-medium text-neutral-950">memory_entry_id {entry.memory_entry_id}</span>
              <span className="block">Used by decision {decision.ai_decision_id}</span>
              <span className="block">{entry.content}</span>
            </li>
          ))}
        </ul>
      )}
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
  return (
    <div className="rounded-md border border-neutral-200 bg-white p-3">
      <h5 className="inline-flex items-center gap-1.5 text-xs font-semibold uppercase text-neutral-500">
        <Search aria-hidden="true" className="size-3.5" />
        Retrieved context records
      </h5>
      {linked.length === 0 ? (
        <p className="mt-2 text-sm text-neutral-600">No retrieval_record_ids were linked to this decision.</p>
      ) : (
        <ul className="mt-2 grid gap-2">
          {linked.map((record) => (
            <li key={record.retrieval_record_id} className="text-sm text-neutral-700">
              <span className="font-medium text-neutral-950">retrieval_record_id {record.retrieval_record_id}</span>
              <span className="block">
                Linked decision {record.ai_decision_id} · source {record.source_type}:{record.source_id} · score{" "}
                {record.score.toFixed(2)}
              </span>
              <span className="block">{record.content}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function LinkedDialogue({
  dialogue,
}: Readonly<{
  dialogue: AiSelfDialogueRecord[];
}>) {
  return (
    <div className="rounded-md border border-neutral-200 bg-white p-3">
      <h5 className="inline-flex items-center gap-1.5 text-xs font-semibold uppercase text-neutral-500">
        <MessageSquareText aria-hidden="true" className="size-3.5" />
        Self-dialogue timeline
      </h5>
      {dialogue.length === 0 ? (
        <p className="mt-2 text-sm text-neutral-600">No self_dialogue records were linked to this decision.</p>
      ) : (
        <ol className="mt-2 grid gap-2">
          {dialogue.map((entry) => (
            <li key={entry.self_dialogue_id} className="border-l-2 border-violet-200 pl-3 text-sm text-neutral-700">
              <span className="font-medium text-neutral-950">
                #{entry.sequence} {entry.role} · self_dialogue_id {entry.self_dialogue_id}
              </span>
              <span className="block">Linked decision {entry.ai_decision_id}</span>
              <span className="block">{entry.content}</span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

function RejectedOutputList({ records }: Readonly<{ records: AiRejectedOutput[] }>) {
  return (
    <div className="rounded-md border border-rose-200 bg-rose-50 p-3">
      <h5 className="inline-flex items-center gap-1.5 text-xs font-semibold uppercase text-rose-700">
        <ShieldAlert aria-hidden="true" className="size-3.5" />
        Rejected AI outputs
      </h5>
      {records.length === 0 ? (
        <p className="mt-2 text-sm text-rose-700">No rejected AI outputs were linked to this decision.</p>
      ) : (
        <ul className="mt-2 grid gap-3">
          {records.map((record) => (
            <li key={record.rejected_output_id} className="rounded-md bg-white p-3 text-sm text-neutral-700">
              <div className="flex flex-wrap gap-2">
                <InlineMeta label="rejected_output_id" value={record.rejected_output_id} />
                <InlineMeta label="state_hash" value={record.state_hash} />
              </div>
              <p className="mt-2 font-medium text-neutral-950">Validation errors</p>
              <p>{validationText(record.validation_errors)}</p>
              <div className="mt-2 grid gap-2 md:grid-cols-2">
                <div>
                  <p className="text-[11px] font-semibold uppercase text-neutral-500">Raw output</p>
                  <pre className="mt-1 overflow-x-auto rounded-md bg-neutral-950 p-2 text-xs text-white">{record.raw_output}</pre>
                </div>
                <div>
                  <p className="text-[11px] font-semibold uppercase text-neutral-500">Parsed output</p>
                  <pre className="mt-1 overflow-x-auto rounded-md bg-neutral-950 p-2 text-xs text-white">
                    {jsonBlock(record.parsed_output)}
                  </pre>
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
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
  return (
    <article className="rounded-md border border-neutral-200 bg-neutral-50 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h4 className="text-sm font-semibold text-neutral-950">
            Decision {decision.ai_decision_id} · {playerName(game, decision.player_id)}
          </h4>
          <div className="mt-2 flex flex-wrap gap-2">
            <InlineMeta label="ai_decision_id" value={decision.ai_decision_id} />
            <InlineMeta label="ai_profile_id" value={decision.ai_profile_id} />
            <InlineMeta label="state_hash" value={decision.state_hash} />
          </div>
        </div>
        <StatusBadge status={decision.status} />
      </div>

      <p className="mt-3 text-xs text-neutral-600">
        Trace state/legal actions -&gt; prompt context/retrieved records/memory -&gt; raw output/parsed output -&gt; accepted or
        rejected status.
      </p>

      <div className="mt-3 grid gap-3">
        <div className="rounded-md border border-neutral-200 bg-white p-3">
          <h5 className="inline-flex items-center gap-1.5 text-xs font-semibold uppercase text-neutral-500">
            <GitBranch aria-hidden="true" className="size-3.5" />
            Legal actions snapshot
          </h5>
          <ul className="mt-2 grid gap-2 text-sm text-neutral-700">
            {decision.legal_actions.map((action) => (
              <li key={`${action.actor_id}-${action.type}-${JSON.stringify(action.payload)}`}>
                <span className="font-medium text-neutral-950">{action.type}</span>
                <span className="block">
                  actor {action.actor_id} · expected_state_hash {action.expected_state_hash} · event_sequence{" "}
                  {action.expected_event_sequence}
                </span>
                <pre className="mt-1 overflow-x-auto rounded-md bg-neutral-100 p-2 text-xs text-neutral-800">
                  {jsonBlock(action.payload)}
                </pre>
              </li>
            ))}
          </ul>
        </div>

        <div className="grid gap-3 xl:grid-cols-2">
          <div className="rounded-md border border-neutral-200 bg-white p-3">
            <h5 className="inline-flex items-center gap-1.5 text-xs font-semibold uppercase text-neutral-500">
              <Brain aria-hidden="true" className="size-3.5" />
              Prompt context
            </h5>
            <pre className="mt-2 overflow-x-auto rounded-md bg-neutral-950 p-3 text-xs text-white">
              {jsonBlock(decision.prompt_context)}
            </pre>
          </div>
          <LinkedDialogue dialogue={context.dialogue} />
        </div>

        <div className="grid gap-3 xl:grid-cols-2">
          <LinkedMemory decision={decision} memory={context.memory} />
          <LinkedRetrievals decision={decision} retrieval={context.retrieval} />
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
            <p className="mt-2 text-sm text-neutral-700">
              <span className="font-medium text-neutral-950">Validation errors</span>{" "}
              {validationText(decision.validation_errors)}
            </p>
          </div>
        </div>

        <RejectedOutputList records={context.rejectedOutputs} />
      </div>
    </article>
  );
}

export function AiAuditPanel({ apiBaseUrl, game, gameId }: AiAuditPanelProps) {
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

  return (
    <section aria-label="AI audit" className="grid content-start gap-4">
      <div className="rounded-md border border-neutral-200 bg-white p-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <h2 id="ai-audit-title" className="text-base font-semibold text-neutral-950">
              AI audit
            </h2>
            <p className="mt-1 text-sm text-neutral-600">
              Private local research view for server-owned AI reasoning records. codex exec runtime is scheduled for Phase 7.
            </p>
          </div>
          <span className="inline-flex w-fit items-center gap-1.5 rounded-full bg-violet-50 px-2 py-1 text-xs font-medium text-violet-700 ring-1 ring-inset ring-violet-200">
            <Brain aria-hidden="true" className="size-3" />
            {profiles.length} profiles · {decisions.length} decisions
          </span>
        </div>
        <p className="mt-3 text-xs text-neutral-600">
          Traceability: state/legal actions -&gt; Prompt context, Memory entries, and Retrieved context records -&gt; Raw output,
          Parsed output, Validation errors, and accepted or rejected status.
        </p>
        <p className="mt-2 text-xs text-neutral-600">
          Audit sections: AI profile, Decision history, Self-dialogue timeline, Memory entries, Retrieved context records,
          Rejected AI outputs, Validation errors.
        </p>
      </div>

      {hasError ? <ErrorNote text="AI audit records are unavailable from the API." /> : null}

      <ProfilesView game={game} isLoading={profilesQuery.isLoading} profiles={profiles} />

      <section className="rounded-md border border-neutral-200 bg-white p-4" aria-labelledby="ai-decisions-title">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h3 id="ai-decisions-title" className="text-sm font-semibold text-neutral-950">
              Decision history
            </h3>
            <p className="mt-1 text-xs text-neutral-600">
              Decisions link ai_decision_id, ai_profile_id, state_hash, legal_actions, prompt_context, raw_output,
              parsed_output, validation_errors, memory_entry_ids, and retrieval_record_ids.
            </p>
          </div>
          <GitBranch aria-hidden="true" className="size-4 text-violet-700" />
        </div>

        <div className="mt-3 grid gap-3">
          {decisionsQuery.isLoading ? <EmptyState text="Loading Decision history from the API." /> : null}
          {!decisionsQuery.isLoading && decisions.length === 0 ? (
            <EmptyState text="No AI decisions returned by the API." />
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

      <section className="rounded-md border border-neutral-200 bg-white p-4" aria-labelledby="ai-memory-title">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h3 id="ai-memory-title" className="text-sm font-semibold text-neutral-950">
              Memory entries
            </h3>
            <p className="mt-1 text-xs text-neutral-600">All local memory records loaded for this private audit view.</p>
          </div>
          <Database aria-hidden="true" className="size-4 text-violet-700" />
        </div>
        {memoryQuery.isLoading ? <EmptyState text="Loading Memory entries from the API." /> : null}
        {!memoryQuery.isLoading && memory.length === 0 ? <EmptyState text="No memory records returned by the API." /> : null}
        {memory.length > 0 ? (
          <ul className="mt-3 grid gap-2 text-sm text-neutral-700">
            {memory.map((entry) => (
              <li key={entry.memory_entry_id} className="rounded-md border border-neutral-200 bg-neutral-50 p-3">
                <span className="font-medium text-neutral-950">memory_entry_id {entry.memory_entry_id}</span>
                <span className="block">
                  {playerName(game, entry.player_id)} · {entry.kind} · ai_profile_id {entry.ai_profile_id}
                </span>
                <span className="block">{entry.content}</span>
              </li>
            ))}
          </ul>
        ) : null}
      </section>

      <section className="rounded-md border border-neutral-200 bg-white p-4" aria-labelledby="ai-retrieval-title">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h3 id="ai-retrieval-title" className="text-sm font-semibold text-neutral-950">
              Retrieved context records
            </h3>
            <p className="mt-1 text-xs text-neutral-600">Retrieved context records that decisions used.</p>
          </div>
          <Search aria-hidden="true" className="size-4 text-violet-700" />
        </div>
        {retrievalQuery.isLoading ? <EmptyState text="Loading Retrieved context records from the API." /> : null}
        {!retrievalQuery.isLoading && retrieval.length === 0 ? (
          <EmptyState text="No retrieved context records returned by the API." />
        ) : null}
        {retrieval.length > 0 ? (
          <ul className="mt-3 grid gap-2 text-sm text-neutral-700">
            {retrieval.map((record) => (
              <li key={record.retrieval_record_id} className="rounded-md border border-neutral-200 bg-neutral-50 p-3">
                <span className="font-medium text-neutral-950">retrieval_record_id {record.retrieval_record_id}</span>
                <span className="block">
                  Linked decision {record.ai_decision_id} · source {record.source_type}:{record.source_id}
                </span>
                <span className="block">{record.content}</span>
              </li>
            ))}
          </ul>
        ) : null}
      </section>
    </section>
  );
}
