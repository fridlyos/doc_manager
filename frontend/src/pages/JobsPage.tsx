import { useQuery } from "@tanstack/react-query";
import { fetchJobs } from "../api/client";

export function JobsPage() {
  const jobs = useQuery({
    queryKey: ["jobs"],
    queryFn: fetchJobs,
    refetchInterval: 5_000,
  });

  return (
    <section>
      <h2>Jobs</h2>
      {jobs.isLoading && <p>Loading jobs…</p>}
      {jobs.isError && <p className="error">Unable to load jobs: {String(jobs.error)}</p>}
      {jobs.data?.data.length === 0 && <p className="empty">No jobs yet.</p>}
      {jobs.data && jobs.data.data.length > 0 && (
        <table className="resources">
          <thead>
            <tr>
              <th>Type</th>
              <th>Status</th>
              <th>Attempts</th>
              <th>Requested</th>
              <th>Error</th>
            </tr>
          </thead>
          <tbody>
            {jobs.data.data.map((job) => (
              <tr key={job.id}>
                <td>{job.job_type}</td>
                <td>
                  <span className={`status status-${job.status}`}>{job.status}</span>
                </td>
                <td>
                  {job.attempt_count}/{job.max_attempts}
                </td>
                <td>{new Date(job.requested_at).toLocaleString()}</td>
                <td>{job.error?.message ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
