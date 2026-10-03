import { Page, Route } from "@playwright/test";

// Minimal API envelope helpers mirroring backend/api/envelope.py.
export function collection(data: unknown[]) {
  return { data, page: { limit: 50, has_more: false, next_cursor: null } };
}
export function resource(data: unknown) {
  return { data, meta: { api_version: "1", request_id: "e2e" } };
}

export const LOCATION = {
  id: "loc-e2e",
  name: "Contracts",
  scan_root: "/sources/contracts",
  display_root: "/sources/contracts",
  path_style: "linux",
  enabled: true,
  read_only: true,
  external_generation_policy: "deny",
  scan_interval_minutes: null,
  last_successful_scan_at: null,
  revision: 1,
};

export interface MockState {
  // The scan job returned for the location (list + detail). null = never scanned.
  scanJob?: Record<string, unknown> | null;
  // Records calls the test wants to assert.
  calls: { method: string; url: string }[];
}

export function scanJob(overrides: Record<string, unknown> = {}) {
  return {
    id: "job-scan-1",
    job_type: "scan_location",
    status: "running",
    progress: { phase: "scanning", current: 500, total: null, unit: "files", updated_at: null, detail: null },
    attempt_count: 1,
    max_attempts: 3,
    requested_at: "2026-10-03T12:00:00Z",
    started_at: "2026-10-03T12:00:01Z",
    finished_at: null,
    cancel_requested_at: null,
    retry_of_job_id: null,
    root_job_id: null,
    target: { resource_type: "source_location", resource_id: LOCATION.id },
    error: null,
    scan_summary: {
      phase: "scanning",
      discovered: 1200,
      scanned: 500,
      target: 10000,
      changed: 40,
      missing: 0,
      indexed: 450,
      index_failed: 2,
      index_remaining: 750,
      index_total: 1200,
    },
    ...overrides,
  };
}

/**
 * Install a default mocked API. `state.scanJob` controls what the location's scan
 * job looks like (list + detail); tests mutate it to simulate progress/terminal
 * states. Returns the shared state (incl. a call log) for assertions.
 */
export async function installMock(page: Page, scan: Record<string, unknown> | null = null) {
  const state: MockState = { scanJob: scan, calls: [] };

  await page.route("**/api/v1/**", async (route: Route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname;
    const method = req.method();
    state.calls.push({ method, url: path + url.search });

    const json = (body: unknown, status = 200) =>
      route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

    // Job control actions.
    if (method === "POST" && /\/jobs\/[^/]+\/cancel$/.test(path)) {
      return json(resource(scanJob({ status: "cancelled", finished_at: "2026-10-03T12:05:00Z" })));
    }
    if (method === "POST" && /\/jobs\/[^/]+\/retry$/.test(path)) {
      return json(resource(scanJob({ id: "job-scan-2", status: "queued" })), 202);
    }
    if (method === "POST" && /\/locations\/[^/]+\/scan$/.test(path)) {
      state.scanJob = scanJob();
      return json(resource(state.scanJob), 202);
    }
    if (method === "POST") return json(resource({ ok: true }), 202);

    // Reads.
    if (path === "/api/v1/system/status") {
      return json({
        version: "test",
        environment: "e2e",
        generation_provider: "ollama",
        external_llm_enabled: false,
        ready: true,
        search_only: false,
        components: [{ name: "postgres", required: true, status: "up" }],
      });
    }
    if (path === "/api/v1/system/providers") {
      return json(resource([{ provider_id: "ollama", data_boundary: "local", eligible: true }]));
    }
    if (path === "/api/v1/locations/capabilities") {
      return json(resource({ filesystem_profile: "unix", native_picker_available: false }));
    }
    if (path === "/api/v1/locations") return json(collection([LOCATION]));
    if (path.startsWith("/api/v1/jobs/")) {
      return json(resource(state.scanJob ?? scanJob()));
    }
    if (path === "/api/v1/jobs") {
      return json(collection(state.scanJob ? [state.scanJob] : []));
    }
    if (path === "/api/v1/coverage") return json(resource([]));
    // Documents, errors, duplicates, sync-plans, etc. → empty collections.
    return json(collection([]));
  });

  return state;
}
