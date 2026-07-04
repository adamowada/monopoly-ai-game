export const ID_PREFIXES = {
  game: "game",
  player: "player",
  space: "space",
  property: "property",
  card: "card",
  action: "action",
  event: "event",
  negotiation: "negotiation",
  deal: "deal",
  contract: "contract",
  obligation: "obligation",
  aiDecision: "ai-decision",
  memory: "memory",
} as const;

export type EntityIdKind = keyof typeof ID_PREFIXES;
export type EntityIdPrefix = (typeof ID_PREFIXES)[EntityIdKind];
export type EntityId<Prefix extends EntityIdPrefix = EntityIdPrefix> = `${Prefix}_${string}`;

export type GameId = EntityId<typeof ID_PREFIXES.game>;
export type PlayerId = EntityId<typeof ID_PREFIXES.player>;
export type SpaceId = EntityId<typeof ID_PREFIXES.space>;
export type PropertyId = EntityId<typeof ID_PREFIXES.property>;
export type CardId = EntityId<typeof ID_PREFIXES.card>;
export type ActionId = EntityId<typeof ID_PREFIXES.action>;
export type EventId = EntityId<typeof ID_PREFIXES.event>;
export type NegotiationId = EntityId<typeof ID_PREFIXES.negotiation>;
export type DealId = EntityId<typeof ID_PREFIXES.deal>;
export type ContractId = EntityId<typeof ID_PREFIXES.contract>;
export type ObligationId = EntityId<typeof ID_PREFIXES.obligation>;
export type AiDecisionId = EntityId<typeof ID_PREFIXES.aiDecision>;
export type MemoryId = EntityId<typeof ID_PREFIXES.memory>;
