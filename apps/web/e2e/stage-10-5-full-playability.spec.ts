import { expect, test, type Page } from "@playwright/test";

const mockApiPort = process.env.MOCK_API_PORT ?? "18101";
const mockApiBaseUrl = `http://127.0.0.1:${mockApiPort}`;

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

async function advanceAiTurnTo(page: Page, currentName: string, nextName: string) {
  await expectActivePlayer(page, currentName);
  const controls = page.getByRole("region", { name: "Turn controls" });
  for (let attempt = 0; attempt < 5; attempt += 1) {
    const activePlayer = page.getByRole("region", { name: "Active player" });
    if (((await activePlayer.textContent()) ?? "").includes(nextName)) {
      return;
    }
    const stepButton = controls.getByRole("button", { name: "Step AI" });
    await expect(stepButton).toBeEnabled();
    await stepButton.click();
    await expect(page.getByRole("status", { name: "AI step status" })).toContainText("AI done");
    try {
      await expect(activePlayer).toContainText(nextName, { timeout: 1_000 });
      return;
    } catch {
      await expect(activePlayer).toContainText(currentName);
    }
  }
  await expectActivePlayer(page, nextName);
}

async function readMockJson<T>(page: Page, path: string): Promise<T> {
  const response = await page.request.get(`${mockApiBaseUrl}${path}`);
  expect(response.ok()).toBeTruthy();
  return (await response.json()) as T;
}

test("completes a 2-human-player full-table browser round", async ({ page }) => {
  await createGame(page, "stage-10-5-two-human-full-round", twoHumanPlayers);

  const controls = page.getByRole("region", { name: "Turn controls" });
  const log = page.getByRole("region", { name: "Game log" });
  const mediterranean = page.getByRole("region", { name: "Property detail: Mediterranean Avenue" });
  const reading = page.getByRole("region", { name: "Property detail: Reading Railroad" });
  const bank = page.getByRole("region", { name: "Bank inventory" });

  await expectActivePlayer(page, "Ada");
  await clickTurnControl(page, "Roll dice");
  await expect(controls.getByRole("button", { name: "Buy property" })).toBeEnabled();
  await expect(mediterranean).toContainText("Owner Bank/unowned");

  await clickTurnControl(page, "Buy property");
  await expect(mediterranean).toContainText("Owner Ada");
  await expect(await expectActivePlayer(page, "Ada")).toContainText("$1,440");
  await expect(log).toContainText("PROPERTY_OWNER_SET");

  await clickTurnControl(page, "End turn");
  await expectActivePlayer(page, "Grace");

  await clickTurnControl(page, "Roll dice");
  await expect(controls.getByRole("button", { name: "Settle debt" })).toBeEnabled();
  await clickTurnControl(page, "Settle debt");
  await expect(log).toContainText("RENT_PAID");
  await expect(await expectActivePlayer(page, "Grace")).toContainText("$1,498");

  await clickTurnControl(page, "End turn");
  await expect(await expectActivePlayer(page, "Ada")).toContainText("$1,442");

  await expect(mediterranean.getByRole("button", { name: "Build house" })).toBeEnabled();
  await mediterranean.getByRole("button", { name: "Build house" }).click();
  await expect(mediterranean).toContainText("Houses: 1");
  await expect(bank).toContainText("Houses remaining 31");

  await expect(reading.getByRole("button", { name: "Mortgage" })).toBeEnabled();
  await reading.getByRole("button", { name: "Mortgage" }).click();
  await expect(reading).toContainText("Mortgaged");
  await expect(log).toContainText("PROPERTY_MORTGAGE_SET");
});

test("completes a 5-player mixed human/fake-AI full-table browser round", async ({ page }) => {
  await createGame(page, "stage-10-5-five-player-mixed-round", mixedPlayers);

  const auction = page.getByRole("region", { name: "Auction", exact: true });
  const log = page.getByRole("region", { name: "Game log" });

  await expectActivePlayer(page, "Ada");
  await clickTurnControl(page, "Roll dice");
  await expect(auction.getByRole("button", { name: "Start auction" })).toBeEnabled();
  await auction.getByRole("button", { name: "Start auction" }).click();
  await expect(auction).toContainText(/Auction state\s*Active/);

  await expect(auction.getByRole("group", { name: "Grace auction controls" }).getByRole("button", { name: "Bid" })).toBeVisible();
  await auction.getByRole("group", { name: "Grace auction controls" }).getByRole("button", { name: "Step AI" }).click();
  await expect(auction).toContainText(/Current high bidder\s*Grace/);
  await expect(auction).toContainText(/Current high bid\s*\$1/);

  await auction.getByRole("group", { name: "Ada auction controls" }).getByRole("button", { name: "Pass" }).click();
  await auction.getByRole("group", { name: "Linus auction controls" }).getByRole("button", { name: "Pass" }).click();
  await auction.getByRole("group", { name: "Marie auction controls" }).getByRole("button", { name: "Step AI" }).click();
  await auction.getByRole("group", { name: "Nia auction controls" }).getByRole("button", { name: "Step AI" }).click();

  await expect(auction).toContainText("Grace won Mediterranean Avenue for $1");
  await expect(page.getByRole("region", { name: "Property detail: Mediterranean Avenue" })).toContainText("Owner Grace");
  await expect(log).toContainText("AUCTION_RESULT");

  await clickTurnControl(page, "End turn");
  await advanceAiTurnTo(page, "Grace", "Linus");

  await clickTurnControl(page, "Roll dice");
  await clickTurnControl(page, "Buy property");
  await expect(page.getByRole("region", { name: "Property detail: Baltic Avenue" })).toContainText("Owner Linus");
  await clickTurnControl(page, "End turn");

  await advanceAiTurnTo(page, "Marie", "Nia");
  await advanceAiTurnTo(page, "Nia", "Ada");

  await expectActivePlayer(page, "Ada");
  const audit = page.getByRole("region", { name: "AI audit" });
  await expect(audit).toContainText("AI audit");
  await expect(audit).toContainText("Decision history");
  await expect(audit).toContainText("ai_decision_id");
  await expect(audit).toContainText("Legal actions snapshot");
});

test("verifies accepted deal execution and later contract enforcement in browser", async ({ page }) => {
  const gameId = await createGame(page, "stage-10-5-contract-enforcement", twoHumanPlayers);

  const negotiation = page.getByRole("region", { name: "Negotiation inbox" });
  await negotiation.getByLabel("Negotiation topic").fill("Settleable contract");
  await negotiation.getByLabel("Negotiation context").fill("Create an immediately enforceable browser contract.");
  await negotiation.getByRole("button", { name: "Start negotiation" }).click();
  await expect(page.getByRole("region", { name: "Negotiation thread" })).toContainText("Settleable contract");

  await page.getByRole("button", { name: "Add sample complex instruments" }).click();
  await expect(page.getByRole("region", { name: "Contract preview" })).toContainText("Complex instruments");
  await page.getByRole("button", { name: "Propose deal" }).click();

  const deal = page.getByRole("region", { name: "Deal v1" });
  await expect(deal).toContainText("Proposed");
  await deal.getByRole("button", { name: "Accept" }).click();
  await expect(deal).toContainText("Accepted");

  const contracts = page.getByRole("region", { name: "Contracts obligations panel" });
  await expect(contracts).toContainText("Active contracts");
  await expect(contracts).toContainText("pending");
  await expect(contracts.getByRole("button", { name: "Enforce obligation" })).toBeEnabled();
  await contracts.getByRole("button", { name: "Enforce obligation" }).click();

  await expect(page.getByRole("status", { name: "Contract enforcement status" })).toContainText(
    "Contract enforcement settled 1 obligation",
  );
  await expect(contracts).toContainText("CONTRACT_TRIGGERED_TRANSFER");
  await expect(contracts).toContainText("Ada paid Grace");
  await expect(page.getByRole("region", { name: "Game log" })).toContainText("CONTRACT_TRIGGERED_TRANSFER");

  const state = await readMockJson<{ state_hash: string; event_sequence: number; state: { turn: { current_player_id: string } } }>(
    page,
    `/games/${gameId}/state`,
  );
  const rejected = await page.request.post(`${mockApiBaseUrl}/games/${gameId}/actions`, {
    headers: {
      "Idempotency-Key": `stage-10-5-rejected-${Date.now()}`,
    },
    data: {
      actor_id: state.state.turn.current_player_id,
      type: "BUY_PROPERTY",
      payload: { property_id: "property_boardwalk" },
      expected_state_hash: state.state_hash,
      expected_event_sequence: state.event_sequence,
    },
  });
  expect(rejected.status()).toBe(422);
  await expect(page.getByRole("alert", { name: "Rejected action" })).toContainText("Rejected action");
});
