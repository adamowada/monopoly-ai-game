import { expect, test } from "@playwright/test";

test("creates a configured game and navigates to the board shell", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByRole("heading", { level: 2, name: "Game setup" })).toBeVisible();
  await page.getByRole("textbox", { name: "Seed" }).fill("stage-5-e2e-seed");
  await page.getByRole("textbox", { name: "Player 1 name" }).fill("Ada");
  await page.getByRole("textbox", { name: "Player 2 name" }).fill("Grace");
  await page.getByRole("combobox", { name: "Player 2 type" }).selectOption("ai");
  await page.getByRole("textbox", { name: "Player 2 color hex" }).fill("#7c3aed");
  await page.getByRole("spinbutton", { name: "Max negotiation rounds" }).fill("4");
  await page.getByRole("spinbutton", { name: "Proposal limit per player" }).fill("3");

  await page.getByRole("button", { name: "Create game" }).click();

  await expect(page).toHaveURL(/\/games\/mock-game-\d+$/);
  expect(page.url()).toContain("/games/");
  await expect(page.getByRole("heading", { level: 1, name: /Game board/ })).toBeVisible();
  await expect(page.getByText("stage-5-e2e-seed")).toBeVisible();
  await expect(page.getByRole("row", { name: /Ada human #0f766e/ })).toBeVisible();
  await expect(page.getByRole("row", { name: /Grace ai #7c3aed/ })).toBeVisible();
  await expect(page.getByText("Max rounds: 4")).toBeVisible();
  await expect(page.getByText("Proposal limit/player: 3")).toBeVisible();
});

test("blocks client invalid setup and displays server validation errors", async ({ page }) => {
  await page.goto("/");

  await page.getByRole("textbox", { name: "Player 1 name" }).fill("Ada");
  await page.getByRole("textbox", { name: "Player 2 name" }).fill("Ada");
  await page.getByRole("button", { name: "Create game" }).click();

  const setup = page.getByRole("region", { name: "Game setup" });
  await expect(setup.getByRole("alert")).toContainText("Player names must be unique");
  await expect(page).toHaveURL("/");

  await page.getByRole("textbox", { name: "Player 2 name" }).fill("Grace");
  await page.getByRole("textbox", { name: "Seed" }).fill("server-reject");
  await page.getByRole("button", { name: "Create game" }).click();

  await expect(setup.getByRole("alert")).toContainText("Server rejected setup");
  await expect(page).toHaveURL("/");
});
