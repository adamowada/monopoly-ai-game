import { readFileSync } from "node:fs";
import { join } from "node:path";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const packageJson = JSON.parse(readFileSync(join(root, "package.json"), "utf8"));

assert.equal(packageJson.name, "@monopoly-ai-game/web");
assert.equal(packageJson.scripts.dev, "node scripts/dev.mjs");
assert.equal(packageJson.scripts.test, "node scripts/scaffold-check.mjs");
assert.equal(packageJson.scripts.typecheck, "tsc --noEmit");

const page = readFileSync(join(root, "app", "page.tsx"), "utf8");
for (const marker of [
  "Monopoly 2.0 Game Table",
  "DashboardShell",
  "readBackendHealth",
]) {
  assert.ok(page.includes(marker), `page is missing ${marker}`);
}

console.log("web scaffold: ok");
