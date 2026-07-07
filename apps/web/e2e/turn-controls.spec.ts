import { expect, test } from "@playwright/test";

async function createGame(page: import("@playwright/test").Page, seed: string) {
  await page.goto("/");

  await page.getByRole("textbox", { name: "Seed" }).fill(seed);
  await page.getByRole("textbox", { name: "Player 1 name" }).fill("Ada");
  await page.getByRole("textbox", { name: "Player 2 name" }).fill("Grace");
  await page.getByRole("textbox", { name: "Player 1 color hex" }).fill("#0f766e");
  await page.getByRole("textbox", { name: "Player 2 color hex" }).fill("#7c3aed");
  await page.getByRole("button", { name: "Create game" }).click();

  await expect(page).toHaveURL(/\/games\/mock-game-\d+$/);
  await expect(page.getByRole("region", { name: "Classic Monopoly-style board" })).toBeVisible();
}

test("shows legal turn controls, rolls from the returned action, logs accepted events, and refreshes on accepted events", async ({ page }) => {
  const legalActionsResponses: string[] = [];
  page.on("response", (response) => {
    if (response.url().includes("/legal-actions")) {
      legalActionsResponses.push(response.url());
    }
  });

  await createGame(page, "stage-5-turn-controls-accept");

  const controls = page.getByRole("region", { name: "Turn controls" });
  await expect(controls).toBeVisible();
  await expect(page.getByRole("region", { name: "Active player" })).toContainText("Ada");
  await expect(controls.getByRole("button", { name: "Roll dice" })).toBeEnabled();
  await expect(controls.getByRole("button", { name: "End turn" })).toBeDisabled();
  await expect(controls.getByRole("button", { name: "Buy property" })).toHaveCount(0);
  await expect(controls.getByText("Loading moves")).toHaveCount(0);

  const legalActionFetchesBeforeAction = legalActionsResponses.length;

  const actionRequest = page.waitForRequest((request) => request.url().includes("/actions") && request.method() === "POST");
  await controls.getByRole("button", { name: "Roll dice" }).click();
  const submitted = await actionRequest;
  expect(submitted.headers()["idempotency-key"]).toBeTruthy();
  expect(submitted.postDataJSON()).toMatchObject({ type: "ROLL_DICE" });

  await expect(page.getByLabel("Ada token at Chance, position 7")).toBeVisible();
  await expect(page.getByLabel("Ada token at GO, position 0")).toHaveCount(0);
  await page.getByRole("tab", { name: "Contracts" }).click();
  const log = page.getByRole("region", { name: "Game log" });
  await expect(log).toContainText("DICE_ROLLED");
  await expect(log).toContainText("TOKEN_MOVED");
  await expect
    .poll(() => legalActionsResponses.length, { message: "accepted event should refresh legal actions" })
    .toBeGreaterThan(legalActionFetchesBeforeAction);
});

test("shows Rejected action for a mock stale action without moving the active token", async ({ page }) => {
  await createGame(page, "stage-5-turn-controls-reject");

  await expect(page.getByLabel("Ada token at GO, position 0")).toBeVisible();
  await page.getByRole("region", { name: "Turn controls" }).getByRole("button", { name: "Roll dice" }).click();

  const rejection = page.getByRole("alert", { name: "Rejected action" });
  await expect(rejection).toBeVisible();
  await expect(rejection).toContainText("stale_action");
  await expect(page.getByLabel("Ada token at GO, position 0")).toBeVisible();
  await expect(page.getByLabel("Ada token at Chance, position 7")).toHaveCount(0);
});
