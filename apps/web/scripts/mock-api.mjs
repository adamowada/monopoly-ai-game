import { createServer } from "node:http";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const port = Number.parseInt(process.env.MOCK_API_PORT ?? "18101", 10);
const games = new Map();
let gameCounter = 0;
const scriptDir = dirname(fileURLToPath(import.meta.url));
const classicData = JSON.parse(readFileSync(resolve(scriptDir, "../../../content/rules/classic_monopoly.json"), "utf8"));
const propertyData = classicData.properties;
const auctionFallbackPropertyId = "property_mediterranean_avenue";
const aiStepPathSuffix = "/ai/step";
const activeNegotiationStatuses = new Set(["opened", "active", "countered"]);

const corsHeaders = {
  "access-control-allow-headers": "accept, content-type, Idempotency-Key",
  "access-control-allow-methods": "GET, POST, OPTIONS",
  "access-control-allow-origin": "*",
  vary: "origin",
};

function json(response, statusCode, payload) {
  response.writeHead(statusCode, { ...corsHeaders, "content-type": "application/json" });
  response.end(JSON.stringify(payload));
}

function healthResponse() {
  return {
    status: "ok",
    service: "api",
    stage: "phase-1-stage-1.3",
    environment: "test",
    database: "configured",
  };
}

function nowIso() {
  return new Date().toISOString();
}

function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function isActiveNegotiationStatus(status) {
  return activeNegotiationStatuses.has(status);
}

function isHexColor(value) {
  return typeof value === "string" && /^#[0-9a-fA-F]{6}$/.test(value);
}

function readBody(request) {
  return new Promise((resolve, reject) => {
    let body = "";
    request.setEncoding("utf8");
    request.on("data", (chunk) => {
      body += chunk;
    });
    request.on("end", () => {
      try {
        resolve(body ? JSON.parse(body) : {});
      } catch (error) {
        reject(error);
      }
    });
    request.on("error", reject);
  });
}

function validationError(message, field) {
  return { msg: message, loc: field ? ["body", field] : ["body"] };
}

function validateBoardPosition(value) {
  return Number.isInteger(value) && value >= 0 && value <= 39;
}

function validateCreateGamePayload(payload) {
  const errors = [];
  if (!isObject(payload)) {
    return [validationError("request body must be an object")];
  }

  const players = payload.players;
  if (!Array.isArray(players) || players.length < 2 || players.length > 5) {
    errors.push(validationError("game setup requires 2 to 5 players", "players"));
  } else {
    const names = [];
    for (const [index, player] of players.entries()) {
      if (!isObject(player)) {
        errors.push(validationError("player setup must be an object", `players.${index}`));
        continue;
      }
      if (typeof player.name !== "string" || player.name.trim().length === 0) {
        errors.push(validationError("player names are required", `players.${index}.name`));
      } else {
        names.push(player.name.trim().toLowerCase());
      }
      if (player.kind !== "human" && player.kind !== "ai") {
        errors.push(validationError("player kind must be human or ai", `players.${index}.kind`));
      }
    }
    if (new Set(names).size !== names.length) {
      errors.push(validationError("player names must be unique", "players"));
    }
  }

  if (payload.seed === "server-reject") {
    errors.push(validationError("Server rejected setup: seed is reserved for validation tests", "seed"));
  }

  const settings = isObject(payload.settings) ? payload.settings : {};
  const playerColors = settings.player_colors;
  if (!Array.isArray(playerColors) || playerColors.length !== players?.length) {
    errors.push(validationError("settings.player_colors must include one color per player", "settings.player_colors"));
  } else {
    const seenSeats = new Set();
    const seenColors = new Set();
    for (const [index, entry] of playerColors.entries()) {
      if (!isObject(entry)) {
        errors.push(validationError("player color entries must be objects", `settings.player_colors.${index}`));
        continue;
      }
      if (entry.seat_order !== index) {
        errors.push(validationError("player color seat order must match player order", `settings.player_colors.${index}.seat_order`));
      }
      if (!isHexColor(entry.color)) {
        errors.push(validationError("player colors must be valid hex colors", `settings.player_colors.${index}.color`));
      } else {
        seenColors.add(entry.color.toLowerCase());
      }
      seenSeats.add(entry.seat_order);
    }
    if (seenSeats.size !== playerColors.length) {
      errors.push(validationError("player color seat orders must be unique", "settings.player_colors"));
    }
    if (seenColors.size !== playerColors.length) {
      errors.push(validationError("player colors must be unique", "settings.player_colors"));
    }
  }

  const cutoffs = settings.negotiation_cutoffs;
  if (!isObject(cutoffs)) {
    errors.push(validationError("settings.negotiation_cutoffs is required", "settings.negotiation_cutoffs"));
  } else {
    if (!Number.isInteger(cutoffs.max_rounds) || cutoffs.max_rounds < 1 || cutoffs.max_rounds > 20) {
      errors.push(validationError("max negotiation rounds must be between 1 and 20", "settings.negotiation_cutoffs.max_rounds"));
    }
    if (
      !Number.isInteger(cutoffs.max_proposals_per_player) ||
      cutoffs.max_proposals_per_player < 1 ||
      cutoffs.max_proposals_per_player > 50
    ) {
      errors.push(
        validationError(
          "proposal limit per player must be between 1 and 50",
          "settings.negotiation_cutoffs.max_proposals_per_player",
        ),
      );
    }
  }

  return errors;
}

function createGame(payload) {
  const id = `mock-game-${++gameCounter}`;
  const createdAt = nowIso();
  const seed = payload.seed ?? `mock-seed-${gameCounter}`;
  const game = {
    id,
    status: "active",
    ruleset_version: "classic-v1",
    seed,
    current_phase: "START_TURN",
    settings: payload.settings ?? {},
    event_sequence: 0,
    events: [],
    rejected_actions: [],
    negotiations: [],
    negotiation_messages: {},
    deals: [],
    contracts: [],
    obligations: [],
    contract_outcomes: [],
    ai_profiles: [],
    ai_decisions: [],
    ai_self_dialogue: [],
    ai_memory_entries: [],
    ai_retrieval_records: [],
    ai_rejected_outputs: [],
    negotiation_counter: 0,
    message_counter: 0,
    deal_counter: 0,
    property_ownership: createDefaultPropertyOwnership(),
    bank_inventory: { houses: 32, hotels: 12 },
    active_auction: null,
    stream_clients: new Set(),
    created_at: createdAt,
    updated_at: createdAt,
    players: payload.players.map((player, index) => ({
      id: `${id}-player-${index + 1}`,
      game_id: id,
      seat_order: index,
      name: player.name.trim(),
      controller_type: player.kind,
      status: "active",
      state: {
        cash: 1500,
        position: 0,
      },
      created_at: createdAt,
      updated_at: createdAt,
    })),
  };
  configurePropertyManagementSeed(game);
  configureNegotiationSeed(game);
  configureContractsLogSeed(game);
  configureAiAuditSeed(game);
  games.set(id, game);
  return game;
}

function createDefaultPropertyOwnership() {
  return propertyData.map((property) => ({
    property_id: property.id,
    owner_id: null,
    mortgaged: false,
    houses: 0,
    hotel: false,
    hotels: 0,
  }));
}

function isPropertyManagementSeed(seed) {
  return typeof seed === "string" && seed.startsWith("stage-5-property-management");
}

function propertyOwnership(game, propertyId) {
  return game.property_ownership.find((ownership) => ownership.property_id === propertyId) ?? null;
}

function propertyById(propertyId) {
  return propertyData.find((property) => property.id === propertyId) ?? null;
}

function propertyAtPosition(position) {
  return propertyData.find((property) => property.board_position === position) ?? null;
}

function purchasablePropertyId(game) {
  const actor = activePlayer(game);
  if (!actor) {
    return null;
  }
  const property = propertyAtPosition(actor.state.position) ?? propertyById(auctionFallbackPropertyId);
  if (!property) {
    return null;
  }
  const ownership = propertyOwnership(game, property.id);
  return ownership?.owner_id === null ? property.id : null;
}

function setPropertyOwnership(game, propertyId, patch) {
  const ownership = propertyOwnership(game, propertyId);
  if (!ownership) {
    return;
  }
  Object.assign(ownership, patch);
  if (ownership.hotel) {
    ownership.hotels = 1;
    ownership.houses = 0;
  } else {
    ownership.hotels = 0;
  }
}

function configurePropertyManagementSeed(game) {
  if (!isPropertyManagementSeed(game.seed)) {
    return;
  }
  const ada = game.players[0]?.id ?? null;
  const grace = game.players[1]?.id ?? null;
  game.current_phase = "PRE_ROLL_MANAGEMENT";
  setPropertyOwnership(game, "property_mediterranean_avenue", {
    owner_id: ada,
    mortgaged: false,
    houses: 0,
    hotel: false,
    hotels: 0,
  });
  setPropertyOwnership(game, "property_baltic_avenue", {
    owner_id: ada,
    mortgaged: false,
    houses: 0,
    hotel: false,
    hotels: 0,
  });
  setPropertyOwnership(game, "property_park_place", {
    owner_id: grace,
    mortgaged: true,
    houses: 0,
    hotel: false,
    hotels: 0,
  });
  setPropertyOwnership(game, "property_boardwalk", {
    owner_id: grace,
    mortgaged: false,
    houses: 0,
    hotel: true,
    hotels: 1,
  });
}

function isNegotiationSeed(seed) {
  return typeof seed === "string" && seed.startsWith("stage-5-6-seeded");
}

function createNegotiationRecord(game, payload) {
  const createdAt = nowIso();
  game.negotiation_counter += 1;
  const negotiation = {
    id: `${game.id}-negotiation-${game.negotiation_counter}`,
    game_id: game.id,
    opened_by_player_id: payload.opened_by_player_id,
    participant_player_ids: [...payload.participant_player_ids],
    topic: payload.topic,
    context: payload.context ?? "",
    status: "opened",
    round_number: 1,
    created_at: createdAt,
    updated_at: createdAt,
  };
  game.negotiations.unshift(negotiation);
  game.negotiation_messages[negotiation.id] = [];
  game.updated_at = createdAt;
  return negotiation;
}

function configureNegotiationSeed(game) {
  if (!isNegotiationSeed(game.seed) || game.players.length < 2) {
    return;
  }
  const participants = game.players.slice(0, 2).map((player) => player.id);
  const negotiation = createNegotiationRecord(game, {
    opened_by_player_id: participants[0],
    participant_player_ids: participants,
    topic: "Seeded negotiation",
    context: "Stage 5.6 deterministic seeded negotiation.",
  });
  game.message_counter += 1;
  game.negotiation_messages[negotiation.id].push({
    id: `${game.id}-message-${game.message_counter}`,
    game_id: game.id,
    negotiation_id: negotiation.id,
    author_player_id: participants[0],
    body: "Seeded opening message.",
    created_at: nowIso(),
  });
}

function isContractsLogSeed(seed) {
  return typeof seed === "string" && seed.startsWith("stage-5-7");
}

function isAiAuditSeed(seed) {
  return typeof seed === "string" && seed.startsWith("stage-5-8");
}

function configureAiAuditSeed(game) {
  if (!isAiAuditSeed(game.seed)) {
    return;
  }

  const aiPlayers = game.players.filter((player) => player.controller_type === "ai");
  if (aiPlayers.length === 0) {
    return;
  }

  const createdAt = "2026-07-04T00:10:00.000Z";
  const decisionAt = "2026-07-04T00:11:00.000Z";
  const dialogueAt = "2026-07-04T00:11:01.000Z";
  const memoryAt = "2026-07-04T00:10:30.000Z";
  const retrievalAt = "2026-07-04T00:10:45.000Z";
  const rejectedAt = "2026-07-04T00:11:30.000Z";
  const profileTraits = [
    ["risk-aware", "rent-focused", "cash-buffered"],
    ["opportunistic", "auction-curious", "trade-probing"],
    ["defensive", "utility-aware", "jail-cautious"],
  ];

  game.ai_profiles = aiPlayers.map((player, index) => ({
    ai_profile_id: `${game.id}-ai-profile-${index + 1}`,
    game_id: game.id,
    player_id: player.id,
    display_name: `${player.name} audit profile`,
    traits: profileTraits[index % profileTraits.length],
    personality:
      index === 0
        ? "Careful analyst that prefers legal certainty before committing cash."
        : "Fast negotiator that searches for pressure points in short-term liquidity.",
    play_style:
      index === 0
        ? "Builds cash buffers before auctions and chooses the smallest legal tempo-preserving action."
        : "Uses retrieved context to probe trades while avoiding actions outside the legal snapshot.",
    persona_summary:
      index === 0
        ? `${player.name} is a careful analyst who preserves cash before pressing for trades.`
        : `${player.name} is a fast negotiator who looks for pressure without leaving the legal snapshot.`,
    created_at: createdAt,
  }));

  const profile = game.ai_profiles[0];
  const player = aiPlayers[0];
  const memoryEntryId = `${game.id}-ai-memory-1`;
  const retrievalRecordId = `${game.id}-ai-retrieval-1`;
  const decisionId = `${game.id}-ai-decision-1`;
  const currentStateHash = stateHash(game);

  game.ai_memory_entries = [
    {
      memory_entry_id: memoryEntryId,
      game_id: game.id,
      ai_profile_id: profile.ai_profile_id,
      player_id: player.id,
      kind: "strategy",
      content: `${player.name} remembers Ada prefers keeping $200 cash after trades.`,
      importance: 0.74,
      created_at: memoryAt,
    },
  ];

  game.ai_retrieval_records = [
    {
      retrieval_record_id: retrievalRecordId,
      game_id: game.id,
      ai_decision_id: decisionId,
      ai_profile_id: profile.ai_profile_id,
      memory_entry_id: memoryEntryId,
      source_type: "memory",
      source_id: memoryEntryId,
      score: 0.93,
      content: "Retrieved context records show Ada usually rejects deals that drain her reserve below $200.",
      created_at: retrievalAt,
    },
  ];

  game.ai_decisions = [
    {
      ai_decision_id: decisionId,
      game_id: game.id,
      ai_profile_id: profile.ai_profile_id,
      player_id: player.id,
      state_hash: currentStateHash,
      legal_actions: [
        legalAction(game, "ROLL_DICE", {}, player.id),
        legalAction(game, "DECLARE_BANKRUPTCY", { reason: "insolvent" }, player.id),
      ],
      prompt_context: {
        phase: game.current_phase,
        active_player_id: game.players[0]?.id ?? null,
        inspected_player_id: player.id,
        legal_action_count: 2,
        note: "Private local research view; codex exec runtime is scheduled for Phase 7.",
      },
      raw_output: "{\"action\":\"ROLL_DICE\",\"rationale\":\"Only tempo-preserving legal move.\"}",
      parsed_output: {
        action: "ROLL_DICE",
        confidence: 0.81,
        rationale: "Only tempo-preserving legal move.",
      },
      validation_errors: [],
      memory_entry_ids: [memoryEntryId],
      retrieval_record_ids: [retrievalRecordId],
      status: "accepted",
      created_at: decisionAt,
    },
  ];

  game.ai_self_dialogue = [
    {
      self_dialogue_id: `${game.id}-self-dialogue-1`,
      game_id: game.id,
      ai_decision_id: decisionId,
      ai_profile_id: profile.ai_profile_id,
      sequence: 1,
      role: "planner",
      content: "Legal actions snapshot has ROLL_DICE and DECLARE_BANKRUPTCY; bankruptcy is not appropriate.",
      created_at: dialogueAt,
    },
    {
      self_dialogue_id: `${game.id}-self-dialogue-2`,
      game_id: game.id,
      ai_decision_id: decisionId,
      ai_profile_id: profile.ai_profile_id,
      sequence: 2,
      role: "critic",
      content: "Memory entries and retrieved context records do not change the legal action boundary.",
      created_at: dialogueAt,
    },
  ];

  game.ai_rejected_outputs = [
    {
      rejected_output_id: `${game.id}-rejected-ai-output-1`,
      game_id: game.id,
      ai_decision_id: decisionId,
      ai_profile_id: profile.ai_profile_id,
      player_id: player.id,
      state_hash: currentStateHash,
      raw_output: "{\"action\":\"BUY_PROPERTY\",\"property_id\":\"property_boardwalk\"}",
      parsed_output: {
        action: "BUY_PROPERTY",
        property_id: "property_boardwalk",
      },
      validation_errors: [
        {
          code: "illegal_action",
          message: "BUY_PROPERTY is not in the Legal actions snapshot.",
          field: "parsed_output.action",
        },
      ],
      created_at: rejectedAt,
    },
  ];
}

function configureContractsLogSeed(game) {
  if (!isContractsLogSeed(game.seed) || game.players.length < 2) {
    return;
  }

  const ada = game.players[0];
  const grace = game.players[1];
  const aiPlayer = game.players.find((player) => player.controller_type === "ai") ?? game.players[2] ?? grace;
  const agreementId = `${game.id}-agreement-1`;
  const createdAt = "2026-07-04T00:01:00.000Z";
  const acceptedAt = "2026-07-04T00:02:00.000Z";
  const decisionAt = "2026-07-04T00:03:00.000Z";
  const transferAt = "2026-07-04T00:05:00.000Z";
  const rejectedAt = "2026-07-04T00:06:00.000Z";

  game.current_phase = "PRE_ROLL_MANAGEMENT";
  game.negotiation_counter += 1;
  const negotiation = {
    id: `${game.id}-negotiation-${game.negotiation_counter}`,
    game_id: game.id,
    opened_by_player_id: ada.id,
    participant_player_ids: [ada.id, grace.id],
    topic: "Stage 5.7 rent share",
    context: "Seeded source agreement for contracts, obligations, and game-log UI.",
    status: "accepted",
    round_number: 1,
    created_at: createdAt,
    updated_at: acceptedAt,
  };
  game.negotiations.unshift(negotiation);
  game.message_counter += 1;
  game.negotiation_messages[negotiation.id] = [
    {
      id: `${game.id}-message-${game.message_counter}`,
      game_id: game.id,
      negotiation_id: negotiation.id,
      author_player_id: ada.id,
      body: "Ada proposes a rent-share source agreement.",
      created_at: createdAt,
    },
  ];

  game.deal_counter += 1;
  const deal = {
    id: `${game.id}-deal-${game.deal_counter}`,
    game_id: game.id,
    negotiation_id: negotiation.id,
    proposer_player_id: ada.id,
    participant_player_ids: [ada.id, grace.id],
    parent_deal_id: null,
    version: 1,
    status: "accepted",
    terms: [
      {
        kind: "rent_share",
        from_player_id: ada.id,
        to_player_id: grace.id,
        amount: 50,
        due_condition: "next orange rent collection",
        summary: "Ada pays Grace $50 when the orange rent is collected.",
      },
      {
        kind: "cash_transfer",
        from_player_id: ada.id,
        to_player_id: grace.id,
        amount: 75,
        due_turn: 4,
        summary: "Ada settles an existing $75 obligation with Grace.",
      },
    ],
    validation_errors: [],
    accepted_at: acceptedAt,
    rejected_at: null,
    created_at: createdAt,
    updated_at: acceptedAt,
  };
  game.deals.unshift(deal);

  const actionEvent = createAcceptedEvent(game, "DICE_ROLLED", { dice: [3, 4], total: 7 }, ada.id);
  actionEvent.created_at = "2026-07-04T00:00:30.000Z";
  const dealEvent = createAcceptedEvent(
    game,
    "DEAL_ACCEPTED",
    { deal_id: deal.id, source_agreement_id: agreementId },
    ada.id,
  );
  dealEvent.created_at = acceptedAt;
  const aiEvent = createAcceptedEvent(
    game,
    "AI_DECISION_RECORDED",
    {
      player_id: aiPlayer.id,
      decision: `${aiPlayer.name} declined a risky rent-share counteroffer.`,
    },
    aiPlayer.id,
  );
  aiEvent.created_at = decisionAt;

  const contract = {
    id: `${game.id}-contract-1`,
    game_id: game.id,
    deal_id: deal.id,
    source_agreement_id: agreementId,
    effective_event_id: dealEvent.id,
    party_player_ids: [ada.id, grace.id],
    status: "active",
    terms: deal.terms,
    term_summary: "Ada pays Grace $50 when the orange rent is collected.",
    created_at: acceptedAt,
    effective_at: acceptedAt,
  };
  const settledObligationId = `${game.id}-obligation-settled`;
  const transferSummary = "Ada paid Grace $75 from the source agreement.";
  const transferEvent = createAcceptedEvent(
    game,
    "CONTRACT_TRIGGERED_TRANSFER",
    {
      contract_id: contract.id,
      obligation_id: settledObligationId,
      deal_id: deal.id,
      source_agreement_id: agreementId,
      from_player_id: ada.id,
      to_player_id: grace.id,
      amount: 75,
      summary: transferSummary,
    },
    null,
  );
  transferEvent.created_at = transferAt;

  game.contracts = [contract];
  game.obligations = [
    {
      id: `${game.id}-obligation-upcoming`,
      game_id: game.id,
      contract_id: contract.id,
      obligated_player_id: ada.id,
      counterparty_player_id: grace.id,
      status: "pending",
      due_turn: 6,
      due_condition: "next orange rent collection",
      amount: 50,
      asset_summary: "$50 cash transfer",
      transfer_summary: null,
      triggering_event_id: null,
      settled_at: null,
      created_at: acceptedAt,
    },
    {
      id: settledObligationId,
      game_id: game.id,
      contract_id: contract.id,
      obligated_player_id: ada.id,
      counterparty_player_id: grace.id,
      status: "settled",
      due_turn: 4,
      due_condition: "first railroad rent collection",
      amount: 75,
      asset_summary: "$75 cash transfer",
      transfer_summary: transferSummary,
      triggering_event_id: transferEvent.id,
      settled_at: transferAt,
      created_at: acceptedAt,
    },
  ];
  game.contract_outcomes = [
    {
      id: `${contract.id}:${settledObligationId}`,
      game_id: game.id,
      source_deal_id: deal.id,
      contract_id: contract.id,
      obligation_id: settledObligationId,
      obligation_type: "rent_share",
      trigger: { type: "rent_collected", property_id: "property_reading_railroad" },
      classic_rule_interaction: {
        policy: {
          rent_share_reduced_rent: "share_actual_paid",
          impossible_state_prevention: "strict",
        },
        policy_key: "rent_share_reduced_rent",
        policy_value: "share_actual_paid",
        deterministic: true,
      },
      decision: { status: "settled", decision: "rent_share_cash_transfer" },
      resulting_state_effect: {
        cash_transfers: [
          { player_id: ada.id, amount: -75 },
          { player_id: grace.id, amount: 75 },
        ],
      },
      explanation_text:
        `Contract outcome explanation: source deal ${deal.id} produced contract ${contract.id} ` +
        `and obligation ${settledObligationId} with trigger rent_collected; ` +
        "classic-rule interaction rent_share_reduced_rent=share_actual_paid; " +
        "decision rent_share_cash_transfer; resulting state/effect cash transfer.",
    },
  ];

  const rejection = createRejectedAction(
    game,
    {
      actor_id: ada.id,
      type: "BUY_PROPERTY",
      payload: { property_id: "property_boardwalk" },
    },
    "illegal_action",
    [
      {
        code: "illegal_action",
        message: "BUY_PROPERTY is not currently legal",
        field: "type",
      },
    ],
  );
  rejection.created_at = rejectedAt;
  game.updated_at = rejectedAt;
}

function negotiationById(game, negotiationId) {
  return game.negotiations.find((negotiation) => negotiation.id === negotiationId) ?? null;
}

function dealById(game, dealId) {
  return game.deals.find((deal) => deal.id === dealId) ?? null;
}

function negotiationValidationError(reasonCode, message, field) {
  return {
    status: "rejected",
    reason_code: reasonCode,
    validation_errors: [
      {
        code: reasonCode,
        message,
        field,
      },
    ],
  };
}

function validParticipantIds(game, participantIds) {
  return (
    Array.isArray(participantIds) &&
    participantIds.length >= 2 &&
    participantIds.every((playerId) => typeof playerId === "string" && Boolean(playerById(game, playerId)))
  );
}

function validateCreateNegotiationPayload(game, payload) {
  if (!isObject(payload)) {
    return negotiationValidationError("malformed_negotiation", "request body must be a JSON object", "body");
  }
  if (!playerById(game, payload.opened_by_player_id)) {
    return negotiationValidationError("invalid_participant", "opened_by_player_id must reference a player", "opened_by_player_id");
  }
  if (!validParticipantIds(game, payload.participant_player_ids)) {
    return negotiationValidationError(
      "invalid_participant",
      "participant_player_ids must contain at least two game players",
      "participant_player_ids",
    );
  }
  if (!payload.participant_player_ids.includes(payload.opened_by_player_id)) {
    return negotiationValidationError("invalid_participant", "opened_by_player_id must be a participant", "opened_by_player_id");
  }
  if (typeof payload.topic !== "string" || payload.topic.trim().length === 0) {
    return negotiationValidationError("missing_topic", "topic is required", "topic");
  }
  return null;
}

function validateOpenNegotiation(negotiation) {
  if (!negotiation) {
    return negotiationValidationError("missing_negotiation", "negotiation was not found", "negotiation_id");
  }
  if (!isActiveNegotiationStatus(negotiation.status)) {
    return negotiationValidationError("closed_negotiation", "negotiation is closed and cannot mutate", "negotiation_id");
  }
  return null;
}

function validateMessagePayload(game, negotiation, payload) {
  const closed = validateOpenNegotiation(negotiation);
  if (closed) {
    return closed;
  }
  if (!isObject(payload)) {
    return negotiationValidationError("malformed_message", "request body must be a JSON object", "body");
  }
  if (!negotiation.participant_player_ids.includes(payload.author_player_id)) {
    return negotiationValidationError("invalid_author", "author_player_id must be a negotiation participant", "author_player_id");
  }
  if (typeof payload.body !== "string" || payload.body.trim().length === 0) {
    return negotiationValidationError("missing_message", "message body is required", "body");
  }
  return null;
}

function isValidTerm(term) {
  return (
    isObject(term) &&
    ["cash_transfer", "property_transfer", "loan", "option", "rent_share", "risk_transfer"].includes(term.kind)
  );
}

function validateDealPayload(game, payload) {
  if (!isObject(payload)) {
    return negotiationValidationError("malformed_deal", "request body must be a JSON object", "body");
  }
  const negotiation = negotiationById(game, payload.negotiation_id);
  const closed = validateOpenNegotiation(negotiation);
  if (closed) {
    return closed;
  }
  if (!negotiation.participant_player_ids.includes(payload.proposer_player_id)) {
    return negotiationValidationError("invalid_proposer", "proposer_player_id must be a negotiation participant", "proposer_player_id");
  }
  if (!validParticipantIds(game, payload.participant_player_ids)) {
    return negotiationValidationError("invalid_participant", "participant_player_ids must contain game players", "participant_player_ids");
  }
  if (!Array.isArray(payload.terms) || payload.terms.length === 0 || !payload.terms.every(isValidTerm)) {
    return negotiationValidationError(
      "invalid_terms",
      "terms must include at least one supported structured deal term",
      "terms",
    );
  }
  if (payload.parent_deal_id !== null && payload.parent_deal_id !== undefined) {
    const parent = dealById(game, payload.parent_deal_id);
    if (!parent || parent.negotiation_id !== negotiation.id) {
      return negotiationValidationError("invalid_parent_deal", "parent_deal_id must reference this negotiation", "parent_deal_id");
    }
  }
  return null;
}

function createDealRecord(game, payload) {
  const createdAt = nowIso();
  const negotiationDeals = game.deals.filter((deal) => deal.negotiation_id === payload.negotiation_id);
  game.deal_counter += 1;
  const deal = {
    id: `${game.id}-deal-${game.deal_counter}`,
    game_id: game.id,
    negotiation_id: payload.negotiation_id,
    proposer_player_id: payload.proposer_player_id,
    participant_player_ids: [...payload.participant_player_ids],
    parent_deal_id: payload.parent_deal_id ?? null,
    version: negotiationDeals.length + 1,
    status: "proposed",
    terms: payload.terms,
    validation_errors: [],
    accepted_at: null,
    rejected_at: null,
    created_at: createdAt,
    updated_at: createdAt,
  };
  game.deals.unshift(deal);
  const negotiation = negotiationById(game, payload.negotiation_id);
  if (negotiation) {
    negotiation.status = payload.parent_deal_id ? "countered" : "active";
    negotiation.round_number = Math.max(negotiation.round_number, deal.version);
    negotiation.updated_at = createdAt;
  }
  game.updated_at = createdAt;
  return deal;
}

function validateDealMutation(game, dealId, action) {
  const deal = dealById(game, dealId);
  if (!deal) {
    return {
      deal: null,
      error: negotiationValidationError("missing_deal", "deal was not found", "deal_id"),
    };
  }
  const negotiation = negotiationById(game, deal.negotiation_id);
  const closed = validateOpenNegotiation(negotiation);
  if (closed) {
    return { deal, error: closed };
  }
  if (deal.status !== "proposed") {
    return {
      deal,
      error: negotiationValidationError(`invalid_${action}`, `${action} requires a proposed deal`, "status"),
    };
  }
  return { deal, error: null };
}

function acceptDealRecord(game, deal) {
  const acceptedAt = nowIso();
  deal.status = "accepted";
  deal.accepted_at = acceptedAt;
  deal.updated_at = acceptedAt;
  const negotiation = negotiationById(game, deal.negotiation_id);
  if (negotiation) {
    negotiation.status = "accepted";
    negotiation.updated_at = acceptedAt;
  }
  game.updated_at = acceptedAt;
  return deal;
}

function rejectDealRecord(game, deal) {
  const rejectedAt = nowIso();
  deal.status = "rejected";
  deal.rejected_at = rejectedAt;
  deal.updated_at = rejectedAt;
  const negotiation = negotiationById(game, deal.negotiation_id);
  if (negotiation) {
    negotiation.status = "rejected";
    negotiation.updated_at = rejectedAt;
  }
  game.updated_at = rejectedAt;
  return deal;
}

function expireNegotiationRecord(game, negotiation) {
  const expiredAt = nowIso();
  negotiation.status = "expired";
  negotiation.updated_at = expiredAt;
  for (const deal of game.deals) {
    if (deal.negotiation_id === negotiation.id && deal.status === "proposed") {
      deal.status = "expired";
      deal.updated_at = expiredAt;
    }
  }
  game.updated_at = expiredAt;
  return negotiation;
}

function gameState(game) {
  return {
    game_id: game.id,
    state: {
      game_id: game.id,
      seed: game.seed,
      players: game.players.map((player) => ({
        id: player.id,
        ...player.state,
      })),
      property_ownership: game.property_ownership,
      bank_inventory: game.bank_inventory,
      active_auction: game.active_auction,
      turn: {
        phase: game.current_phase,
        current_player_index: 0,
        current_player_id: game.players[0]?.id ?? null,
      },
    },
    state_hash: stateHash(game),
    event_sequence: game.event_sequence,
  };
}

function stateHash(game) {
  return `mock-state-${game.id}-${game.event_sequence}`;
}

function activePlayer(game) {
  return game.players[0] ?? null;
}

function playerById(game, playerId) {
  return game.players.find((player) => player.id === playerId) ?? null;
}

function legalAction(game, type, payload = {}, actorPlayerId = activePlayer(game)?.id ?? "") {
  return {
    actor_id: actorPlayerId,
    type,
    payload,
    expected_state_hash: stateHash(game),
    expected_event_sequence: game.event_sequence,
    description: null,
    schema: {},
  };
}

function legalActionsFor(game, actorPlayerId) {
  const actor = playerById(game, actorPlayerId);
  if (!actor) {
    return [];
  }
  if (game.active_auction) {
    return auctionLegalActionsFor(game, actor);
  }
  const turnActor = activePlayer(game);
  if (!turnActor || turnActor.id !== actorPlayerId) {
    return [];
  }
  if (isPropertyManagementSeed(game.seed) && game.current_phase === "PRE_ROLL_MANAGEMENT") {
    return propertyManagementLegalActionsFor(game);
  }
  if (game.current_phase === "PURCHASE_OR_AUCTION") {
    const propertyId = purchasablePropertyId(game);
    if (!propertyId) {
      return [];
    }
    const price = propertyById(propertyId)?.price;
    return [
      legalAction(game, "BUY_PROPERTY", { property_id: propertyId, ...(price ? { price } : {}) }, actor.id),
      legalAction(game, "START_AUCTION", { property_id: propertyId }, actor.id),
    ];
  }
  return [legalAction(game, "ROLL_DICE", {}, actor.id)];
}

function auctionMinimumBid(game) {
  const highBidAmount = game.active_auction?.high_bid_amount;
  return Number.isInteger(highBidAmount) ? highBidAmount + 1 : 1;
}

function bidSchema(minimumBid) {
  return {
    type: "object",
    properties: {
      amount: {
        type: "integer",
        minimum: minimumBid,
      },
    },
    required: ["amount"],
  };
}

function auctionLegalActionsFor(game, actor) {
  const auction = game.active_auction;
  if (!auction || actor.status !== "active" || auction.passed_player_ids.includes(actor.id)) {
    return [];
  }
  const minimumBid = auctionMinimumBid(game);
  const actions = [];
  if ((actor.state.cash ?? 0) >= minimumBid) {
    actions.push({
      ...legalAction(game, "BID_AUCTION", { property_id: auction.property_id, amount: minimumBid }, actor.id),
      schema: bidSchema(minimumBid),
    });
  }
  actions.push(legalAction(game, "PASS_AUCTION", { property_id: auction.property_id }, actor.id));
  return actions;
}

function propertyManagementLegalActionsFor(game) {
  if (game.seed.startsWith("stage-5-property-management-reject")) {
    return [legalAction(game, "BUY_HOUSE", { property_id: "property_baltic_avenue", cost: 50 })];
  }

  const mediterranean = propertyOwnership(game, "property_mediterranean_avenue");
  const baltic = propertyOwnership(game, "property_baltic_avenue");
  const parkPlace = propertyOwnership(game, "property_park_place");
  const actions = [];
  if (mediterranean?.owner_id === activePlayer(game)?.id && !mediterranean.mortgaged && mediterranean.houses === 0 && !mediterranean.hotel) {
    actions.push(legalAction(game, "BUY_HOUSE", { property_id: "property_mediterranean_avenue", cost: 50 }));
  }
  if (baltic?.owner_id === activePlayer(game)?.id && !baltic.mortgaged && mediterranean?.houses === 0 && !mediterranean?.hotel) {
    actions.push(legalAction(game, "MORTGAGE_PROPERTY", { property_id: "property_baltic_avenue", proceeds: 30 }));
  }
  if (parkPlace?.owner_id === activePlayer(game)?.id && parkPlace.mortgaged) {
    actions.push(legalAction(game, "UNMORTGAGE_PROPERTY", { property_id: "property_park_place", cost: 220 }));
  }
  return actions;
}

function createAcceptedEvent(game, eventType, payload, actorPlayerId) {
  game.event_sequence += 1;
  const event = {
    id: `${game.id}-event-${game.event_sequence}`,
    game_id: game.id,
    sequence: game.event_sequence,
    actor_player_id: actorPlayerId,
    event_type: eventType,
    payload,
    state_hash: stateHash(game),
    created_at: nowIso(),
  };
  game.events.push(event);
  return event;
}

function createRejectedAction(game, submittedAction, reasonCode, validationErrors) {
  const actor = typeof submittedAction?.actor_id === "string" ? submittedAction.actor_id : null;
  const actionType = typeof submittedAction?.type === "string" ? submittedAction.type : "UNKNOWN";
  const record = {
    id: `${game.id}-rejection-${game.rejected_actions.length + 1}`,
    game_id: game.id,
    actor_player_id: actor,
    action_type: actionType,
    payload: isObject(submittedAction?.payload) ? submittedAction.payload : {},
    reason_code: reasonCode,
    validation_errors: validationErrors,
    legal_action_context: {
      phase: game.current_phase,
      legal_actions: legalActionsFor(game, actor ?? "").map((action) => action.type),
    },
    phase: game.current_phase,
    state_hash: stateHash(game),
    created_at: nowIso(),
  };
  game.rejected_actions.unshift(record);
  return record;
}

function rejectedPayload(record, submittedAction) {
  return {
    status: "rejected",
    rejected_action_id: record.id,
    reason_code: record.reason_code,
    validation_errors: record.validation_errors,
    legal_action_context: record.legal_action_context,
    submitted_action: submittedAction,
  };
}

function writeSse(response, event) {
  response.write(`id: ${event.sequence ?? Date.now()}\nevent: game_event\ndata: ${JSON.stringify(event)}\n\n`);
}

function broadcastSse(game, event) {
  for (const response of game.stream_clients) {
    if (!response.destroyed) {
      writeSse(response, event);
    }
  }
}

function acceptRollDice(game, action) {
  const actor = activePlayer(game);
  const fromPosition = actor?.state.position ?? 0;
  const isAuctionSeed = typeof game.seed === "string" && game.seed.startsWith("stage-5-auction");
  const toPosition = isAuctionSeed ? 1 : 7;
  const rolled = createAcceptedEvent(
    game,
    "DICE_ROLLED",
    isAuctionSeed ? { dice: [1], total: 1 } : { dice: [3, 4], total: 7 },
    actor?.id ?? null,
  );

  if (actor) {
    actor.state = {
      ...actor.state,
      position: toPosition,
    };
    actor.updated_at = nowIso();
  }
  game.current_phase = "PURCHASE_OR_AUCTION";
  game.updated_at = nowIso();
  const moved = createAcceptedEvent(
    game,
    "TOKEN_MOVED",
    { player_id: actor?.id ?? null, from_position: fromPosition, to_position: toPosition },
    actor?.id ?? null,
  );
  const acceptedEvents = [rolled, moved];
  for (const event of acceptedEvents) {
    broadcastSse(game, event);
  }
  return {
    status: "accepted",
    game_id: game.id,
    accepted_events: acceptedEvents,
    state: gameState(game).state,
    state_hash: stateHash(game),
    event_sequence: game.event_sequence,
  };
}

function rejectAction(game, action, reasonCode, message, field = "expected_state_hash") {
  const validationErrors = [
    {
      code: reasonCode,
      message,
      field,
    },
  ];
  const record = createRejectedAction(game, action, reasonCode, validationErrors);
  broadcastSse(game, {
    sequence: game.event_sequence,
    event_type: "ACTION_REJECTED",
    payload: { rejected_action_id: record.id, reason_code: reasonCode },
  });
  return rejectedPayload(record, action);
}

function aiValidationError(reasonCode, message, field) {
  return {
    code: reasonCode,
    message,
    field,
  };
}

function aiProfileIdForPlayer(game, playerId) {
  const profile = game.ai_profiles.find((candidate) => candidate.player_id === playerId);
  return profile?.ai_profile_id ?? `${game.id}-runtime-ai-profile-${playerId || "unknown-player"}`;
}

function createAiDecisionRecord(game, payload, status, patch = {}) {
  const createdAt = nowIso();
  const decisionNumber = game.ai_decisions.length + 1;
  const decisionId = `${game.id}-ai-decision-${decisionNumber}`;
  const playerId = typeof payload.player_id === "string" && payload.player_id.length > 0 ? payload.player_id : "unknown-player";
  const decisionType = payload.decision_type ?? "action_decision";
  const legalActions = legalActionsFor(game, playerId);
  const decision = {
    id: decisionId,
    ai_decision_id: decisionId,
    game_id: game.id,
    ai_profile_id: aiProfileIdForPlayer(game, playerId),
    player_id: playerId,
    decision_type: decisionType,
    negotiation_id: payload.negotiation_id ?? null,
    status,
    phase: game.current_phase,
    state_hash: stateHash(game),
    legal_actions: legalActions,
    prompt_context_hash: `mock-ai-context-${decisionNumber}`,
    prompt_context: {
      mock: true,
      phase: game.current_phase,
      legal_action_count: legalActions.length,
      request_context: payload.request_context ?? {},
    },
    raw_output: JSON.stringify({ mock: true, decision_type: decisionType }),
    parsed_output: { mock: true, decision_type: decisionType },
    validation_errors: [],
    memory_entry_ids: [],
    retrieval_record_ids: [],
    validation_result: { no_substitute_move: true, substitute_move: null },
    accepted_event_id: null,
    rejected_action_id: null,
    created_at: createdAt,
    ...patch,
  };
  game.ai_decisions.unshift(decision);
  return decision;
}

function aiStepPayload({
  game,
  payload,
  decision,
  status,
  acceptedEvents = [],
  rejectedActionId = null,
  outcome = {},
  reasonCode = null,
  validationErrors = [],
  negotiation = null,
  message = null,
  deal = null,
}) {
  const acceptedEventId = acceptedEvents[0]?.id ?? null;
  if (acceptedEventId) {
    decision.accepted_event_id = acceptedEventId;
  }
  if (rejectedActionId) {
    decision.rejected_action_id = rejectedActionId;
  }
  decision.validation_errors = validationErrors;
  return {
    status,
    game_id: game.id,
    player_id: payload.player_id,
    decision_type: payload.decision_type ?? "action_decision",
    negotiation_id: payload.negotiation_id ?? null,
    ai_decision_id: decision.ai_decision_id ?? decision.id,
    accepted_events: acceptedEvents,
    accepted_event_id: acceptedEventId,
    rejected_action_id: rejectedActionId,
    game_status: game.status,
    consumed_response_opportunity: false,
    consumed_negotiation_opportunity: null,
    outcome,
    reason_code: reasonCode,
    validation_errors: validationErrors,
    negotiation,
    message,
    deal,
  };
}

function sampleAiDealTerms(game, negotiation, proposerPlayerId) {
  const participants = negotiation.participant_player_ids;
  const recipient = participants.find((playerId) => playerId !== proposerPlayerId) ?? participants[0];
  return [
    {
      kind: "cash_transfer",
      from_player_id: proposerPlayerId,
      to_player_id: recipient,
      amount: 90,
      summary: `${playerById(game, proposerPlayerId)?.name ?? "AI"} pays $90 now`,
    },
    {
      kind: "rent_share",
      from_player_id: recipient,
      to_player_id: proposerPlayerId,
      property_id: "property_reading_railroad",
      percentage: 20,
      expires_round: 4,
      summary: `${playerById(game, proposerPlayerId)?.name ?? "AI"} receives 20% rent share`,
    },
  ];
}

function applyMockAiNegotiationStep(game, payload, decision) {
  if (payload.decision_type === "open_negotiation") {
    const recipient = game.players.find((player) => player.id !== payload.player_id && player.status === "active");
    if (!recipient) {
      const errors = [aiValidationError("participant_not_in_game", "open_negotiation requires another active player", "participant_player_ids")];
      const rejection = createRejectedAction(
        game,
        { actor_id: payload.player_id, type: "AI_OPEN_NEGOTIATION", payload },
        "participant_not_in_game",
        errors,
      );
      decision.status = "rejected";
      return aiStepPayload({
        game,
        payload,
        decision,
        status: "rejected",
        rejectedActionId: rejection.id,
        outcome: { kind: "open_negotiation", status: "rejected" },
        reasonCode: "participant_not_in_game",
        validationErrors: errors,
      });
    }

    const negotiation = createNegotiationRecord(game, {
      opened_by_player_id: payload.player_id,
      participant_player_ids: [payload.player_id, recipient.id],
      topic: "AI opened negotiation",
      context: "Mock AI open_negotiation decision.",
    });
    decision.status = "accepted";
    decision.negotiation_id = negotiation.id;
    decision.parsed_output = {
      mock: true,
      decision_type: "open_negotiation",
      negotiation: {
        participant_player_ids: negotiation.participant_player_ids,
        context: { topic: negotiation.topic, description: negotiation.context },
      },
    };
    decision.validation_result = {
      ...decision.validation_result,
      lifecycle_result: {
        kind: "open_negotiation",
        status: "done",
        negotiation_id: negotiation.id,
      },
    };
    return aiStepPayload({
      game,
      payload: { ...payload, negotiation_id: negotiation.id },
      decision,
      status: "done",
      outcome: { kind: "open_negotiation", status: "done", negotiation_id: negotiation.id },
      negotiation,
    });
  }

  const negotiation = negotiationById(game, payload.negotiation_id);
  const validation = validateOpenNegotiation(negotiation);
  if (validation) {
    const errors = validation.validation_errors;
    const rejection = createRejectedAction(
      game,
      { actor_id: payload.player_id, type: `AI_${payload.decision_type ?? "action_decision"}`, payload },
      validation.reason_code,
      errors,
    );
    decision.status = "rejected";
    return aiStepPayload({
      game,
      payload,
      decision,
      status: "rejected",
      rejectedActionId: rejection.id,
      outcome: { kind: "ai_negotiation", status: "rejected" },
      reasonCode: validation.reason_code,
      validationErrors: errors,
    });
  }
  if (!negotiation.participant_player_ids.includes(payload.player_id)) {
    const errors = [
      aiValidationError("player_not_participant", "AI player must be a negotiation participant", "player_id"),
    ];
    const rejection = createRejectedAction(
      game,
      { actor_id: payload.player_id, type: `AI_${payload.decision_type ?? "action_decision"}`, payload },
      "player_not_participant",
      errors,
    );
    decision.status = "rejected";
    return aiStepPayload({
      game,
      payload,
      decision,
      status: "rejected",
      rejectedActionId: rejection.id,
      outcome: { kind: "ai_negotiation", status: "rejected" },
      reasonCode: "player_not_participant",
      validationErrors: errors,
    });
  }

  if (payload.decision_type === "negotiation_message") {
    game.message_counter += 1;
    const createdAt = nowIso();
    const author = playerById(game, payload.player_id);
    const message = {
      id: `${game.id}-message-${game.message_counter}`,
      game_id: game.id,
      negotiation_id: negotiation.id,
      author_player_id: payload.player_id,
      body: `${author?.name ?? "AI"} proposes a structured trade window.`,
      created_at: createdAt,
    };
    game.negotiation_messages[negotiation.id] = [...(game.negotiation_messages[negotiation.id] ?? []), message];
    negotiation.updated_at = createdAt;
    game.updated_at = createdAt;
    decision.status = "accepted";
    return aiStepPayload({
      game,
      payload,
      decision,
      status: "done",
      outcome: { kind: "negotiation_message", status: "done", message_id: message.id },
      negotiation,
      message,
    });
  }

  if (payload.decision_type === "deal_proposal" || payload.decision_type === "counteroffer") {
    const currentDeal = game.deals.find((deal) => deal.negotiation_id === negotiation.id && deal.status === "proposed") ?? null;
    const deal = createDealRecord(game, {
      negotiation_id: negotiation.id,
      proposer_player_id: payload.player_id,
      participant_player_ids: negotiation.participant_player_ids,
      parent_deal_id: payload.decision_type === "counteroffer" ? currentDeal?.id ?? null : null,
      terms: sampleAiDealTerms(game, negotiation, payload.player_id),
    });
    decision.status = "accepted";
    return aiStepPayload({
      game,
      payload,
      decision,
      status: "done",
      outcome: { kind: payload.decision_type, status: "done", deal_id: deal.id },
      negotiation,
      deal,
    });
  }

  if (payload.decision_type === "accept_reject") {
    const deal = game.deals.find((item) => item.negotiation_id === negotiation.id && item.status === "proposed") ?? null;
    if (!deal) {
      const errors = [aiValidationError("deal_not_found", "AI response requires a proposed deal", "deal_id")];
      const rejection = createRejectedAction(
        game,
        { actor_id: payload.player_id, type: "AI_ACCEPT_REJECT", payload },
        "deal_not_found",
        errors,
      );
      decision.status = "rejected";
      return aiStepPayload({
        game,
        payload,
        decision,
        status: "rejected",
        rejectedActionId: rejection.id,
        outcome: { kind: "accept_reject", status: "rejected" },
        reasonCode: "deal_not_found",
        validationErrors: errors,
      });
    }
    const acceptedDeal = acceptDealRecord(game, deal);
    decision.status = "accepted";
    return aiStepPayload({
      game,
      payload,
      decision,
      status: "done",
      outcome: { kind: "accept_reject", status: "done", deal_id: acceptedDeal.id, decision: "accept" },
      negotiation: negotiationById(game, negotiation.id),
      deal: acceptedDeal,
    });
  }

  const errors = [aiValidationError("unsupported_ai_decision_type", "unsupported AI decision type", "decision_type")];
  const rejection = createRejectedAction(
    game,
    { actor_id: payload.player_id, type: `AI_${payload.decision_type ?? "unknown"}`, payload },
    "unsupported_ai_decision_type",
    errors,
  );
  decision.status = "rejected";
  return aiStepPayload({
    game,
    payload,
    decision,
    status: "rejected",
    rejectedActionId: rejection.id,
    outcome: { kind: "unsupported_ai_decision_type", status: "rejected" },
    reasonCode: "unsupported_ai_decision_type",
    validationErrors: errors,
  });
}

function chooseMockAuctionAiAction(game, playerId) {
  const legalActions = legalActionsFor(game, playerId);
  return (
    legalActions.find((action) => action.type === "BID_AUCTION") ??
    legalActions.find((action) => action.type === "PASS_AUCTION") ??
    null
  );
}

function applyMockAiAuctionStep(game, payload, decision) {
  const action = chooseMockAuctionAiAction(game, payload.player_id);
  if (!action) {
    const errors = [
      aiValidationError("no_legal_auction_action", "AI bidder has no legal auction action", "player_id"),
    ];
    const rejection = createRejectedAction(
      game,
      { actor_id: payload.player_id, type: "AI_ACTION_DECISION", payload },
      "no_legal_auction_action",
      errors,
    );
    decision.status = "rejected";
    decision.validation_errors = errors;
    return {
      statusCode: 200,
      body: aiStepPayload({
        game,
        payload,
        decision,
        status: "rejected",
        rejectedActionId: rejection.id,
        outcome: { kind: "action_decision", status: "rejected" },
        reasonCode: "no_legal_auction_action",
        validationErrors: errors,
      }),
    };
  }

  decision.raw_output = JSON.stringify({
    action: action.type,
    payload: action.payload,
    rationale: "Auction is active; choose a legal auction bidder action.",
  });
  decision.parsed_output = {
    mock: true,
    decision_type: "action_decision",
    action: action.type,
    payload: action.payload,
  };

  const auctionResponse = acceptAuctionAction(game, action);
  if (auctionResponse?.payload.status !== "accepted") {
    const rejectedActionId = auctionResponse?.payload.rejected_action_id ?? null;
    const validationErrors = auctionResponse?.payload.validation_errors ?? [
      aiValidationError("auction_action_rejected", "mock auction action was rejected", "action"),
    ];
    decision.status = "rejected";
    return {
      statusCode: 200,
      body: aiStepPayload({
        game,
        payload,
        decision,
        status: "rejected",
        rejectedActionId,
        outcome: { kind: "action_decision", status: "rejected", action: action.type },
        reasonCode: auctionResponse?.payload.reason_code ?? "auction_action_rejected",
        validationErrors,
      }),
    };
  }

  decision.status = "accepted";
  return {
    statusCode: 200,
    body: aiStepPayload({
      game,
      payload,
      decision,
      status: "accepted",
      acceptedEvents: auctionResponse.payload.accepted_events,
      outcome: { kind: "action_decision", status: "accepted", action: action.type },
    }),
  };
}

function applyMockAiStep(game, payload) {
  if (!isObject(payload)) {
    const decision = createAiDecisionRecord(game, { player_id: null, decision_type: "action_decision" }, "rejected");
    const errors = [aiValidationError("malformed_ai_step", "request body must be a JSON object", "body")];
    return {
      statusCode: 422,
      body: aiStepPayload({
        game,
        payload: { player_id: "", decision_type: "action_decision", negotiation_id: null },
        decision,
        status: "rejected",
        outcome: { kind: "ai_step", status: "rejected" },
        reasonCode: "malformed_ai_step",
        validationErrors: errors,
      }),
    };
  }
  const player = playerById(game, payload.player_id);
  if (!player) {
    const errors = [aiValidationError("unknown_player", "player_id must reference a game player", "player_id")];
    return { statusCode: 422, body: { status: "rejected", reason_code: "unknown_player", validation_errors: errors } };
  }
  if (player.controller_type !== "ai") {
    const errors = [aiValidationError("human_player_not_ai_controlled", "AI step requests require an AI-controlled player", "player_id")];
    return {
      statusCode: 409,
      body: { status: "rejected", reason_code: "human_player_not_ai_controlled", validation_errors: errors },
    };
  }

  const decisionType = payload.decision_type ?? "action_decision";
  const existingNegotiationDecisionTypes = ["negotiation_message", "deal_proposal", "counteroffer", "accept_reject"];
  if (existingNegotiationDecisionTypes.includes(decisionType) && !payload.negotiation_id) {
    const errors = [aiValidationError("negotiation_id_required", "negotiation_id is required for AI negotiation decisions", "negotiation_id")];
    return {
      statusCode: 422,
      body: { status: "rejected", reason_code: "negotiation_id_required", validation_errors: errors },
    };
  }
  if (decisionType === "open_negotiation" && payload.negotiation_id) {
    const errors = [aiValidationError("negotiation_id_forbidden", "open_negotiation creates a new negotiation and cannot target an existing negotiation_id", "negotiation_id")];
    return {
      statusCode: 422,
      body: { status: "rejected", reason_code: "negotiation_id_forbidden", validation_errors: errors },
    };
  }

  const decision = createAiDecisionRecord(game, { ...payload, decision_type: decisionType }, "requested");
  if (typeof game.seed === "string" && game.seed.includes("stage-7-6-ai-blocked")) {
    const errors = [aiValidationError("codex_exec_timeout", "mock Codex AI step timed out", null)];
    const rejection = createRejectedAction(
      game,
      { actor_id: payload.player_id, type: `AI_${decisionType}`, payload },
      "codex_exec_timeout",
      errors,
    );
    game.status = "AI_BLOCKED";
    decision.status = "rejected";
    return {
      statusCode: 200,
      body: aiStepPayload({
        game,
        payload: { ...payload, decision_type: decisionType },
        decision,
        status: "blocked",
        rejectedActionId: rejection.id,
        outcome: { kind: "ai_blocked", status: "blocked" },
        reasonCode: "codex_exec_timeout",
        validationErrors: errors,
      }),
    };
  }

  if (decisionType !== "action_decision") {
    return {
      statusCode: 200,
      body: applyMockAiNegotiationStep(game, { ...payload, decision_type: decisionType }, decision),
    };
  }

  if (game.active_auction) {
    return applyMockAiAuctionStep(game, { ...payload, decision_type: decisionType, negotiation_id: null }, decision);
  }

  const action = legalAction(game, "ROLL_DICE", {}, payload.player_id);
  const accepted = acceptRollDice(game, action);
  decision.status = "accepted";
  return {
    statusCode: 200,
    body: aiStepPayload({
      game,
      payload: { ...payload, decision_type: decisionType, negotiation_id: null },
      decision,
      status: "accepted",
      acceptedEvents: accepted.accepted_events,
      outcome: { kind: "action_decision", status: "accepted" },
    }),
  };
}

function actionMatchesLegalAction(candidate, action) {
  if (candidate.type !== action.type) {
    return false;
  }
  const candidatePropertyId = isObject(candidate.payload) ? candidate.payload.property_id : undefined;
  const actionPropertyId = isObject(action.payload) ? action.payload.property_id : undefined;
  if (candidatePropertyId !== undefined || actionPropertyId !== undefined) {
    return candidatePropertyId === actionPropertyId;
  }
  return true;
}

function createAcceptedResponse(game, acceptedEvents) {
  game.updated_at = nowIso();
  for (const event of acceptedEvents) {
    broadcastSse(game, event);
  }
  return {
    status: "accepted",
    game_id: game.id,
    accepted_events: acceptedEvents,
    state: gameState(game).state,
    state_hash: stateHash(game),
    event_sequence: game.event_sequence,
  };
}

function createManagementAcceptedResponse(game, acceptedEvents) {
  return createAcceptedResponse(game, acceptedEvents);
}

function activeAuctionPlayerIds(game) {
  return game.players.filter((player) => player.status === "active").map((player) => player.id);
}

function shouldCloseAuction(game) {
  const auction = game.active_auction;
  if (!auction) {
    return false;
  }
  const unpassedPlayerIds = activeAuctionPlayerIds(game).filter((playerId) => !auction.passed_player_ids.includes(playerId));
  if (!auction.high_bidder_id) {
    return unpassedPlayerIds.length === 0;
  }
  return unpassedPlayerIds.filter((playerId) => playerId !== auction.high_bidder_id).length === 0;
}

function activeAuctionSetEvent(game, actorPlayerId, auction) {
  return createAcceptedEvent(
    game,
    "ACTIVE_AUCTION_SET",
    auction
      ? {
          active: true,
          property_id: auction.property_id,
          high_bidder_id: auction.high_bidder_id,
          high_bid_amount: auction.high_bid_amount,
          passed_player_ids: auction.passed_player_ids,
        }
      : { active: false },
    actorPlayerId,
  );
}

function closeAuctionEvents(game, actorPlayerId) {
  const auction = game.active_auction;
  if (!auction) {
    return [];
  }

  const events = [];
  if (auction.high_bidder_id && Number.isInteger(auction.high_bid_amount)) {
    const winner = playerById(game, auction.high_bidder_id);
    if (winner) {
      winner.state = {
        ...winner.state,
        cash: (winner.state.cash ?? 0) - auction.high_bid_amount,
      };
      winner.updated_at = nowIso();
      setPropertyOwnership(game, auction.property_id, { owner_id: winner.id });
      events.push(
        createAcceptedEvent(
          game,
          "PLAYER_CASH_DELTA",
          { player_id: winner.id, amount: -auction.high_bid_amount },
          actorPlayerId,
        ),
      );
      events.push(
        createAcceptedEvent(
          game,
          "PROPERTY_OWNER_SET",
          { property_id: auction.property_id, owner_id: winner.id },
          actorPlayerId,
        ),
      );
      events.push(
        createAcceptedEvent(
          game,
          "AUCTION_RESULT",
          {
            property_id: auction.property_id,
            winner_id: winner.id,
            winning_bid: auction.high_bid_amount,
            passed_player_ids: auction.passed_player_ids,
          },
          actorPlayerId,
        ),
      );
    }
  } else {
    events.push(
      createAcceptedEvent(
        game,
        "AUCTION_RESULT",
        {
          property_id: auction.property_id,
          winner_id: null,
          winning_bid: null,
          passed_player_ids: auction.passed_player_ids,
        },
        actorPlayerId,
      ),
    );
  }

  game.active_auction = null;
  events.push(activeAuctionSetEvent(game, actorPlayerId, null));
  return events;
}

function acceptStartAuction(game, action) {
  const propertyId = action.payload.property_id;
  const ownership = propertyOwnership(game, propertyId);
  if (!propertyById(propertyId) || ownership?.owner_id !== null || game.active_auction) {
    return {
      statusCode: 422,
      payload: rejectAction(game, action, "illegal_action", "auction can only start for an unowned property", "payload.property_id"),
    };
  }
  game.active_auction = {
    property_id: propertyId,
    high_bidder_id: null,
    high_bid_amount: null,
    passed_player_ids: [],
  };
  return {
    statusCode: 200,
    payload: createAcceptedResponse(game, [activeAuctionSetEvent(game, action.actor_id, game.active_auction)]),
  };
}

function acceptBidAuction(game, action) {
  const auction = game.active_auction;
  const actor = playerById(game, action.actor_id);
  if (!auction || !actor) {
    return {
      statusCode: 422,
      payload: rejectAction(game, action, "illegal_action", "there is no active auction for this bidder", "type"),
    };
  }
  const amount = action.payload.amount;
  if (!Number.isInteger(amount)) {
    return {
      statusCode: 422,
      payload: rejectAction(game, action, "malformed_action", "auction bid amount must be an integer", "payload.amount"),
    };
  }
  if (amount <= (auction.high_bid_amount ?? 0)) {
    return {
      statusCode: 422,
      payload: rejectAction(game, action, "illegal_action", "auction bid must increase the current high bid", "payload.amount"),
    };
  }
  if ((actor.state.cash ?? 0) < amount) {
    return {
      statusCode: 422,
      payload: rejectAction(game, action, "illegal_action", "insufficient cash for auction bid", "payload.amount"),
    };
  }
  if (auction.passed_player_ids.includes(actor.id)) {
    return {
      statusCode: 422,
      payload: rejectAction(game, action, "illegal_action", "bidder has already passed this auction", "actor_id"),
    };
  }

  game.active_auction = {
    ...auction,
    high_bidder_id: actor.id,
    high_bid_amount: amount,
  };
  const events = [activeAuctionSetEvent(game, actor.id, game.active_auction)];
  if (shouldCloseAuction(game)) {
    events.push(...closeAuctionEvents(game, actor.id));
  }
  return {
    statusCode: 200,
    payload: createAcceptedResponse(game, events),
  };
}

function acceptPassAuction(game, action) {
  const auction = game.active_auction;
  const actor = playerById(game, action.actor_id);
  if (!auction || !actor) {
    return {
      statusCode: 422,
      payload: rejectAction(game, action, "illegal_action", "there is no active auction for this bidder", "type"),
    };
  }
  if (auction.passed_player_ids.includes(actor.id)) {
    return {
      statusCode: 422,
      payload: rejectAction(game, action, "illegal_action", "bidder has already passed this auction", "actor_id"),
    };
  }

  game.active_auction = {
    ...auction,
    passed_player_ids: [...auction.passed_player_ids, actor.id],
  };
  const events = [activeAuctionSetEvent(game, actor.id, game.active_auction)];
  if (shouldCloseAuction(game)) {
    events.push(...closeAuctionEvents(game, actor.id));
  }
  return {
    statusCode: 200,
    payload: createAcceptedResponse(game, events),
  };
}

function acceptAuctionAction(game, action) {
  if (action.type === "START_AUCTION") {
    return acceptStartAuction(game, action);
  }
  if (action.type === "BID_AUCTION") {
    return acceptBidAuction(game, action);
  }
  if (action.type === "PASS_AUCTION") {
    return acceptPassAuction(game, action);
  }
  return null;
}

function acceptBankInventory(game, actorPlayerId, houses, hotels) {
  game.bank_inventory = { houses, hotels };
  return createAcceptedEvent(game, "BANK_INVENTORY_SET", { houses, hotels }, actorPlayerId);
}

function acceptPropertyImprovements(game, actorPlayerId, propertyId, houses, hotel) {
  setPropertyOwnership(game, propertyId, { houses, hotel, hotels: hotel ? 1 : 0 });
  return createAcceptedEvent(
    game,
    "PROPERTY_IMPROVEMENTS_SET",
    { property_id: propertyId, houses, hotel },
    actorPlayerId,
  );
}

function acceptPropertyMortgage(game, actorPlayerId, propertyId, mortgaged) {
  setPropertyOwnership(game, propertyId, { mortgaged });
  return createAcceptedEvent(
    game,
    "PROPERTY_MORTGAGE_SET",
    { property_id: propertyId, mortgaged },
    actorPlayerId,
  );
}

function acceptBuyHouse(game, action) {
  const propertyId = action.payload.property_id;
  const ownership = propertyOwnership(game, propertyId);
  if (!ownership) {
    return createManagementAcceptedResponse(game, []);
  }
  const actorPlayerId = action.actor_id;
  const events = [];
  if (ownership.houses < 4) {
    events.push(acceptBankInventory(game, actorPlayerId, game.bank_inventory.houses - 1, game.bank_inventory.hotels));
    events.push(acceptPropertyImprovements(game, actorPlayerId, propertyId, ownership.houses + 1, false));
    return createManagementAcceptedResponse(game, events);
  }
  events.push(acceptBankInventory(game, actorPlayerId, game.bank_inventory.houses + 4, game.bank_inventory.hotels - 1));
  events.push(acceptPropertyImprovements(game, actorPlayerId, propertyId, 0, true));
  return createManagementAcceptedResponse(game, events);
}

function acceptSellHouse(game, action) {
  const propertyId = action.payload.property_id;
  const ownership = propertyOwnership(game, propertyId);
  if (!ownership) {
    return createManagementAcceptedResponse(game, []);
  }
  const actorPlayerId = action.actor_id;
  const events = [];
  if (ownership.hotel) {
    events.push(acceptBankInventory(game, actorPlayerId, game.bank_inventory.houses - 4, game.bank_inventory.hotels + 1));
    events.push(acceptPropertyImprovements(game, actorPlayerId, propertyId, 4, false));
    return createManagementAcceptedResponse(game, events);
  }
  events.push(acceptBankInventory(game, actorPlayerId, game.bank_inventory.houses + 1, game.bank_inventory.hotels));
  events.push(acceptPropertyImprovements(game, actorPlayerId, propertyId, Math.max(0, ownership.houses - 1), false));
  return createManagementAcceptedResponse(game, events);
}

function acceptManagementAction(game, action) {
  if (action.type === "BUY_HOUSE") {
    return acceptBuyHouse(game, action);
  }
  if (action.type === "SELL_HOUSE") {
    return acceptSellHouse(game, action);
  }
  if (action.type === "MORTGAGE_PROPERTY") {
    return createManagementAcceptedResponse(game, [
      acceptPropertyMortgage(game, action.actor_id, action.payload.property_id, true),
    ]);
  }
  if (action.type === "UNMORTGAGE_PROPERTY") {
    return createManagementAcceptedResponse(game, [
      acceptPropertyMortgage(game, action.actor_id, action.payload.property_id, false),
    ]);
  }
  return null;
}

function setMockPlayerPosition(gameId, seatOrder, position) {
  const game = games.get(gameId);
  if (!game) {
    return { state: "missing-game" };
  }
  const player = game.players.find((candidate) => candidate.seat_order === seatOrder);
  if (!player) {
    return { state: "missing-player" };
  }
  const updatedAt = nowIso();
  player.state = {
    ...player.state,
    position,
  };
  player.updated_at = updatedAt;
  game.updated_at = updatedAt;
  return { state: "updated", game };
}

const server = createServer(async (request, response) => {
  const url = new URL(request.url ?? "/", `http://${request.headers.host ?? "127.0.0.1"}`);

  if (request.method === "OPTIONS") {
    response.writeHead(204, corsHeaders);
    response.end();
    return;
  }

  if (request.method === "GET" && url.pathname === "/health") {
    json(response, 200, healthResponse());
    return;
  }

  if (request.method === "POST" && url.pathname === "/games") {
    try {
      const payload = await readBody(request);
      const errors = validateCreateGamePayload(payload);
      if (errors.length > 0) {
        json(response, 422, { detail: errors });
        return;
      }
      json(response, 201, createGame(payload));
      return;
    } catch {
      json(response, 400, { detail: [validationError("request body must be valid JSON")] });
      return;
    }
  }

  const positionMatch = url.pathname.match(/^\/__test\/games\/([^/]+)\/players\/(\d+)\/position$/);
  if (request.method === "POST" && positionMatch) {
    try {
      const payload = await readBody(request);
      const position = payload.position;
      if (!validateBoardPosition(position)) {
        json(response, 422, { detail: [validationError("position must be an integer from 0 through 39", "position")] });
        return;
      }

      const gameId = decodeURIComponent(positionMatch[1]);
      const seatOrder = Number.parseInt(positionMatch[2], 10);
      const result = setMockPlayerPosition(gameId, seatOrder, position);
      if (result.state === "missing-game") {
        json(response, 404, { error: "game not found" });
        return;
      }
      if (result.state === "missing-player") {
        json(response, 404, { error: "player not found" });
        return;
      }

      json(response, 200, result.game);
      return;
    } catch {
      json(response, 400, { detail: [validationError("request body must be valid JSON")] });
      return;
    }
  }

  const gameMatch = url.pathname.match(/^\/games\/([^/]+)(?:\/(state))?$/);
  if (request.method === "GET" && gameMatch) {
    const gameId = decodeURIComponent(gameMatch[1]);
    const game = games.get(gameId);
    if (!game) {
      json(response, 404, { error: "game not found" });
      return;
    }
    json(response, 200, gameMatch[2] === "state" ? gameState(game) : game);
    return;
  }

  const legalActionsMatch = url.pathname.match(/^\/games\/([^/]+)\/legal-actions$/);
  if (request.method === "GET" && legalActionsMatch) {
    const gameId = decodeURIComponent(legalActionsMatch[1]);
    const game = games.get(gameId);
    if (!game) {
      json(response, 404, { error: "game not found" });
      return;
    }
    const actorPlayerId = url.searchParams.get("actor_player_id");
    if (!actorPlayerId) {
      json(response, 422, { detail: [validationError("actor_player_id is required", "actor_player_id")] });
      return;
    }
    json(response, 200, {
      game_id: game.id,
      actor_player_id: actorPlayerId,
      legal_actions: legalActionsFor(game, actorPlayerId),
      state_hash: stateHash(game),
      event_sequence: game.event_sequence,
    });
    return;
  }

  const negotiationsMatch = url.pathname.match(/^\/games\/([^/]+)\/negotiations$/);
  if (negotiationsMatch) {
    const gameId = decodeURIComponent(negotiationsMatch[1]);
    const game = games.get(gameId);
    if (!game) {
      json(response, 404, { error: "game not found" });
      return;
    }

    if (request.method === "GET") {
      json(response, 200, { negotiations: game.negotiations });
      return;
    }

    if (request.method === "POST") {
      try {
        const payload = await readBody(request);
        const validation = validateCreateNegotiationPayload(game, payload);
        if (validation) {
          json(response, 422, validation);
          return;
        }
        const negotiation = createNegotiationRecord(game, {
          opened_by_player_id: payload.opened_by_player_id,
          participant_player_ids: [...new Set(payload.participant_player_ids)],
          topic: payload.topic.trim(),
          context: typeof payload.context === "string" ? payload.context.trim() : "",
        });
        json(response, 201, { status: "ok", negotiation });
        return;
      } catch {
        json(response, 400, negotiationValidationError("malformed_json", "request body must be valid JSON", "body"));
        return;
      }
    }
  }

  const messagesMatch = url.pathname.match(/^\/games\/([^/]+)\/negotiations\/([^/]+)\/messages$/);
  if (messagesMatch) {
    const gameId = decodeURIComponent(messagesMatch[1]);
    const negotiationId = decodeURIComponent(messagesMatch[2]);
    const game = games.get(gameId);
    if (!game) {
      json(response, 404, { error: "game not found" });
      return;
    }
    const negotiation = negotiationById(game, negotiationId);

    if (request.method === "GET") {
      if (!negotiation) {
        json(response, 404, { error: "negotiation not found" });
        return;
      }
      json(response, 200, { messages: game.negotiation_messages[negotiationId] ?? [] });
      return;
    }

    if (request.method === "POST") {
      try {
        const payload = await readBody(request);
        const validation = validateMessagePayload(game, negotiation, payload);
        if (validation) {
          json(response, 422, validation);
          return;
        }
        game.message_counter += 1;
        const message = {
          id: `${game.id}-message-${game.message_counter}`,
          game_id: game.id,
          negotiation_id: negotiation.id,
          author_player_id: payload.author_player_id,
          body: payload.body.trim(),
          created_at: nowIso(),
        };
        game.negotiation_messages[negotiation.id] = [...(game.negotiation_messages[negotiation.id] ?? []), message];
        negotiation.updated_at = message.created_at;
        game.updated_at = message.created_at;
        json(response, 201, { status: "ok", message });
        return;
      } catch {
        json(response, 400, negotiationValidationError("malformed_json", "request body must be valid JSON", "body"));
        return;
      }
    }
  }

  const dealsMatch = url.pathname.match(/^\/games\/([^/]+)\/deals$/);
  if (dealsMatch) {
    const gameId = decodeURIComponent(dealsMatch[1]);
    const game = games.get(gameId);
    if (!game) {
      json(response, 404, { error: "game not found" });
      return;
    }

    if (request.method === "GET") {
      const negotiationId = url.searchParams.get("negotiation_id");
      const deals = negotiationId ? game.deals.filter((deal) => deal.negotiation_id === negotiationId) : game.deals;
      json(response, 200, { deals });
      return;
    }

    if (request.method === "POST") {
      try {
        const payload = await readBody(request);
        const validation = validateDealPayload(game, payload);
        if (validation) {
          json(response, 422, validation);
          return;
        }
        const deal = createDealRecord(game, {
          negotiation_id: payload.negotiation_id,
          proposer_player_id: payload.proposer_player_id,
          participant_player_ids: [...new Set(payload.participant_player_ids)],
          parent_deal_id: payload.parent_deal_id ?? null,
          terms: payload.terms,
        });
        json(response, 201, { status: "ok", deal });
        return;
      } catch {
        json(response, 400, negotiationValidationError("malformed_json", "request body must be valid JSON", "body"));
        return;
      }
    }
  }

  const contractsMatch = url.pathname.match(/^\/games\/([^/]+)\/contracts$/);
  if (request.method === "GET" && contractsMatch) {
    const gameId = decodeURIComponent(contractsMatch[1]);
    const game = games.get(gameId);
    if (!game) {
      json(response, 404, { error: "game not found" });
      return;
    }
    json(response, 200, { contracts: game.contracts ?? [] });
    return;
  }

  const obligationsMatch = url.pathname.match(/^\/games\/([^/]+)\/obligations$/);
  if (request.method === "GET" && obligationsMatch) {
    const gameId = decodeURIComponent(obligationsMatch[1]);
    const game = games.get(gameId);
    if (!game) {
      json(response, 404, { error: "game not found" });
      return;
    }
    json(response, 200, { obligations: game.obligations ?? [] });
    return;
  }

  const contractOutcomesMatch = url.pathname.match(/^\/games\/([^/]+)\/contracts\/outcomes$/);
  if (request.method === "GET" && contractOutcomesMatch) {
    const gameId = decodeURIComponent(contractOutcomesMatch[1]);
    const game = games.get(gameId);
    if (!game) {
      json(response, 404, { error: "game not found" });
      return;
    }
    json(response, 200, { outcomes: game.contract_outcomes ?? [] });
    return;
  }

  const contractExplainMatch = url.pathname.match(/^\/games\/([^/]+)\/contracts\/([^/]+)\/explain$/);
  if (request.method === "GET" && contractExplainMatch) {
    const gameId = decodeURIComponent(contractExplainMatch[1]);
    const contractId = decodeURIComponent(contractExplainMatch[2]);
    const game = games.get(gameId);
    if (!game) {
      json(response, 404, { error: "game not found" });
      return;
    }
    const outcomes = (game.contract_outcomes ?? []).filter((outcome) => outcome.contract_id === contractId);
    if (outcomes.length === 0) {
      json(response, 404, { error: "contract not found" });
      return;
    }
    json(response, 200, { contract_id: contractId, outcomes });
    return;
  }

  const aiProfilesMatch = url.pathname.match(/^\/games\/([^/]+)\/ai\/profiles$/);
  if (request.method === "GET" && aiProfilesMatch) {
    const gameId = decodeURIComponent(aiProfilesMatch[1]);
    const game = games.get(gameId);
    if (!game) {
      json(response, 404, { error: "game not found" });
      return;
    }
    json(response, 200, { profiles: game.ai_profiles ?? [] });
    return;
  }

  const aiDecisionsMatch = url.pathname.match(/^\/games\/([^/]+)\/ai\/decisions$/);
  if (request.method === "GET" && aiDecisionsMatch) {
    const gameId = decodeURIComponent(aiDecisionsMatch[1]);
    const game = games.get(gameId);
    if (!game) {
      json(response, 404, { error: "game not found" });
      return;
    }
    json(response, 200, { decisions: game.ai_decisions ?? [] });
    return;
  }

  const aiSelfDialogueMatch = url.pathname.match(/^\/games\/([^/]+)\/ai\/self-dialogue$/);
  if (request.method === "GET" && aiSelfDialogueMatch) {
    const gameId = decodeURIComponent(aiSelfDialogueMatch[1]);
    const game = games.get(gameId);
    if (!game) {
      json(response, 404, { error: "game not found" });
      return;
    }
    json(response, 200, { self_dialogue: game.ai_self_dialogue ?? [] });
    return;
  }

  const aiMemoryMatch = url.pathname.match(/^\/games\/([^/]+)\/ai\/memory$/);
  if (request.method === "GET" && aiMemoryMatch) {
    const gameId = decodeURIComponent(aiMemoryMatch[1]);
    const game = games.get(gameId);
    if (!game) {
      json(response, 404, { error: "game not found" });
      return;
    }
    json(response, 200, { memory_entries: game.ai_memory_entries ?? [] });
    return;
  }

  const aiRetrievalRecordsMatch = url.pathname.match(/^\/games\/([^/]+)\/ai\/retrieval-records$/);
  if (request.method === "GET" && aiRetrievalRecordsMatch) {
    const gameId = decodeURIComponent(aiRetrievalRecordsMatch[1]);
    const game = games.get(gameId);
    if (!game) {
      json(response, 404, { error: "game not found" });
      return;
    }
    json(response, 200, { retrieval_records: game.ai_retrieval_records ?? [] });
    return;
  }

  const aiRejectedOutputsMatch = url.pathname.match(/^\/games\/([^/]+)\/ai\/rejected-outputs$/);
  if (request.method === "GET" && aiRejectedOutputsMatch) {
    const gameId = decodeURIComponent(aiRejectedOutputsMatch[1]);
    const game = games.get(gameId);
    if (!game) {
      json(response, 404, { error: "game not found" });
      return;
    }
    json(response, 200, { rejected_outputs: game.ai_rejected_outputs ?? [] });
    return;
  }

  const acceptDealMatch = url.pathname.match(/^\/games\/([^/]+)\/deals\/([^/]+)\/accept$/);
  if (request.method === "POST" && acceptDealMatch) {
    const gameId = decodeURIComponent(acceptDealMatch[1]);
    const dealId = decodeURIComponent(acceptDealMatch[2]);
    const game = games.get(gameId);
    if (!game) {
      json(response, 404, { error: "game not found" });
      return;
    }
    const result = validateDealMutation(game, dealId, "accept");
    if (result.error) {
      json(response, 409, result.error);
      return;
    }
    json(response, 200, { status: "ok", deal: acceptDealRecord(game, result.deal) });
    return;
  }

  const rejectDealMatch = url.pathname.match(/^\/games\/([^/]+)\/deals\/([^/]+)\/reject$/);
  if (request.method === "POST" && rejectDealMatch) {
    const gameId = decodeURIComponent(rejectDealMatch[1]);
    const dealId = decodeURIComponent(rejectDealMatch[2]);
    const game = games.get(gameId);
    if (!game) {
      json(response, 404, { error: "game not found" });
      return;
    }
    const result = validateDealMutation(game, dealId, "reject");
    if (result.error) {
      json(response, 409, result.error);
      return;
    }
    json(response, 200, { status: "ok", deal: rejectDealRecord(game, result.deal) });
    return;
  }

  const expireNegotiationMatch = url.pathname.match(/^\/games\/([^/]+)\/negotiations\/([^/]+)\/expire$/);
  if (request.method === "POST" && expireNegotiationMatch) {
    const gameId = decodeURIComponent(expireNegotiationMatch[1]);
    const negotiationId = decodeURIComponent(expireNegotiationMatch[2]);
    const game = games.get(gameId);
    if (!game) {
      json(response, 404, { error: "game not found" });
      return;
    }
    const negotiation = negotiationById(game, negotiationId);
    const validation = validateOpenNegotiation(negotiation);
    if (validation) {
      json(response, 409, validation);
      return;
    }
    json(response, 200, { status: "ok", negotiation: expireNegotiationRecord(game, negotiation) });
    return;
  }

  const aiStepMatch = url.pathname.endsWith(aiStepPathSuffix)
    ? url.pathname.slice(0, -aiStepPathSuffix.length).match(/^\/games\/([^/]+)$/)
    : null;
  if (request.method === "POST" && aiStepMatch) {
    const gameId = decodeURIComponent(aiStepMatch[1]);
    const game = games.get(gameId);
    if (!game) {
      json(response, 404, { error: "game not found" });
      return;
    }
    try {
      const payload = await readBody(request);
      const result = applyMockAiStep(game, payload);
      json(response, result.statusCode, result.body);
      return;
    } catch {
      json(response, 400, {
        status: "rejected",
        reason_code: "malformed_json",
        validation_errors: [aiValidationError("malformed_json", "request body must be valid JSON", "body")],
      });
      return;
    }
  }

  const actionsMatch = url.pathname.match(/^\/games\/([^/]+)\/actions$/);
  if (request.method === "POST" && actionsMatch) {
    const gameId = decodeURIComponent(actionsMatch[1]);
    const game = games.get(gameId);
    if (!game) {
      json(response, 404, { error: "game not found" });
      return;
    }
    const idempotencyKey = request.headers["idempotency-key"];
    if (typeof idempotencyKey !== "string" || idempotencyKey.trim().length === 0) {
      json(response, 400, {
        status: "rejected",
        reason_code: "missing_idempotency_key",
        validation_errors: [
          {
            code: "missing_idempotency_key",
            message: "POST /games/{game_id}/actions requires an Idempotency-Key header",
            field: "Idempotency-Key",
          },
        ],
      });
      return;
    }

    try {
      const action = await readBody(request);
      if (!isObject(action)) {
        json(response, 422, {
          status: "rejected",
          reason_code: "malformed_action",
          validation_errors: [
            {
              code: "malformed_action",
              message: "request body must be a JSON object",
              field: "body",
            },
          ],
          submitted_action: action,
        });
        return;
      }

      if (game.seed.startsWith("stage-5-turn-controls-reject")) {
        json(
          response,
          409,
          rejectAction(
            game,
            action,
            "stale_action",
            "action expected state no longer matches current state",
            "expected_state_hash",
          ),
        );
        return;
      }

      const isCurrentState =
        action.expected_state_hash === stateHash(game) && action.expected_event_sequence === game.event_sequence;
      if (!isCurrentState) {
        json(
          response,
          409,
          rejectAction(
            game,
            action,
            "stale_action",
            "action expected state no longer matches current state",
            "expected_state_hash",
          ),
        );
        return;
      }

      const legalActions = legalActionsFor(game, action.actor_id);
      if (!legalActions.some((candidate) => actionMatchesLegalAction(candidate, action))) {
        json(response, 422, rejectAction(game, action, "illegal_action", `${action.type} is not currently legal`, "type"));
        return;
      }

      if (game.seed.startsWith("stage-5-property-management-reject") && action.type === "BUY_HOUSE") {
        json(
          response,
          409,
          rejectAction(
            game,
            action,
            "even_building_rule",
            "building must follow the even building rule",
            "payload.property_id",
          ),
        );
        return;
      }

      if (action.type === "ROLL_DICE") {
        json(response, 200, acceptRollDice(game, action));
        return;
      }

      const auctionResponse = acceptAuctionAction(game, action);
      if (auctionResponse) {
        json(response, auctionResponse.statusCode, auctionResponse.payload);
        return;
      }

      const managementResponse = acceptManagementAction(game, action);
      if (managementResponse) {
        json(response, 200, managementResponse);
        return;
      }

      json(response, 200, {
        status: "accepted",
        game_id: game.id,
        accepted_events: [],
        state: gameState(game).state,
        state_hash: stateHash(game),
        event_sequence: game.event_sequence,
      });
      return;
    } catch {
      json(response, 400, { detail: [validationError("request body must be valid JSON")] });
      return;
    }
  }

  const eventsMatch = url.pathname.match(/^\/games\/([^/]+)\/events$/);
  if (request.method === "GET" && eventsMatch) {
    const gameId = decodeURIComponent(eventsMatch[1]);
    const game = games.get(gameId);
    if (!game) {
      json(response, 404, { error: "game not found" });
      return;
    }
    json(response, 200, { events: game.events });
    return;
  }

  const streamMatch = url.pathname.match(/^\/games\/([^/]+)\/events\/stream$/);
  if (request.method === "GET" && streamMatch) {
    const gameId = decodeURIComponent(streamMatch[1]);
    const game = games.get(gameId);
    if (!game) {
      json(response, 404, { error: "game not found" });
      return;
    }
    response.writeHead(200, {
      ...corsHeaders,
      "cache-control": "no-store",
      "connection": "keep-alive",
      "content-type": "text/event-stream",
    });
    response.write(": connected\n\n");
    game.stream_clients.add(response);
    for (const event of game.events) {
      writeSse(response, event);
    }
    setTimeout(() => {
      if (game.stream_clients.has(response) && !response.destroyed) {
        writeSse(response, {
          sequence: game.event_sequence,
          event_type: "STREAM_CONNECTED",
          payload: { game_id: game.id },
        });
      }
    }, 100);
    request.on("close", () => {
      game.stream_clients.delete(response);
    });
    return;
  }

  const rejectedActionsMatch = url.pathname.match(/^\/games\/([^/]+)\/rejected-actions$/);
  if (request.method === "GET" && rejectedActionsMatch) {
    const gameId = decodeURIComponent(rejectedActionsMatch[1]);
    const game = games.get(gameId);
    if (!game) {
      json(response, 404, { error: "game not found" });
      return;
    }
    const actorPlayerId = url.searchParams.get("actor_player_id");
    const records = actorPlayerId
      ? game.rejected_actions.filter((record) => record.actor_player_id === actorPlayerId)
      : game.rejected_actions;
    json(response, 200, { rejected_actions: records });
    return;
  }

  json(response, 404, { error: "not found" });
});

server.listen(port, "127.0.0.1");

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => {
    server.close(() => process.exit(0));
  });
}
