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
}

test("human player sees property management regions and accepted Build house updates property and bank state", async ({
  page,
}) => {
  await createGame(page, "stage-5-property-management-accept");

  const management = page.getByRole("region", { name: "Property management" });
  await expect(management).toBeVisible();
  await expect(page.getByRole("region", { name: "Property list by owner" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Bank inventory" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Monopoly groups" })).toBeVisible();

  const legalDeeds = page.getByRole("region", { name: "Legal deed actions" });
  const mediterranean = legalDeeds.getByRole("region", { name: "Property detail: Mediterranean Avenue" });
  const bankInventory = page.getByRole("region", { name: "Bank inventory" });
  await expect(mediterranean).toContainText("Owner Ada");
  await expect(mediterranean).toContainText("Houses: 0");
  await expect(bankInventory).toContainText("Houses remaining 32");
  await management.getByRole("button", { name: "Open deed catalog" }).click();
  const catalog = page.getByRole("region", { name: "Deed catalog" });
  const mediterraneanCatalog = catalog.getByRole("region", { name: "Property detail: Mediterranean Avenue" });
  await expect(mediterraneanCatalog).toContainText("Houses: 0");

  const actionRequest = page.waitForRequest((request) => request.url().includes("/actions") && request.method() === "POST");
  await mediterranean.getByRole("button", { name: "Build house" }).click();
  const submitted = await actionRequest;
  expect(submitted.headers()["idempotency-key"]).toBeTruthy();
  expect(submitted.postDataJSON()).toMatchObject({
    type: "BUY_HOUSE",
    payload: { property_id: "property_mediterranean_avenue" },
  });

  await expect(mediterraneanCatalog).toContainText("Houses: 1");
  await expect(bankInventory).toContainText("Houses remaining 31");
  await page.getByRole("tab", { name: "Contracts" }).click();
  await expect(page.getByRole("region", { name: "Game log" })).toContainText("PROPERTY_IMPROVEMENTS_SET");
});

test("accepted Mortgage action updates visible property mortgage state", async ({ page }) => {
  await createGame(page, "stage-5-property-management-accept");

  const management = page.getByRole("region", { name: "Property management" });
  const legalDeeds = page.getByRole("region", { name: "Legal deed actions" });
  const baltic = legalDeeds.getByRole("region", { name: "Property detail: Baltic Avenue" });
  await expect(baltic).toContainText("Owner Ada");
  await expect(baltic).toContainText("Unmortgaged");
  await management.getByRole("button", { name: "Open deed catalog" }).click();
  const catalog = page.getByRole("region", { name: "Deed catalog" });
  const balticCatalog = catalog.getByRole("region", { name: "Property detail: Baltic Avenue" });
  await expect(balticCatalog).toContainText("Unmortgaged");

  const actionRequest = page.waitForRequest((request) => request.url().includes("/actions") && request.method() === "POST");
  await baltic.getByRole("button", { name: "Mortgage" }).click();
  const submitted = await actionRequest;
  expect(submitted.headers()["idempotency-key"]).toBeTruthy();
  expect(submitted.postDataJSON()).toMatchObject({
    type: "MORTGAGE_PROPERTY",
    payload: { property_id: "property_baltic_avenue" },
  });

  await expect(balticCatalog).toContainText("Mortgaged");
  await page.getByRole("tab", { name: "Contracts" }).click();
  await expect(page.getByRole("region", { name: "Game log" })).toContainText("PROPERTY_MORTGAGE_SET");
});

test("mock backend rejection for even-building shows Rejected action without mutating visible property state", async ({
  page,
}) => {
  await createGame(page, "stage-5-property-management-reject");

  const baltic = page.getByRole("region", { name: "Property detail: Baltic Avenue" });
  const bankInventory = page.getByRole("region", { name: "Bank inventory" });
  await expect(baltic).toContainText("Houses: 0");
  await expect(baltic).toContainText("Hotels: 0");
  await expect(bankInventory).toContainText("Houses remaining 32");

  await baltic.getByRole("button", { name: "Build house" }).click();

  const rejection = page.getByRole("alert", { name: "Rejected action" });
  await expect(rejection).toBeVisible();
  await expect(rejection).toContainText("even_building_rule");
  await expect(baltic).toContainText("Houses: 0");
  await expect(baltic).toContainText("Hotels: 0");
  await expect(bankInventory).toContainText("Houses remaining 32");
  await expect(bankInventory).not.toContainText("Houses remaining 31");
});
