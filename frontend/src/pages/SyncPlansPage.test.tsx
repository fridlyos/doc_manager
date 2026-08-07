import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { SyncPlansPage } from "./SyncPlansPage";

afterEach(() => vi.restoreAllMocks());

function locations() {
  return {
    ok: true,
    status: 200,
    json: async () => ({
      data: [
        { id: "l1", name: "north", revision: 1 },
        { id: "l2", name: "south", revision: 1 },
      ],
      page: {},
    }),
  } as Response;
}

const PLAN = {
  id: "p1",
  source_location_id: "l1",
  target_location_id: "l2",
  status: "ready",
  item_count: 2,
  covered_percent: 50,
  summary: { copy: 1, conflict: 1 },
  error_code: null,
  built_at: null,
  created_at: "2026-01-01T00:00:00Z",
};

function plans(data: unknown[]) {
  return { ok: true, status: 200, json: async () => ({ data, page: {} }) } as Response;
}

function items() {
  return {
    ok: true,
    status: 200,
    json: async () => ({
      data: [
        {
          id: "i1",
          action: "copy",
          reason: "missing_in_target",
          source_relative_path: "new.txt",
          source_sha256: "a",
          target_relative_path: null,
          target_sha256: null,
        },
        {
          id: "i2",
          action: "conflict",
          reason: "path_hash_mismatch",
          source_relative_path: "clash.txt",
          source_sha256: "b",
          target_relative_path: "clash.txt",
          target_sha256: "c",
        },
      ],
      page: {},
    }),
  } as Response;
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <SyncPlansPage />
    </QueryClientProvider>,
  );
}

test("lists plans and expands items with conflict/copy", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation((url) => {
    const href = String(url);
    if (href.includes("/locations")) return Promise.resolve(locations());
    if (href.includes("/sync-plans/p1/items")) return Promise.resolve(items());
    if (href.includes("/sync-plans")) return Promise.resolve(plans([PLAN]));
    return Promise.resolve(plans([]));
  });
  renderPage();

  expect(await screen.findByText("north → south")).toBeInTheDocument();
  expect(screen.getByText("50%")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "View" }));
  expect(await screen.findByText("new.txt")).toBeInTheDocument();
  expect(screen.getAllByText("clash.txt").length).toBeGreaterThan(0);
  expect(screen.getByText("Conflict")).toBeInTheDocument();
});

test("compare posts source + target to create a plan", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((url, init) => {
    const href = String(url);
    if (href.includes("/locations")) return Promise.resolve(locations());
    if (init?.method === "POST") {
      return Promise.resolve({ ok: true, status: 202, json: async () => ({ data: PLAN }) } as Response);
    }
    return Promise.resolve(plans([]));
  });
  renderPage();

  await screen.findAllByRole("option", { name: "north" });
  const [sourceSel, targetSel] = screen.getAllByRole("combobox");
  fireEvent.change(sourceSel, { target: { value: "l1" } });
  fireEvent.change(targetSel, { target: { value: "l2" } });
  fireEvent.click(screen.getByRole("button", { name: "Compare" }));

  await waitFor(() => {
    const call = fetchMock.mock.calls.find(
      ([u, i]) => String(u).includes("/sync-plans") && (i as RequestInit)?.method === "POST",
    );
    expect(call).toBeTruthy();
    const body = JSON.parse((call?.[1] as RequestInit).body as string);
    expect(body).toEqual({ source_location_id: "l1", target_location_id: "l2" });
    expect((call?.[1] as RequestInit).headers).toHaveProperty("Idempotency-Key");
  });
});
