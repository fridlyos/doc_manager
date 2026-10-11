import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { fetchSystemStatus } from "../api/client";

function StatusStrip() {
  const status = useQuery({
    queryKey: ["header", "provider"],
    queryFn: fetchSystemStatus,
    refetchInterval: 15_000,
  });
  if (!status.data) return null;
  const { ready, search_only, components } = status.data;
  const down = components.filter((c) => c.required && c.status !== "up");
  return (
    <p className="notice home-status-strip">
      {ready ? (search_only ? "Search-only — no provider ready." : "System ready.") : "Not ready."}
      {down.length > 0 && <> {down.map((c) => c.name).join(", ")} down.</>}{" "}
      <Link to="/status">System status →</Link>
    </p>
  );
}

export function HomePage() {
  return (
    <section className="home">
      <h2>doc_manager</h2>
      <p className="notice">
        Local-first document search and cataloging. Point it at your folders, and it indexes them
        so you can search or ask questions with citations back to the source file. Everything runs
        locally unless you explicitly opt in to an external provider.
      </p>
      <StatusStrip />

      <h3>Getting started</h3>
      <p>Use the menu on the left to move between modules. A typical first run:</p>
      <ol className="home-steps">
        <li>
          <strong>Library → Locations</strong> — add a folder and scan it. The scan discovers files
          and indexes them in the background.
        </li>
        <li>
          <strong>Ask &amp; Search → Search</strong> — find matching passages once indexing has
          progressed.
        </li>
        <li>
          <strong>Ask &amp; Search → Chat</strong> — hold a conversation grounded in your
          documents, with citations (needs a generation provider; otherwise search-only).
        </li>
        <li>
          <strong>Operations → Jobs</strong> — watch background work, retry failures.
        </li>
      </ol>
      <p className="notice">
        New here? The <Link to="/tutorial">Tutorial</Link> walks through the whole flow with a
        diagram.
      </p>
    </section>
  );
}
