import { z } from "zod";

export const RejectedActionValidationErrorSchema = z.object({
  code: z.string().min(1),
  message: z.string().min(1),
  field: z.string().nullable().optional(),
});

export const RejectedActionRecordSchema = z.object({
  id: z.string().min(1),
  game_id: z.string().min(1),
  actor_player_id: z.string().min(1).nullable(),
  action_type: z.string().min(1),
  payload: z.record(z.string(), z.unknown()),
  reason_code: z.string().min(1),
  validation_errors: z.array(RejectedActionValidationErrorSchema),
  legal_action_context: z.record(z.string(), z.unknown()).nullable(),
  phase: z.string().min(1).nullable(),
  state_hash: z.string().min(1).nullable(),
  created_at: z.string().min(1),
});

export const RejectedActionsResponseSchema = z.object({
  rejected_actions: z.array(RejectedActionRecordSchema),
});

export const RejectedActionsSnapshotSchema = z.discriminatedUnion("state", [
  z.object({
    state: z.literal("loaded"),
    checkedAt: z.string().min(1),
    rejectedActions: z.array(RejectedActionRecordSchema),
  }),
  z.object({
    state: z.literal("error"),
    checkedAt: z.string().min(1),
    error: z.string().min(1),
  }),
]);

export type RejectedActionRecord = z.infer<typeof RejectedActionRecordSchema>;
export type RejectedActionsSnapshot = z.infer<typeof RejectedActionsSnapshotSchema>;

type RejectedActionsFetcher = (input: string, init: RequestInit) => Promise<Response>;

type ReadRejectedActionsOptions = {
  gameId: string;
  actorPlayerId?: string;
  baseUrl?: string;
  fetcher?: RejectedActionsFetcher;
  checkedAt?: () => string;
};

function getDefaultBackendBaseUrl(): string {
  return (
    process.env.INTERNAL_API_BASE_URL ??
    process.env.NEXT_PUBLIC_API_BASE_URL ??
    "http://127.0.0.1:8000"
  );
}

function toRejectedActionsUrl(baseUrl: string, gameId: string, actorPlayerId?: string): string {
  const query = actorPlayerId ? `?actor_player_id=${encodeURIComponent(actorPlayerId)}` : "";
  return `${baseUrl.replace(/\/+$/, "")}/games/${encodeURIComponent(gameId)}/rejected-actions${query}`;
}

function errorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return String(error);
}

export async function readRejectedActions({
  gameId,
  actorPlayerId,
  baseUrl = getDefaultBackendBaseUrl(),
  fetcher = fetch,
  checkedAt = () => new Date().toISOString(),
}: ReadRejectedActionsOptions): Promise<RejectedActionsSnapshot> {
  const timestamp = checkedAt();

  try {
    const response = await fetcher(toRejectedActionsUrl(baseUrl, gameId, actorPlayerId), {
      cache: "no-store",
      headers: { accept: "application/json" },
    });

    if (!response.ok) {
      throw new Error(`Rejected actions returned HTTP ${response.status}`);
    }

    const payload: unknown = await response.json();
    const parsed = RejectedActionsResponseSchema.safeParse(payload);
    if (!parsed.success) {
      throw new Error(`Invalid rejected actions response: ${parsed.error.message}`);
    }

    return {
      state: "loaded",
      checkedAt: timestamp,
      rejectedActions: parsed.data.rejected_actions,
    };
  } catch (error) {
    return {
      state: "error",
      checkedAt: timestamp,
      error: errorMessage(error),
    };
  }
}
