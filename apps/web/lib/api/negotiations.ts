import { z } from "zod";

export const NegotiationStatusSchema = z.enum(["opened", "active", "countered", "accepted", "rejected", "expired", "executed"]);
export const DealStatusSchema = z.enum(["proposed", "accepted", "rejected", "expired"]);
export const DealTermKindSchema = z.enum([
  "cash_transfer",
  "property_transfer",
  "loan",
  "option",
  "rent_share",
  "risk_transfer",
]);

export const ValidationErrorSchema = z.object({
  code: z.string().min(1),
  message: z.string().min(1),
  field: z.string().nullable().optional(),
});

export const NegotiationSchema = z.object({
  id: z.string().min(1),
  game_id: z.string().min(1),
  opened_by_player_id: z.string().min(1),
  participant_player_ids: z.array(z.string().min(1)).min(1),
  topic: z.string().min(1),
  context: z.string(),
  status: NegotiationStatusSchema,
  round_number: z.number().int().nonnegative(),
  created_at: z.coerce.string().min(1),
  updated_at: z.coerce.string().min(1),
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

export const DealSchema = z.object({
  id: z.string().min(1),
  game_id: z.string().min(1),
  negotiation_id: z.string().min(1),
  proposer_player_id: z.string().min(1),
  participant_player_ids: z.array(z.string().min(1)).min(1),
  parent_deal_id: z.string().min(1).nullable(),
  version: z.number().int().positive(),
  status: DealStatusSchema,
  terms: z.array(DealTermSchema),
  validation_errors: z.array(ValidationErrorSchema).default([]),
  accepted_at: z.coerce.string().nullable().optional(),
  rejected_at: z.coerce.string().nullable().optional(),
  created_at: z.coerce.string().min(1),
  updated_at: z.coerce.string().min(1),
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

export const NegotiationMutationResponseSchema = z.discriminatedUnion("status", [
  z.object({
    status: z.literal("ok"),
    negotiation: NegotiationSchema,
  }),
  RejectedMutationResponseSchema,
]);

export const MessageMutationResponseSchema = z.discriminatedUnion("status", [
  z.object({
    status: z.literal("ok"),
    message: NegotiationMessageSchema,
  }),
  RejectedMutationResponseSchema,
]);

export const DealMutationResponseSchema = z.discriminatedUnion("status", [
  z.object({
    status: z.literal("ok"),
    deal: DealSchema,
  }),
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
    body: JSON.stringify(input),
  });
  const payload = await readJson(response, "Create negotiation", true);
  return parseOrThrow(NegotiationMutationResponseSchema, payload, "negotiation mutation");
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
  return parseOrThrow(DealMutationResponseSchema, payload, "deal mutation");
}

export async function acceptDeal({
  gameId,
  dealId,
  baseUrl = getDefaultBackendBaseUrl(),
  fetcher = fetch,
}: DealScopedOptions): Promise<DealMutationResponse> {
  const response = await fetcher(gameUrl(baseUrl, gameId, `/deals/${encodeURIComponent(dealId)}/accept`), {
    method: "POST",
    cache: "no-store",
    headers: { accept: "application/json" },
  });
  const payload = await readJson(response, "Accept deal", true);
  return parseOrThrow(DealMutationResponseSchema, payload, "deal acceptance");
}

export async function rejectDeal({
  gameId,
  dealId,
  baseUrl = getDefaultBackendBaseUrl(),
  fetcher = fetch,
}: DealScopedOptions): Promise<DealMutationResponse> {
  const response = await fetcher(gameUrl(baseUrl, gameId, `/deals/${encodeURIComponent(dealId)}/reject`), {
    method: "POST",
    cache: "no-store",
    headers: { accept: "application/json" },
  });
  const payload = await readJson(response, "Reject deal", true);
  return parseOrThrow(DealMutationResponseSchema, payload, "deal rejection");
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
  return parseOrThrow(NegotiationMutationResponseSchema, payload, "negotiation expiration");
}
