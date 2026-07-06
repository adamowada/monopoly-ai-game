import { expect, test, type Locator, type Page } from "@playwright/test";

type TestPlayer = {
  name: string;
  kind: "human" | "ai";
  color: string;
};

const players: TestPlayer[] = [
  { name: "Ada", kind: "human", color: "#0f766e" },
  { name: "Grace", kind: "human", color: "#7c3aed" },
];

const FULL_REVIEW_LONG_TASK_LIMIT = 30;

async function createGame(page: Page) {
  await page.addInitScript(() => {
    const bucket = { longTasks: [] as number[] };
    Object.defineProperty(window, "__stage114RenderMetrics", {
      configurable: true,
      value: bucket,
    });
    try {
      const observer = new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) {
          bucket.longTasks.push(entry.duration);
        }
      });
      observer.observe({ type: "longtask", buffered: true });
    } catch {
      // Long task entries are Chromium-only; the test still records timing, DOM, and heap signals.
    }
  });

  await page.goto("/");
  await page.getByRole("textbox", { name: "Seed" }).fill("stage-10-5-two-human-full-round-stage-11-4");
  for (const [index, player] of players.entries()) {
    const playerNumber = index + 1;
    await page.getByRole("textbox", { name: `Player ${playerNumber} name` }).fill(player.name);
    await page.getByRole("combobox", { name: `Player ${playerNumber} type` }).selectOption(player.kind);
    await page.getByRole("textbox", { name: `Player ${playerNumber} color hex` }).fill(player.color);
  }

  await page.getByRole("button", { name: "Create game" }).click();
  await expect(page).toHaveURL(/\/games\/mock-game-\d+$/);
}

async function clickFirstEnabled(page: Page, locator: Locator): Promise<boolean> {
  const count = await locator.count();
  for (let index = 0; index < count; index += 1) {
    const candidate = locator.nth(index);
    if ((await candidate.isVisible()) && (await candidate.isEnabled())) {
      const actionResponse = page.waitForResponse(
        (response) => response.url().includes("/actions") && response.request().method() === "POST",
      );
      await candidate.click();
      await actionResponse;
      return true;
    }
  }
  return false;
}

async function submitOneStateUpdate(page: Page): Promise<string> {
  const controls = page.getByRole("region", { name: "Turn controls" });
  const actions = ["Roll dice", "Buy property", "Settle debt", "End turn"];
  const deadline = Date.now() + 8_000;
  while (Date.now() < deadline) {
    for (const actionName of actions) {
      const action = controls.getByRole("button", { name: actionName, exact: true });
      if (await clickFirstEnabled(page, action)) {
        return actionName;
      }
    }
    await page.waitForTimeout(100);
  }
  throw new Error("No enabled state update action was available");
}

test("stage-11-4-render-reliability: board panels property management and AI audit remain readable after repeated state updates with performance signal", async ({
  page,
}) => {
  test.setTimeout(60_000);
  await createGame(page);

  const board = page.getByRole("region", { name: "Classic Monopoly-style board" });
  const propertyManagement = page.getByRole("region", { name: "Property management" });
  const contracts = page.getByRole("region", { name: "Contracts obligations panel" });
  const negotiation = page.getByRole("region", { name: "Negotiation inbox" });
  const aiAudit = page.getByRole("region", { name: "AI audit" });
  const activePlayer = page.getByRole("region", { name: "Active player" });

  await expect(board).toBeVisible();
  await expect(propertyManagement).toBeVisible();
  await expect(contracts).toBeVisible();
  await expect(negotiation).toBeVisible();
  await expect(aiAudit).toBeVisible();

  const updateCount = 24;
  const startedAt = await page.evaluate(() => performance.now());
  const submittedActions: string[] = [];
  for (let index = 0; index < updateCount; index += 1) {
    submittedActions.push(await submitOneStateUpdate(page));
  }
  const metrics = await page.evaluate((startMark) => {
    const performanceWithMemory = performance as Performance & {
      memory?: { usedJSHeapSize?: number };
    };
    const renderMetrics = window as typeof window & {
      __stage114RenderMetrics?: { longTasks: number[] };
    };
    return {
      elapsedMs: performance.now() - startMark,
      domNodes: document.querySelectorAll("*").length,
      longTaskCount: renderMetrics.__stage114RenderMetrics?.longTasks.length ?? 0,
      maxLongTaskMs: Math.max(0, ...(renderMetrics.__stage114RenderMetrics?.longTasks ?? [])),
      resourceCount: performance.getEntriesByType("resource").length,
      usedJSHeapSize: performanceWithMemory.memory?.usedJSHeapSize ?? null,
    };
  }, startedAt);

  await expect(board).toBeVisible();
  await expect(board).toContainText("GO");
  await expect(activePlayer).toBeVisible();
  await expect(activePlayer).toContainText(/Ada|Grace/);
  await expect(propertyManagement).toBeVisible();
  await expect(propertyManagement).toContainText("Property management");
  await expect(propertyManagement).toContainText("Bank inventory");
  await expect(contracts).toContainText("Active contracts");
  await expect(negotiation).toContainText("Negotiation inbox");
  await expect(aiAudit).toBeVisible();
  await expect(aiAudit).toContainText("AI notebook");

  expect(submittedActions).toContain("Roll dice");
  expect(submittedActions).toContain("End turn");
  expect(metrics.elapsedMs / updateCount).toBeLessThan(1_250);
  expect(metrics.domNodes).toBeLessThan(7_500);
  expect(metrics.longTaskCount).toBeLessThan(FULL_REVIEW_LONG_TASK_LIMIT);
  expect(metrics.maxLongTaskMs).toBeLessThan(1_000);
  expect(metrics.resourceCount).toBeLessThan(350);
  if (metrics.usedJSHeapSize !== null) {
    expect(metrics.usedJSHeapSize).toBeLessThan(140 * 1024 * 1024);
  }
});
