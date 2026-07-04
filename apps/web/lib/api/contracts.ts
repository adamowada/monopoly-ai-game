import { z } from "zod";

export const ContractStatusSchema = z.enum(["draft", "active", "fulfilled", "void", "cancelled", "breached"]);
export const ObligationStatusSchema = z.enum(["pending", "due", "scheduled", "settled", "cancelled", "failed"]);

export const ContractTermSchema = z
  .object({
    kind: z.string().min(1),
    summary: z.string().optional(),
  })
  .catchall(z.unknown());

export const ContractRecordSchema = z.object({
  id: z.string().min(1),
  game_id: z.string().min(1),
  deal_id: z.string().min(1).nullable(),
  source_agreement_id: z.string().min(1).nullable(),
  effective_event_id: z.string().min(1).nullable(),
  party_player_ids: z.array(z.string().min(1)).min(1),
  status: ContractStatusSchema,
  terms: z.array(ContractTermSchema),
  term_summary: z.string().min(1),
  created_at: z.coerce.string().min(1),
  effective_at: z.coerce.string().nullable(),
});

export const ObligationRecordSchema = z.object({
  id: z.string().min(1),
  game_id: z.string().min(1),
  contract_id: z.string().min(1),
  obligated_player_id: z.string().min(1),
  counterparty_player_id: z.string().min(1).nullable(),
  status: ObligationStatusSchema,
  due_turn: z.number().int().nonnegative().nullable(),
  due_condition: z.string().min(1).nullable(),
  amount: z.number().nonnegative().nullable(),
  asset_summary: z.string().min(1).nullable(),
  transfer_summary: z.string().min(1).nullable(),
  triggering_event_id: z.string().min(1).nullable(),
  settled_at: z.coerce.string().nullable(),
  created_at: z.coerce.string().min(1),
});

export const ContractsResponseSchema = z.object({
  contracts: z.array(ContractRecordSchema),
});

export const ObligationsResponseSchema = z.object({
  obligations: z.array(ObligationRecordSchema),
});

export type ContractRecord = z.infer<typeof ContractRecordSchema>;
export type ObligationRecord = z.infer<typeof ObligationRecordSchema>;

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

export async function readContracts({
  gameId,
  baseUrl = getDefaultBackendBaseUrl(),
  fetcher = fetch,
}: GameApiOptions): Promise<ContractRecord[]> {
  const response = await fetcher(gameUrl(baseUrl, gameId, "/contracts"), {
    cache: "no-store",
    headers: { accept: "application/json" },
  });
  const payload = await readJson(response, "Load contracts");
  return parseOrThrow(ContractsResponseSchema, payload, "contracts").contracts;
}

export async function readObligations({
  gameId,
  baseUrl = getDefaultBackendBaseUrl(),
  fetcher = fetch,
}: GameApiOptions): Promise<ObligationRecord[]> {
  const response = await fetcher(gameUrl(baseUrl, gameId, "/obligations"), {
    cache: "no-store",
    headers: { accept: "application/json" },
  });
  const payload = await readJson(response, "Load obligations");
  return parseOrThrow(ObligationsResponseSchema, payload, "obligations").obligations;
}
