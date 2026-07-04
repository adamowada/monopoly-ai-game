import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DashboardShell } from "./dashboard-shell";

function renderDashboard() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <DashboardShell
        initialHealth={{
          state: "online",
          checkedAt: "2026-07-04T00:00:00.000Z",
          health: {
            status: "ok",
            service: "api",
            stage: "phase-1-stage-1.3",
            environment: "test",
            database: "configured",
          },
        }}
      />
    </QueryClientProvider>,
  );
}

describe("DashboardShell", () => {
  it("renders the operational app shell with backend health and tier records", () => {
    renderDashboard();

    expect(
      screen.getByRole("heading", {
        level: 1,
        name: "Local Game Research Console",
      }),
    ).toBeInTheDocument();

    const navigation = screen.getAllByRole("navigation", { name: "Stack navigation" })[0];
    expect(within(navigation).getByRole("link", { name: /Overview/ })).toHaveAttribute("href", "#overview");
    expect(within(navigation).getByRole("link", { name: /Tier health/ })).toHaveAttribute("href", "#tier-health");

    const healthStatus = screen.getByRole("status", { name: "Backend health" });
    expect(healthStatus).toHaveTextContent("ok");
    expect(healthStatus).toHaveTextContent("phase-1-stage-1.3");
    expect(healthStatus).toHaveTextContent("test");

    expect(screen.getByRole("row", { name: /FastAPI service ok phase-1-stage-1.3/ })).toBeInTheDocument();
    expect(screen.getByRole("row", { name: /Next.js app ready Stage 1.4 shell/ })).toBeInTheDocument();
    expect(screen.getByRole("row", { name: /Postgres configured compose service/ })).toBeInTheDocument();
  });
});
