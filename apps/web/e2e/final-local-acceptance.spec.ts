import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

type FinalPlayer = {
  name: string;
  kind: "human" | "ai";
  color: string;
};

type GameMetadata = {
  id: string;
  players: Array<{ id: string; name: string; controller_type: "human" | "ai" }>;
};

type LegalAction = {
  actor_id: string;
  type: string;
  payload: Record<string, unknown>;
  expected_state_hash: string;
  expected_event_sequence: number;
};

const apiBaseUrl = process.env.PLAYWRIGHT_API_BASE_URL ?? "http://localhost:8000";

test.describe("final-local-acceptance", () => {
  test.skip(process.env.PLAYWRIGHT_FINAL_LOCAL_ACCEPTANCE !== "1", "final local acceptance runs only against docker compose");
  test.setTimeout(420_000);

  test("Create game Roll dice Step AI Start negotiation Propose deal Enforce obligation Rejected action AI audit", async ({
    page,
    request,
  }) => {
    const mixedGameId = await createGame(page, "final-local-acceptance-mixed", [
      { name: "Ada", kind: "human", color: "#0f766e" },
      { name: "Grace", kind: "ai", color: "#7c3aed" },
      { name: "Linus", kind: "human", color: "#2563eb" },
      { name: "Marie", kind: "ai", color: "#dc2626" },
      { name: "Nia", kind: "ai", color: "#ca8a04" },
    ]);

    await expect(page.getByRole("region", { name: "Classic Monopoly-style board" })).toBeVisible();
    await expect(page.getByRole("region", { name: "Players" })).toContainText("Ada");
    await expect(page.getByRole("region", { name: "Players" })).toContainText("Grace");
    await expect(page.getByRole("region", { name: "Players" })).toContainText("ai");

    const staleRollAction = await completeHumanTurnAndCaptureRoll(page, mixedGameId);
    await rejectStaleAction(request, mixedGameId, staleRollAction);
    await page.reload();
    await expect(page.getByRole("alert", { name: "Rejected action" })).toContainText("stale_action");

    await expect(page.getByRole("region", { name: "Active player" })).toContainText("Grace");
    const aiResponse = page.waitForResponse(
      (response) => response.url().includes(`/games/${mixedGameId}/ai/step`) && response.request().method() === "POST",
      { timeout: 360_000 },
    );
    await page.getByRole("region", { name: "Turn controls" }).getByRole("button", { name: "Step AI" }).click();
    const aiBody = (await (await aiResponse).json()) as { status: string; accepted_events?: unknown[] };
    expect(aiBody.status).toBe("accepted");
    expect(aiBody.accepted_events?.length ?? 0).toBeGreaterThan(0);
    await expect(page.getByRole("status", { name: "AI step status" })).toContainText("AI done", { timeout: 360_000 });

    const aiAudit = page.getByRole("region", { name: "AI audit" });
    await expect(aiAudit).toContainText("Decision history");
    await expect(aiAudit).toContainText("Self-dialogue timeline");
    await expect(aiAudit).toContainText("Memory entries");
    await expect(aiAudit).toContainText("Raw output");
    await expect(aiAudit).toContainText("Parsed output");

    const contractGameId = await createGame(page, "final-local-acceptance-contract", [
      { name: "Ada", kind: "human", color: "#0f766e" },
      { name: "Grace", kind: "human", color: "#7c3aed" },
    ]);
    const contractGame = await readGame(request, contractGameId);

    await page.getByLabel("Negotiation topic").fill("Final Local Acceptance structured package");
    await page.getByLabel("Negotiation context").fill("Verify complex negotiations and exact acceptance.");
    await page.getByRole("button", { name: "Start negotiation" }).click();
    await expect(page.getByRole("region", { name: "Negotiation thread" })).toContainText(
      "Final Local Acceptance structured package",
    );

    await page.getByRole("button", { name: "Add sample complex instruments" }).click();
    const contractPreview = page.getByRole("region", { name: "Contract preview" });
    await expect(contractPreview).toContainText("Complex instruments");
    await expect(contractPreview).toContainText("6 terms");
    await expect(page.getByRole("button", { name: "Propose deal" })).toBeEnabled();
    await page.getByRole("button", { name: "Propose deal" }).click();
    const deal = page.getByRole("region", { name: "Deal v1" });
    await expect(deal).toContainText("Proposed");

    await acceptCurrentDeal(page, contractGameId, "proposed");
    await page.reload();
    await expect(page.getByRole("region", { name: "Deal v1" })).toContainText("Proposed");
    await acceptCurrentDeal(page, contractGameId, "accepted");
    await expect(page.getByRole("region", { name: "Deal v1" })).toContainText("Accepted");

    const acceptedDealId = await latestAcceptedDealId(request, contractGameId);
    await createContractFromDeal(request, contractGameId, acceptedDealId);
    await page.reload();
    await expect(page.getByRole("region", { name: "Contracts obligations panel" })).toContainText("Active contracts");
    await expect(page.getByRole("region", { name: "Contracts obligations panel" })).toContainText("pending");
    await page.getByRole("button", { name: "Enforce obligation" }).first().click();
    await expect(page.getByRole("status", { name: "Contract enforcement status" })).toContainText(
      "Contract enforcement settled",
    );
    await expect(page.getByRole("region", { name: "Game log" })).toContainText("PLAYER_CASH_DELTA");

    await page.getByLabel("Negotiation topic").fill("Final Local Acceptance cutoff expiration");
    await page.getByLabel("Negotiation context").fill("Verify deterministic cutoff closes without a substitute action.");
    await page.getByRole("button", { name: "Start negotiation" }).click();
    await expect(page.getByRole("region", { name: "Negotiation thread" })).toContainText(
      "Final Local Acceptance cutoff expiration",
    );
    await page.getByRole("button", { name: "Expire negotiation" }).click();
    await expect(page.getByRole("region", { name: "Negotiation thread" })).toContainText("Expired");
    await expect(page.getByRole("region", { name: "Negotiation thread" })).toContainText(
      "Expired negotiation is visibly closed",
    );

    expect(contractGame.players).toHaveLength(2);
  });
});

async function createGame(page: Page, seed: string, players: FinalPlayer[]): Promise<string> {
  await page.goto("/");

  for (let index = 2; index < players.length; index += 1) {
    await page.getByRole("button", { name: "Add player" }).click();
  }

  await page.getByRole("textbox", { name: "Seed" }).fill(seed);
  await page.getByLabel("Max negotiation rounds").fill("2");
  await page.getByLabel("Proposal limit per player").fill("2");
  for (const [index, player] of players.entries()) {
    const playerNumber = index + 1;
    await page.getByRole("textbox", { name: `Player ${playerNumber} name` }).fill(player.name);
    await page.getByRole("combobox", { name: `Player ${playerNumber} type` }).selectOption(player.kind);
    await page.getByRole("textbox", { name: `Player ${playerNumber} color hex` }).fill(player.color);
  }

  await Promise.all([
    page.waitForURL(/\/games\/[0-9a-f-]{36}$/),
    page.getByRole("button", { name: "Create game" }).click(),
  ]);
  await expect(page.getByRole("region", { name: "Classic Monopoly-style board" })).toBeVisible({ timeout: 30_000 });
  return page.url().split("/").pop() ?? "";
}

async function completeHumanTurnAndCaptureRoll(page: Page, gameId: string): Promise<LegalAction> {
  const controls = page.getByRole("region", { name: "Turn controls" });
  await expect(page.getByRole("region", { name: "Active player" })).toContainText("Ada");
  const rollRequest = page.waitForRequest(
    (request) => request.url().endsWith(`/games/${gameId}/actions`) && request.method() === "POST",
  );
  await controls.getByRole("button", { name: "Roll dice" }).click();
  const staleRollAction = (await rollRequest).postDataJSON() as LegalAction;

  await clickIfEnabled(controls, "Buy property");
  await clickIfEnabled(controls, "Settle debt");
  await expect(controls.getByRole("button", { name: "End turn" })).toBeEnabled();
  await controls.getByRole("button", { name: "End turn" }).click();
  return staleRollAction;
}

async function clickIfEnabled(scope: ReturnType<Page["getByRole"]>, name: string): Promise<boolean> {
  const button = scope.getByRole("button", { name });
  if ((await button.count()) === 0) {
    return false;
  }
  const first = button.first();
  if ((await first.isVisible()) && (await first.isEnabled())) {
    await first.click();
    return true;
  }
  return false;
}

async function acceptCurrentDeal(page: Page, gameId: string, expectedStatus: "proposed" | "accepted"): Promise<void> {
  const acceptResponse = page.waitForResponse(
    (response) =>
      response.url().includes(`/games/${gameId}/deals/`) &&
      response.url().endsWith("/accept") &&
      response.request().method() === "POST",
  );
  await page.getByRole("region", { name: "Deal v1" }).getByRole("button", { name: "Accept" }).click();
  const response = await acceptResponse;
  expect(response.ok()).toBeTruthy();
  const body = (await response.json()) as { status: string };
  expect(body.status).toBe(expectedStatus);
}

async function rejectStaleAction(request: APIRequestContext, gameId: string, staleAction: LegalAction): Promise<void> {
  const response = await request.post(`${apiBaseUrl}/games/${gameId}/actions`, {
    data: staleAction,
    headers: {
      "Idempotency-Key": `final-local-acceptance-stale-${Date.now()}`,
    },
  });
  expect(response.status()).toBe(409);
  const body = (await response.json()) as { status: string; reason_code: string };
  expect(body.status).toBe("rejected");
  expect(body.reason_code).toBe("stale_action");
}

async function readGame(request: APIRequestContext, gameId: string): Promise<GameMetadata> {
  const response = await request.get(`${apiBaseUrl}/games/${gameId}`);
  expect(response.ok()).toBeTruthy();
  return (await response.json()) as GameMetadata;
}

async function latestAcceptedDealId(request: APIRequestContext, gameId: string): Promise<string> {
  const response = await request.get(`${apiBaseUrl}/games/${gameId}/deals`);
  expect(response.ok()).toBeTruthy();
  const body = (await response.json()) as { deals: Array<{ id: string; status: string }> };
  const accepted = body.deals.find((candidate) => candidate.status === "accepted");
  expect(accepted?.id).toBeTruthy();
  return accepted?.id ?? "";
}

async function createContractFromDeal(request: APIRequestContext, gameId: string, dealId: string): Promise<void> {
  const response = await request.post(`${apiBaseUrl}/games/${gameId}/contracts/from-deal`, {
    data: { deal_id: dealId },
  });
  expect([200, 201]).toContain(response.status());
  const body = (await response.json()) as { contract?: { id: string }; obligations?: unknown[] };
  expect(body.contract?.id).toBeTruthy();
  expect(body.obligations?.length ?? 0).toBeGreaterThan(0);
}
