import { expect, test } from "@playwright/test";

async function createNegotiationGame(page: import("@playwright/test").Page) {
  await page.goto("/");

  await page.getByRole("button", { name: "Add player" }).click();
  await page.getByRole("textbox", { name: "Seed" }).fill("stage-5-6-negotiation-flow");
  await page.getByRole("textbox", { name: "Player 1 name" }).fill("Ada");
  await page.getByRole("textbox", { name: "Player 2 name" }).fill("Grace");
  await page.getByRole("textbox", { name: "Player 3 name" }).fill("Linus");
  await page.getByRole("textbox", { name: "Player 1 color hex" }).fill("#0f766e");
  await page.getByRole("textbox", { name: "Player 2 color hex" }).fill("#7c3aed");
  await page.getByRole("textbox", { name: "Player 3 color hex" }).fill("#c2410c");
  await page.getByRole("button", { name: "Create game" }).click();

  await expect(page).toHaveURL(/\/games\/mock-game-\d+$/);
}

async function startNegotiation(page: import("@playwright/test").Page, topic: string) {
  const panel = page.getByRole("region", { name: "Negotiation inbox" });
  await panel.getByLabel("Negotiation topic").fill(topic);
  await panel.getByLabel("Negotiation context").fill(`${topic} context`);
  await panel.getByRole("button", { name: "Start negotiation" }).click();
  await expect(page.getByRole("region", { name: "Negotiation thread" })).toContainText(topic);
}

async function proposeSampleDeal(page: import("@playwright/test").Page) {
  await page.getByRole("button", { name: "Add sample complex instruments" }).click();
  await expect(page.getByRole("region", { name: "Contract preview" })).toContainText("Contract preview");
  await expect(page.getByRole("region", { name: "Contract preview" })).toContainText("Complex instruments");
  await expect(page.getByRole("region", { name: "Contract preview" })).toContainText("immediate_cash_transfer");
  await expect(page.getByRole("region", { name: "Contract preview" })).toContainText("deferred_cash_payment");
  await expect(page.getByRole("region", { name: "Contract preview" })).toContainText("interest_bearing_debt");
  await expect(page.getByRole("region", { name: "Contract preview" })).toContainText("property_purchase_option");
  await expect(page.getByRole("region", { name: "Contract preview" })).toContainText("rent_share");
  await expect(page.getByRole("region", { name: "Contract preview" })).toContainText("insurance_payout");
  await page.getByRole("button", { name: "Propose deal" }).click();
}

test("human can propose, message, counter, accept, reject, and expire negotiation deals", async ({ page }) => {
  await createNegotiationGame(page);

  await expect(page.getByRole("region", { name: "Negotiation inbox" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Negotiation thread" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Structured deal builder" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Contract preview" })).toBeVisible();

  await startNegotiation(page, "Propose complex package");

  await page.getByLabel("Freeform message").fill("This freeform message should appear after API acceptance.");
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(page.getByRole("region", { name: "Negotiation thread" })).toContainText(
    "This freeform message should appear after API acceptance.",
  );

  await proposeSampleDeal(page);
  const firstDeal = page.getByRole("region", { name: "Deal v1" });
  await expect(firstDeal).toContainText("Proposed");

  await firstDeal.getByRole("button", { name: "Counteroffer" }).click();
  await expect(page.getByRole("region", { name: "Structured deal builder" })).toContainText("Counteroffer");
  await proposeSampleDeal(page);
  const counterDeal = page.getByRole("region", { name: "Deal v2" });
  await expect(counterDeal).toContainText("Parent deal");
  await expect(counterDeal).toContainText("Counteroffer");

  await counterDeal.getByRole("button", { name: "Accept" }).click();
  await expect(counterDeal).toContainText("Accepted");
  await expect(counterDeal.getByRole("button", { name: "Accept" })).toHaveCount(0);

  await startNegotiation(page, "Reject package");
  await proposeSampleDeal(page);
  const rejectedDeal = page.getByRole("region", { name: "Deal v1" });
  await rejectedDeal.getByRole("button", { name: "Reject" }).click();
  await expect(rejectedDeal).toContainText("Rejected");
  await expect(rejectedDeal.getByRole("button", { name: "Accept" })).toHaveCount(0);

  await startNegotiation(page, "Expired package");
  await proposeSampleDeal(page);
  await page.getByRole("button", { name: "Expire negotiation" }).click();
  const expiredThread = page.getByRole("region", { name: "Negotiation thread" });
  await expect(expiredThread).toContainText("Expired");
  await expect(expiredThread).toContainText("visibly closed");
  await expect(expiredThread.getByRole("button", { name: "Accept", exact: true })).toHaveCount(0);
});
