import { fireEvent, render, screen } from "@testing-library/react";
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
  return render(
    <DashboardShell initialRejectedActions={rejectedActions} />,
  );
}

describe("DashboardShell", () => {
  it("renders a board-game setup surface instead of an admin dashboard shell", () => {
    renderDashboard();

    expect(
      screen.getByRole("heading", {
        level: 1,
        name: "Monopoly 2.0 Game Table",
      }),
    ).toBeInTheDocument();

    expect(screen.queryByRole("navigation", { name: "Game prep navigation" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Setup" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Connection details" })).not.toBeInTheDocument();
    expect(screen.queryByRole("status", { name: "Referee readiness" })).not.toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { level: 2, name: "Set the seats, then open the board." }),
    ).not.toBeInTheDocument();

    expect(screen.getByRole("region", { name: "Choose seats" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { level: 2, name: "Table check" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { level: 2, name: "Table areas" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Connection details" })).not.toBeInTheDocument();
    expect(screen.queryByRole("row", { name: /Rules referee ready Move validation/ })).not.toBeInTheDocument();
  });

  it("keeps rule rulings inside troubleshooting details", () => {
    renderDashboard([rejectedAction]);

    expect(screen.queryByRole("heading", { level: 2, name: "Rule rulings" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Rule rulings" }));
    expect(screen.getByRole("heading", { level: 2, name: "Rule rulings" })).toBeInTheDocument();
    expect(screen.getByRole("row", { name: /illegal_action START_TURN BUY_PROPERTY/ })).toBeInTheDocument();
  });
});
