import { expect, test } from "@playwright/test";

async function createAiAuditGame(page: import("@playwright/test").Page) {
  await page.goto("/");

  await page.getByRole("button", { name: "Add player" }).click();
  await page.getByRole("textbox", { name: "Seed" }).fill("stage-5-8-ai-audit-flow");
  await page.getByRole("textbox", { name: "Player 1 name" }).fill("Ada");
  await page.getByRole("textbox", { name: "Player 2 name" }).fill("Grace");
  await page.getByRole("textbox", { name: "Player 3 name" }).fill("Linus");
  await page.getByRole("combobox", { name: "Player 2 type" }).selectOption("ai");
  await page.getByRole("combobox", { name: "Player 3 type" }).selectOption("ai");
  await page.getByRole("textbox", { name: "Player 1 color hex" }).fill("#0f766e");
  await page.getByRole("textbox", { name: "Player 2 color hex" }).fill("#7c3aed");
  await page.getByRole("textbox", { name: "Player 3 color hex" }).fill("#c2410c");
  await page.getByRole("button", { name: "Create game" }).click();

  await expect(page).toHaveURL(/\/games\/mock-game-\d+$/);
}

test("user can inspect AI audit records for profiles, decisions, memory, retrievals, dialogue, and rejected AI outputs", async ({
  page,
}) => {
  await createAiAuditGame(page);

  const panel = page.getByRole("region", { name: "AI audit" });
  await expect(panel).toBeVisible();
  await expect(panel).toContainText("Private local AI notebook");

  await expect(panel).toContainText("AI profile");
  await expect(panel).toContainText("Grace audit profile");
  await expect(panel).toContainText("Linus audit profile");
  await expect(panel).toContainText("Traits");
  await expect(panel).toContainText("Personality");
  await expect(panel).toContainText("Play style");

  await expect(panel).toContainText("Decision history");
  await expect(panel).toContainText("ai_decision_id");
  await expect(panel).toContainText("ai_profile_id");
  await expect(panel).toContainText("state_hash");
  await expect(panel).toContainText("Legal actions snapshot");
  await expect(panel).toContainText("ROLL_DICE");
  await expect(panel).toContainText("Prompt context");
  await expect(panel).toContainText("Parsed output");

  await expect(panel).toContainText("Self-dialogue timeline");
  await expect(panel).toContainText("Linked decision");
  await expect(panel).toContainText("Memory entries");
  await expect(panel).toContainText("Used by decision");
  await expect(panel).toContainText("superseded_by_memory_id");
  await expect(panel).toContainText("Retrieved context records");
  await expect(panel).toContainText("retrieval_record_id");

  await expect(panel).toContainText("Rejected AI outputs");
  await expect(panel).toContainText("Validation errors");
  await expect(panel).toContainText("BUY_PROPERTY is not in the Legal actions snapshot.");
});
