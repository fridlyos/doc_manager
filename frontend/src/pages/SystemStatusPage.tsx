import { useQuery } from "@tanstack/react-query";
import { fetchSystemStatus } from "../api/client";

export function SystemStatusPage() {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["system-status"],
    queryFn: fetchSystemStatus,
    refetchInterval: 10_000,
  });

  if (isLoading) return <p>Loading system status…</p>;
  if (isError) return <p className="error">Unable to reach API: {String(error)}</p>;
  if (!data) return null;

  return (
    <section>
      <h2>System status</h2>
      <dl className="status-meta">
        <dt>Version</dt>
        <dd>{data.version}</dd>
        <dt>Environment</dt>
        <dd>{data.environment}</dd>
        <dt>Generation provider</dt>
        <dd>
          {data.generation_provider}{" "}
          <span className={data.external_llm_enabled ? "badge badge-external" : "badge badge-local"}>
            {data.external_llm_enabled ? "External enabled" : "Local"}
          </span>
        </dd>
        <dt>Mode</dt>
        <dd>{data.ready ? (data.search_only ? "Search-only (no provider ready)" : "Ready") : "Not ready"}</dd>
      </dl>

      <table className="components">
        <thead>
          <tr>
            <th>Component</th>
            <th>Required</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {data.components.map((c) => (
            <tr key={c.name}>
              <td>{c.name}</td>
              <td>{c.required ? "yes" : "no"}</td>
              <td className={`status status-${c.status}`}>{c.status}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
