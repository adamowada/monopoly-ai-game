import { useQueryClient } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Providers } from "./providers";

function QueryDefaultsProbe() {
  const queryClient = useQueryClient();
  const queries = queryClient.getDefaultOptions().queries;

  return (
    <dl aria-label="Query defaults">
      <dt>gcTime</dt>
      <dd>{String(queries?.gcTime)}</dd>
      <dt>refetchOnWindowFocus</dt>
      <dd>{String(queries?.refetchOnWindowFocus)}</dd>
    </dl>
  );
}

describe("Providers", () => {
  it("uses a short query cache window so long play sessions release stale turn snapshots", () => {
    render(
      <Providers>
        <QueryDefaultsProbe />
      </Providers>,
    );

    expect(screen.getByLabelText("Query defaults")).toHaveTextContent("gcTime60000");
    expect(screen.getByLabelText("Query defaults")).toHaveTextContent("refetchOnWindowFocusfalse");
  });
});
