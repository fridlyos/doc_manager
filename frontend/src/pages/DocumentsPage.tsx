import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  DocumentState,
  DocumentSummary,
  fetchDocuments,
  reindexDocument,
} from "../api/client";

const STATE_FILTERS: { value: DocumentState | "all"; label: string }[] = [
  { value: "all", label: "All" },
  { value: "indexed", label: "Indexed" },
  { value: "failed", label: "Failed" },
  { value: "unsupported", label: "Unsupported" },
  { value: "discovered", label: "Discovered" },
  { value: "missing", label: "Missing" },
];

function formatBytes(bytes: number | null): string {
  if (bytes === null) return "—";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(1)} ${units[unit]}`;
}

export function DocumentsPage() {
  const queryClient = useQueryClient();
  const [state, setState] = useState<DocumentState | "all">("all");
  const [expanded, setExpanded] = useState<string | null>(null);

  const documents = useQuery({
    queryKey: ["documents", state],
    queryFn: () => fetchDocuments(state === "all" ? undefined : state),
    refetchInterval: 5_000,
  });

  const reindex = useMutation({
    mutationFn: reindexDocument,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["documents"] });
    },
  });

  return (
    <section>
      <h2>Documents</h2>
      <div className="filter-bar">
        {STATE_FILTERS.map((option) => (
          <button
            key={option.value}
            className={state === option.value ? "chip chip-active" : "chip"}
            onClick={() => setState(option.value)}
          >
            {option.label}
          </button>
        ))}
      </div>
      {reindex.isError && <p className="error">Reindex failed: {String(reindex.error)}</p>}
      {reindex.isSuccess && <p className="notice">Reindex queued.</p>}
      {documents.isLoading && <p>Loading documents…</p>}
      {documents.isError && (
        <p className="error">Unable to load documents: {String(documents.error)}</p>
      )}
      {documents.data?.data.length === 0 && <p className="empty">No documents match this filter.</p>}
      {documents.data && documents.data.data.length > 0 && (
        <table className="resources">
          <thead>
            <tr>
              <th>Name</th>
              <th>Type</th>
              <th>State</th>
              <th>Size</th>
              <th>Modified</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {documents.data.data.map((doc) => (
              <DocumentRow
                key={doc.id}
                doc={doc}
                expanded={expanded === doc.id}
                onToggle={() => setExpanded(expanded === doc.id ? null : doc.id)}
                onReindex={() => reindex.mutate(doc.id)}
                reindexing={reindex.isPending && reindex.variables === doc.id}
              />
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function DocumentRow({
  doc,
  expanded,
  onToggle,
  onReindex,
  reindexing,
}: {
  doc: DocumentSummary;
  expanded: boolean;
  onToggle: () => void;
  onReindex: () => void;
  reindexing: boolean;
}) {
  return (
    <>
      <tr>
        <td>
          <button className="linklike" onClick={onToggle}>
            {doc.file_name}
          </button>
        </td>
        <td>{doc.extension}</td>
        <td>
          <span className={`status status-${doc.state}`}>{doc.state}</span>
        </td>
        <td>{formatBytes(doc.size_bytes)}</td>
        <td>{doc.modified_at ? new Date(doc.modified_at).toLocaleString() : "—"}</td>
        <td>
          <button disabled={reindexing} onClick={onReindex}>
            {reindexing ? "Queuing…" : "Reindex"}
          </button>
        </td>
      </tr>
      {expanded && (
        <tr className="detail-row">
          <td colSpan={6}>
            <dl className="doc-detail">
              <dt>Path</dt>
              <dd>
                <code>{doc.display_path}</code>
              </dd>
              <dt>SHA-256</dt>
              <dd>
                <code>{doc.sha256 ?? "—"}</code>
              </dd>
              <dt>Extraction</dt>
              <dd>{doc.extraction_status ?? "—"}</dd>
              {doc.error && (
                <>
                  <dt>Error</dt>
                  <dd className="error">
                    <strong>{doc.error.code}</strong>
                    {doc.error.message ? ` — ${doc.error.message}` : ""}
                  </dd>
                </>
              )}
              {doc.content_object && (
                <>
                  <dt>Pages</dt>
                  <dd>{doc.content_object.page_count}</dd>
                  <dt>Characters</dt>
                  <dd>{doc.content_object.character_count.toLocaleString()}</dd>
                  <dt>Extractor</dt>
                  <dd>
                    {doc.content_object.extractor_name} v{doc.content_object.extractor_version}
                  </dd>
                </>
              )}
              <dt>Indexed</dt>
              <dd>{doc.indexed_at ? new Date(doc.indexed_at).toLocaleString() : "—"}</dd>
            </dl>
          </td>
        </tr>
      )}
    </>
  );
}
