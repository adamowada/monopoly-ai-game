import { expect, test } from "@playwright/test";

const mockApiBaseUrl = `http://127.0.0.1:${process.env.MOCK_API_PORT ?? "18101"}`;

function currentGameId(pageUrl: string): string {
  const gameId = new URL(pageUrl).pathname.split("/").filter(Boolean).at(-1);
  if (!gameId) {
    throw new Error(`Expected game id in URL: ${pageUrl}`);
  }
  return gameId;
}

test("renders a 40-space board and updates token position after mocked state movement", async ({ page }) => {
  await page.goto("/");

  await page.getByRole("textbox", { name: "Seed" }).fill("stage-5-board-e2e-seed");
  await page.getByRole("textbox", { name: "Player 1 name" }).fill("Ada");
  await page.getByRole("textbox", { name: "Player 2 name" }).fill("Grace");
  await page.getByRole("textbox", { name: "Player 1 color hex" }).fill("#0f766e");
  await page.getByRole("textbox", { name: "Player 2 color hex" }).fill("#7c3aed");

  await page.getByRole("button", { name: "Create game" }).click();

  const board = page.getByRole("region", { name: "Classic Monopoly-style board" });
  await expect(board).toBeVisible();
  await expect(page.locator("[data-board-space]")).toHaveCount(40);
  await expect(board).toContainText("Monopoly 2.0");
  await expect(board.getByRole("img", { name: "Chance deck art" })).toBeVisible();
  await expect(board.getByRole("img", { name: "Community Chest deck art" })).toBeVisible();
  await expect(board.locator("[data-space-art]")).toHaveCount(40);
  await expect(page.locator('[data-board-space][data-space-index="0"]')).toContainText("GO");
  await expect(page.locator('[data-board-space][data-space-index="39"]')).toContainText("Boardwalk");
  await expect(page.getByLabel("Ada token at GO, position 0")).toBeVisible();

  const response = await page.request.post(
    `${mockApiBaseUrl}/__test/games/${encodeURIComponent(currentGameId(page.url()))}/players/0/position`,
    {
      data: { position: 24 },
    },
  );
  expect(response.ok()).toBeTruthy();

  await page.reload();

  await expect(page.getByLabel("Ada token at Illinois Avenue, position 24")).toBeVisible();
  await expect(page.getByLabel("Ada token at GO, position 0")).toHaveCount(0);
});
