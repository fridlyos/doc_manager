import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { cancelJob, fetchJobs, retryJob, Job, JobStatus } from "../api/client";
import { StatusPill } from "../components/StatusPill";

const STATUSES: JobStatus[] = [
  "queued",
  "running",
  "retry_wait",
  "succeeded",
  "failed",
  "cancelled",
  "superseded",
];
const TYPES = [
  "scan_location",
  "index_file",
  "reindex_all_for_profile",
  "remove_stale_vectors",
  "build_duplicate_report",
  "build_sync_plan",
];
const ACTIVE = new Set<JobStatus>(["queued", "running", "retry_wait"]);
const RETRYABLE = new Set<JobStatus>(["failed", "cancelled"]);

function progressText(job: Job): string {
  const p = job.progress;
  if (!p || p.current == null || p.phase == null) return "—";
  return `${p.phase} ${p.current}${p.total != null ? `/${p.total}` : ""}`;
}

export function JobsPage() {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<JobStatus | "">("");
  const [jobType, setJobType] = useState("");

  const jobs = useQuery({
    queryKey: ["jobs", status, jobType],
    queryFn: () =>
      fetchJobs({
        ...(status ? { status: [status] } : {}),
        ...(jobType ? { job_type: [jobType] } : {}),
      }),
    refetchInterval: 5_000,
  });
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["jobs"] });
  const cancel = useMutation({ mutationFn: cancelJob, onSuccess: invalidate });
  const retry = useMutation({ mutationFn: retryJob, onSuccess: invalidate });

  return (
    <section>
      <h2>Jobs</h2>
      <div className="filter-bar">
        <label>
          Status
          <select value={status} onChange={(e) => setStatus(e.target.value as JobStatus | "")}>
            <option value="">all</option>
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <label>
          Type
          <select value={jobType} onChange={(e) => setJobType(e.target.value)}>
            <option value="">all</option>
            {TYPES.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </label>
      </div>

      {jobs.isLoading && <p>Loading jobs…</p>}
      {jobs.isError && (
        <p className="error" role="alert">
          Unable to load jobs: {String(jobs.error)}
        </p>
      )}
      {jobs.data?.data.length === 0 && <p className="empty">No jobs match.</p>}
      {jobs.data && jobs.data.data.length > 0 && (
        <table className="resources" aria-live="polite">
          <thead>
            <tr>
              <th>Type</th>
              <th>Status</th>
              <th>Progress</th>
              <th>Attempts</th>
              <th>Requested</th>
              <th>Actions</th>
              <th>Error</th>
            </tr>
          </thead>
          <tbody>
            {jobs.data.data.map((job) => (
              <tr key={job.id}>
                <td>{job.job_type}</td>
                <td>
                  <StatusPill status={job.status} />
                </td>
                <td>{progressText(job)}</td>
                <td>
                  {job.attempt_count}/{job.max_attempts}
                </td>
                <td>{new Date(job.requested_at).toLocaleString()}</td>
                <td className="row-actions">
                  <button
                    disabled={!ACTIVE.has(job.status) || cancel.isPending}
                    onClick={() => cancel.mutate(job.id)}
                  >
                    Cancel
                  </button>
                  <button
                    disabled={!RETRYABLE.has(job.status) || retry.isPending}
                    onClick={() => retry.mutate(job.id)}
                  >
                    Retry
                  </button>
                </td>
                <td>{job.error?.message ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {(cancel.isError || retry.isError) && (
        <p className="error" role="alert">
          {String(cancel.error ?? retry.error)}
        </p>
      )}
    </section>
  );
}
