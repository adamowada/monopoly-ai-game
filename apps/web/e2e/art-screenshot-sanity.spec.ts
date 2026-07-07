import { inflateSync } from "node:zlib";
import { expect, test, type Page, type TestInfo } from "@playwright/test";

type PngInfo = {
  width: number;
  height: number;
  uniqueColorCount: number;
};

async function createGame(page: Page, seed: string) {
  await page.goto("/");
  await page.getByRole("textbox", { name: "Seed" }).fill(seed);
  await page.getByRole("textbox", { name: "Player 1 name" }).fill("Ada");
  await page.getByRole("textbox", { name: "Player 2 name" }).fill("Grace");
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

  await expectViewportScreenshot(page, testInfo, "game-table-mobile", 390, 844);
  await expect(page.getByRole("region", { name: "Turn controls" })).toBeInViewport();
});
