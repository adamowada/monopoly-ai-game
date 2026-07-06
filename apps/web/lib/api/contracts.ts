import { z } from "zod";

export const ContractStatusSchema = z.enum([
  "draft",
  "active",
  "closed",
  "fulfilled",
  "void",
  "cancelled",
  "breached",
  "defaulted",
]);
export const ObligationStatusSchema = z.enum([
  "pending",
  "due",
  "scheduled",
  "settled",
  "deferred",
  "rejected",
  "cancelled",
  "failed",
  "defaulted",
]);

export const ContractTermSchema = z
  .object({
    kind: z.string().min(1),
    summary: z.string().optional(),
  })
  .catchall(z.unknown());

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function stringValue(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function numberValue(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string" && item.length > 0) : [];
}

function termsArray(value: unknown): z.infer<typeof ContractTermSchema>[] {
  const rawTerms = Array.isArray(value) ? value : isRecord(value) && Array.isArray(value.terms) ? value.terms : [];
  return rawTerms
    .map((term) => ContractTermSchema.safeParse(term))
    .filter((parsed): parsed is { success: true; data: z.infer<typeof ContractTermSchema> } => parsed.success)
    .map((parsed) => parsed.data);
}

function termSummary(terms: z.infer<typeof ContractTermSchema>[]): string {
  return terms.map((term) => stringValue(term.summary) ?? term.kind).join("; ") || "Contract terms recorded.";
}

const RawContractRecordSchema = z.object({
  id: z.string().min(1),
  game_id: z.string().min(1),
  deal_id: z.string().min(1).nullable(),
  source_agreement_id: z.string().min(1).nullable().optional(),
  effective_event_id: z.string().min(1).nullable(),
  party_player_ids: z.array(z.string().min(1)).optional(),
  status: ContractStatusSchema,
  terms: z.unknown(),
  term_summary: z.string().min(1).optional(),
  created_at: z.coerce.string().min(1),
  effective_at: z.coerce.string().nullable().optional(),
  executed_at: z.coerce.string().nullable().optional(),
});

export const ContractRecordSchema = RawContractRecordSchema.transform((raw) => {
  const termsRecord = isRecord(raw.terms) ? raw.terms : {};
  const terms = termsArray(raw.terms);
  return {
    id: raw.id,
    game_id: raw.game_id,
    deal_id: raw.deal_id,
    source_agreement_id: raw.source_agreement_id ?? stringValue(termsRecord.source_negotiation_id) ?? raw.deal_id,
    effective_event_id: raw.effective_event_id,
    party_player_ids: raw.party_player_ids ?? stringArray(termsRecord.participants),
    status: raw.status,
    terms,
    term_summary: raw.term_summary ?? termSummary(terms),
    created_at: raw.created_at,
    effective_at: raw.effective_at ?? raw.executed_at ?? null,
  };
});

const RawObligationRecordSchema = z.object({
  id: z.string().min(1),
  game_id: z.string().min(1),
  contract_id: z.string().min(1),
  obligated_player_id: z.string().min(1).optional(),
  counterparty_player_id: z.string().min(1).nullable().optional(),
  owed_by_player_id: z.string().min(1).nullable().optional(),
  owed_to_player_id: z.string().min(1).nullable().optional(),
  settled_event_id: z.string().min(1).nullable().optional(),
  status: ObligationStatusSchema,
  obligation_type: z.string().min(1).optional(),
  schedule: z.unknown().optional(),
  terms: z.unknown().optional(),
  due_at: z.coerce.string().nullable().optional(),
  due_turn: z.number().int().nonnegative().nullable().optional(),
  due_condition: z.string().min(1).nullable().optional(),
  amount: z.number().nonnegative().nullable().optional(),
  asset_summary: z.string().min(1).nullable().optional(),
  transfer_summary: z.string().min(1).nullable().optional(),
  triggering_event_id: z.string().min(1).nullable().optional(),
  settled_at: z.coerce.string().nullable().optional(),
  created_at: z.coerce.string().min(1),
});

export const ObligationRecordSchema = RawObligationRecordSchema.transform((raw) => {
  const terms = isRecord(raw.terms) ? raw.terms : {};
  const schedule = isRecord(raw.schedule) ? raw.schedule : {};
  const trigger = isRecord(schedule.trigger) ? schedule.trigger : {};
  const triggerType = stringValue(trigger.type);
  const dueTurn = raw.due_turn ?? numberValue(trigger.due_turn) ?? numberValue(trigger.round);
  const amount = raw.amount ?? numberValue(terms.amount);
  const propertyId = stringValue(terms.property_id);
  const assetSummary =
    raw.asset_summary ??
    (propertyId ? `property ${propertyId}` : amount !== null ? `$${amount.toLocaleString("en-US")} transfer` : null);
  const dueCondition = raw.due_condition ?? (triggerType && triggerType !== "immediate" && dueTurn === null ? triggerType : null);

  return {
    id: raw.id,
    game_id: raw.game_id,
    contract_id: raw.contract_id,
    obligated_player_id: raw.obligated_player_id ?? raw.owed_by_player_id ?? "unassigned",
    counterparty_player_id: raw.counterparty_player_id ?? raw.owed_to_player_id ?? null,
    status: raw.status,
    due_turn: dueTurn,
    due_condition: dueCondition,
    amount,
    asset_summary: assetSummary,
    transfer_summary: raw.transfer_summary ?? assetSummary,
    triggering_event_id: raw.triggering_event_id ?? raw.settled_event_id ?? null,
    settled_at: raw.settled_at ?? null,
    created_at: raw.created_at,
  };
});

export const ContractsResponseSchema = z.object({
  contracts: z.array(ContractRecordSchema),
});

export const ObligationsResponseSchema = z.object({
  obligations: z.array(ObligationRecordSchema),
});

export const ContractOutcomeExplanationSchema = z.object({
  id: z.string().min(1),
  game_id: z.string().min(1),
  source_deal_id: z.string().min(1).nullable(),
  contract_id: z.string().min(1),
  obligation_id: z.string().min(1).nullable(),
  obligation_type: z.string().min(1),
  trigger: z.record(z.string(), z.unknown()),
  classic_rule_interaction: z.record(z.string(), z.unknown()),
  decision: z.record(z.string(), z.unknown()),
  resulting_state_effect: z.record(z.string(), z.unknown()),
  explanation_text: z.string().min(1),
});

export const ContractOutcomesResponseSchema = z.object({
  outcomes: z.array(ContractOutcomeExplanationSchema),
});

export const ContractSettlementResponseSchema = z.object({
  status: z.literal("ok"),
  game_id: z.string().min(1),
  settled_obligation_ids: z.array(z.string().min(1)),
  defaulted_obligation_ids: z.array(z.string().min(1)),
  accepted_events: z.array(z.record(z.string(), z.unknown())),
  state_hash: z.string().min(1),
  event_sequence: z.number().int().nonnegative(),
});

export const ContractLifecycleRejectedResponseSchema = z.object({
  status: z.literal("rejected"),
  reason_code: z.string().min(1),
  validation_errors: z.array(
    z.object({
      code: z.string().min(1),
      message: z.string().min(1),
      field: z.string().nullable().optional(),
    }),
  ),
});

export const ContractEnforcementResultSchema = z.discriminatedUnion("status", [
  ContractSettlementResponseSchema,
  ContractLifecycleRejectedResponseSchema,
]);

export type ContractRecord = z.infer<typeof ContractRecordSchema>;
export type ObligationRecord = z.infer<typeof ObligationRecordSchema>;
export type ContractOutcomeExplanation = z.infer<typeof ContractOutcomeExplanationSchema>;
export type ContractSettlementResponse = z.infer<typeof ContractSettlementResponseSchema>;
export type ContractLifecycleRejectedResponse = z.infer<typeof ContractLifecycleRejectedResponseSchema>;
export type ContractEnforcementResult = z.infer<typeof ContractEnforcementResultSchema>;

type ApiFetcher = (input: string, init: RequestInit) => Promise<Response>;

type GameApiOptions = {
  gameId: string;
  baseUrl?: string;
  fetcher?: ApiFetcher;
};

type EnforceContractsOptions = GameApiOptions & {
  triggerContext?: Record<string, unknown>;
};

type SettleContractOptions = GameApiOptions & {
  contractId: string;
  obligationId: string;
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

async function readJson(response: Response, action: string, allowRejected = false): Promise<unknown> {
  let payload: unknown = null;
  try {
    payload = await response.json();
  } catch {
    if (!response.ok) {
      throw new Error(`${action} returned HTTP ${response.status}`);
    }
  }

  if (!response.ok) {
    const isRejected = allowRejected && payload && typeof payload === "object" && "status" in payload;
    if (!isRejected) {
      throw new Error(`${action} returned HTTP ${response.status}`);
    }
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

export async function readContractOutcomes({
  gameId,
  baseUrl = getDefaultBackendBaseUrl(),
  fetcher = fetch,
}: GameApiOptions): Promise<ContractOutcomeExplanation[]> {
  const response = await fetcher(gameUrl(baseUrl, gameId, "/contracts/outcomes"), {
    cache: "no-store",
    headers: { accept: "application/json" },
  });
  const payload = await readJson(response, "Load contract outcomes");
  return parseOrThrow(ContractOutcomesResponseSchema, payload, "contract outcomes").outcomes;
}

export async function enforceContracts({
  gameId,
  triggerContext = {},
  baseUrl = getDefaultBackendBaseUrl(),
  fetcher = fetch,
}: EnforceContractsOptions): Promise<ContractEnforcementResult> {
  const response = await fetcher(gameUrl(baseUrl, gameId, "/contracts/enforce"), {
    method: "POST",
    cache: "no-store",
    headers: {
      accept: "application/json",
      "content-type": "application/json",
    },
    body: JSON.stringify({
      trigger_context: triggerContext,
    }),
  });
  const payload = await readJson(response, "Enforce contracts", true);
  return parseOrThrow(ContractEnforcementResultSchema, payload, "contract enforcement");
}

export async function settleContract({
  gameId,
  contractId,
  obligationId,
  baseUrl = getDefaultBackendBaseUrl(),
  fetcher = fetch,
}: SettleContractOptions): Promise<ContractEnforcementResult> {
  const response = await fetcher(gameUrl(baseUrl, gameId, `/contracts/${encodeURIComponent(contractId)}/settle`), {
    method: "POST",
    cache: "no-store",
    headers: {
      accept: "application/json",
      "content-type": "application/json",
    },
    body: JSON.stringify({
      obligation_id: obligationId,
    }),
  });
  const payload = await readJson(response, "Settle contract", true);
  return parseOrThrow(ContractEnforcementResultSchema, payload, "contract settlement");
}
