import { describe, expect, it } from "vitest";

import playwrightConfig from "./playwright.config";
import packageJson from "./package.json" with { type: "json" };

describe("Playwright Chrome configuration", () => {
  it("runs the browser e2e suite against installed Google Chrome", () => {
    expect(playwrightConfig.projects).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          name: "chrome",
          use: expect.objectContaining({
            channel: "chrome",
          }),
        }),
      ]),
    );
  });

  it("exposes an explicit Chrome e2e script for human-browser parity checks", () => {
    expect(packageJson.scripts["test:e2e:chrome"]).toBe("playwright test --project=chrome");
  });
});
