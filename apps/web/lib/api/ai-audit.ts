import { z } from "zod";

import { LegalActionSchema } from "./gameplay";

export const AiValidationErrorSchema = z.object({
  code: z.string().min(1),
  message: z.string().min(1),
  field: z.string().min(1).nullable().optional(),
});

export const AiProfileSchema = z.object({
  ai_profile_id: z.string().min(1),
  game_id: z.string().min(1),
  player_id: z.string().min(1),
  display_name: z.string().min(1),
  traits: z.array(z.string().min(1)),
  personality: z.string().min(1),
  play_style: z.string().min(1),
  persona_summary: z.string().min(1),
  created_at: z.coerce.string().min(1),
});

export const AiDecisionSchema = z.object({
  ai_decision_id: z.string().min(1),
  game_id: z.string().min(1),
  ai_profile_id: z.string().min(1),
  player_id: z.string().min(1),
  state_hash: z.string().min(1),
  legal_actions: z.array(LegalActionSchema),
  prompt_context: z.record(z.string(), z.unknown()),
  raw_output: z.string().min(1),
  parsed_output: z.record(z.string(), z.unknown()),
  validation_errors: z.array(AiValidationErrorSchema),
  memory_entry_ids: z.array(z.string().min(1)),
  retrieval_record_ids: z.array(z.string().min(1)),
  status: z.enum(["accepted", "rejected"]),
  created_at: z.coerce.string().min(1),
});

export const AiSelfDialogueRecordSchema = z.object({
  self_dialogue_id: z.string().min(1),
  game_id: z.string().min(1),
  ai_decision_id: z.string().min(1),
  ai_profile_id: z.string().min(1),
  sequence: z.number().int().positive(),
  role: z.string().min(1),
  content: z.string().min(1),
  created_at: z.coerce.string().min(1),
});

export const AiMemoryEntrySchema = z.object({
  memory_entry_id: z.string().min(1),
  game_id: z.string().min(1),
  ai_profile_id: z.string().min(1),
  player_id: z.string().min(1),
  kind: z.string().min(1),
  content: z.string().min(1),
  importance: z.number().min(0).max(1).nullable().optional(),
  created_at: z.coerce.string().min(1),
});

export const AiRetrievalRecordSchema = z.object({
  retrieval_record_id: z.string().min(1),
  game_id: z.string().min(1),
  ai_decision_id: z.string().min(1),
  ai_profile_id: z.string().min(1),
  memory_entry_id: z.string().min(1).nullable().optional(),
  source_type: z.string().min(1),
  source_id: z.string().min(1),
  score: z.number().min(0).max(1),
  content: z.string().min(1),
  created_at: z.coerce.string().min(1),
});

export const AiRejectedOutputSchema = z.object({
  rejected_output_id: z.string().min(1),
  game_id: z.string().min(1),
  ai_decision_id: z.string().min(1).nullable(),
  ai_profile_id: z.string().min(1),
  player_id: z.string().min(1),
  state_hash: z.string().min(1),
  raw_output: z.string().min(1),
  parsed_output: z.record(z.string(), z.unknown()).nullable(),
  validation_errors: z.array(AiValidationErrorSchema).min(1),
  created_at: z.coerce.string().min(1),
});

export const AiProfilesResponseSchema = z.object({
  profiles: z.array(AiProfileSchema),
});

export const AiDecisionsResponseSchema = z.object({
  decisions: z.array(AiDecisionSchema),
});

export const AiSelfDialogueResponseSchema = z.object({
  self_dialogue: z.array(AiSelfDialogueRecordSchema),
});

export const AiMemoryEntriesResponseSchema = z.object({
  memory_entries: z.array(AiMemoryEntrySchema),
});

export const AiRetrievalRecordsResponseSchema = z.object({
  retrieval_records: z.array(AiRetrievalRecordSchema),
});

export const AiRejectedOutputsResponseSchema = z.object({
  rejected_outputs: z.array(AiRejectedOutputSchema),
});

export type AiValidationError = z.infer<typeof AiValidationErrorSchema>;
export type AiProfile = z.infer<typeof AiProfileSchema>;
export type AiDecision = z.infer<typeof AiDecisionSchema>;
export type AiSelfDialogueRecord = z.infer<typeof AiSelfDialogueRecordSchema>;
export type AiMemoryEntry = z.infer<typeof AiMemoryEntrySchema>;
export type AiRetrievalRecord = z.infer<typeof AiRetrievalRecordSchema>;
export type AiRejectedOutput = z.infer<typeof AiRejectedOutputSchema>;

type ApiFetcher = (input: string, init: RequestInit) => Promise<Response>;

type GameApiOptions = {
  gameId: string;
  baseUrl?: string;
  fetcher?: ApiFetcher;
};

function getDefaultBackendBaseUrl(): string {
  return (
    process.env.NEXT_PUBLIC_API_BASE_URL ??
    process.env.INTERNAL_API_BASE_URL ??
    "http://127.0.0.1:8000"
  );
}

function backendBaseUrl(baseUrl = getDefaultBackendBaseUrl()): string {
  return baseUrl.replace(/\/+$/, "");
}

function gameUrl(baseUrl: string, gameId: string, resource: string): string {
  return `${backendBaseUrl(baseUrl)}/games/${encodeURIComponent(gameId)}${resource}`;
}

async function readJson(response: Response, action: string): Promise<unknown> {
  let payload: unknown = null;
  try {
    payload = await response.json();
  } catch {
    if (!response.ok) {
      throw new Error(`${action} returned HTTP ${response.status}`);
    }
  }

  if (!response.ok) {
    throw new Error(`${action} returned HTTP ${response.status}`);
  }
  return payload;
}

function parseOrThrow<T>(schema: z.ZodType<T>, payload: unknown, action: string): T {
  const parsed = schema.safeParse(payload);
  if (!parsed.success) {
    throw new Error(`Invalid ${action} response: ${parsed.error.message}`);
  }
  return parsed.data;
}

async function readAiAuditResource<T>({
  action,
  baseUrl,
  gameId,
  resource,
  schema,
  fetcher,
}: GameApiOptions & {
  action: string;
  resource: string;
  schema: z.ZodType<T>;
}): Promise<T> {
  const response = await (fetcher ?? fetch)(gameUrl(baseUrl ?? getDefaultBackendBaseUrl(), gameId, resource), {
    cache: "no-store",
    headers: { accept: "application/json" },
  });
  const payload = await readJson(response, action);
  return parseOrThrow(schema, payload, action);
}

export async function readAiProfiles(options: GameApiOptions): Promise<AiProfile[]> {
  return (
    await readAiAuditResource({
      ...options,
      action: "Load AI profiles",
      resource: "/ai/profiles",
      schema: AiProfilesResponseSchema,
    })
  ).profiles;
}

export async function readAiDecisions(options: GameApiOptions): Promise<AiDecision[]> {
  return (
    await readAiAuditResource({
      ...options,
      action: "Load AI decisions",
      resource: "/ai/decisions",
      schema: AiDecisionsResponseSchema,
    })
  ).decisions;
}

export async function readAiSelfDialogue(options: GameApiOptions): Promise<AiSelfDialogueRecord[]> {
  return (
    await readAiAuditResource({
      ...options,
      action: "Load AI self-dialogue",
      resource: "/ai/self-dialogue",
      schema: AiSelfDialogueResponseSchema,
    })
  ).self_dialogue;
}

export async function readAiMemoryEntries(options: GameApiOptions): Promise<AiMemoryEntry[]> {
  return (
    await readAiAuditResource({
      ...options,
      action: "Load AI memory",
      resource: "/ai/memory",
      schema: AiMemoryEntriesResponseSchema,
    })
  ).memory_entries;
}

export async function readAiRetrievalRecords(options: GameApiOptions): Promise<AiRetrievalRecord[]> {
  return (
    await readAiAuditResource({
      ...options,
      action: "Load AI retrieval records",
      resource: "/ai/retrieval-records",
      schema: AiRetrievalRecordsResponseSchema,
    })
  ).retrieval_records;
}

export async function readAiRejectedOutputs(options: GameApiOptions): Promise<AiRejectedOutput[]> {
  return (
    await readAiAuditResource({
      ...options,
      action: "Load rejected AI outputs",
      resource: "/ai/rejected-outputs",
      schema: AiRejectedOutputsResponseSchema,
    })
  ).rejected_outputs;
}
