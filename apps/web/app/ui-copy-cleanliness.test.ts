import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const appDir = path.join(process.cwd(), "app");

const bannedUiCopy = [
  "Created from accepted structured terms.",
  "Hotel conversion:",
  "Loading AI profiles.",
  "Loading decision history.",
  "Loading memory entries.",
  "Loading messages.",
  "Loading negotiations.",
  "Loading notebook stream.",
  "Loading retrieved context.",
  "No active contracts.",
  "No active player assigned.",
  "No AI decisions.",
  "No AI profiles.",
  "No AI thoughts or memories.",
  "No auction result yet.",
  "No completed turn result yet.",
  "No contract outcome explanations.",
  "No legal auction action",
  "No log entries match the selected filters.",
  "No matching log events yet.",
  "No memory records.",
  "No messages yet.",
  "No negotiation selected.",
  "No negotiations yet.",
  "No rejected actions recorded.",
  "No retrieved context.",
  "No saved games yet.",
  "No settled obligations.",
  "No upcoming obligations.",
];

function sourceFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      return sourceFiles(fullPath);
    }
    if (!entry.isFile() || !/\.(tsx|ts)$/.test(entry.name) || entry.name.endsWith(".test.tsx") || entry.name.endsWith(".test.ts")) {
      return [];
    }
    return [fullPath];
  });
}

describe("UI copy cleanliness", () => {
  const files = sourceFiles(appDir);

  it("does not render paragraph tags in app UI source", () => {
    const offenders = files.filter((filePath) => /<\/?p\b/.test(readFileSync(filePath, "utf8")));
    expect(offenders).toEqual([]);
  });

  it("does not keep filler loading or empty-state prose in app UI source", () => {
    const offenders = files.flatMap((filePath) => {
      const source = readFileSync(filePath, "utf8");
      return bannedUiCopy
        .filter((copy) => source.includes(copy))
        .map((copy) => `${path.relative(process.cwd(), filePath)}: ${copy}`);
    });

    expect(offenders).toEqual([]);
  });
});
