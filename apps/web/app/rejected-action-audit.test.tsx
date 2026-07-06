import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RejectedActionAuditView } from "./rejected-action-audit";
import type { RejectedActionRecord } from "../lib/api/rejected-actions";

const records: RejectedActionRecord[] = [
  {
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
  },
];

describe("RejectedActionAuditView", () => {
  it("renders rejected action rows with actor, reason, phase, type, timestamp, and validation details", () => {
    render(<RejectedActionAuditView records={records} />);

    expect(
      screen.getByRole("heading", {
        level: 2,
        name: "Rule rulings",
      }),
    ).toBeInTheDocument();

    const row = screen.getByRole("row", {
      name: /33333333-3333-3333-3333-333333333333 illegal_action START_TURN BUY_PROPERTY/,
    });
    expect(within(row).getByText("33333333-3333-3333-3333-333333333333")).toBeInTheDocument();
    expect(within(row).getByText("illegal_action")).toBeInTheDocument();
    expect(within(row).getByText("START_TURN")).toBeInTheDocument();
    expect(within(row).getByText("BUY_PROPERTY")).toBeInTheDocument();
    expect(within(row).getByText("Jul 04, 2026, 12:00 PM")).toBeInTheDocument();
    expect(within(row).getByText(/payload.property_id/)).toBeInTheDocument();
    expect(within(row).getByText(/player is not on property_boardwalk/)).toBeInTheDocument();
  });

  it("renders an empty state without interactive affordance when no rejected actions exist", () => {
    render(<RejectedActionAuditView records={[]} />);

    expect(screen.getByText("No rejected actions recorded.")).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
