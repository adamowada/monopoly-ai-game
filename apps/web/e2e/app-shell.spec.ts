import { expect, test } from "@playwright/test";

test("opens the app shell as a direct table setup surface", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByRole("heading", { level: 1, name: "Monopoly 2.0 Game Table" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Game prep navigation" })).toHaveCount(0);
  await expect(page.getByRole("status", { name: "Referee readiness" })).toHaveCount(0);
  await expect(page.getByRole("region", { name: "Choose seats" })).toBeVisible();
  await expect(page.getByRole("table", { name: "Configured players" })).toHaveCount(0);

  await expect(page.getByRole("heading", { name: "Table check" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Connection details" })).toHaveCount(0);
  await expect(page.getByRole("row", { name: /Rules referee ready Move validation/ })).toHaveCount(0);
});
