import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { JobsPage } from "./JobsPage";

afterEach(() => vi.restoreAllMocks());

test("renders durable job state", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(
      JSON.stringify({
        data: [
          {
            id: "job-1",
            job_type: "scan_location",
            status: "succeeded",
            attempt_count: 1,
            max_attempts: 3,
            requested_at: "2026-01-02T03:04:05Z",
            finished_at: "2026-01-02T03:04:06Z",
            target: null,
            error: null,
          },
        ],
        page: { limit: 50, has_more: false, next_cursor: null },
      }),
      { status: 200 },
    ),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <JobsPage />
    </QueryClientProvider>,
  );

  // Scope to the table — "scan_location"/"succeeded" also appear as filter options.
  const table = await screen.findByRole("table");
  expect(within(table).getByText("scan_location")).toBeInTheDocument();
  expect(within(table).getByText("succeeded")).toBeInTheDocument();
  expect(within(table).getByText("1/3")).toBeInTheDocument();
});
