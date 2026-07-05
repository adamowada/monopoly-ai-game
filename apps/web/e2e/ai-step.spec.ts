import { expect, test } from "@playwright/test";

async function createAiFirstGame(page: import("@playwright/test").Page, seed: string) {
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

  const panel = page.getByRole("region", { name: "AI audit" });
  await expect(panel).toContainText("Decision history");
  await expect(panel).toContainText(`ai_decision_id ${aiStepBody.ai_decision_id}`);
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
  await expect(firstDeal).toContainText("rent_share");

  await aiControls.getByRole("button", { name: "Ask AI counteroffer" }).click();
  await expect(page.getByRole("region", { name: "Deal v2" })).toContainText("Counteroffer");

  await aiControls.getByRole("button", { name: "Ask AI accept/reject" }).click();
  await expect(page.getByRole("region", { name: "Deal v2" })).toContainText("Accepted");
});
