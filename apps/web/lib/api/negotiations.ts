import { z } from "zod";

export const NegotiationStatusSchema = z.enum(["opened", "active", "countered", "accepted", "rejected", "expired", "executed"]);
export const DealStatusSchema = z.enum(["proposed", "accepted", "rejected", "expired"]);
export const DealTermKindSchema = z.enum([
  "immediate_cash_transfer",
  "immediate_property_transfer",
  "deferred_cash_payment",
  "installment_loan",
  "interest_bearing_debt",
  "collateralized_loan",
  "property_purchase_option",
  "rent_share",
  "insurance_payout",
  "conditional_obligation",
]);

export const ValidationErrorSchema = z.object({
  code: z.string().min(1),
  message: z.string().min(1),
  field: z.string().nullable().optional(),
});

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function stringValue(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string" && item.length > 0) : [];
}

function contextText(value: unknown): string {
  if (typeof value === "string") {
    return value;
  }
  if (!isRecord(value)) {
    return "";
  }
  return stringValue(value.body) ?? stringValue(value.description) ?? stringValue(value.context) ?? JSON.stringify(value);
}

const RawNegotiationSchema = z.object({
  id: z.string().min(1),
  game_id: z.string().min(1),
  opened_by_player_id: z.string().min(1).nullable().optional(),
  participant_player_ids: z.array(z.string().min(1)).optional(),
  topic: z.string().min(1).optional(),
  context: z.unknown().optional(),
  status: NegotiationStatusSchema,
  round_number: z.number().int().nonnegative(),
  pending_deal_id: z.string().min(1).nullable().optional(),
  current_deal_id: z.string().min(1).nullable().optional(),
  acceptances: z.record(z.string(), z.array(z.string())).optional(),
  invalidated_acceptances: z.record(z.string(), z.array(z.string())).optional(),
  created_at: z.coerce.string().min(1),
  updated_at: z.coerce.string().min(1),
});

export const NegotiationSchema = RawNegotiationSchema.transform((raw) => {
  const rawContext = isRecord(raw.context) ? raw.context : {};
  const nestedContext = isRecord(rawContext.context) ? rawContext.context : rawContext;
  const participantPlayerIds = raw.participant_player_ids ?? stringArray(rawContext.participant_player_ids);
  const topic =
    raw.topic ??
    stringValue(nestedContext.topic) ??
    stringValue(nestedContext.subject) ??
    `Negotiation ${raw.id}`;

  return {
    id: raw.id,
    game_id: raw.game_id,
    opened_by_player_id: raw.opened_by_player_id ?? "",
    participant_player_ids: participantPlayerIds,
    topic,
    context: contextText(nestedContext),
    status: raw.status,
    round_number: raw.round_number,
    pending_deal_id: raw.pending_deal_id ?? null,
    current_deal_id: raw.current_deal_id ?? null,
    acceptances: raw.acceptances ?? {},
    invalidated_acceptances: raw.invalidated_acceptances ?? {},
    created_at: raw.created_at,
    updated_at: raw.updated_at,
  };
});

export const NegotiationMessageSchema = z.object({
  id: z.string().min(1),
  game_id: z.string().min(1),
  negotiation_id: z.string().min(1),
  author_player_id: z.string().min(1),
  body: z.string().min(1),
  created_at: z.coerce.string().min(1),
});

export const DealTermSchema = z
  .object({
    kind: DealTermKindSchema,
    summary: z.string().optional(),
  })
  .catchall(z.unknown());

const RawDealSchema = z.object({
  id: z.string().min(1),
  game_id: z.string().min(1),
  negotiation_id: z.string().min(1).nullable().optional(),
  proposed_by_player_id: z.string().min(1).nullable().optional(),
  proposer_player_id: z.string().min(1).nullable().optional(),
  participant_player_ids: z.array(z.string().min(1)).optional(),
  parent_deal_id: z.string().min(1).nullable(),
  version: z.number().int().positive().optional(),
  deal_version: z.number().int().positive().optional(),
  status: DealStatusSchema,
  terms: z.unknown(),
  validation_errors: z.array(ValidationErrorSchema).nullable().optional(),
  accepted_at: z.coerce.string().nullable().optional(),
  rejected_at: z.coerce.string().nullable().optional(),
  created_at: z.coerce.string().min(1),
  updated_at: z.coerce.string().min(1),
});

export const DealSchema = RawDealSchema.transform((raw) => {
  const termsRecord = isRecord(raw.terms) ? raw.terms : {};
  const rawTerms = Array.isArray(raw.terms) ? raw.terms : Array.isArray(termsRecord.terms) ? termsRecord.terms : [];
  const terms = rawTerms
    .map((term) => DealTermSchema.safeParse(term))
    .filter((parsed): parsed is { success: true; data: DealTerm } => parsed.success)
    .map((parsed) => parsed.data);
  const participantPlayerIds = raw.participant_player_ids ?? stringArray(termsRecord.participants);

  return {
    id: raw.id,
    game_id: raw.game_id,
    negotiation_id: raw.negotiation_id ?? "",
    proposer_player_id: raw.proposer_player_id ?? raw.proposed_by_player_id ?? "",
    participant_player_ids: participantPlayerIds,
    parent_deal_id: raw.parent_deal_id,
    version: raw.version ?? raw.deal_version ?? 1,
    status: raw.status,
    terms,
    validation_errors: raw.validation_errors ?? [],
    accepted_at: raw.accepted_at ?? null,
    rejected_at: raw.rejected_at ?? null,
    created_at: raw.created_at,
    updated_at: raw.updated_at,
  };
});

export const NegotiationsResponseSchema = z.object({
  negotiations: z.array(NegotiationSchema),
});

export const NegotiationMessagesResponseSchema = z.object({
  messages: z.array(NegotiationMessageSchema),
});

export const DealsResponseSchema = z.object({
  deals: z.array(DealSchema),
});

export const RejectedMutationResponseSchema = z.object({
  status: z.literal("rejected"),
  reason_code: z.string().min(1),
  validation_errors: z.array(ValidationErrorSchema),
});

const WrappedNegotiationMutationResponseSchema = z.object({
  status: z.literal("ok"),
  negotiation: NegotiationSchema,
});

export const NegotiationMutationResponseSchema = z.union([
  WrappedNegotiationMutationResponseSchema,
  RejectedMutationResponseSchema,
]);

export const MessageMutationResponseSchema = z.discriminatedUnion("status", [
  z.object({
    status: z.literal("ok"),
    message: NegotiationMessageSchema,
  }),
  RejectedMutationResponseSchema,
]);

const WrappedDealMutationResponseSchema = z.object({
  status: z.literal("ok"),
  deal: DealSchema,
});

export const DealMutationResponseSchema = z.union([
  WrappedDealMutationResponseSchema,
  RejectedMutationResponseSchema,
]);

export type Negotiation = z.infer<typeof NegotiationSchema>;
export type NegotiationMessage = z.infer<typeof NegotiationMessageSchema>;
export type Deal = z.infer<typeof DealSchema>;
export type DealTerm = z.infer<typeof DealTermSchema>;
export type DealTermKind = z.infer<typeof DealTermKindSchema>;
export type ValidationError = z.infer<typeof ValidationErrorSchema>;
export type NegotiationMutationResponse = z.infer<typeof NegotiationMutationResponseSchema>;
export type MessageMutationResponse = z.infer<typeof MessageMutationResponseSchema>;
export type DealMutationResponse = z.infer<typeof DealMutationResponseSchema>;

type ApiFetcher = (input: string, init: RequestInit) => Promise<Response>;

type GameApiOptions = {
  gameId: string;
  baseUrl?: string;
  fetcher?: ApiFetcher;
};

export type CreateNegotiationInput = {
  opened_by_player_id: string;
  participant_player_ids: string[];
  topic: string;
  context: string;
};

export type CreateNegotiationMessageInput = {
  negotiationId: string;
  author_player_id: string;
  body: string;
};

export type CreateDealInput = {
  negotiation_id: string;
  proposer_player_id: string;
  participant_player_ids: string[];
  parent_deal_id: string | null;
  terms: DealTerm[];
};

type NegotiationScopedOptions = GameApiOptions & {
  negotiationId: string;
  viewerPlayerId?: string | null;
};

type DealScopedOptions = GameApiOptions & {
  dealId: string;
  playerId?: string | null;
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

function parseNegotiationMutation(payload: unknown): NegotiationMutationResponse {
  const wrapped = WrappedNegotiationMutationResponseSchema.safeParse(payload);
  if (wrapped.success) {
    return wrapped.data;
  }
  const rejected = RejectedMutationResponseSchema.safeParse(payload);
  if (rejected.success) {
    return rejected.data;
  }
  const negotiation = NegotiationSchema.safeParse(payload);
  if (negotiation.success) {
    return { status: "ok", negotiation: negotiation.data };
  }
  throw new Error(`Invalid negotiation mutation response: ${wrapped.error.message}`);
}

function parseDealMutation(payload: unknown): DealMutationResponse {
  const wrapped = WrappedDealMutationResponseSchema.safeParse(payload);
  if (wrapped.success) {
    return wrapped.data;
  }
  const rejected = RejectedMutationResponseSchema.safeParse(payload);
  if (rejected.success) {
    return rejected.data;
  }
  const deal = DealSchema.safeParse(payload);
  if (deal.success) {
    return { status: "ok", deal: deal.data };
  }
  throw new Error(`Invalid deal mutation response: ${wrapped.error.message}`);
}

export async function readNegotiations({
  gameId,
  baseUrl = getDefaultBackendBaseUrl(),
  fetcher = fetch,
}: GameApiOptions): Promise<Negotiation[]> {
  const response = await fetcher(gameUrl(baseUrl, gameId, "/negotiations"), {
    cache: "no-store",
    headers: { accept: "application/json" },
  });
  const payload = await readJson(response, "Load negotiations");
  return parseOrThrow(NegotiationsResponseSchema, payload, "negotiations").negotiations;
}

export async function createNegotiation({
  gameId,
  baseUrl = getDefaultBackendBaseUrl(),
  fetcher = fetch,
  input,
}: GameApiOptions & { input: CreateNegotiationInput }): Promise<NegotiationMutationResponse> {
  const response = await fetcher(gameUrl(baseUrl, gameId, "/negotiations"), {
    method: "POST",
    cache: "no-store",
    headers: {
      accept: "application/json",
      "content-type": "application/json",
    },
    body: JSON.stringify({
      opened_by_player_id: input.opened_by_player_id,
      participant_player_ids: input.participant_player_ids,
      context: {
        topic: input.topic,
        body: input.context,
      },
    }),
  });
  const payload = await readJson(response, "Create negotiation", true);
  return parseNegotiationMutation(payload);
}

export async function readNegotiationMessages({
  gameId,
  negotiationId,
  viewerPlayerId = null,
  baseUrl = getDefaultBackendBaseUrl(),
  fetcher = fetch,
}: NegotiationScopedOptions): Promise<NegotiationMessage[]> {
  const query = viewerPlayerId ? `?viewer_player_id=${encodeURIComponent(viewerPlayerId)}` : "";
  const response = await fetcher(
    gameUrl(baseUrl, gameId, `/negotiations/${encodeURIComponent(negotiationId)}/messages${query}`),
    {
      cache: "no-store",
      headers: { accept: "application/json" },
    },
  );
  const payload = await readJson(response, "Load negotiation messages");
  return parseOrThrow(NegotiationMessagesResponseSchema, payload, "negotiation messages").messages;
}

export async function createNegotiationMessage({
  gameId,
  baseUrl = getDefaultBackendBaseUrl(),
  fetcher = fetch,
  input,
}: GameApiOptions & { input: CreateNegotiationMessageInput }): Promise<MessageMutationResponse> {
  const response = await fetcher(
    gameUrl(baseUrl, gameId, `/negotiations/${encodeURIComponent(input.negotiationId)}/messages`),
    {
      method: "POST",
      cache: "no-store",
      headers: {
        accept: "application/json",
        "content-type": "application/json",
      },
      body: JSON.stringify({
        author_player_id: input.author_player_id,
        body: input.body,
      }),
    },
  );
  const payload = await readJson(response, "Create negotiation message", true);
  return parseOrThrow(MessageMutationResponseSchema, payload, "message mutation");
}

export async function readDeals({
  gameId,
  baseUrl = getDefaultBackendBaseUrl(),
  fetcher = fetch,
}: GameApiOptions): Promise<Deal[]> {
  const response = await fetcher(gameUrl(baseUrl, gameId, "/deals"), {
    cache: "no-store",
    headers: { accept: "application/json" },
  });
  const payload = await readJson(response, "Load deals");
  return parseOrThrow(DealsResponseSchema, payload, "deals").deals;
}

export async function createDeal({
  gameId,
  baseUrl = getDefaultBackendBaseUrl(),
  fetcher = fetch,
  input,
}: GameApiOptions & { input: CreateDealInput }): Promise<DealMutationResponse> {
  const response = await fetcher(gameUrl(baseUrl, gameId, "/deals"), {
    method: "POST",
    cache: "no-store",
    headers: {
      accept: "application/json",
      "content-type": "application/json",
    },
    body: JSON.stringify(input),
  });
  const payload = await readJson(response, "Create deal", true);
  return parseDealMutation(payload);
}

export async function acceptDeal({
  gameId,
  dealId,
  playerId = null,
  baseUrl = getDefaultBackendBaseUrl(),
  fetcher = fetch,
}: DealScopedOptions): Promise<DealMutationResponse> {
  const body = playerId ? JSON.stringify({ player_id: playerId }) : undefined;
  const response = await fetcher(gameUrl(baseUrl, gameId, `/deals/${encodeURIComponent(dealId)}/accept`), {
    method: "POST",
    cache: "no-store",
    headers: {
      accept: "application/json",
      ...(body ? { "content-type": "application/json" } : {}),
    },
    ...(body ? { body } : {}),
  });
  const payload = await readJson(response, "Accept deal", true);
  return parseDealMutation(payload);
}

export async function rejectDeal({
  gameId,
  dealId,
  playerId = null,
  baseUrl = getDefaultBackendBaseUrl(),
  fetcher = fetch,
}: DealScopedOptions): Promise<DealMutationResponse> {
  const body = playerId ? JSON.stringify({ player_id: playerId }) : undefined;
  const response = await fetcher(gameUrl(baseUrl, gameId, `/deals/${encodeURIComponent(dealId)}/reject`), {
    method: "POST",
    cache: "no-store",
    headers: {
      accept: "application/json",
      ...(body ? { "content-type": "application/json" } : {}),
    },
    ...(body ? { body } : {}),
  });
  const payload = await readJson(response, "Reject deal", true);
  return parseDealMutation(payload);
}

export async function expireNegotiation({
  gameId,
  negotiationId,
  baseUrl = getDefaultBackendBaseUrl(),
  fetcher = fetch,
}: NegotiationScopedOptions): Promise<NegotiationMutationResponse> {
  const response = await fetcher(gameUrl(baseUrl, gameId, `/negotiations/${encodeURIComponent(negotiationId)}/expire`), {
    method: "POST",
    cache: "no-store",
    headers: { accept: "application/json" },
  });
  const payload = await readJson(response, "Expire negotiation", true);
  return parseNegotiationMutation(payload);
}

export async function executeNegotiation({
  gameId,
  negotiationId,
  baseUrl = getDefaultBackendBaseUrl(),
  fetcher = fetch,
}: NegotiationScopedOptions): Promise<NegotiationMutationResponse> {
  const response = await fetcher(gameUrl(baseUrl, gameId, `/negotiations/${encodeURIComponent(negotiationId)}/execute`), {
    method: "POST",
    cache: "no-store",
    headers: { accept: "application/json" },
  });
  const payload = await readJson(response, "Execute negotiation", true);
  return parseNegotiationMutation(payload);
}
