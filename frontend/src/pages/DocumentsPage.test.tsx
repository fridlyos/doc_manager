import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { DocumentsPage } from "./DocumentsPage";

afterEach(() => vi.restoreAllMocks());

function docsResponse() {
  return new Response(
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
          modified_at: "2026-01-02T03:04:05Z",
          sha256: "e".repeat(64),
          extraction_status: "failed",
          error: { code: "encrypted", message: "the pdf is password protected" },
          content_object: null,
          indexed_at: null,
          updated_at: "2026-01-02T03:04:05Z",
        },
      ],
      page: { limit: 50, has_more: false, next_cursor: null },
    }),
    { status: 200 },
  );
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <DocumentsPage />
    </QueryClientProvider>,
  );
}

test("lists documents and shows error detail on expand", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(docsResponse());
  renderPage();

  expect(await screen.findByText("locked.pdf")).toBeInTheDocument();
  expect(screen.getByText("failed")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "locked.pdf" }));
  expect(await screen.findByText("/sources/docs/locked.pdf")).toBeInTheDocument();
  expect(screen.getByText("encrypted")).toBeInTheDocument();
});

test("reindex posts to the document reindex endpoint", async () => {
  const fetchMock = vi
    .spyOn(globalThis, "fetch")
    .mockResolvedValueOnce(docsResponse())
    .mockResolvedValue(
      new Response(JSON.stringify({ data: { id: "job-9" }, meta: {} }), { status: 202 }),
    );
  renderPage();

  const reindexButton = await screen.findByRole("button", { name: "Reindex" });
  fireEvent.click(reindexButton);

  await waitFor(() => {
    const call = fetchMock.mock.calls.find(([url]) =>
      String(url).includes("/api/v1/documents/doc-1/reindex"),
    );
    expect(call).toBeTruthy();
    expect((call?.[1] as RequestInit).method).toBe("POST");
    expect((call?.[1] as RequestInit).headers).toHaveProperty("Idempotency-Key");
  });
});
