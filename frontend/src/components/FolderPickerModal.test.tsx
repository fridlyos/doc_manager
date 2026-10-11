import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { FolderPickerModal } from "./FolderPickerModal";

afterEach(() => vi.restoreAllMocks());

function browseResponse(body: unknown) {
  return new Response(JSON.stringify({ data: body, meta: {} }), { status: 200 });
}

function renderModal(onSelect = vi.fn(), onClose = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <FolderPickerModal pathStyle="linux" onSelect={onSelect} onClose={onClose} />
    </QueryClientProvider>,
  );
  return { onSelect, onClose };
}

test("opens on the host drive, navigates into a directory and selects it", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch");
  // The modal opens directly on /hostfs (the Windows/host drive).
  fetchMock.mockResolvedValueOnce(
    browseResponse({
      path: "/hostfs",
      path_style: "linux",
      parent: null,
      entries: [
        { name: "Users", path: "/hostfs/Users", kind: "dir" },
        { name: "pagefile.sys", path: "/hostfs/pagefile.sys", kind: "file" },
      ],
    }),
  );
  // After descending into Users.
  fetchMock.mockResolvedValueOnce(
    browseResponse({
      path: "/hostfs/Users",
      path_style: "linux",
      parent: "/hostfs",
      entries: [{ name: "fridl", path: "/hostfs/Users/fridl", kind: "dir" }],
    }),
  );

  const { onSelect, onClose } = renderModal();

  // File row is not a button (non-navigable).
  expect(await screen.findByText(/pagefile\.sys/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /pagefile\.sys/ })).toBeNull();

  fireEvent.click(await screen.findByRole("button", { name: /Users/ }));
  expect(await screen.findByText(/fridl/)).toBeInTheDocument();

  // Selecting stores the real container path, not the display string.
  fireEvent.click(screen.getByRole("button", { name: "Select this folder" }));
  await waitFor(() => expect(onSelect).toHaveBeenCalledWith("/hostfs/Users"));
  expect(onClose).toHaveBeenCalled();
});

test("ascending to the roots disables select and shows friendly mount labels", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch");
  // Opens on /hostfs; parent null means Up returns to the roots list.
  fetchMock.mockResolvedValueOnce(
    browseResponse({
      path: "/hostfs",
      path_style: "linux",
      parent: null,
      entries: [{ name: "Users", path: "/hostfs/Users", kind: "dir" }],
    }),
  );
  // The roots list (currentPath === null).
  fetchMock.mockResolvedValueOnce(
    browseResponse({
      path: null,
      path_style: "linux",
      parent: null,
      entries: [
        { name: "/hostfs", path: "/hostfs", kind: "dir" },
        { name: "/sources", path: "/sources", kind: "dir" },
      ],
    }),
  );

  renderModal();
  // Wait for the initial /hostfs listing, then ascend to the roots.
  await screen.findByRole("button", { name: /Users/ });
  fireEvent.click(screen.getByRole("button", { name: /↑ Up/ }));

  // Mount roots render with friendly labels, not the raw /hostfs path.
  expect(await screen.findByRole("button", { name: /Windows \/ host drive/ })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Select this folder" })).toBeDisabled();
});

test("Escape closes the modal", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch");
  fetchMock.mockResolvedValueOnce(
    browseResponse({ path: "/hostfs", path_style: "linux", parent: null, entries: [] }),
  );
  const { onClose } = renderModal();
  await screen.findByText("No folders here.");
  fireEvent.keyDown(window, { key: "Escape" });
  expect(onClose).toHaveBeenCalled();
});
