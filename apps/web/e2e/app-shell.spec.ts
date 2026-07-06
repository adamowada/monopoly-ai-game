import { expect, test } from "@playwright/test";

test("opens the app shell and verifies backend health connectivity", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByRole("heading", { level: 1, name: "Monopoly 2.0 Game Table" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Table navigation" })).toBeVisible();

  const health = page.getByRole("status", { name: "Backend health" });
  await expect(health).toContainText("ok");
  await expect(health).toContainText("api");
  await expect(health).toContainText("phase-1-stage-1.3");
  await expect(health).toContainText("test");

  await expect(page.getByRole("row", { name: /FastAPI service ok phase-1-stage-1.3/ })).toBeVisible();
});
