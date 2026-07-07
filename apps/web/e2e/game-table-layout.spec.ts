import { expect, test, type Page } from "@playwright/test";

async function createGame(page: Page, seed: string) {
  await page.goto("/");
  await page.getByRole("textbox", { name: "Seed" }).fill(seed);
  await page.getByRole("textbox", { name: "Player 1 name" }).fill("Ada");
  await page.getByRole("textbox", { name: "Player 2 name" }).fill("Grace");
  await page.getByRole("textbox", { name: "Player 1 color hex" }).fill("#0f766e");
  await page.getByRole("textbox", { name: "Player 2 color hex" }).fill("#7c3aed");
  await page.getByRole("button", { name: "Create game" }).click();
  await expect(page).toHaveURL(/\/games\/mock-game-\d+$/);
}

async function top(locator: ReturnType<Page["locator"]>) {
  const box = await locator.boundingBox();
  expect(box).not.toBeNull();
  return box?.y ?? 0;
}

test("desktop play surface is board-first and keeps secondary systems behind tabs", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await createGame(page, "art-plan-desktop-layout");

  const board = page.getByRole("region", { name: "Classic Monopoly-style board" });
  const controls = page.getByRole("region", { name: "Turn controls" });
  const trays = page.getByRole("region", { name: "Player trays" });
  const propertyManagement = page.getByRole("region", { name: "Property management" });
  const contracts = page.getByRole("region", { name: "Contracts obligations panel" });
  const deals = page.getByRole("region", { name: "Negotiation inbox" });
  const aiAudit = page.getByRole("region", { name: "AI audit" });

  await expect(board).toBeVisible();
  await expect(controls).toBeVisible();
  await expect(trays).toContainText("Ada");
  await expect(trays).toContainText("$1,500");
  await expect(propertyManagement).toBeVisible();
  await expect(contracts).toBeHidden();
  await expect(deals).toBeHidden();
  await expect(aiAudit).toBeHidden();

  const boardBox = await board.boundingBox();
  const controlsBox = await controls.boundingBox();
  expect(boardBox).not.toBeNull();
  expect(controlsBox).not.toBeNull();
  expect((boardBox?.width ?? 0) * (boardBox?.height ?? 0)).toBeGreaterThan(
    (controlsBox?.width ?? 0) * (controlsBox?.height ?? 0),
  );

  await expect(page.getByRole("button", { name: "Open game menu" })).toBeHidden();
  await page.getByRole("tab", { name: "AI notebook" }).click();
  await expect(page.getByRole("region", { name: "AI audit" })).toBeVisible();
});

test("mobile play surface puts current turn immediately after the board", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await createGame(page, "art-plan-mobile-layout");

  const board = page.getByRole("region", { name: "Classic Monopoly-style board" });
  const controls = page.getByRole("region", { name: "Turn controls" });
  const activePlayer = page.getByRole("region", { name: "Active player" });
  const trays = page.getByRole("region", { name: "Player trays" });

  await expect(board).toBeVisible();
  await expect(controls).toBeVisible();
  await expect(activePlayer).toBeVisible();
  await expect(trays).toBeVisible();
  await controls.scrollIntoViewIfNeeded();
  await expect(controls.getByRole("button", { name: "Roll dice" })).toBeInViewport();

  expect(await top(board)).toBeLessThan(await top(controls));
  expect(await top(controls)).toBeLessThan(await top(activePlayer));
  expect(await top(controls)).toBeLessThan(await top(trays));
});
