import { useQuery } from "@tanstack/react-query";
import { fetchJob, fetchJobs, Job } from "../api/client";
import { CountsRow } from "./CountsRow";
import { ProgressBar } from "./ProgressBar";
import { StatusPill } from "./StatusPill";

const ACTIVE = new Set<Job["status"]>(["queued", "running", "retry_wait"]);

function elapsed(job: Job): string {
  if (!job.started_at) return "not started";
  const end = job.finished_at ?? job.progress.updated_at ?? new Date().toISOString();
  const ms = Date.parse(end) - Date.parse(job.started_at);
  if (!Number.isFinite(ms) || ms < 0) return "—";
  const s = Math.round(ms / 1000);
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
}

function errorLabel(job: Job): string | null {
  if (!job.error) return null;
  if (job.error.code === "source_unavailable") {
    return "Source unavailable — the drive is disconnected or the sentinel does not match. Nothing was marked missing.";
  }
  return `Scan ${job.status}: ${job.error.message}`;
}

/**
 * Live scan-progress panel for one location (Phase 9). Finds the location's latest
 * scan job, then polls its detail (for the scan_summary breakdown) while active.
 * Renders purely from the polled GET, so a page refresh re-attaches to a running
 * worker scan. Returns null when the location has never been scanned.
 */
export function ScanProgress({ locationId }: { locationId: string }) {
  const list = useQuery({
    queryKey: ["jobs", "scan", locationId],
    queryFn: () => fetchJobs({ source_location_id: locationId, job_type: ["scan_location"] }),
    refetchInterval: 4000,
  });
  const latest = list.data?.data[0];
  const active = latest ? ACTIVE.has(latest.status) : false;

  const detail = useQuery({
    queryKey: ["job", latest?.id],
    queryFn: () => fetchJob(latest!.id),
    enabled: Boolean(latest),
    refetchInterval: active ? 1500 : false,
  });

  if (!latest) return null;
  const job = detail.data ?? latest;
  const summary = job.scan_summary;
  const d = job.progress.detail;
  const discovered = summary?.discovered ?? d?.discovered ?? 0;
  const scanned = summary?.scanned ?? d?.scanned ?? 0;
  const target = summary?.target ?? d?.target ?? Math.max(10_000, discovered);
  const failed = summary?.index_failed ?? 0;
  const err = errorLabel(job);

  return (
    <div className="scan-progress" aria-live="polite" aria-busy={active}>
      <div className="scan-progress-head">
        <StatusPill status={job.status} />
        <span className="muted">elapsed {elapsed(job)}</span>
      </div>
      <ProgressBar value={scanned} max={target} label="files scanned for this location" />
      <CountsRow
        counts={[
          { label: "discovered", value: discovered },
          { label: "indexed", value: summary?.indexed ?? 0, tone: "up" },
          { label: "changed", value: summary?.changed ?? d?.changed ?? 0 },
          { label: "failed", value: failed, tone: failed > 0 ? "down" : "muted" },
          { label: "missing", value: summary?.missing ?? d?.missing ?? 0, tone: "warn" },
          { label: "remaining", value: summary?.index_remaining ?? 0 },
        ]}
      />
      {err && (
        <p className="error" role="alert">
          {err}
        </p>
      )}
    </div>
  );
}
