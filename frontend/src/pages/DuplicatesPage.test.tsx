import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { DuplicatesPage } from "./DuplicatesPage";

afterEach(() => vi.restoreAllMocks());

function listResponse() {
  return new Response(
    JSON.stringify({
      data: [
        { id: "g1", kind: "exact", group_hash: "a".repeat(64), member_count: 2, built_at: null },
        { id: "g2", kind: "text", group_hash: "b".repeat(64), member_count: 3, built_at: null },
      ],
      page: { limit: 50, has_more: false, next_cursor: null },
    }),
    { status: 200 },
  );
}

function groupResponse() {
  return new Response(
    JSON.stringify({
      data: {
        id: "g1",
        kind: "exact",
        group_hash: "a".repeat(64),
        member_count: 2,
        built_at: null,
        members: [
          {
            catalog_entry_id: "e1",
            source_location_id: "l1",
            display_path: "/sources/one.pdf",
            state: "indexed",
            sha256: "a".repeat(64),
          },
          {
            catalog_entry_id: "e2",
            source_location_id: "l1",
            display_path: "/sources/two.pdf",
            state: "indexed",
            sha256: "a".repeat(64),
          },
        ],
      },
    }),
    { status: 200 },
  );
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <DuplicatesPage />
    </QueryClientProvider>,
  );
}

test("lists duplicate groups and expands member paths", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation((url) => {
    if (String(url).includes("/duplicates/g1")) return Promise.resolve(groupResponse());
    return Promise.resolve(listResponse());
  });
  renderPage();

  expect(await screen.findByText("exact")).toBeInTheDocument();
  expect(screen.getByText("text")).toBeInTheDocument();

  fireEvent.click(screen.getAllByRole("button", { name: "Show paths" })[0]);
  expect(await screen.findByText("/sources/one.pdf")).toBeInTheDocument();
  expect(screen.getByText("/sources/two.pdf")).toBeInTheDocument();
});

test("rebuild posts to the rebuild endpoint", async () => {
  const fetchMock = vi
    .spyOn(globalThis, "fetch")
    .mockImplementation((url) => {
      if (String(url).includes("/duplicates/rebuild")) {
        return Promise.resolve(new Response(JSON.stringify({ data: { id: "j1" } }), { status: 202 }));
      }
      return Promise.resolve(listResponse());
    });
  renderPage();

  await screen.findByText("exact");
  fireEvent.click(screen.getByRole("button", { name: "Rebuild report" }));

  await waitFor(() => {
    const call = fetchMock.mock.calls.find(([u]) => String(u).includes("/duplicates/rebuild"));
    expect(call).toBeTruthy();
    expect((call?.[1] as RequestInit).method).toBe("POST");
    expect((call?.[1] as RequestInit).headers).toHaveProperty("Idempotency-Key");
  });
});
