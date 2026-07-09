import { expect, test, type Page } from "@playwright/test";

const mockApiPort = process.env.MOCK_API_PORT ?? "18101";
const mockApiBaseUrl = `http://127.0.0.1:${mockApiPort}`;

async function createAiFirstGame(page: Page, seed: string) {
  await page.goto("/");

  await page.getByRole("textbox", { name: "Seed" }).fill(seed);
  await page.getByRole("textbox", { name: "Player 1 name" }).fill("Grace");
  await page.getByRole("combobox", { name: "Player 1 type" }).selectOption("ai");
  await page.getByRole("textbox", { name: "Player 2 name" }).fill("Ada");
  await page.getByRole("textbox", { name: "Player 1 color hex" }).fill("#7c3aed");
  await page.getByRole("textbox", { name: "Player 2 color hex" }).fill("#0f766e");
  await page.getByRole("button", { name: "Create game" }).click();

  await expect(page).toHaveURL(/\/games\/mock-game-\d+$/);
  await expect(page.getByRole("region", { name: "Active player" })).toContainText("Grace");
}

async function createAuctionGameWithAiBidder(page: Page) {
  await page.goto("/");

  await page.getByRole("textbox", { name: "Seed" }).fill("stage-7-6-ai-auction-step");
  await page.getByRole("textbox", { name: "Player 1 name" }).fill("Ada");
  await page.getByRole("textbox", { name: "Player 2 name" }).fill("Grace");
  await page.getByRole("combobox", { name: "Player 2 type" }).selectOption("ai");
  await page.getByRole("textbox", { name: "Player 1 color hex" }).fill("#0f766e");
  await page.getByRole("textbox", { name: "Player 2 color hex" }).fill("#7c3aed");
  await page.getByRole("button", { name: "Create game" }).click();

  await expect(page).toHaveURL(/\/games\/mock-game-\d+$/);
  const gameId = page.url().split("/").pop() ?? "";
  const game = await readMockJson<{
    players: Array<{ id: string; name: string; controller_type: "human" | "ai" }>;
  }>(page, `/games/${gameId}`);
  const graceId = game.players.find((player) => player.name === "Grace" && player.controller_type === "ai")?.id;
  expect(graceId).toBeTruthy();

  const rollResponse = page.waitForResponse(
    (response) => response.url().endsWith(`/games/${gameId}/actions`) && response.request().method() === "POST",
  );
  await page.getByRole("region", { name: "Turn controls" }).getByRole("button", { name: "Roll dice" }).click();
  const rollBody = (await (await rollResponse).json()) as {
    status: string;
    accepted_events?: Array<{ event_type: string }>;
  };
  expect(rollBody.status).toBe("accepted");
  expect(rollBody.accepted_events?.map((event) => event.event_type)).toEqual(["DICE_ROLLED", "PLAYER_POSITION_SET"]);

  const state = await readMockJson<{
    state: { turn: { phase: string }; players: Array<{ id: string; position: number }> };
  }>(page, `/games/${gameId}/state`);
  expect(state.state.turn.phase).toBe("PURCHASE_OR_AUCTION");
  expect(state.state.players.every((player) => player.position === 0)).toBe(false);

  const legalActions = await readMockJson<{ legal_actions: Array<{ type: string }> }>(
    page,
    `/games/${gameId}/legal-actions?actor_player_id=${game.players[0]?.id ?? ""}`,
  );
  expect(legalActions.legal_actions.map((action) => action.type)).toContain("START_AUCTION");

  const auction = page.getByRole("region", { name: "Auction", exact: true });
  await auction.getByRole("button", { name: "Start auction" }).click();
  await expect(auction).toContainText(/Auction state\s*Active/);

  return { auction, gameId, graceId: graceId ?? "" };
}

async function readMockJson<T>(page: Page, path: string): Promise<T> {
  const response = await page.request.get(`${mockApiBaseUrl}${path}`);
  expect(response.ok()).toBeTruthy();
  return (await response.json()) as T;
}

test("mixed human/AI game progresses through the AI step path and stalls remain visible/auditable", async ({ page }) => {
  // mixed human/AI game progresses; stalls visible and auditable
  await createAiFirstGame(page, "stage-7-6-ai-step-mixed");

  const controls = page.getByRole("region", { name: "Turn controls" });
  const aiStepRequest = page.waitForRequest((request) => request.url().includes("/ai/step") && request.method() === "POST");
  await controls.getByRole("button", { name: "Step AI" }).click();
  expect((await aiStepRequest).postDataJSON()).toMatchObject({
    decision_type: "action_decision",
    mandatory: true,
    request_context: { mode: "manual" },
  });

  await expect(page.getByRole("status", { name: "AI step status" })).toContainText("AI done");
  await page.getByRole("tab", { name: "Contracts" }).click();
  await expect(page.getByRole("region", { name: "Game log" })).toContainText("DICE_ROLLED");

  await createAiFirstGame(page, "stage-7-6-ai-blocked");
  await page.getByRole("region", { name: "Turn controls" }).getByRole("button", { name: "Step AI" }).click();
  await expect(page.getByRole("status", { name: "AI step status" })).toContainText("AI blocked");
  await expect(page.getByRole("alert", { name: "Rejected action" })).toContainText("codex_exec_timeout");
});

test("Mock AI step decisions satisfy AI audit schema", async ({ page }) => {
  await createAiFirstGame(page, "stage-7-6-ai-step-audit-schema");

  const controls = page.getByRole("region", { name: "Turn controls" });
  const aiStepResponse = page.waitForResponse(
    (response) => response.url().includes("/ai/step") && response.request().method() === "POST",
  );
  await controls.getByRole("button", { name: "Step AI" }).click();
  const aiStepBody = (await (await aiStepResponse).json()) as { ai_decision_id?: string };

  expect(aiStepBody.ai_decision_id).toMatch(/^mock-game-\d+-ai-decision-\d+$/);
  await expect(page.getByRole("status", { name: "AI step status" })).toContainText("AI done");

  await page.reload();
  await page.getByRole("tab", { name: "AI notebook" }).click();

  const panel = page.getByRole("region", { name: "AI audit" });
  await expect(panel).toContainText("Decision history");
  await panel.getByRole("button", { name: "Show AI technical trace" }).first().click();
  await expect(panel).toContainText(`ai_decision_id ${aiStepBody.ai_decision_id}`);
});

test("Mock AI auction steps choose auction actions", async ({ page }) => {
  const { auction, gameId, graceId } = await createAuctionGameWithAiBidder(page);
  const graceControls = auction.getByRole("group", { name: "Grace auction controls" });
  await expect(graceControls.getByRole("button", { name: "Step AI" })).toBeEnabled();

  const aiStepRequest = page.waitForRequest((request) => request.url().includes("/ai/step") && request.method() === "POST");
  const aiStepResponse = page.waitForResponse(
    (response) => response.url().includes("/ai/step") && response.request().method() === "POST",
  );
  await graceControls.getByRole("button", { name: "Step AI" }).click();

  expect((await aiStepRequest).postDataJSON()).toMatchObject({
    player_id: graceId,
    decision_type: "action_decision",
    mandatory: true,
    request_context: { mode: "auction_ai_bidder" },
  });

  const aiStepBody = (await (await aiStepResponse).json()) as {
    accepted_events: Array<{ actor_player_id: string | null; event_type: string }>;
  };
  const aiStepEventTypes = aiStepBody.accepted_events.map((event) => event.event_type);
  expect(aiStepEventTypes).toContain("ACTIVE_AUCTION_SET");
  expect(aiStepEventTypes).not.toContain("DICE_ROLLED");
  expect(aiStepBody.accepted_events.some((event) => event.actor_player_id === graceId)).toBe(true);

  await expect(auction).toContainText(/Current high bidder\s*Grace/);
  await page.getByRole("tab", { name: "Contracts" }).click();
  await expect(page.getByRole("region", { name: "Game log" })).toContainText("ACTIVE_AUCTION_SET");

  const state = await readMockJson<{
    state: { active_auction: { high_bidder_id: string | null; high_bid_amount: number | null; passed_player_ids: string[] } | null };
  }>(page, `/games/${gameId}/state`);
  const auctionAction =
    state.state.active_auction?.high_bidder_id === graceId
      ? "BID_AUCTION"
      : state.state.active_auction?.passed_player_ids.includes(graceId)
        ? "PASS_AUCTION"
        : null;
  expect(["BID_AUCTION", "PASS_AUCTION"]).toContain(auctionAction);
  expect(state.state.active_auction?.high_bidder_id).toBe(graceId);
  expect(state.state.active_auction?.high_bid_amount).toBe(1);
});

test("AIs initiate and respond to negotiations through AI controls", async ({ page }) => {
  // AIs initiate and respond to negotiations
  await page.goto("/");

  await page.getByRole("button", { name: "Add player" }).click();
  await page.getByRole("textbox", { name: "Seed" }).fill("stage-7-6-ai-negotiations");
  await page.getByRole("textbox", { name: "Player 1 name" }).fill("Ada");
  await page.getByRole("textbox", { name: "Player 2 name" }).fill("Grace");
  await page.getByRole("textbox", { name: "Player 3 name" }).fill("Linus");
  await page.getByRole("combobox", { name: "Player 3 type" }).selectOption("ai");
  await page.getByRole("textbox", { name: "Player 1 color hex" }).fill("#0f766e");
  await page.getByRole("textbox", { name: "Player 2 color hex" }).fill("#7c3aed");
  await page.getByRole("textbox", { name: "Player 3 color hex" }).fill("#c2410c");
  await page.getByRole("button", { name: "Create game" }).click();

  await expect(page).toHaveURL(/\/games\/mock-game-\d+$/);
  await page.getByRole("tab", { name: "Deals" }).click();
  const panel = page.getByRole("region", { name: "Negotiation inbox" });
  await panel.getByLabel("Negotiation topic").fill("AI structured package");
  await panel.getByLabel("Negotiation context").fill("Ask Linus for a complex offer.");
  await panel.getByRole("checkbox", { name: "Linus" }).check();
  await panel.getByRole("checkbox", { name: "Grace" }).uncheck();
  await panel.getByRole("button", { name: "Start negotiation" }).click();

  const aiControls = page.getByRole("region", { name: "AI negotiation controls" });
  await expect(aiControls).toContainText("Linus");

  await aiControls.getByRole("button", { name: "Ask AI message" }).click();
  await expect(page.getByRole("region", { name: "Negotiation thread" })).toContainText("structured trade window");

  await aiControls.getByRole("button", { name: "Ask AI offer" }).click();
  const firstDeal = page.getByRole("region", { name: "Deal v1" });
  await expect(firstDeal).toContainText("Rent Share");

  await aiControls.getByRole("button", { name: "Ask AI counteroffer" }).click();
  await expect(page.getByRole("region", { name: "Deal v2" })).toContainText("Counteroffer");

  await aiControls.getByRole("button", { name: "Ask AI accept/reject" }).click();
  await expect(page.getByRole("region", { name: "Deal v2" })).toContainText("Accepted");
});
