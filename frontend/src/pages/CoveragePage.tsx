import { useQuery } from "@tanstack/react-query";
import { CoverageEntry, fetchCoverage } from "../api/client";

const STATES = ["indexed", "failed", "unsupported", "missing", "discovered", "queued"];

export function CoveragePage() {
  const coverage = useQuery({
    queryKey: ["coverage"],
    queryFn: fetchCoverage,
    refetchInterval: 10_000,
  });

  return (
    <section>
      <h2>Coverage</h2>
      <p className="notice">Catalog coverage per source location, by document state.</p>
      {coverage.isLoading && <p>Loading coverage…</p>}
      {coverage.isError && <p className="error">Unable to load: {String(coverage.error)}</p>}
      {coverage.data?.length === 0 && <p className="empty">No source locations configured.</p>}
      {coverage.data && coverage.data.length > 0 && (
        <table className="resources">
          <thead>
            <tr>
              <th>Location</th>
              <th>Total</th>
              {STATES.map((s) => (
                <th key={s}>{s}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {coverage.data.map((loc: CoverageEntry) => (
              <tr key={loc.source_location_id}>
                <td>{loc.name}</td>
                <td>{loc.total}</td>
                {STATES.map((s) => (
                  <td key={s} className={loc.by_state[s] ? `status-${s}` : "empty"}>
                    {loc.by_state[s] ?? 0}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
