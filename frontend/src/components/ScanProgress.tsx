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

// Which stage the scan is in, used to pick the right progress denominator and
// drive the human-readable "current action" line.
type Stage =
  | { kind: "waiting"; label: string }
  | { kind: "discovering"; label: string; value: number; max: number }
  | { kind: "indexing"; label: string; value: number; max: number }
  | { kind: "reconciling"; label: string }
  | { kind: "done"; label: string }
  | { kind: "ended"; label: string };

function stageFor(job: Job): Stage {
  const summary = job.scan_summary;
  const d = job.progress.detail;
  const discovered = summary?.discovered ?? d?.discovered ?? 0;
  const scanned = summary?.scanned ?? d?.scanned ?? 0;
  const indexTotal = summary?.index_total ?? 0;
  const indexDone = (summary?.indexed ?? 0) + (summary?.index_failed ?? 0);
  const indexRemaining = summary?.index_remaining ?? 0;
  const detailPhase = summary?.phase ?? d?.phase ?? job.progress.phase ?? "";
  const discovering = detailPhase === "scanning" || job.progress.phase === "files_discovered";

  if (job.cancel_requested_at) return { kind: "reconciling", label: "Cancelling…" };

  switch (job.status) {
    case "queued":
      return { kind: "waiting", label: "Queued — waiting for a worker…" };
    case "retry_wait":
      return { kind: "waiting", label: "Waiting to retry…" };
    case "failed":
      return { kind: "ended", label: "Scan failed" };
    case "cancelled":
      return { kind: "ended", label: "Scan cancelled" };
    case "superseded":
      return { kind: "ended", label: "Superseded by a newer scan" };
    case "running":
      if (discovering) {
        return {
          kind: "discovering",
          label: `Discovering files — ${scanned.toLocaleString()} of ${discovered.toLocaleString()}…`,
          value: scanned,
          max: discovered,
        };
      }
      if (indexTotal > 0 && indexDone < indexTotal) {
        return {
          kind: "indexing",
          label: `Indexing ${indexDone.toLocaleString()} of ${indexTotal.toLocaleString()} files…`,
          value: indexDone,
          max: indexTotal,
        };
      }
      return { kind: "reconciling", label: "Reconciling catalog…" };
    case "succeeded":
      // The scan row can finish while its enqueued index children keep running.
      if (indexTotal > 0 && indexRemaining > 0) {
        return {
          kind: "indexing",
          label: `Scan done — indexing ${indexDone.toLocaleString()} of ${indexTotal.toLocaleString()} files…`,
          value: indexDone,
          max: indexTotal,
        };
      }
      return { kind: "done", label: "Scan complete" };
    default:
      return { kind: "ended", label: job.status };
  }
}

/**
 * Live scan-progress panel for one location (Phase 9, improved Phase 10). Finds
 * the location's latest scan job, then polls its detail (for the scan_summary
 * breakdown) while active. Shows a human-readable current-action line plus a
 * progress bar scaled to real work — discovery (scanned/discovered) then
 * indexing (done/total) — falling back to an indeterminate bar before a count is
 * known. Renders purely from the polled GET, so a page refresh re-attaches to a
 * running worker scan. Returns null when the location has never been scanned.
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
  const failed = summary?.index_failed ?? 0;
  const err = errorLabel(job);
  const stage = stageFor(job);

  // Indeterminate while we are doing work but have no meaningful total yet.
  const hasBar = stage.kind === "discovering" || stage.kind === "indexing";
  const indeterminate =
    (stage.kind === "waiting" || stage.kind === "reconciling") ||
    (hasBar && (stage as { max: number }).max <= 0);

  return (
    <div className="scan-progress" aria-live="polite" aria-busy={active}>
      <div className="scan-progress-head">
        <StatusPill status={job.status} />
        <span className="muted">elapsed {elapsed(job)}</span>
      </div>
      <p className="scan-action">{stage.label}</p>
      {(hasBar || indeterminate) && (
        <ProgressBar
          value={hasBar ? (stage as { value: number }).value : 0}
          max={hasBar ? (stage as { max: number }).max : 0}
          label="scan progress for this location"
          indeterminate={indeterminate}
        />
      )}
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
