import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { GameApiPanel } from "./game-api-panel";

describe("GameApiPanel", () => {
  it("renders create and load controls in the app shell", () => {
    render(<GameApiPanel />);

    expect(screen.getByRole("heading", { level: 2, name: "Saved table" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create game" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Game ID" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Load game" })).toBeInTheDocument();
  });
});
