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

test("navigates into a directory and selects it", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch");
  // Initial roots listing.
  fetchMock.mockResolvedValueOnce(
    browseResponse({
      path: null,
      path_style: "linux",
      parent: null,
      entries: [{ name: "/sources", path: "/sources", kind: "dir" }],
    }),
  );
  // After descending into /sources.
  fetchMock.mockResolvedValueOnce(
    browseResponse({
      path: "/sources",
      path_style: "linux",
      parent: null,
      entries: [
        { name: "nas", path: "/sources/nas", kind: "dir" },
        { name: "readme.md", path: "/sources/readme.md", kind: "file" },
      ],
    }),
  );

  const { onSelect, onClose } = renderModal();

  fireEvent.click(await screen.findByRole("button", { name: /\/sources/ }));

  // File row is not a button (non-navigable).
  expect(await screen.findByText(/nas/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /readme\.md/ })).toBeNull();

  fireEvent.click(screen.getByRole("button", { name: "Select this folder" }));
  await waitFor(() => expect(onSelect).toHaveBeenCalledWith("/sources"));
  expect(onClose).toHaveBeenCalled();
});

test("select is disabled at the roots level", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch");
  fetchMock.mockResolvedValueOnce(
    browseResponse({
      path: null,
      path_style: "linux",
      parent: null,
      entries: [{ name: "/sources", path: "/sources", kind: "dir" }],
    }),
  );
  renderModal();
  await screen.findByRole("button", { name: /\/sources/ });
  expect(screen.getByRole("button", { name: "Select this folder" })).toBeDisabled();
});

test("Escape closes the modal", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch");
  fetchMock.mockResolvedValueOnce(
    browseResponse({ path: null, path_style: "linux", parent: null, entries: [] }),
  );
  const { onClose } = renderModal();
  await screen.findByText("No folders here.");
  fireEvent.keyDown(window, { key: "Escape" });
  expect(onClose).toHaveBeenCalled();
});
