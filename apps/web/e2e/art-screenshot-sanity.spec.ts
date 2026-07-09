import { inflateSync } from "node:zlib";
import { expect, test, type Page, type TestInfo } from "@playwright/test";

const mockApiPort = process.env.MOCK_API_PORT ?? "18101";
const mockApiBaseUrl = `http://127.0.0.1:${mockApiPort}`;

type PngInfo = {
  width: number;
  height: number;
  uniqueColorCount: number;
};

type TestPlayer = {
  color: string;
  kind: "human" | "ai";
  name: string;
};

const defaultPlayers: TestPlayer[] = [
  { color: "#0f766e", kind: "human", name: "Ada" },
  { color: "#7c3aed", kind: "human", name: "Grace" },
];

async function createGame(page: Page, seed: string, players: TestPlayer[] = defaultPlayers) {
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
}

function paeth(left: number, up: number, upLeft: number) {
  const estimate = left + up - upLeft;
  const leftDistance = Math.abs(estimate - left);
  const upDistance = Math.abs(estimate - up);
  const upLeftDistance = Math.abs(estimate - upLeft);
  if (leftDistance <= upDistance && leftDistance <= upLeftDistance) {
    return left;
  }
  return upDistance <= upLeftDistance ? up : upLeft;
}

function readPngInfo(buffer: Buffer): PngInfo {
  expect(buffer.subarray(0, 8).toString("hex")).toBe("89504e470d0a1a0a");

  let cursor = 8;
  let width = 0;
  let height = 0;
  let colorType = -1;
  const idatChunks: Buffer[] = [];

  while (cursor < buffer.length) {
    const length = buffer.readUInt32BE(cursor);
    const type = buffer.subarray(cursor + 4, cursor + 8).toString("ascii");
    const data = buffer.subarray(cursor + 8, cursor + 8 + length);
    if (type === "IHDR") {
      width = data.readUInt32BE(0);
      height = data.readUInt32BE(4);
      expect(data[8]).toBe(8);
      colorType = data[9] ?? -1;
    }
    if (type === "IDAT") {
      idatChunks.push(data);
    }
    if (type === "IEND") {
      break;
    }
    cursor += length + 12;
  }

  const bytesPerPixel = colorType === 6 ? 4 : colorType === 2 ? 3 : 0;
  expect(bytesPerPixel).toBeGreaterThan(0);

  const inflated = inflateSync(Buffer.concat(idatChunks));
  const rowLength = width * bytesPerPixel;
  const previous = Buffer.alloc(rowLength);
  const current = Buffer.alloc(rowLength);
  const colors = new Set<string>();
  let sourceOffset = 0;

  for (let row = 0; row < height; row += 1) {
    const filter = inflated[sourceOffset];
    sourceOffset += 1;
    for (let column = 0; column < rowLength; column += 1) {
      const raw = inflated[sourceOffset + column] ?? 0;
      const left = column >= bytesPerPixel ? current[column - bytesPerPixel] ?? 0 : 0;
      const up = previous[column] ?? 0;
      const upLeft = column >= bytesPerPixel ? previous[column - bytesPerPixel] ?? 0 : 0;
      const value =
        filter === 0
          ? raw
          : filter === 1
            ? raw + left
            : filter === 2
              ? raw + up
              : filter === 3
                ? raw + Math.floor((left + up) / 2)
                : raw + paeth(left, up, upLeft);
      current[column] = value & 0xff;
    }
    for (let column = 0; column < rowLength; column += bytesPerPixel * 16) {
      colors.add(current.subarray(column, column + bytesPerPixel).toString("hex"));
    }
    current.copy(previous);
    sourceOffset += rowLength;
  }

  return { width, height, uniqueColorCount: colors.size };
}

async function expectViewportScreenshot(page: Page, testInfo: TestInfo, name: string, width: number, height: number) {
  await page.setViewportSize({ width, height });
  const screenshotPath = testInfo.outputPath(`${name}.png`);
  const buffer = await page.screenshot({ fullPage: false, path: screenshotPath });
  const png = readPngInfo(buffer);
  expect(png.width).toBe(width);
  expect(png.height).toBe(height);
  expect(png.uniqueColorCount).toBeGreaterThan(24);
}

test("captures nonblank desktop and mobile game-table screenshots", async ({ page }, testInfo) => {
  await createGame(page, "art-screenshot-sanity");

  await expectViewportScreenshot(page, testInfo, "game-table-desktop", 1440, 900);
  await expect(page.getByRole("region", { name: "Turn controls" })).toBeVisible();

  await expectViewportScreenshot(page, testInfo, "game-table-tablet", 768, 1024);
  await expect(page.getByRole("region", { name: "Turn controls" })).toBeVisible();

  await expectViewportScreenshot(page, testInfo, "game-table-mobile", 390, 844);
  await expect(page.getByRole("region", { name: "Turn controls" })).toBeInViewport();
});

test("captures five-player long-name and stacked-token game-table screenshots", async ({ page }, testInfo) => {
  await createGame(page, "art-screenshot-five-player-stack", [
    { color: "#0f766e", kind: "human", name: "Ada Lovelace Longname" },
    { color: "#7c3aed", kind: "ai", name: "Grace Hopper Longname" },
    { color: "#2563eb", kind: "human", name: "Linus Torvalds Longname" },
    { color: "#dc2626", kind: "ai", name: "Marie Curie Longname" },
    { color: "#ca8a04", kind: "ai", name: "Nia Franklin Longname" },
  ]);

  await expect(page.locator("[data-player-token][data-space-index='0']")).toHaveCount(5);
  await expect(page.getByRole("region", { name: "Player trays" })).toContainText("Ada Lovelace Longname");
  await expectViewportScreenshot(page, testInfo, "game-table-five-player-stack", 1440, 900);
});

test("captures contract-heavy and rejected-log secondary screenshots", async ({ page }, testInfo) => {
  await createGame(page, "stage-5-7-contracts-log", [
    { color: "#0f766e", kind: "human", name: "Ada" },
    { color: "#7c3aed", kind: "human", name: "Grace" },
    { color: "#c2410c", kind: "ai", name: "Linus" },
  ]);
  await page.getByRole("tab", { name: "Contracts" }).click();

  const panel = page.getByRole("region", { name: "Contracts obligations panel" });
  await expect(panel).toContainText("Agreement between Ada, Grace");
  await expect(panel).not.toContainText("contract_id");
  await expect(page.getByRole("region", { name: "Game log" })).toContainText("Rejected action");
  await expectViewportScreenshot(page, testInfo, "game-table-contract-heavy", 1180, 900);
});

test("captures AI thinking and rejected-action stress screenshots", async ({ page }, testInfo) => {
  await page.route(`${mockApiBaseUrl}/games/*/ai/step`, async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 900));
    const response = await route.fetch();
    await route.fulfill({ response });
  });

  await createGame(page, "stage-7-6-ai-step-mixed", [
    { color: "#7c3aed", kind: "ai", name: "Grace" },
    { color: "#0f766e", kind: "human", name: "Ada" },
  ]);

  const controls = page.getByRole("region", { name: "Turn controls" });
  const aiStepDone = page.waitForResponse((response) => response.url().includes("/ai/step") && response.request().method() === "POST");
  await controls.getByRole("button", { name: "Step AI" }).click();
  await expect(page.getByRole("status", { name: "AI step status" })).toContainText("AI thinking");
  await expectViewportScreenshot(page, testInfo, "game-table-ai-thinking", 900, 760);
  await aiStepDone;
  await expect(page.getByRole("status", { name: "AI step status" })).toContainText("AI done");

  await page.unroute(`${mockApiBaseUrl}/games/*/ai/step`);
  await createGame(page, "stage-7-6-ai-blocked", [
    { color: "#7c3aed", kind: "ai", name: "Grace" },
    { color: "#0f766e", kind: "human", name: "Ada" },
  ]);
  await page.getByRole("region", { name: "Turn controls" }).getByRole("button", { name: "Step AI" }).click();
  await expect(page.getByRole("alert", { name: "Rejected action" })).toContainText("codex_exec_timeout");
  await expectViewportScreenshot(page, testInfo, "game-table-ai-blocked-rejection", 900, 760);
});

test("captures game-over winner board art screenshot", async ({ page }, testInfo) => {
  await createGame(page, "art-screenshot-game-over", [
    { color: "#0f766e", kind: "human", name: "Ada Champion" },
    { color: "#7c3aed", kind: "human", name: "Grace Bankrupt" },
  ]);

  await expect(page.getByRole("status", { name: "Winner Ada Champion!" })).toBeVisible();
  await expectViewportScreenshot(page, testInfo, "game-table-game-over", 1440, 900);
});
