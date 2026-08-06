import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { CoveragePage } from "./CoveragePage";

afterEach(() => vi.restoreAllMocks());

test("renders per-location coverage counts by state", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(
      JSON.stringify({
        data: [
          {
            source_location_id: "l1",
            name: "north-library",
            total: 3,
            by_state: { indexed: 2, failed: 1 },
          },
        ],
      }),
      { status: 200 },
    ),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <CoveragePage />
    </QueryClientProvider>,
  );

  expect(await screen.findByText("north-library")).toBeInTheDocument();
  const row = screen.getByText("north-library").closest("tr")!;
  expect(row).toHaveTextContent("3"); // total
  expect(row).toHaveTextContent("2"); // indexed
  expect(row).toHaveTextContent("1"); // failed
});
