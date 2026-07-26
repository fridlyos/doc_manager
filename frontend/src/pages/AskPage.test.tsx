import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { AskPage } from "./AskPage";

afterEach(() => vi.restoreAllMocks());

function sseFrames(...events: { event: string; data: unknown }[]): string {
  return events
    .map((e, i) => `id: ${i + 1}\nevent: ${e.event}\ndata: ${JSON.stringify(e.data)}\n\n`)
    .join("");
}

function streamResponse(text: string): unknown {
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(text));
      controller.close();
    },
  });
  return { ok: true, status: 200, body: stream };
}

function providersResponse(): unknown {
  return {
    ok: true,
    status: 200,
    json: async () => ({
      data: [
        { provider_id: "ollama", data_boundary: "local", eligible: true },
        { provider_id: "openai", data_boundary: "external", eligible: true },
      ],
    }),
  };
}

const RESULT = {
  id: "ask-1",
  status: "completed",
  answer: "It renews in December [1].",
  answer_format: "markdown",
  provider: { provider_id: "ollama", model_id: "llama3.1:8b", data_boundary: "local", invoked: true },
  data_boundary: { classification: "local", external_transfer_occurred: false, external_payload: {} },
  retrieval: { candidate_count: 5, selected_evidence_count: 1, sufficient: true },
  citations: [
    {
      citation_id: "E1",
      ordinal: 1,
      chunk_id: "c1",
      page_start: 4,
      page_end: 4,
      snippet: "the term renews through 31 December",
      similarity_score: 0.81,
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
    },
  ],
  finish_reason: "stop",
  usage: null,
  timing: { retrieval_ms: 10, generation_ms: 100, total_ms: 110 },
  warnings: [],
};

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <AskPage />
    </QueryClientProvider>,
  );
}

test("loads providers and shows the Local boundary badge", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation((url) => {
    if (String(url).includes("/system/providers")) return Promise.resolve(providersResponse() as Response);
    return Promise.resolve(streamResponse("") as Response);
  });
  renderPage();
  await screen.findByRole("option", { name: "ollama" });
  expect(screen.getByText("Local")).toBeInTheDocument();
});

test("streams deltas then reconciles the final answer and citation", async () => {
  const sse = sseFrames(
    { event: "ask.started", data: { provider: { provider_id: "ollama", data_boundary: "local" } } },
    { event: "retrieval.completed", data: { retrieval: { candidate_count: 5 } } },
    { event: "generation.started", data: { provider: { provider_id: "ollama" } } },
    { event: "answer.delta", data: { delta: "It renews " } },
    { event: "answer.delta", data: { delta: "in December [1]." } },
    { event: "ask.result", data: { data: RESULT } },
  );
  vi.spyOn(globalThis, "fetch").mockImplementation((url) => {
    if (String(url).includes("/system/providers")) return Promise.resolve(providersResponse() as Response);
    return Promise.resolve(streamResponse(sse) as Response);
  });
  renderPage();

  await screen.findByRole("option", { name: "ollama" });
  fireEvent.change(screen.getByPlaceholderText(/Acme agreement renew/i), {
    target: { value: "when does it renew?" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Ask" }));

  expect(await screen.findByText("It renews in December [1].")).toBeInTheDocument();
  expect(screen.getByText("/sources/legal/acme.pdf")).toBeInTheDocument();
  expect(screen.getByText("[1]")).toBeInTheDocument();
  expect(screen.getByText(/the term renews/)).toBeInTheDocument();
});

test("external confirmation prompts, then resends with acknowledgement", async () => {
  const confirmResult = {
    ...RESULT,
    status: "external_confirmation_required",
    answer: null,
    provider: { ...RESULT.provider, provider_id: "openai", data_boundary: "external", invoked: false },
    confirmation: { provider_id: "openai", evidence_blocks: 2, evidence_characters: 900 },
  };
  const calls: RequestInit[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation((url, init) => {
    if (String(url).includes("/system/providers")) return Promise.resolve(providersResponse() as Response);
    calls.push(init as RequestInit);
    const body = calls.length === 1 ? confirmResult : RESULT;
    return Promise.resolve(
      streamResponse(sseFrames({ event: "ask.result", data: { data: body } })) as Response,
    );
  });
  renderPage();

  await screen.findByRole("option", { name: "ollama" });
  // Switch to the external provider.
  fireEvent.change(screen.getByRole("combobox"), { target: { value: "openai" } });
  expect(await screen.findByText("External")).toBeInTheDocument();
  fireEvent.change(screen.getByPlaceholderText(/Acme agreement renew/i), {
    target: { value: "renewal?" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Ask" }));

  const confirmButton = await screen.findByRole("button", { name: /Send to external provider/i });
  // First request did not acknowledge.
  expect(JSON.parse(calls[0].body as string).external_processing_acknowledged).toBe(false);

  fireEvent.click(confirmButton);
  await waitFor(() => expect(calls.length).toBe(2));
  expect(JSON.parse(calls[1].body as string).external_processing_acknowledged).toBe(true);
});

test("renders an ask.error", async () => {
  const sse = sseFrames({
    event: "ask.error",
    data: { problem: { detail: "The selected provider did not finish in time." } },
  });
  vi.spyOn(globalThis, "fetch").mockImplementation((url) => {
    if (String(url).includes("/system/providers")) return Promise.resolve(providersResponse() as Response);
    return Promise.resolve(streamResponse(sse) as Response);
  });
  renderPage();

  await screen.findByRole("option", { name: "ollama" });
  fireEvent.change(screen.getByPlaceholderText(/Acme agreement renew/i), {
    target: { value: "q" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Ask" }));
  expect(await screen.findByText(/did not finish in time/)).toBeInTheDocument();
});
