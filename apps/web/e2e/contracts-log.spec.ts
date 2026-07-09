import { expect, test } from "@playwright/test";

async function createContractsGame(page: import("@playwright/test").Page) {
  await page.goto("/");

  await page.getByRole("button", { name: "Add player" }).click();
  await page.getByRole("textbox", { name: "Seed" }).fill("stage-5-7-contracts-log");
  await page.getByRole("textbox", { name: "Player 1 name" }).fill("Ada");
  await page.getByRole("textbox", { name: "Player 2 name" }).fill("Grace");
  await page.getByRole("textbox", { name: "Player 3 name" }).fill("Linus");
  await page.getByRole("combobox", { name: "Player 3 type" }).selectOption("ai");
  await page.getByRole("textbox", { name: "Player 1 color hex" }).fill("#0f766e");
  await page.getByRole("textbox", { name: "Player 2 color hex" }).fill("#7c3aed");
  await page.getByRole("textbox", { name: "Player 3 color hex" }).fill("#c2410c");
  await page.getByRole("button", { name: "Create game" }).click();

  await expect(page).toHaveURL(/\/games\/mock-game-\d+$/);
}

test("shows contracts, obligations, settlement history, source-linked transfers, and log filters", async ({ page }) => {
  await createContractsGame(page);
  await page.getByRole("tab", { name: "Contracts" }).click();

  const panel = page.getByRole("region", { name: "Contracts obligations panel" });
  await expect(panel).toBeVisible();

  await expect(panel).toContainText("Active contracts");
  await expect(panel).toContainText("Agreement between Ada, Grace");
  await expect(panel).toContainText("Parties Ada, Grace");
  await expect(panel).not.toContainText("deal_id");
  await expect(panel).not.toContainText("source_agreement_id");
  await expect(panel).not.toContainText("effective_event_id");

  await expect(panel).toContainText("Upcoming obligations");
  await expect(panel).toContainText("Ada owes Grace");
  await expect(panel).toContainText("Turn");
  await expect(panel).not.toContainText("obligation_id");
  await expect(panel).not.toContainText("contract_id");
  await expect(panel).not.toContainText("due_turn");
  await expect(panel).toContainText("Counterparty Grace");

  await expect(panel).toContainText("Obligation settlement history");
  await expect(panel).toContainText("Settled");

  await panel.getByRole("button", { name: "Show contract technical record" }).first().click();
  await expect(panel).toContainText("deal_id");
  await expect(panel).toContainText("source_agreement_id");
  await expect(panel).toContainText("effective_event_id");
  await panel.getByRole("button", { name: "Show obligation technical record" }).first().click();
  await expect(panel).toContainText("obligation_id");
  await expect(panel).toContainText("contract_id");
  await expect(panel).toContainText("due_turn");

  const log = page.getByRole("region", { name: "Game log" });
  await expect(log).toContainText("Full game log");
  await expect(log).toContainText("Actions");
  await expect(log).toContainText("Deals");
  await expect(log).toContainText("AI decisions");
  await expect(log).toContainText("Rejections");
  await expect(log).toContainText("DICE_ROLLED");
  await expect(log).toContainText("Deal");
  await expect(log).toContainText("AI_DECISION_RECORDED");
  await expect(log).toContainText("Rejected action");
  await expect(log).toContainText("CONTRACT_TRIGGERED_TRANSFER");
  await expect(log).toContainText("Contract-triggered transfer");
  await expect(log).toContainText("Source agreement");

  await log.getByLabel("Actions").uncheck();
  await expect(log).not.toContainText("DICE_ROLLED");
  await expect(log).not.toContainText("CONTRACT_TRIGGERED_TRANSFER");
  await expect(log).toContainText("Deal");
  await expect(log).toContainText("AI_DECISION_RECORDED");
  await expect(log).toContainText("Rejected action");

  await log.getByLabel("Rejections").uncheck();
  await expect(log).not.toContainText("Rejected action");
});
