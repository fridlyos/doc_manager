import { Link } from "react-router-dom";

type Step = { title: string; body: string; to: string };

const QUICK_START: Step[] = [
  {
    title: "1. Add a location",
    body: "Point doc_manager at a folder (local path, mapped drive, or UNC share). This registers a scan root; it does not copy or move anything.",
    to: "/locations",
  },
  {
    title: "2. Scan it",
    body: "Click Scan to discover files under that root. A background job walks the tree and records what it finds — new, changed, and deleted files.",
    to: "/locations",
  },
  {
    title: "3. Watch it index",
    body: "Each discovered file becomes an indexing job: extract text, split into chunks, embed, and store. Track progress live, or check all jobs at once.",
    to: "/jobs",
  },
  {
    title: "4. Search or Ask",
    body: "Search returns the raw matching passages. Ask sends your question to an LLM and returns a grounded answer with citations back to source files.",
    to: "/search",
  },
  {
    title: "5. Check catalog health",
    body: "Coverage shows how many files per location are indexed vs. failed vs. missing. Errors lists failures you can retry. Duplicates flags repeated content.",
    to: "/coverage",
  },
];

const CONCEPTS: { title: string; body: string; to: string }[] = [
  {
    title: "Locations",
    body: "A location is a scan root — a folder doc_manager watches. Each location can be scanned on demand or on a schedule, tested for reachability, or disabled without deleting its catalog entries.",
    to: "/locations",
  },
  {
    title: "Search",
    body: "Semantic search over indexed chunks. Returns the closest-matching passages with a similarity score, source path, and page range — no LLM call involved.",
    to: "/search",
  },
  {
    title: "Ask",
    body: "Retrieval-augmented question answering. Evidence is gathered locally first; only the evidence text (never file paths or names) is sent to the chosen provider. External providers require an explicit confirmation showing exactly what would be sent.",
    to: "/ask",
  },
  {
    title: "Documents",
    body: "The catalog of every file discovered across all locations, with its current state (indexed, failed, unsupported, missing, discovered, queued).",
    to: "/documents",
  },
  {
    title: "Duplicates",
    body: "Groups files that are byte-identical (exact) or that extract to the same text (text) — useful for spotting copies across locations before cleaning up.",
    to: "/duplicates",
  },
  {
    title: "Coverage",
    body: "A per-location breakdown of document states, so you can see at a glance whether a location is fully indexed or still catching up.",
    to: "/coverage",
  },
  {
    title: "Sync Plans",
    body: "Compares two locations and proposes which files to copy to bring one in line with the other — review before anything is written.",
    to: "/sync-plans",
  },
  {
    title: "Errors",
    body: "Documents that failed to index, with the reason, and a one-click retry once the underlying issue is fixed.",
    to: "/errors",
  },
  {
    title: "Jobs",
    body: "Every background unit of work (scan, index, rebuild, cleanup) with its status. Jobs can be retried or cancelled; a manual retry always creates a new linked job.",
    to: "/jobs",
  },
];

function PipelineDiagram() {
  const stages = [
    { label: "Location", detail: "scan root" },
    { label: "Scan", detail: "discover files" },
    { label: "Extract + chunk", detail: "text, pages" },
    { label: "Embed", detail: "vectors" },
    { label: "Catalog", detail: "Postgres + Qdrant" },
  ];
  const outputs = [
    { label: "Search", detail: "ranked passages" },
    { label: "Ask", detail: "cited answer" },
  ];

  return (
    <svg
      className="pipeline-diagram"
      viewBox="0 0 900 220"
      role="img"
      aria-label="Pipeline: Location leads to Scan, Extract and chunk, Embed, then Catalog, which feeds Search and Ask."
    >
      <defs>
        <marker
          id="tutorial-arrowhead"
          viewBox="0 0 10 10"
          refX="8"
          refY="5"
          markerWidth="6"
          markerHeight="6"
          orient="auto-start-reverse"
        >
          <path d="M0 0 L10 5 L0 10 z" className="pipeline-arrowhead" />
        </marker>
      </defs>
      {stages.map((s, i) => {
        const x = 10 + i * 175;
        return (
          <g key={s.label}>
            <rect x={x} y={70} width={150} height={60} rx={8} className="pipeline-box" />
            <text x={x + 75} y={95} textAnchor="middle" className="pipeline-label">
              {s.label}
            </text>
            <text x={x + 75} y={113} textAnchor="middle" className="pipeline-detail">
              {s.detail}
            </text>
            {i < stages.length - 1 && (
              <path d={`M${x + 150} 100 L${x + 175} 100`} className="pipeline-arrow" />
            )}
          </g>
        );
      })}
      <path d="M815 100 V140 Q815 150 805 150 H490 Q480 150 480 160" className="pipeline-arrow" />
      {outputs.map((o, i) => {
        const x = 330 + i * 175;
        return (
          <g key={o.label}>
            <rect x={x} y={160} width={150} height={50} rx={8} className="pipeline-box pipeline-box-output" />
            <text x={x + 75} y={182} textAnchor="middle" className="pipeline-label">
              {o.label}
            </text>
            <text x={x + 75} y={198} textAnchor="middle" className="pipeline-detail">
              {o.detail}
            </text>
          </g>
        );
      })}
      <path d="M480 185 H330" className="pipeline-arrow" />
    </svg>
  );
}

export function TutorialPage() {
  return (
    <section className="tutorial">
      <h2>Tutorial</h2>
      <p className="notice">
        doc_manager indexes documents from local or network folders so you can search and ask
        questions over them with citations back to the source file. Everything runs locally
        unless you explicitly opt in to an external provider.
      </p>

      <h3>How it works</h3>
      <PipelineDiagram />

      <h3>Quick start</h3>
      <ol className="tutorial-steps">
        {QUICK_START.map((step) => (
          <li key={step.title} className="tutorial-step">
            <h4>{step.title}</h4>
            <p>{step.body}</p>
            <Link to={step.to}>Open →</Link>
          </li>
        ))}
      </ol>

      <h3>Pages explained</h3>
      <div className="tutorial-concepts">
        {CONCEPTS.map((c) => (
          <article key={c.title} className="tutorial-concept">
            <h4>
              <Link to={c.to}>{c.title}</Link>
            </h4>
            <p>{c.body}</p>
          </article>
        ))}
      </div>
    </section>
  );
}
