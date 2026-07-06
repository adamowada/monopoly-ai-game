import { defineConfig, devices } from "@playwright/test";

const webPort = process.env.PORT ?? "13101";
const mockApiPort = process.env.MOCK_API_PORT ?? "18101";
const baseURL = process.env.PLAYWRIGHT_BASE_URL ?? `http://127.0.0.1:${webPort}`;
const mockApiBaseUrl = `http://127.0.0.1:${mockApiPort}`;
const shouldStartServers = !process.env.PLAYWRIGHT_BASE_URL;

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  expect: {
    timeout: 10_000,
  },
  use: {
    baseURL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: shouldStartServers
    ? [
        {
          command: "node scripts/mock-api.mjs",
          url: `${mockApiBaseUrl}/health`,
          env: {
            MOCK_API_PORT: mockApiPort,
          },
          reuseExistingServer: !process.env.CI,
          timeout: 30_000,
        },
        {
          command: `pnpm exec next dev --hostname 127.0.0.1 --port ${webPort}`,
          url: baseURL,
          env: {
            HOSTNAME: "127.0.0.1",
            PORT: webPort,
            INTERNAL_API_BASE_URL: mockApiBaseUrl,
            NEXT_PUBLIC_API_BASE_URL: mockApiBaseUrl,
            NEXT_TELEMETRY_DISABLED: "1",
          },
          reuseExistingServer: !process.env.CI,
          timeout: 60_000,
        },
      ]
    : undefined,
});
