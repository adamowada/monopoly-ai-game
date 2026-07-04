import { createServer } from "node:http";

const port = Number.parseInt(process.env.MOCK_API_PORT ?? "18101", 10);
const games = new Map();
let gameCounter = 0;

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
  games.set(id, game);
  return game;
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

function legalAction(game, type, payload = {}) {
  const actor = activePlayer(game);
  return {
    actor_id: actor?.id ?? "",
    type,
    payload,
    expected_state_hash: stateHash(game),
    expected_event_sequence: game.event_sequence,
    description: null,
    schema: {},
  };
}

function legalActionsFor(game, actorPlayerId) {
  const actor = activePlayer(game);
  if (!actor || actor.id !== actorPlayerId) {
    return [];
  }
  if (game.current_phase === "PURCHASE_OR_AUCTION") {
    return [
      legalAction(game, "BUY_PROPERTY", { property_id: "property_chance_7" }),
      legalAction(game, "START_AUCTION", { property_id: "property_chance_7" }),
    ];
  }
  return [legalAction(game, "ROLL_DICE")];
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
  const toPosition = 7;
  const rolled = createAcceptedEvent(game, "DICE_ROLLED", { dice: [3, 4], total: 7 }, actor?.id ?? null);

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

      const legalTypes = new Set(legalActionsFor(game, action.actor_id).map((candidate) => candidate.type));
      if (!legalTypes.has(action.type)) {
        json(response, 422, rejectAction(game, action, "illegal_action", `${action.type} is not currently legal`, "type"));
        return;
      }

      if (action.type === "ROLL_DICE") {
        json(response, 200, acceptRollDice(game, action));
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
