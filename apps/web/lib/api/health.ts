import { z } from "zod";

export const BackendHealthSchema = z.object({
  status: z.literal("ok"),
  service: z.literal("api"),
  stage: z.string().min(1),
  environment: z.string().min(1),
  database: z.string().min(1),
});

export const HealthSnapshotSchema = z.discriminatedUnion("state", [
  z.object({
    state: z.literal("online"),
    checkedAt: z.string().min(1),
    health: BackendHealthSchema,
  }),
  z.object({
    state: z.literal("offline"),
    checkedAt: z.string().min(1),
    error: z.string().min(1),
  }),
]);

export type BackendHealth = z.infer<typeof BackendHealthSchema>;
export type HealthSnapshot = z.infer<typeof HealthSnapshotSchema>;

type HealthFetcher = (input: string, init: RequestInit) => Promise<Response>;

type ReadBackendHealthOptions = {
  baseUrl?: string;
  fetcher?: HealthFetcher;
  checkedAt?: () => string;
};

function getDefaultBackendBaseUrl(): string {
  return (
    process.env.INTERNAL_API_BASE_URL ??
    process.env.NEXT_PUBLIC_API_BASE_URL ??
    "http://127.0.0.1:8000"
  );
}

function toHealthUrl(baseUrl: string): string {
  return `${baseUrl.replace(/\/+$/, "")}/health`;
}

function errorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return String(error);
}

export async function readBackendHealth({
  baseUrl = getDefaultBackendBaseUrl(),
  fetcher = fetch,
  checkedAt = () => new Date().toISOString(),
}: ReadBackendHealthOptions = {}): Promise<HealthSnapshot> {
  const timestamp = checkedAt();

  try {
    const response = await fetcher(toHealthUrl(baseUrl), {
      cache: "no-store",
      headers: { accept: "application/json" },
    });

    if (!response.ok) {
      throw new Error(`Backend health returned HTTP ${response.status}`);
    }

    const payload: unknown = await response.json();
    const parsed = BackendHealthSchema.safeParse(payload);
    if (!parsed.success) {
      throw new Error(`Invalid backend health response: ${parsed.error.message}`);
    }

    return {
      state: "online",
      checkedAt: timestamp,
      health: parsed.data,
    };
  } catch (error) {
    return {
      state: "offline",
      checkedAt: timestamp,
      error: errorMessage(error),
    };
  }
}
