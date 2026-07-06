import { expect, test } from "@playwright/test";

test("opens the app shell and verifies table connection", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByRole("heading", { level: 1, name: "Monopoly 2.0 Game Table" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Table navigation" })).toBeVisible();

  const health = page.getByRole("status", { name: "Table connection" });
  await expect(health).toContainText("Ready");
  await expect(health).toContainText("Rules referee");
  await expect(health).toContainText("Move validation ready");
  await expect(health).toContainText("Local table");

  await expect(page.getByRole("row", { name: /Rules referee ready Move validation/ })).toBeVisible();
});
