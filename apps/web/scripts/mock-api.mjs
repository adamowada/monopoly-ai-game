import { createServer } from "node:http";

const port = Number.parseInt(process.env.MOCK_API_PORT ?? "18101", 10);
const games = new Map();
let gameCounter = 0;

const corsHeaders = {
  "access-control-allow-headers": "accept, content-type",
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
      players: game.players.map((player) => player.state),
      turn: {
        phase: game.current_phase,
        current_player_index: 0,
        current_player_id: game.players[0]?.id ?? null,
      },
    },
    state_hash: `mock-state-${game.id}`,
    event_sequence: 0,
  };
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

  json(response, 404, { error: "not found" });
});

server.listen(port, "127.0.0.1");

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => {
    server.close(() => process.exit(0));
  });
}
