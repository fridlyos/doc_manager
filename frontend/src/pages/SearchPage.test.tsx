import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { SearchPage } from "./SearchPage";

afterEach(() => vi.restoreAllMocks());

function mockFetch(hit: unknown) {
  return vi.spyOn(globalThis, "fetch").mockImplementation((url: string | URL | Request) => {
    const href = String(url);
    if (href.includes("/api/v1/locations")) {
      return Promise.resolve(
        new Response(JSON.stringify({ data: [], page: {} }), { status: 200 }),
      );
    }
    // /api/v1/search
    return Promise.resolve(
      new Response(
        JSON.stringify({ data: { results: [hit], result_count: 1, top_k: 12 }, meta: {} }),
        { status: 200 },
      ),
    );
  });
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <SearchPage />
    </QueryClientProvider>,
  );
}

const HIT = {
  chunk_id: "c1",
  content_object_id: "o1",
  similarity_score: 0.8124,
  page_start: 4,
  page_end: 4,
  snippet: "The term renews through 31 December 2026.",
  availability: "current",
  paths: [
    {
      catalog_entry_id: "e1",
      source_location_id: "l1",
      display_path: "/sources/legal/acme.pdf",
      state: "indexed",
      is_primary: true,
    },
  ],
};

test("submits a query and renders a hit with path, page, and score", async () => {
  const fetchMock = mockFetch(HIT);
  renderPage();

  fireEvent.change(screen.getByPlaceholderText(/when does the contract renew/i), {
    target: { value: "renew" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Search" }));

  expect(await screen.findByText("/sources/legal/acme.pdf")).toBeInTheDocument();
  expect(screen.getByText(/The term renews/)).toBeInTheDocument();
  expect(screen.getByText("p.4")).toBeInTheDocument();
  expect(screen.getByText("current")).toBeInTheDocument();
  expect(screen.getByText("0.812")).toBeInTheDocument();

  const searchCall = fetchMock.mock.calls.find(([url]) => String(url).includes("/api/v1/search"));
  expect(searchCall).toBeTruthy();
  expect(JSON.parse((searchCall?.[1] as RequestInit).body as string)).toEqual({ query: "renew" });
});

test("includes extension filter in the request", async () => {
  const fetchMock = mockFetch(HIT);
  renderPage();

  fireEvent.change(screen.getByPlaceholderText(/when does the contract renew/i), {
    target: { value: "renew" },
  });
  fireEvent.change(screen.getByPlaceholderText("pdf, md"), { target: { value: ".PDF, md" } });
  fireEvent.click(screen.getByRole("button", { name: "Search" }));

  await waitFor(() => {
    const call = fetchMock.mock.calls.find(([url]) => String(url).includes("/api/v1/search"));
    expect(call).toBeTruthy();
    expect(JSON.parse((call?.[1] as RequestInit).body as string)).toEqual({
      query: "renew",
      filters: { extensions: ["pdf", "md"] },
    });
  });
});
