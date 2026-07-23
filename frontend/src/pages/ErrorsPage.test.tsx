import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { ErrorsPage } from "./ErrorsPage";

afterEach(() => vi.restoreAllMocks());

test("shows per-document extraction errors with a retry action", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(
      JSON.stringify({
        data: [
          {
            id: "doc-1",
            source_location_id: "loc-1",
            display_path: "/sources/docs/locked.pdf",
            file_name: "locked.pdf",
            extension: "pdf",
            mime_type: null,
            state: "failed",
            size_bytes: 2048,
            modified_at: null,
            sha256: null,
            extraction_status: "failed",
            error: { code: "encrypted", message: "password protected" },
            content_object: null,
            indexed_at: null,
            updated_at: "2026-01-02T03:04:05Z",
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
      <ErrorsPage />
    </QueryClientProvider>,
  );

  expect(await screen.findByText("locked.pdf")).toBeInTheDocument();
  expect(screen.getByText("encrypted")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
});
