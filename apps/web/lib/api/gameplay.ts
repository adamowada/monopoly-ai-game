import { z } from "zod";

export const GameStateResponseSchema = z.object({
  game_id: z.string().min(1),
  state: z.record(z.string(), z.unknown()),
  state_hash: z.string().min(1),
  event_sequence: z.number().int().nonnegative(),
});

export const LegalActionSchema = z.object({
  actor_id: z.string().min(1),
  type: z.string().min(1),
  payload: z.record(z.string(), z.unknown()),
  expected_state_hash: z.string().min(1),
  expected_event_sequence: z.number().int().nonnegative(),
  description: z.string().nullable().optional(),
  schema: z.record(z.string(), z.unknown()).optional().default({}),
});

export const LegalActionsResponseSchema = z.object({
  game_id: z.string().min(1),
  actor_player_id: z.string().min(1),
  legal_actions: z.array(LegalActionSchema),
  state_hash: z.string().min(1),
  event_sequence: z.number().int().nonnegative(),
});

export const AcceptedEventSchema = z.object({
  id: z.string().min(1),
  game_id: z.string().min(1),
  sequence: z.number().int().nonnegative(),
  actor_player_id: z.string().min(1).nullable(),
  event_type: z.string().min(1),
  payload: z.record(z.string(), z.unknown()),
  state_hash: z.string().min(1),
  created_at: z.coerce.string().min(1),
});

export const EventsResponseSchema = z.object({
  events: z.array(AcceptedEventSchema),
});

export const ActionAcceptedResponseSchema = z.object({
  status: z.literal("accepted"),
  game_id: z.string().min(1),
  accepted_events: z.array(AcceptedEventSchema),
  state: z.record(z.string(), z.unknown()),
  state_hash: z.string().min(1),
  event_sequence: z.number().int().nonnegative(),
});

export const ActionRejectedResponseSchema = z.object({
  status: z.literal("rejected"),
  rejected_action_id: z.string().min(1).optional(),
  reason_code: z.string().min(1),
  validation_errors: z.array(
    z.object({
      code: z.string().min(1),
      message: z.string().min(1),
      field: z.string().nullable().optional(),
    }),
  ),
  legal_action_context: z.record(z.string(), z.unknown()).nullable().optional(),
  submitted_action: z.unknown().optional(),
});

export const ActionSubmissionResultSchema = z.discriminatedUnion("status", [
  ActionAcceptedResponseSchema,
  ActionRejectedResponseSchema,
]);

export type GameStateResponse = z.infer<typeof GameStateResponseSchema>;
export type LegalAction = z.infer<typeof LegalActionSchema>;
export type LegalActionsResponse = z.infer<typeof LegalActionsResponseSchema>;
export type AcceptedEvent = z.infer<typeof AcceptedEventSchema>;
export type ActionAcceptedResponse = z.infer<typeof ActionAcceptedResponseSchema>;
export type ActionRejectedResponse = z.infer<typeof ActionRejectedResponseSchema>;
export type ActionSubmissionResult = z.infer<typeof ActionSubmissionResultSchema>;

type ApiFetcher = (input: string, init: RequestInit) => Promise<Response>;

type ReadGameStateOptions = {
  gameId: string;
  baseUrl?: string;
  fetcher?: ApiFetcher;
};

type ReadLegalActionsOptions = {
  gameId: string;
  actorPlayerId: string;
  baseUrl?: string;
  fetcher?: ApiFetcher;
};

type SubmitGameActionOptions = {
  gameId: string;
  action: LegalAction;
  idempotencyKey: string;
  baseUrl?: string;
  fetcher?: ApiFetcher;
};

type ReadEventsOptions = {
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

export function backendBaseUrl(baseUrl = getDefaultBackendBaseUrl()): string {
  return baseUrl.replace(/\/+$/, "");
}

function gameResourceUrl(baseUrl: string, gameId: string, resource: string): string {
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

  if (!response.ok && !(payload && typeof payload === "object" && "status" in payload)) {
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

export async function readGameState({
  gameId,
  baseUrl = getDefaultBackendBaseUrl(),
  fetcher = fetch,
}: ReadGameStateOptions): Promise<GameStateResponse> {
  const response = await fetcher(gameResourceUrl(baseUrl, gameId, "/state"), {
    cache: "no-store",
    headers: { accept: "application/json" },
  });
  const payload = await readJson(response, "Load game state");
  return parseOrThrow(GameStateResponseSchema, payload, "game state");
}

export async function readLegalActions({
  gameId,
  actorPlayerId,
  baseUrl = getDefaultBackendBaseUrl(),
  fetcher = fetch,
}: ReadLegalActionsOptions): Promise<LegalActionsResponse> {
  const query = `?actor_player_id=${encodeURIComponent(actorPlayerId)}`;
  const response = await fetcher(gameResourceUrl(baseUrl, gameId, `/legal-actions${query}`), {
    cache: "no-store",
    headers: { accept: "application/json" },
  });
  const payload = await readJson(response, "Load legal actions");
  return parseOrThrow(LegalActionsResponseSchema, payload, "legal actions");
}

export async function readEvents({
  gameId,
  baseUrl = getDefaultBackendBaseUrl(),
  fetcher = fetch,
}: ReadEventsOptions): Promise<AcceptedEvent[]> {
  const response = await fetcher(gameResourceUrl(baseUrl, gameId, "/events"), {
    cache: "no-store",
    headers: { accept: "application/json" },
  });
  const payload = await readJson(response, "Load events");
  return parseOrThrow(EventsResponseSchema, payload, "events").events;
}

export async function submitGameAction({
  gameId,
  action,
  idempotencyKey,
  baseUrl = getDefaultBackendBaseUrl(),
  fetcher = fetch,
}: SubmitGameActionOptions): Promise<ActionSubmissionResult> {
  const response = await fetcher(gameResourceUrl(baseUrl, gameId, "/actions"), {
    method: "POST",
    cache: "no-store",
    headers: {
      accept: "application/json",
      "content-type": "application/json",
      "Idempotency-Key": idempotencyKey,
    },
    body: JSON.stringify(action),
  });
  const payload = await readJson(response, "Submit action");
  return parseOrThrow(ActionSubmissionResultSchema, payload, "action submission");
}

export function eventsStreamUrl(gameId: string, baseUrl = getDefaultBackendBaseUrl()): string {
  return gameResourceUrl(baseUrl, gameId, "/events/stream");
}
