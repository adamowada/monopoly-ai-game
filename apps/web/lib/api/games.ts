import { z } from "zod";

export const GamePlayerSchema = z.object({
  id: z.string().min(1),
  game_id: z.string().min(1),
  seat_order: z.number().int().nonnegative(),
  name: z.string().min(1),
  controller_type: z.enum(["human", "ai"]),
  status: z.string().min(1),
  state: z.record(z.string(), z.unknown()),
  created_at: z.string().min(1),
  updated_at: z.string().min(1),
});

export const GameMetadataSchema = z.object({
  id: z.string().min(1),
  status: z.string().min(1),
  ruleset_version: z.string().min(1),
  seed: z.string().min(1).nullable(),
  current_phase: z.string().min(1).nullable(),
  settings: z.record(z.string(), z.unknown()),
  players: z.array(GamePlayerSchema),
  created_at: z.string().min(1),
  updated_at: z.string().min(1),
});

export const GameSnapshotSchema = z.discriminatedUnion("state", [
  z.object({
    state: z.literal("loaded"),
    game: GameMetadataSchema,
  }),
  z.object({
    state: z.literal("error"),
    error: z.string().min(1),
  }),
]);

export const CreateGamePlayerSchema = z.object({
  name: z.string().min(1),
  kind: z.enum(["human", "ai"]),
});

export type GameMetadata = z.infer<typeof GameMetadataSchema>;
export type GameSnapshot = z.infer<typeof GameSnapshotSchema>;
export type CreateGamePlayer = z.infer<typeof CreateGamePlayerSchema>;

type GameFetcher = (input: string, init: RequestInit) => Promise<Response>;

type CreateGameOptions = {
  players: CreateGamePlayer[];
  seed?: string;
  settings?: Record<string, unknown>;
  baseUrl?: string;
  fetcher?: GameFetcher;
};

type ReadGameOptions = {
  gameId: string;
  baseUrl?: string;
  fetcher?: GameFetcher;
};

function getDefaultBackendBaseUrl(): string {
  return (
    process.env.INTERNAL_API_BASE_URL ??
    process.env.NEXT_PUBLIC_API_BASE_URL ??
    "http://127.0.0.1:8000"
  );
}

function gamesUrl(baseUrl: string): string {
  return `${baseUrl.replace(/\/+$/, "")}/games`;
}

function gameUrl(baseUrl: string, gameId: string): string {
  return `${gamesUrl(baseUrl)}/${encodeURIComponent(gameId)}`;
}

function errorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return String(error);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function describeValidationDetail(detail: unknown): string | null {
  if (typeof detail === "string" && detail.trim()) {
    return detail;
  }
  if (!Array.isArray(detail)) {
    return null;
  }
  const messages = detail
    .map((item) => {
      if (isRecord(item)) {
        const message = item.msg ?? item.message;
        if (typeof message === "string" && message.trim()) {
          return message;
        }
      }
      return null;
    })
    .filter((message): message is string => message !== null);
  return messages.length > 0 ? messages.join("; ") : null;
}

async function responseErrorMessage(response: Response, action: string): Promise<string> {
  const fallback = `${action} returned HTTP ${response.status}`;
  try {
    const payload: unknown = await response.json();
    if (!isRecord(payload)) {
      return fallback;
    }
    const directMessage = payload.error ?? payload.message;
    if (typeof directMessage === "string" && directMessage.trim()) {
      return directMessage;
    }
    const validationMessage = describeValidationDetail(payload.detail);
    return validationMessage ? `${fallback}: ${validationMessage}` : fallback;
  } catch {
    return fallback;
  }
}

export async function createGame({
  players,
  seed,
  settings,
  baseUrl = getDefaultBackendBaseUrl(),
  fetcher = fetch,
}: CreateGameOptions): Promise<GameSnapshot> {
  try {
    const response = await fetcher(gamesUrl(baseUrl), {
      method: "POST",
      cache: "no-store",
      headers: {
        accept: "application/json",
        "content-type": "application/json",
      },
      body: JSON.stringify({
        ...(seed ? { seed } : {}),
        players,
        ...(settings ? { settings } : {}),
      }),
    });

    if (!response.ok) {
      throw new Error(await responseErrorMessage(response, "Create game"));
    }

    const payload: unknown = await response.json();
    return { state: "loaded", game: GameMetadataSchema.parse(payload) };
  } catch (error) {
    return { state: "error", error: errorMessage(error) };
  }
}

export async function readGame({
  gameId,
  baseUrl = getDefaultBackendBaseUrl(),
  fetcher = fetch,
}: ReadGameOptions): Promise<GameSnapshot> {
  try {
    const response = await fetcher(gameUrl(baseUrl, gameId), {
      cache: "no-store",
      headers: { accept: "application/json" },
    });

    if (!response.ok) {
      throw new Error(await responseErrorMessage(response, "Load game"));
    }

    const payload: unknown = await response.json();
    return { state: "loaded", game: GameMetadataSchema.parse(payload) };
  } catch (error) {
    return { state: "error", error: errorMessage(error) };
  }
}
