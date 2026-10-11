import { useQuery } from "@tanstack/react-query";
import { fetchCoverage, fetchJob, fetchJobs, Job } from "../api/client";
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
 * Live scan/index status for one location (rebuilt Phase 11). Two clearly
 * separated things:
 *   1. Catalog state — how many of this location's files are indexed and thus
 *      searchable (from coverage). This is the number that matters day to day.
 *   2. The latest scan — what that one run is doing/did, phrased as a sentence so
 *      the per-scan delta ("1 new file indexed") is never mistaken for the total.
 * Renders purely from polled GETs, so a refresh re-attaches to a running scan.
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

  const coverage = useQuery({
    queryKey: ["coverage"],
    queryFn: fetchCoverage,
    refetchInterval: active ? 3000 : 15000,
  });
  const entry = coverage.data?.find((c) => c.source_location_id === locationId);

  // Nothing to show until the location has either coverage or a scan on record.
  if (!entry && !latest) return null;

  const indexed = entry?.by_state.indexed ?? 0;
  const failed = entry?.by_state.failed ?? 0;
  const total = entry?.total ?? 0;
  const pending = Math.max(0, total - indexed - failed);

  return (
    <div className="scan-progress" aria-live="polite" aria-busy={active}>
      {/* 1. Catalog state — what is actually searchable. */}
      <div className="catalog-line">
        <strong>{indexed.toLocaleString()}</strong> of {total.toLocaleString()} files indexed
        {failed > 0 && <span className="catalog-bad"> · {failed.toLocaleString()} failed</span>}
        {pending > 0 && <span className="muted"> · {pending.toLocaleString()} not yet indexed</span>}
      </div>
      {total > 0 && (
        <ProgressBar value={indexed} max={total} label="files indexed in this location" />
      )}

      {/* 2. Latest scan — phrased so a per-scan delta is never read as the total. */}
      {latest && <ScanActivity job={detail.data ?? latest} active={active} />}
    </div>
  );
}

function ScanActivity({ job, active }: { job: Job; active: boolean }) {
  const summary = job.scan_summary;
  const d = job.progress.detail;
  const discovered = summary?.discovered ?? d?.discovered ?? 0;
  const changed = summary?.changed ?? d?.changed ?? 0;
  const indexedThisScan = summary?.indexed ?? 0;
  const remaining = summary?.index_remaining ?? 0;
  const stage = stageFor(job);
  const err = errorLabel(job);

  const hasBar = stage.kind === "discovering" || stage.kind === "indexing";
  const indeterminate =
    stage.kind === "waiting" ||
    stage.kind === "reconciling" ||
    (hasBar && (stage as { max: number }).max <= 0);

  // One sentence describing what this run did, so "1 indexed" reads as a delta.
  const delta =
    discovered > 0
      ? `This scan walked ${discovered.toLocaleString()} file(s): ` +
        `${changed.toLocaleString()} new or changed, ` +
        `${indexedThisScan.toLocaleString()} indexed` +
        (remaining > 0 ? `, ${remaining.toLocaleString()} still indexing.` : ".")
      : null;

  return (
    <div className="scan-activity">
      <div className="scan-progress-head">
        <span className="muted">Latest scan:</span>
        <StatusPill status={job.status} />
        <span className="muted">elapsed {elapsed(job)}</span>
      </div>
      <p className="scan-action">{stage.label}</p>
      {(hasBar || indeterminate) && active && (
        <ProgressBar
          value={hasBar ? (stage as { value: number }).value : 0}
          max={hasBar ? (stage as { max: number }).max : 0}
          label="current scan progress"
          indeterminate={indeterminate}
        />
      )}
      {delta && <p className="scan-delta muted">{delta}</p>}
      {err && (
        <p className="error" role="alert">
          {err}
        </p>
      )}
    </div>
  );
}
