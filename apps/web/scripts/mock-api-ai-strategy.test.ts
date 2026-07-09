import { createServer } from "node:net";
import { spawn, type ChildProcess } from "node:child_process";
import { once } from "node:events";
import { resolve } from "node:path";

import { afterEach, describe, expect, it } from "vitest";

type MockGame = {
  id: string;
  players: Array<{ id: string; name: string }>;
};

type AiStepPayload = {
  accepted_events: Array<{
    event_type: string;
    payload: Record<string, unknown>;
  }>;
  status: string;
};

let mockApiProcess: ChildProcess | null = null;
let mockApiOutput = "";

async function allocatePort(): Promise<number> {
  const server = createServer();
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const address = server.address();
  if (address === null || typeof address === "string") {
    throw new Error("mock API test could not allocate a local TCP port");
  }
  const port = address.port;
  server.close();
  await once(server, "close");
  return port;
}

async function waitForMockApi(baseUrl: string) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < 10_000) {
    try {
      const response = await fetch(`${baseUrl}/health`);
      if (response.ok) {
        return;
      }
    } catch {
      // The child process may still be binding the port.
    }
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error(`mock API did not become healthy:\n${mockApiOutput}`);
}

async function startMockApi(): Promise<string> {
  const port = await allocatePort();
  const scriptPath = resolve(process.cwd(), "scripts/mock-api.mjs");
  mockApiOutput = "";
  const child = spawn(process.execPath, [scriptPath], {
    env: { ...process.env, MOCK_API_PORT: String(port) },
    stdio: ["ignore", "pipe", "pipe"],
  });
  mockApiProcess = child;
  child.stdout?.on("data", (chunk) => {
    mockApiOutput += String(chunk);
  });
  child.stderr?.on("data", (chunk) => {
    mockApiOutput += String(chunk);
  });
  const baseUrl = `http://127.0.0.1:${port}`;
  await waitForMockApi(baseUrl);
  return baseUrl;
}

async function stopMockApi() {
  const child = mockApiProcess;
  mockApiProcess = null;
  if (!child || child.exitCode !== null) {
    return;
  }
  child.kill("SIGTERM");
  const exited = await Promise.race([
    once(child, "exit").then(() => true),
    new Promise<boolean>((resolve) => setTimeout(() => resolve(false), 2_000)),
  ]);
  if (!exited && child.exitCode === null) {
    child.kill("SIGKILL");
    await once(child, "exit");
  }
}

async function postJson<T>(baseUrl: string, path: string, payload: unknown): Promise<T> {
  const response = await fetch(`${baseUrl}${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  const body = (await response.json()) as T;
  expect(response.ok, JSON.stringify(body)).toBe(true);
  return body;
}

async function getJson<T>(baseUrl: string, path: string): Promise<T> {
  const response = await fetch(`${baseUrl}${path}`);
  const body = (await response.json()) as T;
  expect(response.ok, JSON.stringify(body)).toBe(true);
  return body;
}

function createGame(baseUrl: string): Promise<MockGame> {
  return postJson<MockGame>(baseUrl, "/games", {
    seed: "stage-10-5-two-human-full-round-ai-development",
    players: [
      { name: "Ada", kind: "ai" },
      { name: "Grace", kind: "ai" },
    ],
    settings: {
      player_colors: [
        { seat_order: 0, color: "#0f766e" },
        { seat_order: 1, color: "#7c3aed" },
      ],
      negotiation_cutoffs: {
        max_rounds: 8,
        max_proposals_per_player: 12,
      },
    },
  });
}

function stepAi(baseUrl: string, gameId: string, playerId: string): Promise<AiStepPayload> {
  return postJson<AiStepPayload>(baseUrl, `/games/${gameId}/ai/step`, {
    player_id: playerId,
    decision_type: "action_decision",
    mandatory: true,
    mode: "auto",
  });
}

afterEach(async () => {
  await stopMockApi();
});

describe("mock API AI strategy", () => {
  it("develops a complete color group before rolling a later AI turn and pays for the build", async () => {
    const baseUrl = await startMockApi();
    const game = await createGame(baseUrl);
    const ada = game.players[0];
    const grace = game.players[1];

    await stepAi(baseUrl, game.id, ada.id);
    const buyStep = await stepAi(baseUrl, game.id, ada.id);
    expect(buyStep.accepted_events.map((event) => event.event_type)).toContain("PROPERTY_OWNER_SET");
    await stepAi(baseUrl, game.id, ada.id);
    await stepAi(baseUrl, game.id, grace.id);
    await stepAi(baseUrl, game.id, grace.id);
    await stepAi(baseUrl, game.id, grace.id);

    const stateBeforeDevelopment = await getJson<{
      state: { turn: { current_player_id: string; phase: string } };
    }>(baseUrl, `/games/${game.id}/state`);
    expect(stateBeforeDevelopment.state.turn).toMatchObject({
      current_player_id: ada.id,
      phase: "START_TURN",
    });

    const developmentStep = await stepAi(baseUrl, game.id, ada.id);

    expect(developmentStep.status).toBe("accepted");
    expect(developmentStep.accepted_events).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          event_type: "PLAYER_CASH_DELTA",
          payload: expect.objectContaining({
            player_id: ada.id,
            amount: -50,
          }),
        }),
        expect.objectContaining({
          event_type: "PROPERTY_IMPROVEMENTS_SET",
          payload: expect.objectContaining({
            property_id: "property_mediterranean_avenue",
            houses: 1,
            hotel: false,
          }),
        }),
      ]),
    );
    expect(developmentStep.accepted_events.map((event) => event.event_type)).not.toContain("DICE_ROLLED");

    const stateAfterDevelopment = await getJson<{
      state: { players: Array<{ cash: number; id: string }> };
    }>(baseUrl, `/games/${game.id}/state`);
    expect(stateAfterDevelopment.state.players.find((player) => player.id === ada.id)?.cash).toBe(1392);
  });
});
