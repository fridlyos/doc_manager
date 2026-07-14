import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { SystemStatusPage } from "./SystemStatusPage";
import type { SystemStatus } from "../api/client";

afterEach(() => {
  vi.restoreAllMocks();
});

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

test("renders component health from the API", async () => {
  const payload: SystemStatus = {
    version: "0.1.0",
    environment: "test",
    generation_provider: "ollama",
    external_llm_enabled: false,
    ready: true,
    search_only: true,
    components: [
      { name: "postgres", required: true, status: "up" },
      { name: "qdrant", required: true, status: "up" },
      { name: "ollama", required: false, status: "down" },
    ],
  };
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify(payload), { status: 200 }),
  );

  renderWithClient(<SystemStatusPage />);

  await waitFor(() => expect(screen.getByText("System status")).toBeInTheDocument());
  expect(screen.getByText("postgres")).toBeInTheDocument();
  expect(screen.getByText(/Search-only/)).toBeInTheDocument();
});
