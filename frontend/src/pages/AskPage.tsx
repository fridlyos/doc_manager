import { FormEvent, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  AskCitation,
  AskRequestBody,
  AskResultData,
  AskStreamEvent,
  askStream,
  fetchProviders,
} from "../api/client";

function pageLabel(c: Pick<AskCitation, "page_start" | "page_end">): string {
  if (c.page_start === null) return "";
  if (c.page_end === null || c.page_start === c.page_end) return `p.${c.page_start}`;
  return `pp.${c.page_start}–${c.page_end}`;
}

export function AskPage() {
  const providers = useQuery({ queryKey: ["providers"], queryFn: fetchProviders });
  const [providerId, setProviderId] = useState("");
  const [question, setQuestion] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [deltas, setDeltas] = useState("");
  const [result, setResult] = useState<AskResultData | null>(null);
  const [error, setError] = useState<string | null>(null);

  const eligible = useMemo(
    () => (providers.data ?? []).filter((p) => p.eligible),
    [providers.data],
  );
  const selected = eligible.find((p) => p.provider_id === providerId) ?? eligible[0];
  const boundary = selected?.data_boundary ?? "local";

  async function run(acknowledged: boolean) {
    if (!selected || !question.trim()) return;
    setStreaming(true);
    setError(null);
    setResult(null);
    setDeltas("");
    const body: AskRequestBody = {
      question,
      provider_id: selected.provider_id,
      external_processing_acknowledged: acknowledged,
    };
    try {
      await askStream(body, (event: AskStreamEvent) => {
        if (event.event === "answer.delta") {
          setDeltas((prev) => prev + String(event.data.delta ?? ""));
        } else if (event.event === "ask.result") {
          setResult(event.data.data as AskResultData);
        } else if (event.event === "ask.error") {
          const problem = event.data.problem as { detail?: string } | undefined;
          setError(problem?.detail ?? "Generation failed.");
        }
      });
    } catch (err) {
      setError(String(err instanceof Error ? err.message : err));
    } finally {
      setStreaming(false);
    }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void run(false);
  }

  const confirmation = result?.status === "external_confirmation_required" ? result.confirmation : null;

  return (
    <section>
      <h2>Ask</h2>
      <p className="notice">
        Answers are grounded in your indexed evidence, with citations resolved locally.
      </p>

      <form className="create-form" onSubmit={submit}>
        <label>
          Provider
          <select value={providerId} onChange={(e) => setProviderId(e.target.value)}>
            {eligible.length === 0 && <option value="">No provider available</option>}
            {eligible.map((p) => (
              <option key={p.provider_id} value={p.provider_id}>
                {p.provider_id}
              </option>
            ))}
          </select>
        </label>
        <span className={`badge boundary-${boundary}`} aria-label={`data boundary ${boundary}`}>
          {boundary === "external" ? "External" : "Local"}
        </span>
        <label className="ask-question">
          Question
          <input
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="e.g. When does the Acme agreement renew?"
            required
          />
        </label>
        <button type="submit" disabled={streaming || !selected || !question.trim()}>
          {streaming ? "Asking…" : "Ask"}
        </button>
      </form>

      {providers.isError && <p className="error">Unable to load providers.</p>}
      {error && <p className="error">Ask failed: {error}</p>}

      {confirmation && (
        <div className="confirm-box">
          <p>
            <strong>External processing confirmation.</strong> This sends{" "}
            {confirmation.evidence_blocks} evidence block(s) (
            {confirmation.evidence_characters} characters) to{" "}
            <code>{confirmation.provider_id}</code>. No paths, file names, or tags are sent.
          </p>
          <button onClick={() => void run(true)} disabled={streaming}>
            Send to external provider
          </button>
        </div>
      )}

      {(streaming || result) && !confirmation && (
        <article className="answer">
          <div className="answer-body">{result?.answer ?? (deltas || "…")}</div>
          {result?.status === "insufficient_evidence" && (
            <p className="notice">No supporting evidence was found for this question.</p>
          )}
          {result?.status === "refused" && (
            <p className="notice">The model declined to answer.</p>
          )}
          {result && result.warnings.length > 0 && (
            <p className="notice">Warnings: {result.warnings.join(", ")}</p>
          )}
          {result && result.citations.length > 0 && (
            <ol className="citations">
              {result.citations.map((c) => {
                const primary = c.paths.find((p) => p.is_primary) ?? c.paths[0];
                return (
                  <li key={c.citation_id} className="citation">
                    <span className="citation-head">
                      <span className="citation-ordinal">[{c.ordinal}]</span>
                      {primary ? <code>{primary.display_path}</code> : <em>path unavailable</em>}
                      {pageLabel(c) && <span className="chip">{pageLabel(c)}</span>}
                      <span className={`chip availability-${c.availability}`}>{c.availability}</span>
                    </span>
                    <p className="citation-snippet">{c.snippet}</p>
                  </li>
                );
              })}
            </ol>
          )}
        </article>
      )}
    </section>
  );
}
