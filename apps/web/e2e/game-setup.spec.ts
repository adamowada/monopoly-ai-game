import { expect, test } from "@playwright/test";

test("creates a configured game and navigates to the board shell", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByRole("region", { name: "Choose seats" })).toBeVisible();
  await expect(page.getByText("Local tabletop setup")).toHaveCount(0);
  await page.getByRole("textbox", { name: "Seed" }).fill("stage-5-e2e-seed");
  await page.getByRole("textbox", { name: "Player 1 name" }).fill("Ada");
  await page.getByRole("textbox", { name: "Player 2 name" }).fill("Grace");
  await page.getByRole("combobox", { name: "Player 2 type" }).selectOption("ai");
  await page.getByRole("textbox", { name: "Player 2 color hex" }).fill("#7c3aed");
  await page.getByRole("button", { name: "Player 2 token icon Train" }).click();
  await page.getByRole("spinbutton", { name: "Max negotiation rounds" }).fill("4");
  await page.getByRole("spinbutton", { name: "Proposal limit per player" }).fill("3");

  await page.getByRole("button", { name: "Create game" }).click();

  await expect(page).toHaveURL(/\/games\/mock-game-\d+$/);
  expect(page.url()).toContain("/games/");
  await expect(page.getByRole("region", { name: "Classic Monopoly-style board" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Turn controls" })).toBeVisible();
  const trays = page.getByRole("region", { name: "Player trays" });
  await expect(trays).toContainText("Ada");
  await expect(trays).toContainText("Grace");
  await expect(trays).toContainText("$1,500");
  await expect(page.getByLabel("Grace token at GO, position 0")).toHaveAttribute("data-token-icon", "🚂");
  await expect(trays.getByRole("img", { name: "Grace token" })).toHaveAttribute("data-token-icon", "🚂");
  await expect(page.getByText("stage-5-e2e-seed")).toHaveCount(0);
});

test("blocks client invalid setup and displays server validation errors", async ({ page }) => {
  await page.goto("/");

  await page.getByRole("textbox", { name: "Player 1 name" }).fill("Ada");
  await page.getByRole("textbox", { name: "Player 2 name" }).fill("Ada");
  await page.getByRole("button", { name: "Create game" }).click();

  const setup = page.getByRole("region", { name: "Choose seats" });
  await expect(setup.getByRole("alert")).toContainText("Player names must be unique");
  await expect(page).toHaveURL("/");

  await page.getByRole("textbox", { name: "Player 2 name" }).fill("Grace");
  await page.getByRole("textbox", { name: "Seed" }).fill("server-reject");
  await page.getByRole("button", { name: "Create game" }).click();

  await expect(setup.getByRole("alert")).toContainText("Server rejected setup");
  await expect(page).toHaveURL("/");
});
