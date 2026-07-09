import { expect, test, type Page } from "@playwright/test";

type TestPlayer = {
  name: string;
  kind: "human" | "ai";
  color: string;
};

const twoHumanPlayers: TestPlayer[] = [
  { name: "Ada", kind: "human", color: "#0f766e" },
  { name: "Grace", kind: "human", color: "#7c3aed" },
];

const mixedPlayers: TestPlayer[] = [
  { name: "Ada", kind: "human", color: "#0f766e" },
  { name: "Grace", kind: "ai", color: "#7c3aed" },
  { name: "Linus", kind: "human", color: "#2563eb" },
  { name: "Marie", kind: "ai", color: "#dc2626" },
  { name: "Nia", kind: "ai", color: "#ca8a04" },
];

async function createGame(page: Page, seed: string, players: TestPlayer[]) {
  await page.goto("/");

  for (let index = 2; index < players.length; index += 1) {
    await page.getByRole("button", { name: "Add player" }).click();
  }

  await page.getByRole("textbox", { name: "Seed" }).fill(seed);
  for (const [index, player] of players.entries()) {
    const playerNumber = index + 1;
    await page.getByRole("textbox", { name: `Player ${playerNumber} name` }).fill(player.name);
    await page.getByRole("combobox", { name: `Player ${playerNumber} type` }).selectOption(player.kind);
    await page.getByRole("textbox", { name: `Player ${playerNumber} color hex` }).fill(player.color);
  }

  await page.getByRole("button", { name: "Create game" }).click();

  await expect(page).toHaveURL(/\/games\/mock-game-\d+$/);
  await expect(page.getByRole("region", { name: "Classic Monopoly-style board" })).toBeVisible();
  return page.url().split("/").pop() ?? "";
}

async function expectActivePlayer(page: Page, name: string) {
  const activePlayer = page.getByRole("region", { name: "Active player" });
  await expect(activePlayer).toContainText(name);
  return activePlayer;
}

async function clickTurnControl(page: Page, name: string) {
  const controls = page.getByRole("region", { name: "Turn controls" });
  const button = controls.getByRole("button", { name });
  await expect(button).toBeEnabled();
  await button.click();
}

async function openTableView(page: Page, name: "Properties" | "Deals" | "Contracts" | "AI notebook") {
  await page.getByRole("tab", { name }).click();
}

function legalDeed(page: Page, name: string) {
  return page
    .getByRole("region", { name: "Legal deed actions" })
    .getByRole("region", { name: `Property detail: ${name}` });
}

async function openDeedCatalog(page: Page) {
  const catalog = page.getByRole("region", { name: "Deed catalog" });
  if ((await catalog.count()) === 0) {
    await page.getByRole("region", { name: "Property management" }).getByRole("button", { name: "Open deed catalog" }).click();
  }
  await expect(catalog).toBeVisible();
  return catalog;
}

async function catalogDeed(page: Page, name: string) {
  const catalog = await openDeedCatalog(page);
  return catalog.getByRole("region", { name: `Property detail: ${name}` });
}

test("stage-11-3-two-human-playthrough: readable 2 human players loop reaches Roll dice Buy property Settle debt Build house Mortgage Start negotiation Propose deal Accept Enforce obligation", async ({
  page,
}) => {
  await createGame(page, "stage-10-5-two-human-full-round", twoHumanPlayers);

  const board = page.getByRole("region", { name: "Classic Monopoly-style board" });
  const controls = page.getByRole("region", { name: "Turn controls" });
  const log = page.getByRole("region", { name: "Game log" });
  const propertyManagement = page.getByRole("region", { name: "Property management" });
  const contracts = page.getByRole("region", { name: "Contracts obligations panel" });
  const negotiation = page.getByRole("region", { name: "Negotiation inbox" });
  const aiAudit = page.getByRole("region", { name: "AI audit" });
  const auction = page.getByRole("region", { name: "Auction", exact: true });

  await expect(board).toBeVisible();
  await expect(propertyManagement).toBeVisible();
  await expect(contracts).toBeHidden();
  await expect(negotiation).toBeHidden();
  await expect(aiAudit).toBeHidden();
  await expect(log).toBeHidden();

  const bankInventory = page.getByRole("region", { name: "Bank inventory" });

  await expectActivePlayer(page, "Ada");
  await clickTurnControl(page, "Roll dice");
  await expect(controls.getByRole("button", { name: "Buy property" })).toBeEnabled();

  await clickTurnControl(page, "Buy property");
  await expect(await catalogDeed(page, "Mediterranean Avenue")).toContainText("Owner Ada");

  await clickTurnControl(page, "End turn");
  await expectActivePlayer(page, "Grace");

  await clickTurnControl(page, "Roll dice");
  await expect(controls.getByRole("button", { name: "Settle debt" })).toBeEnabled();

  await clickTurnControl(page, "Settle debt");
  await openTableView(page, "Contracts");
  await expect(log).toContainText("RENT_PAID");

  await clickTurnControl(page, "End turn");
  await expectActivePlayer(page, "Ada");

  await openTableView(page, "Properties");
  await expect(legalDeed(page, "Mediterranean Avenue").getByRole("button", { name: "Build house" })).toBeEnabled();
  await legalDeed(page, "Mediterranean Avenue").getByRole("button", { name: "Build house" }).click();
  await expect(await catalogDeed(page, "Mediterranean Avenue")).toContainText("Houses: 1");
  await expect(bankInventory).toContainText("Houses remaining");

  await expect(legalDeed(page, "Reading Railroad").getByRole("button", { name: "Mortgage" })).toBeEnabled();
  await legalDeed(page, "Reading Railroad").getByRole("button", { name: "Mortgage" }).click();
  await expect(await catalogDeed(page, "Reading Railroad")).toContainText("Mortgaged");
  await openTableView(page, "Contracts");
  await expect(log).toContainText("PROPERTY_MORTGAGE_SET");

  await createGame(page, "stage-10-5-contract-enforcement", twoHumanPlayers);
  await expect(board).toBeVisible();
  await openTableView(page, "Contracts");
  await expect(contracts).toBeVisible();
  await openTableView(page, "Deals");
  await expect(negotiation).toBeVisible();

  await negotiation.getByLabel("Negotiation topic").fill("Stage 11.3 UI usability pass");
  await negotiation.getByLabel("Negotiation context").fill("Two-human manual playthrough contract test.");
  await negotiation.getByRole("button", { name: "Start negotiation" }).click();
  await expect(page.getByRole("region", { name: "Negotiation thread" })).toContainText("Stage 11.3 UI usability pass");

  await page.getByRole("button", { name: "Add sample complex instruments" }).click();
  await expect(page.getByRole("region", { name: "Contract preview" })).toContainText("Complex instruments");
  await page.getByRole("button", { name: "Propose deal" }).click();
  const deal = page.getByRole("region", { name: "Deal v1" });
  await expect(deal).toContainText("Proposed");
  await deal.getByRole("button", { name: "Accept" }).click();
  await expect(deal).toContainText("Accepted");

  await openTableView(page, "Contracts");
  await expect(contracts).toContainText("Active contracts");
  await expect(contracts).toContainText("pending");
  await contracts.getByRole("button", { name: "Enforce obligation" }).click();
  await expect(page.getByRole("status", { name: "Contract enforcement status" })).toContainText("Contract enforcement settled");
  await expect(log).toContainText("CONTRACT_TRIGGERED_TRANSFER");

  await createGame(page, "stage-11-3-two-human-auction-pass", twoHumanPlayers);

  await clickTurnControl(page, "Roll dice");
  await expect(auction.getByRole("button", { name: "Start auction" })).toBeVisible();
  const startAuction = auction.getByRole("button", { name: "Start auction" });
  await expect(startAuction).toBeEnabled();
  await startAuction.click();

  await expect(auction).toContainText(/Auction state\s*Active/);
  const auctionControls = page.getByRole("region", { name: "Auction bidder controls" });
  await expect(auctionControls).toBeVisible();
  const adaControls = auctionControls.getByRole("group", { name: "Ada auction controls" });
  const graceControls = auctionControls.getByRole("group", { name: "Grace auction controls" });
  const adaBidButton = adaControls.getByRole("button", { name: "Bid" });
  const gracePassButton = graceControls.getByRole("button", { name: "Pass" });
  await expect(adaBidButton).toBeEnabled();
  await adaBidButton.click();

  await expect(gracePassButton).toBeVisible();
  await expect(gracePassButton).toBeEnabled();
  const passRequest = page.waitForRequest((request) => request.url().includes("/actions") && request.method() === "POST");
  await gracePassButton.click();
  expect((await passRequest).postDataJSON()).toMatchObject({
    type: "PASS_AUCTION",
    payload: { property_id: expect.any(String) },
  });
});

test("stage-11-3-property-management-playthrough: browser reaches Build house Sell house Mortgage Unmortgage controls", async ({
  page,
}) => {
  await createGame(page, "stage-5-property-management-accept", twoHumanPlayers);

  const log = page.getByRole("region", { name: "Game log" });
  const propertyManagement = page.getByRole("region", { name: "Property management" });
  const bankInventory = page.getByRole("region", { name: "Bank inventory" });

  await expect(propertyManagement).toBeVisible();
  await expect(bankInventory).toBeVisible();
  await expectActivePlayer(page, "Ada");
  await expect(legalDeed(page, "Mediterranean Avenue")).toContainText("Owner Ada");
  await expect(legalDeed(page, "Baltic Avenue")).toContainText("Owner Ada");
  await expect(legalDeed(page, "Park Place")).toContainText("Owner Ada");
  await expect(legalDeed(page, "Park Place")).toContainText("Mortgaged");
  await expect(legalDeed(page, "Boardwalk")).toContainText("Owner Ada");
  await expect(legalDeed(page, "Boardwalk")).toContainText("Hotels: 1");

  await expect(legalDeed(page, "Baltic Avenue").getByRole("button", { name: "Mortgage" })).toBeEnabled();
  await legalDeed(page, "Baltic Avenue").getByRole("button", { name: "Mortgage" }).click();
  await expect(await catalogDeed(page, "Baltic Avenue")).toContainText("Mortgaged");
  await openTableView(page, "Contracts");
  await expect(log).toContainText("PROPERTY_MORTGAGE_SET");

  await openTableView(page, "Properties");
  await expect(legalDeed(page, "Park Place").getByRole("button", { name: "Unmortgage" })).toBeEnabled();
  await legalDeed(page, "Park Place").getByRole("button", { name: "Unmortgage" }).click();
  await expect(await catalogDeed(page, "Park Place")).toContainText("Unmortgaged");
  await openTableView(page, "Contracts");
  await expect(log).toContainText("PROPERTY_MORTGAGE_SET");

  await openTableView(page, "Properties");
  await expect(legalDeed(page, "Mediterranean Avenue").getByRole("button", { name: "Build house" })).toBeEnabled();
  await legalDeed(page, "Mediterranean Avenue").getByRole("button", { name: "Build house" }).click();
  await expect(await catalogDeed(page, "Mediterranean Avenue")).toContainText("Houses: 1");
  await expect(bankInventory).toContainText("Houses remaining 31");

  await expect(legalDeed(page, "Boardwalk").getByRole("button", { name: "Sell house" })).toBeEnabled();
  await legalDeed(page, "Boardwalk").getByRole("button", { name: "Sell house" }).click();
  await expect(await catalogDeed(page, "Boardwalk")).toContainText("Houses: 4");
  await expect(await catalogDeed(page, "Boardwalk")).toContainText("Hotels: 0");
  await expect(bankInventory).toContainText("Houses remaining 27");
  await expect(bankInventory).toContainText("Hotels remaining 13");
  await openTableView(page, "Contracts");
  await expect(log).toContainText("PROPERTY_IMPROVEMENTS_SET");
});

test("stage-11-3-mixed-ai-playthrough: mixed human/AI players, Start auction Bid Pass and Step AI with real Codex AI signal path", async ({ page }) => {
  await createGame(page, "stage-10-5-five-player-mixed-round", mixedPlayers);

  const controls = page.getByRole("region", { name: "Turn controls" });
  const log = page.getByRole("region", { name: "Game log" });
  const auction = page.getByRole("region", { name: "Auction", exact: true });
  const aiAudit = page.getByRole("region", { name: "AI audit" });

  await expectActivePlayer(page, "Ada");
  await clickTurnControl(page, "Roll dice");
  await expect(auction.getByRole("button", { name: "Start auction" })).toBeEnabled();
  await auction.getByRole("button", { name: "Start auction" }).click();

  await expect(auction).toContainText(/Auction state\s*Active/);
  await expect(auction).toContainText("Current high bid");

  const bidButtons = auction.getByRole("button", { name: "Bid" });
  let bidPlaced = false;
  for (let index = 0; index < (await bidButtons.count()); index += 1) {
    const button = bidButtons.nth(index);
    if ((await button.isEnabled()) && (await button.isVisible())) {
      await button.click();
      bidPlaced = true;
      break;
    }
  }
  expect(bidPlaced).toBeTruthy();

  const passButtons = auction.getByRole("button", { name: "Pass" });
  let passTaken = false;
  for (let index = 0; index < (await passButtons.count()); index += 1) {
    const button = passButtons.nth(index);
    if ((await button.isEnabled()) && (await button.isVisible())) {
      await button.click();
      passTaken = true;
      break;
    }
  }
  if (passTaken) {
    await expect(auction).toContainText(/Passed players|Auction result/);
  }

  const graceAuctionControls = auction.getByRole("group", { name: "Grace auction controls" });
  const stepAiButton = graceAuctionControls.getByRole("button", { name: "Step AI" });
  await expect(stepAiButton).toHaveCount(1);
  await expect(stepAiButton).toBeVisible();
  await expect(stepAiButton).toBeEnabled();
  await stepAiButton.click();
  await expect(auction).toContainText(/Current high bidder/);
  await openTableView(page, "AI notebook");
  await expect(aiAudit).toContainText("AI notebook");
  await expect(aiAudit).toContainText("Decision history");
});
