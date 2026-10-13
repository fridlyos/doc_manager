import { Link } from "react-router-dom";

// How-it-works flow, drawn as three phases (ingest → store → use) in plain
// HTML so it wraps cleanly on narrow screens instead of shrinking an SVG.
type FlowNode = { label: string; detail: string; to?: string };
type Phase = { key: string; title: string; caption: string; nodes: FlowNode[] };

const FLOW: Phase[] = [
  {
    key: "ingest",
    title: "Ingest",
    caption: "Background jobs, per location",
    nodes: [
      { label: "Location", detail: "A folder: local, mapped drive, UNC or C:\\ path", to: "/locations" },
      { label: "Scan", detail: "Find new, changed and missing files", to: "/locations" },
      { label: "Extract", detail: "PDF, DOCX, TXT, MD, CSV; optional OCR for scanned PDFs" },
      { label: "Chunk + embed", detail: "Split into passages, file name included in the vector" },
    ],
  },
  {
    key: "store",
    title: "Store",
    caption: "Everything stays on this machine",
    nodes: [
      { label: "Catalog", detail: "Postgres: files, states, jobs, duplicates", to: "/documents" },
      { label: "Vector index", detail: "Qdrant: passage embeddings + keyword text" },
    ],
  },
  {
    key: "use",
    title: "Use",
    caption: "Retrieval is always local",
    nodes: [
      { label: "Search", detail: "Hybrid keyword + semantic ranking, no LLM", to: "/search" },
      { label: "Chat", detail: "Multi-turn answers grounded in cited passages", to: "/ask" },
    ],
  },
];

function FlowDiagram() {
  return (
    <ol className="flow" aria-label="How doc_manager works: ingest, store, use">
      {FLOW.map((phase, i) => (
        <li key={phase.key} className={`flow-phase flow-${phase.key}`}>
          <header className="flow-phase-head">
            <span className="flow-phase-num">{i + 1}</span>
            <div>
              <h4>{phase.title}</h4>
              <p>{phase.caption}</p>
            </div>
          </header>
          <ol className="flow-nodes">
            {phase.nodes.map((node) => (
              <li key={node.label} className="flow-node">
                <strong>{node.to ? <Link to={node.to}>{node.label}</Link> : node.label}</strong>
                <span>{node.detail}</span>
              </li>
            ))}
          </ol>
        </li>
      ))}
    </ol>
  );
}

type Step = { title: string; body: string; to: string; cta: string };

const QUICK_START: Step[] = [
  {
    title: "Add a location",
    body: "Pick a folder with the folder picker or paste a path (Windows C:\\ paths and network shares work). Nothing is copied or moved; it only registers a scan root.",
    to: "/locations",
    cta: "Locations",
  },
  {
    title: "Scan it",
    body: "Press Scan on the location's row. Open Details to watch live progress, set a scan interval in minutes for automatic rescans, or test that the folder is reachable.",
    to: "/locations",
    cta: "Locations",
  },
  {
    title: "Let it index",
    body: "Each new or changed file is extracted, chunked and embedded in the background. Jobs shows every unit of work; Errors lists files that failed, with retry.",
    to: "/jobs",
    cta: "Jobs",
  },
  {
    title: "Find things",
    body: "Search matches exact words and meaning together, so both \"invoice 2024\" and \"what did we pay the plumber\" work. Results show the passage, file and score.",
    to: "/search",
    cta: "Search",
  },
  {
    title: "Ask questions",
    body: "Chat answers in a running conversation, citing the passages it used. Pick the model in the header; follow-up questions keep the earlier context.",
    to: "/ask",
    cta: "Chat",
  },
];

type Concept = { title: string; body: string; to: string };
type ConceptGroup = { title: string; items: Concept[] };

// Mirrors the sidebar groups so this page doubles as a map of the menu.
const CONCEPT_GROUPS: ConceptGroup[] = [
  {
    title: "Library",
    items: [
      {
        title: "Locations",
        body: "Folders doc_manager watches. Scan or re-index on demand, schedule rescans, test reachability, or disable without losing the catalog.",
        to: "/locations",
      },
      {
        title: "Documents",
        body: "Every file found, with its state (indexed, failed, unsupported, missing, queued). Filter by location or search by name.",
        to: "/documents",
      },
      {
        title: "Coverage",
        body: "Per-location breakdown of document states: is a folder fully indexed or still catching up?",
        to: "/coverage",
      },
      {
        title: "Duplicates",
        body: "Files that are byte-identical (exact) or extract to the same text, across all locations.",
        to: "/duplicates",
      },
      {
        title: "Sync Plans",
        body: "Compare two locations and get a reviewable list of copies and conflicts. Nothing is written until you act on it.",
        to: "/sync-plans",
      },
    ],
  },
  {
    title: "Ask & Search",
    items: [
      {
        title: "Search",
        body: "Hybrid retrieval: keyword matches (with common synonyms) and semantic matches fused into one ranking. No LLM involved.",
        to: "/search",
      },
      {
        title: "Chat",
        body: "Retrieval-augmented conversation with citations. Evidence is gathered locally, then only that evidence goes to the model.",
        to: "/ask",
      },
    ],
  },
  {
    title: "Operations",
    items: [
      {
        title: "Jobs",
        body: "Every scan, index, rebuild and cleanup job with its status. Retry or cancel; a manual retry creates a new linked job.",
        to: "/jobs",
      },
      {
        title: "Errors",
        body: "Files that failed to index and why, with one-click retry once the cause is fixed.",
        to: "/errors",
      },
      {
        title: "System status",
        body: "Health of each backend component, and which ones are required for the app to work.",
        to: "/status",
      },
    ],
  },
];

export function TutorialPage() {
  return (
    <section className="tutorial">
      <h2>Tutorial</h2>
      <p className="tutorial-lead">
        doc_manager indexes documents in your local and network folders so you can search them and
        chat with them, with every answer cited back to the source file. It runs locally unless you
        explicitly choose an external model.
      </p>

      <h3>How it works</h3>
      <FlowDiagram />

      <aside className="privacy-note" aria-label="Privacy">
        <h4>What leaves your machine</h4>
        <ul>
          <li>
            <span className="badge boundary-local">local</span> Local models (Ollama) see the
            evidence passages and their file names.
          </li>
          <li>
            <span className="badge boundary-external">external</span> External providers get the
            evidence text only, never paths or file names, and only after you confirm exactly what
            will be sent.
          </li>
        </ul>
      </aside>

      <h3>Quick start</h3>
      <ol className="tutorial-steps">
        {QUICK_START.map((step, i) => (
          <li key={step.title} className="tutorial-step">
            <span className="tutorial-step-num" aria-hidden="true">
              {i + 1}
            </span>
            <div>
              <h4>{step.title}</h4>
              <p>{step.body}</p>
              <Link to={step.to}>Open {step.cta} →</Link>
            </div>
          </li>
        ))}
      </ol>

      <h3>Pages explained</h3>
      {CONCEPT_GROUPS.map((group) => (
        <div key={group.title} className="tutorial-group">
          <h4 className="tutorial-group-title">{group.title}</h4>
          <div className="tutorial-concepts">
            {group.items.map((c) => (
              <article key={c.title} className="tutorial-concept">
                <h5>
                  <Link to={c.to}>{c.title}</Link>
                </h5>
                <p>{c.body}</p>
              </article>
            ))}
          </div>
        </div>
      ))}
    </section>
  );
}
