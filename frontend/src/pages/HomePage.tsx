import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { fetchSystemStatus } from "../api/client";

type LinkCard = { title: string; body: string; to: string };

const ACTIONS: LinkCard[] = [
  { title: "Add a location", body: "Point doc_manager at a folder to index.", to: "/locations" },
  { title: "Ask a question", body: "Grounded answers with citations.", to: "/ask" },
  { title: "Search", body: "Semantic search over indexed passages.", to: "/search" },
  { title: "Tutorial", body: "Quick start and how it all fits together.", to: "/tutorial" },
];

const GROUPS: { heading: string; items: LinkCard[] }[] = [
  {
    heading: "Index",
    items: [
      { title: "Locations", body: "Scan roots: add, scan, schedule, test.", to: "/locations" },
      { title: "Documents", body: "Full catalog with per-file state.", to: "/documents" },
      { title: "Coverage", body: "Indexed vs. failed vs. missing, per location.", to: "/coverage" },
      { title: "Duplicates", body: "Exact and text-identical file groups.", to: "/duplicates" },
      { title: "Sync Plans", body: "Compare two locations, review what to copy.", to: "/sync-plans" },
    ],
  },
  {
    heading: "Query",
    items: [
      { title: "Search", body: "Raw ranked passages, no LLM call.", to: "/search" },
      { title: "Ask", body: "RAG question answering with citations.", to: "/ask" },
    ],
  },
  {
    heading: "Operate",
    items: [
      { title: "Status", body: "Version, provider, and component health.", to: "/status" },
      { title: "Jobs", body: "Background work: scan, index, rebuild, cleanup.", to: "/jobs" },
      { title: "Errors", body: "Failed documents with one-click retry.", to: "/errors" },
    ],
  },
];

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
      <Link to="/status">Details →</Link>
    </p>
  );
}

export function HomePage() {
  return (
    <section className="home">
      <h2>doc_manager</h2>
      <p className="notice">
        Local-first document search and cataloging. Index your folders, then search or ask
        questions with citations back to the source file.
      </p>
      <StatusStrip />

      <h3>Quick actions</h3>
      <div className="home-actions">
        {ACTIONS.map((a) => (
          <Link key={a.title} to={a.to} className="home-action">
            <strong>{a.title}</strong>
            <span>{a.body}</span>
          </Link>
        ))}
      </div>

      {GROUPS.map((group) => (
        <div key={group.heading}>
          <h3>{group.heading}</h3>
          <div className="tutorial-concepts">
            {group.items.map((item) => (
              <article key={item.title} className="tutorial-concept">
                <h4>
                  <Link to={item.to}>{item.title}</Link>
                </h4>
                <p>{item.body}</p>
              </article>
            ))}
          </div>
        </div>
      ))}
    </section>
  );
}
