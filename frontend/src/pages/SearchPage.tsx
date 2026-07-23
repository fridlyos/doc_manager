import { FormEvent, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  SearchHit,
  SearchRequest,
  fetchLocations,
  search,
} from "../api/client";

function pageLabel(hit: SearchHit): string {
  if (hit.page_start === null) return "";
  if (hit.page_end === null || hit.page_start === hit.page_end) return `p.${hit.page_start}`;
  return `pp.${hit.page_start}–${hit.page_end}`;
}

export function SearchPage() {
  const [query, setQuery] = useState("");
  const [locationId, setLocationId] = useState("");
  const [extensions, setExtensions] = useState("");

  const locations = useQuery({ queryKey: ["locations"], queryFn: fetchLocations });
  const results = useMutation({ mutationFn: search });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!query.trim()) return;
    const filters: SearchRequest["filters"] = {};
    if (locationId) filters.source_location_ids = [locationId];
    const exts = extensions
      .split(",")
      .map((e) => e.trim().replace(/^\./, "").toLowerCase())
      .filter(Boolean);
    if (exts.length > 0) filters.extensions = exts;
    const body: SearchRequest = { query };
    if (filters.source_location_ids || filters.extensions) body.filters = filters;
    results.mutate(body);
  }

  const data = results.data;

  return (
    <section>
      <h2>Search</h2>
      <form className="create-form" onSubmit={submit}>
        <label>
          Query
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="e.g. when does the contract renew?"
            required
          />
        </label>
        <label>
          Location
          <select value={locationId} onChange={(event) => setLocationId(event.target.value)}>
            <option value="">Any</option>
            {locations.data?.data.map((loc) => (
              <option key={loc.id} value={loc.id}>
                {loc.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Extensions
          <input
            value={extensions}
            onChange={(event) => setExtensions(event.target.value)}
            placeholder="pdf, md"
          />
        </label>
        <button type="submit" disabled={results.isPending || !query.trim()}>
          {results.isPending ? "Searching…" : "Search"}
        </button>
      </form>

      {results.isError && <p className="error">Search failed: {String(results.error)}</p>}
      {data && data.results.length === 0 && (
        <p className="empty">No matches. Try a different query or widen the filters.</p>
      )}
      {data && data.results.length > 0 && (
        <ol className="search-results">
          {data.results.map((hit) => {
            const primary = hit.paths.find((p) => p.is_primary) ?? hit.paths[0];
            return (
              <li key={hit.chunk_id} className="search-hit">
                <div className="search-hit-head">
                  <span className="search-path">
                    {primary ? <code>{primary.display_path}</code> : <em>path unavailable</em>}
                  </span>
                  <span className="search-meta">
                    {pageLabel(hit) && <span className="chip">{pageLabel(hit)}</span>}
                    <span className={`chip availability-${hit.availability}`}>
                      {hit.availability}
                    </span>
                    <span className="search-score">{hit.similarity_score.toFixed(3)}</span>
                  </span>
                </div>
                <p className="search-snippet">{hit.snippet}</p>
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
