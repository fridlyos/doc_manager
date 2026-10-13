import { FormEvent, KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  AskCitation,
  AskHistoryTurn,
  AskRequestBody,
  AskResultData,
  AskStreamEvent,
  askStream,
  fetchProviders,
} from "../api/client";

// How many prior turns to replay to the model. Keeps the prompt bounded; the
// backend caps again as defence in depth.
const MAX_HISTORY_TURNS = 10;

type ChatMessage = {
  role: "user" | "assistant";
  content: string;
  citations?: AskCitation[];
  warnings?: string[];
  status?: string;
};

function pageLabel(c: Pick<AskCitation, "page_start" | "page_end">): string {
  if (c.page_start === null) return "";
  if (c.page_end === null || c.page_start === c.page_end) return `p.${c.page_start}`;
  return `pp.${c.page_start}–${c.page_end}`;
}

function Citations({ citations }: { citations: AskCitation[] }) {
  if (citations.length === 0) return null;
  return (
    <ol className="citations">
      {citations.map((c) => {
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
  );
}

export function AskPage() {
  const providers = useQuery({ queryKey: ["providers"], queryFn: fetchProviders });
  const [providerId, setProviderId] = useState("");
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [deltas, setDeltas] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [confirmation, setConfirmation] =
    useState<NonNullable<AskResultData["confirmation"]> | null>(null);
  // The turn awaiting an external-processing acknowledgement (question + the
  // history as it stood when first asked), so the retry sends the same context.
  const pending = useRef<{ question: string; history: AskHistoryTurn[] } | null>(null);
  // Anchor at the end of the transcript; keep the newest turn in view so a fresh
  // answer (or streaming delta) doesn't require a manual scroll down.
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    try {
      endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
    } catch {
      // scrollIntoView is unimplemented under jsdom (tests) and old browsers.
    }
  }, [messages, deltas, streaming, confirmation]);

  const eligible = useMemo(
    () => (providers.data ?? []).filter((p) => p.eligible),
    [providers.data],
  );
  const selected = eligible.find((p) => p.provider_id === providerId) ?? eligible[0];
  const boundary = selected?.data_boundary ?? "local";

  function historyFromMessages(msgs: ChatMessage[]): AskHistoryTurn[] {
    // Drop empty turns (insufficient_evidence / refused replies carry no answer
    // text): the backend rejects a history turn with blank content (min_length=1),
    // which otherwise fails every later question once a no-answer turn exists.
    // Filter before slicing so the window isn't spent on dropped turns.
    return msgs
      .filter((m) => m.content.trim().length > 0)
      .slice(-MAX_HISTORY_TURNS)
      .map((m) => ({ role: m.role, content: m.content }));
  }

  async function run(q: string, history: AskHistoryTurn[], acknowledged: boolean) {
    if (!selected || !q.trim()) return;
    setStreaming(true);
    setError(null);
    setConfirmation(null);
    setDeltas("");
    const body: AskRequestBody = {
      question: q,
      provider_id: selected.provider_id,
      external_processing_acknowledged: acknowledged,
      history,
    };
    try {
      await askStream(body, (event: AskStreamEvent) => {
        if (event.event === "answer.delta") {
          setDeltas((prev) => prev + String(event.data.delta ?? ""));
        } else if (event.event === "ask.result") {
          const data = event.data.data as AskResultData;
          if (data.status === "external_confirmation_required" && data.confirmation) {
            setConfirmation(data.confirmation);
            return;
          }
          setMessages((prev) => [
            ...prev,
            {
              role: "assistant",
              content: data.answer || "",
              citations: data.citations,
              warnings: data.warnings,
              status: data.status,
            },
          ]);
          pending.current = null;
        } else if (event.event === "ask.error") {
          const problem = event.data.problem as { detail?: string } | undefined;
          setError(problem?.detail ?? "Generation failed.");
        }
      });
    } catch (err) {
      setError(String(err instanceof Error ? err.message : err));
    } finally {
      setStreaming(false);
      setDeltas("");
    }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    send();
  }

  // Enter sends, Shift+Enter inserts a newline. Skip while an IME is composing
  // so Enter can confirm a candidate instead of sending half a word.
  function onMessageKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) return;
    event.preventDefault();
    send();
  }

  function send() {
    const q = question.trim();
    if (!q || streaming || !selected) return;
    const history = historyFromMessages(messages);
    pending.current = { question: q, history };
    setMessages((prev) => [...prev, { role: "user", content: q }]);
    setQuestion("");
    void run(q, history, false);
  }

  function confirmExternal() {
    const p = pending.current;
    if (!p) return;
    void run(p.question, p.history, true);
  }

  function newChat() {
    setMessages([]);
    setDeltas("");
    setError(null);
    setConfirmation(null);
    pending.current = null;
  }

  return (
    <section className="chat">
      <header className="chat-head">
        <div className="chat-title">
          <h2>Chat</h2>
          <p className="chat-sub">Answers only from your indexed documents, cited locally.</p>
        </div>
        <div className="chat-controls">
          <label className="chat-provider">
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
          {messages.length > 0 && (
            <button type="button" className="chat-reset" onClick={newChat} disabled={streaming}>
              New chat
            </button>
          )}
        </div>
      </header>

      {providers.isError && (
        <p className="error" role="alert">
          Unable to load providers.
        </p>
      )}

      <div className="chat-transcript" aria-live="polite">
        {messages.length === 0 && !streaming && (
          <p className="empty">Ask a question to start the conversation.</p>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`chat-msg chat-msg-${m.role}`}>
            <span className="chat-role">{m.role === "user" ? "You" : "Assistant"}</span>
            <div className="chat-bubble">
              <div className="chat-body">{m.content || <em>…</em>}</div>
              {m.role === "assistant" && m.status === "insufficient_evidence" && (
                <p className="notice">
                  No supporting evidence was found. Try rephrasing, using a
                  document&rsquo;s name, or asking about something you&rsquo;ve indexed.
                </p>
              )}
              {m.role === "assistant" && m.status === "refused" && (
                <p className="notice">The model declined to answer.</p>
              )}
              {m.warnings && m.warnings.length > 0 && (
                <p className="notice">Warnings: {m.warnings.join(", ")}</p>
              )}
              {m.citations && <Citations citations={m.citations} />}
            </div>
          </div>
        ))}
        {streaming && !confirmation && (
          <div className="chat-msg chat-msg-assistant">
            <span className="chat-role">Assistant</span>
            <div className="chat-bubble" aria-busy="true">
              <div className="chat-body">{deltas || <span className="chat-typing">…</span>}</div>
            </div>
          </div>
        )}
        <div ref={endRef} aria-hidden="true" />
      </div>

      {confirmation && (
        <div className="confirm-box">
          <p>
            <strong>External processing confirmation.</strong> This sends{" "}
            {confirmation.evidence_blocks} evidence block(s) ({confirmation.evidence_characters}{" "}
            characters) plus the conversation so far to <code>{confirmation.provider_id}</code>. No
            paths, file names, or tags are sent.
          </p>
          <button onClick={confirmExternal} disabled={streaming}>
            Send to external provider
          </button>
        </div>
      )}

      {error && (
        <p className="error" role="alert">
          Chat failed: {error}
        </p>
      )}

      <form className="chat-form" onSubmit={submit}>
        <textarea
          className="chat-input"
          rows={Math.min(8, Math.max(1, question.split("\n").length))}
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={onMessageKeyDown}
          placeholder="Send a message… e.g. When does the Acme agreement renew?"
          aria-label="Message"
          aria-describedby="chat-input-hint"
        />
        <button type="submit" disabled={streaming || !selected || !question.trim()}>
          {streaming ? "Sending…" : "Send"}
        </button>
        <p id="chat-input-hint" className="chat-hint">
          Enter to send · Shift+Enter for a new line
        </p>
      </form>
    </section>
  );
}
