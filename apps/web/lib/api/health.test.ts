import { describe, expect, it, vi } from "vitest";

import { readBackendHealth } from "./health";

describe("readBackendHealth", () => {
  it("reads and validates the backend health endpoint", async () => {
    const fetcher = vi.fn(async () =>
      Response.json({
        status: "ok",
        service: "api",
        stage: "phase-1-stage-1.3",
        environment: "test",
        database: "configured",
      }),
    );

    const snapshot = await readBackendHealth({
      baseUrl: "http://api.test",
      fetcher,
      checkedAt: () => "2026-07-04T00:00:00.000Z",
    });

    expect(fetcher).toHaveBeenCalledWith("http://api.test/health", {
      cache: "no-store",
      headers: { accept: "application/json" },
    });
    expect(snapshot).toEqual({
      state: "online",
      checkedAt: "2026-07-04T00:00:00.000Z",
      health: {
        status: "ok",
        service: "api",
        stage: "phase-1-stage-1.3",
        environment: "test",
        database: "configured",
      },
    });
  });

  it("returns an offline snapshot when the response shape drifts", async () => {
    const fetcher = vi.fn(async () =>
      Response.json({
        status: "ok",
        service: "api",
        environment: "test",
      }),
    );

    const snapshot = await readBackendHealth({
      baseUrl: "http://api.test/",
      fetcher,
      checkedAt: () => "2026-07-04T00:00:00.000Z",
    });

    expect(snapshot.state).toBe("offline");
    expect(snapshot.checkedAt).toBe("2026-07-04T00:00:00.000Z");
    if (snapshot.state === "offline") {
      expect(snapshot.error).toContain("Invalid backend health response");
    }
  });
});
