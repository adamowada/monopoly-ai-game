import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DashboardShell } from "./dashboard-shell";
import type { RejectedActionRecord } from "../lib/api/rejected-actions";

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: vi.fn(),
  }),
}));

const rejectedAction: RejectedActionRecord = {
  id: "11111111-1111-1111-1111-111111111111",
  game_id: "22222222-2222-2222-2222-222222222222",
  actor_player_id: "33333333-3333-3333-3333-333333333333",
  action_type: "BUY_PROPERTY",
  payload: { property_id: "property_boardwalk" },
  reason_code: "illegal_action",
  validation_errors: [
    {
      code: "illegal_action",
      message: "player is not on property_boardwalk",
      field: "payload.property_id",
    },
  ],
  legal_action_context: {
    phase: "START_TURN",
    legal_actions: ["ROLL_DICE", "DECLARE_BANKRUPTCY"],
  },
  phase: "START_TURN",
  state_hash: "abc123",
  created_at: "2026-07-04T12:00:00.000Z",
};

function renderDashboard(rejectedActions: RejectedActionRecord[] = []) {
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
        initialRejectedActions={rejectedActions}
      />
    </QueryClientProvider>,
  );
}

describe("DashboardShell", () => {
  it("renders the tabletop app shell with table connection and area records", () => {
    renderDashboard();

    expect(
      screen.getByRole("heading", {
        level: 1,
        name: "Monopoly 2.0 Game Table",
      }),
    ).toBeInTheDocument();

    const navigation = screen.getAllByRole("navigation", { name: "Table navigation" })[0];
    expect(within(navigation).getByRole("link", { name: /Overview/ })).toHaveAttribute("href", "#overview");
    expect(within(navigation).getByRole("link", { name: /Table check/ })).toHaveAttribute("href", "#table-check");
    expect(within(navigation).getByRole("link", { name: /Rulings/ })).toHaveAttribute(
      "href",
      "#rulings",
    );

    const healthStatus = screen.getByRole("status", { name: "Table connection" });
    expect(healthStatus).toHaveTextContent("Ready");
    expect(healthStatus).toHaveTextContent("Rules referee");
    expect(healthStatus).toHaveTextContent("Move validation ready");
    expect(healthStatus).toHaveTextContent("Local table");
    expect(healthStatus).toHaveTextContent("12:00:00 AM UTC");

    expect(screen.getByRole("row", { name: /Rules referee ready Move validation/ })).toBeInTheDocument();
    expect(screen.getByRole("row", { name: /Game board ready Board and controls/ })).toBeInTheDocument();
    expect(screen.getByRole("row", { name: /Save data ready Game persistence/ })).toBeInTheDocument();
  });

  it("mounts the rule rulings view inside the app shell", () => {
    renderDashboard([rejectedAction]);

    expect(screen.getByRole("heading", { level: 2, name: "Rule rulings" })).toBeInTheDocument();
    expect(screen.getByRole("row", { name: /illegal_action START_TURN BUY_PROPERTY/ })).toBeInTheDocument();
  });
});
