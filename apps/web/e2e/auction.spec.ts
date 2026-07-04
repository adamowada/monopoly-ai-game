import { expect, test } from "@playwright/test";

const mockApiPort = process.env.MOCK_API_PORT ?? "18101";
const mockApiBaseUrl = `http://127.0.0.1:${mockApiPort}`;

async function createAuctionGame(page: import("@playwright/test").Page) {
  await page.goto("/");

  await page.getByRole("button", { name: "Add player" }).click();
  await page.getByRole("textbox", { name: "Seed" }).fill("stage-5-auction-flow");
  await page.getByRole("textbox", { name: "Player 1 name" }).fill("Ada");
  await page.getByRole("textbox", { name: "Player 2 name" }).fill("Grace");
  await page.getByRole("textbox", { name: "Player 3 name" }).fill("Linus");
  await page.getByRole("textbox", { name: "Player 1 color hex" }).fill("#0f766e");
  await page.getByRole("textbox", { name: "Player 2 color hex" }).fill("#7c3aed");
  await page.getByRole("textbox", { name: "Player 3 color hex" }).fill("#c2410c");
  await page.getByRole("button", { name: "Create game" }).click();

  await expect(page).toHaveURL(/\/games\/mock-game-\d+$/);
  await expect(page.getByRole("region", { name: "Classic Monopoly-style board" })).toBeVisible();
  return page.url().split("/").pop() ?? "";
}

async function readMockJson<T>(page: import("@playwright/test").Page, path: string): Promise<T> {
  const response = await page.request.get(`${mockApiBaseUrl}${path}`);
  expect(response.ok()).toBeTruthy();
  return (await response.json()) as T;
}

test("auction can start, receive bids and passes, reject a low bid, and assign the winner", async ({ page }) => {
  const gameId = await createAuctionGame(page);
  const game = await readMockJson<{
    players: Array<{ id: string; name: string }>;
  }>(page, `/games/${gameId}`);
  const adaId = game.players.find((player) => player.name === "Ada")?.id;
  const graceId = game.players.find((player) => player.name === "Grace")?.id;
  const linusId = game.players.find((player) => player.name === "Linus")?.id;
  expect(adaId).toBeTruthy();
  expect(graceId).toBeTruthy();
  expect(linusId).toBeTruthy();

  await page.getByRole("region", { name: "Turn controls" }).getByRole("button", { name: "Roll dice" }).click();

  const auction = page.getByRole("region", { name: "Auction", exact: true });
  await expect(auction).toBeVisible();
  await expect(auction).toContainText("Auction state");
  await expect(auction).toContainText("Mediterranean Avenue");
  await expect(auction).toContainText("Current high bid");
  await expect(auction).toContainText("Remaining bidders");
  await expect(auction).toContainText("Auction result");

  const startRequest = page.waitForRequest((request) => request.url().includes("/actions") && request.method() === "POST");
  await auction.getByRole("button", { name: "Start auction" }).click();
  const startSubmission = await startRequest;
  expect(startSubmission.postDataJSON()).toMatchObject({
    actor_id: adaId,
    type: "START_AUCTION",
    payload: { property_id: "property_mediterranean_avenue" },
  });

  await expect(auction).toContainText(/Auction state\s*Active/);
  await expect(auction).toContainText(/Current high bid\s*No bids yet/);
  await expect(auction).toContainText(/Remaining bidders\s*Ada, Grace, Linus/);

  const graceControls = auction.getByRole("group", { name: "Grace auction controls" });
  const bidRequest = page.waitForRequest((request) => request.url().includes("/actions") && request.method() === "POST");
  await graceControls.getByRole("button", { name: "Bid" }).click();
  const bidSubmission = await bidRequest;
  expect(bidSubmission.postDataJSON()).toMatchObject({
    actor_id: graceId,
    type: "BID_AUCTION",
    payload: { property_id: "property_mediterranean_avenue", amount: 1 },
  });

  await expect(auction).toContainText(/Current high bid\s*\$1/);
  await expect(auction).toContainText(/Current high bidder\s*Grace/);

  const beforeLowBid = await readMockJson<{
    state_hash: string;
    event_sequence: number;
    state: { active_auction: { high_bid_amount: number } };
  }>(page, `/games/${gameId}/state`);
  const rejectedLowBid = await page.request.post(`${mockApiBaseUrl}/games/${gameId}/actions`, {
    headers: {
      "Idempotency-Key": `low-bid-${Date.now()}`,
    },
    data: {
      actor_id: adaId,
      type: "BID_AUCTION",
      payload: { property_id: "property_mediterranean_avenue", amount: 1 },
      expected_state_hash: beforeLowBid.state_hash,
      expected_event_sequence: beforeLowBid.event_sequence,
    },
  });
  expect(rejectedLowBid.status()).toBe(422);
  expect(await rejectedLowBid.json()).toMatchObject({
    status: "rejected",
    reason_code: "illegal_action",
  });
  const afterLowBid = await readMockJson<{
    state_hash: string;
    event_sequence: number;
    state: { active_auction: { high_bid_amount: number } };
  }>(page, `/games/${gameId}/state`);
  expect(afterLowBid.state_hash).toBe(beforeLowBid.state_hash);
  expect(afterLowBid.event_sequence).toBe(beforeLowBid.event_sequence);
  expect(afterLowBid.state.active_auction.high_bid_amount).toBe(1);

  await auction.getByRole("group", { name: "Ada auction controls" }).getByRole("button", { name: "Pass" }).click();
  await expect(auction).toContainText(/Passed players\s*Ada/);

  await auction.getByRole("group", { name: "Linus auction controls" }).getByRole("button", { name: "Pass" }).click();

  await expect(auction).toContainText("Auction result");
  await expect(auction).toContainText("Grace won Mediterranean Avenue for $1");
  await expect(auction).toContainText("Winner Grace");
  await expect(page.getByRole("region", { name: "Property detail: Mediterranean Avenue" })).toContainText("Owner Grace");

  const finalState = await readMockJson<{
    state: {
      active_auction: null;
      players: Array<{ id: string; cash: number }>;
      property_ownership: Array<{ property_id: string; owner_id: string | null }>;
    };
  }>(page, `/games/${gameId}/state`);
  expect(finalState.state.active_auction).toBeNull();
  expect(finalState.state.players.find((player) => player.id === graceId)?.cash).toBe(1499);
  expect(
    finalState.state.property_ownership.find(
      (ownership) => ownership.property_id === "property_mediterranean_avenue",
    )?.owner_id,
  ).toBe(graceId);
});
