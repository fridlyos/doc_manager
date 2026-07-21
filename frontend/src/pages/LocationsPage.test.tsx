import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { LocationsPage, detectPathStyle } from "./LocationsPage";

afterEach(() => vi.restoreAllMocks());

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status });
}

function capabilities(nativePicker: boolean, profile: "windows" | "unix" = "unix") {
  return json({
    data: { filesystem_profile: profile, native_picker_available: nativePicker },
    meta: {},
  });
}

const emptyList = { data: [], page: { limit: 50, has_more: false, next_cursor: null } };

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <LocationsPage />
    </QueryClientProvider>,
  );
}

function postCall(fetchMock: { mock: { calls: unknown[][] } }, url: string, method: string) {
  return fetchMock.mock.calls.find(
    (c) => String(c[0]) === url && (c[1] as RequestInit | undefined)?.method === method,
  );
}

test("detects path style from scan root shape", () => {
  expect(detectPathStyle("/sources/docs")).toBe("linux");
  expect(detectPathStyle("Z:\\Documents")).toBe("mapped_drive");
  expect(detectPathStyle("z:/Documents")).toBe("mapped_drive");
  expect(detectPathStyle("\\\\nas01\\documents")).toBe("unc");
});

test("lists locations and submits a new location", async () => {
  const docs = {
    id: "loc-1",
    name: "Docs",
    scan_root: "/sources/docs",
    display_root: "Z:/Docs",
    enabled: true,
    scan_interval_minutes: null,
    last_successful_scan_at: null,
    revision: 1,
  };
  const fetchMock = vi.spyOn(globalThis, "fetch");
  fetchMock.mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init as RequestInit | undefined)?.method ?? "GET";
    if (url.includes("/capabilities")) return capabilities(false);
    if (url === "/api/v1/locations" && method === "POST") return json({ data: docs }, 201);
    if (url.startsWith("/api/v1/locations")) {
      return json({ data: [docs], page: { limit: 50, has_more: false, next_cursor: null } });
    }
    throw new Error(`unexpected fetch ${method} ${url}`);
  });

  renderPage();

  expect(await screen.findByText("Docs")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Notes" } });
  fireEvent.change(screen.getByLabelText("Scan root"), { target: { value: "Z:\\Notes" } });
  fireEvent.click(screen.getByRole("button", { name: "Add location" }));

  await waitFor(() => expect(postCall(fetchMock, "/api/v1/locations", "POST")).toBeTruthy());
  const post = postCall(fetchMock, "/api/v1/locations", "POST")!;
  expect(JSON.parse((post[1] as RequestInit).body as string)).toEqual({
    name: "Notes",
    scan_root: "Z:\\Notes",
    path_style: "mapped_drive",
  });
});

test("deletes a location with If-Match after confirmation", async () => {
  const confirmMock = vi.spyOn(window, "confirm").mockReturnValue(true);
  const docs = {
    id: "loc-9",
    name: "Docs",
    scan_root: "/sources/docs",
    display_root: "/sources/docs",
    enabled: true,
    scan_interval_minutes: null,
    last_successful_scan_at: null,
    revision: 3,
  };
  const fetchMock = vi.spyOn(globalThis, "fetch");
  fetchMock.mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init as RequestInit | undefined)?.method ?? "GET";
    if (url.includes("/capabilities")) return capabilities(false);
    if (url === "/api/v1/locations/loc-9" && method === "DELETE")
      return new Response(null, { status: 204 });
    if (url.startsWith("/api/v1/locations")) {
      return json({ data: [docs], page: { limit: 50, has_more: false, next_cursor: null } });
    }
    throw new Error(`unexpected fetch ${method} ${url}`);
  });

  renderPage();

  fireEvent.click(await screen.findByRole("button", { name: "Delete" }));
  expect(confirmMock).toHaveBeenCalled();
  await waitFor(() =>
    expect(postCall(fetchMock, "/api/v1/locations/loc-9", "DELETE")).toBeTruthy(),
  );
  const del = postCall(fetchMock, "/api/v1/locations/loc-9", "DELETE")!;
  expect(del[1]).toMatchObject({
    method: "DELETE",
    headers: { "If-Match": '"location-loc-9-3"' },
  });
});

test("browse picker (web fallback) fills the scan-root input", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch");
  fetchMock.mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes("/capabilities")) return capabilities(false); // web modal path
    if (url.includes("/browse")) {
      if (url.includes("path=")) {
        return json({
          data: {
            path: "/sources",
            path_style: "linux",
            parent: null,
            entries: [{ name: "nas", path: "/sources/nas", kind: "dir" }],
          },
          meta: {},
        });
      }
      return json({
        data: {
          path: null,
          path_style: "linux",
          parent: null,
          entries: [{ name: "/sources", path: "/sources", kind: "dir" }],
        },
        meta: {},
      });
    }
    if (url.startsWith("/api/v1/locations")) return json(emptyList);
    throw new Error(`unexpected fetch ${url}`);
  });

  renderPage();

  await screen.findByText("No locations configured.");
  fireEvent.click(screen.getByRole("button", { name: "Browse…" }));
  fireEvent.click(await screen.findByRole("button", { name: /\/sources/ }));
  fireEvent.click(screen.getByRole("button", { name: "Select this folder" }));

  await waitFor(() =>
    expect((screen.getByLabelText("Scan root") as HTMLInputElement).value).toBe("/sources"),
  );
  expect(screen.queryByRole("dialog")).toBeNull();
});

test("browse uses the native picker when available", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch");
  fetchMock.mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init as RequestInit | undefined)?.method ?? "GET";
    if (url.includes("/capabilities")) return capabilities(true, "windows");
    if (url.includes("/pick-folder") && method === "POST") {
      return json({ data: { path: "Z:\\Docs\\Reports", path_style: "mapped_drive" }, meta: {} });
    }
    if (url.startsWith("/api/v1/locations")) return json(emptyList);
    throw new Error(`unexpected fetch ${method} ${url}`);
  });

  renderPage();

  await screen.findByText("No locations configured.");
  fireEvent.click(screen.getByRole("button", { name: "Browse…" }));

  await waitFor(() =>
    expect((screen.getByLabelText("Scan root") as HTMLInputElement).value).toBe(
      "Z:\\Docs\\Reports",
    ),
  );
  // Native path -> no in-app modal is opened.
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(postCall(fetchMock, "/api/v1/locations/pick-folder", "POST")).toBeTruthy();
});
