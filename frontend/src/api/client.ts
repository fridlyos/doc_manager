// Minimal typed API client using native fetch. No third-party HTTP library and
// no external hosts — requests go only to the configured local API base URL.

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "";

export interface Component {
  name: string;
  required: boolean;
  status: "up" | "down" | "disabled" | "unknown";
}

export interface SystemStatus {
  version: string;
  environment: string;
  generation_provider: string;
  external_llm_enabled: boolean;
  ready: boolean;
  search_only: boolean;
  components: Component[];
}

export async function fetchSystemStatus(): Promise<SystemStatus> {
  const resp = await fetch(`${API_BASE_URL}/api/v1/system/status`);
  if (!resp.ok) {
    throw new Error(`system status request failed: ${resp.status}`);
  }
  return (await resp.json()) as SystemStatus;
}

export interface PageMeta {
  limit: number;
  has_more: boolean;
  next_cursor: string | null;
}

export interface Collection<T> {
  data: T[];
  page: PageMeta;
}

export interface Resource<T> {
  data: T;
}

export interface SourceLocation {
  id: string;
  name: string;
  scan_root: string;
  display_root: string;
  enabled: boolean;
  scan_interval_minutes: number | null;
  last_successful_scan_at: string | null;
  revision: number;
}

export type PathStyle = "linux" | "windows" | "unc" | "mapped_drive";

export interface LocationCreate {
  name: string;
  scan_root: string;
  path_style?: PathStyle;
  scan_interval_minutes?: number;
}

export interface BrowseEntry {
  name: string;
  path: string;
  kind: "dir" | "file";
}

export interface BrowseResult {
  path: string | null;
  path_style: PathStyle;
  parent: string | null;
  entries: BrowseEntry[];
}

export interface LocationCapabilities {
  filesystem_profile: "windows" | "unix";
  native_picker_available: boolean;
}

export interface PickedFolder {
  path: string | null;
  path_style: PathStyle | null;
}

export interface Job {
  id: string;
  job_type: string;
  status: string;
  attempt_count: number;
  max_attempts: number;
  requested_at: string;
  finished_at: string | null;
  target: { resource_type: string; resource_id: string } | null;
  error: { code: string; message: string; retryable: boolean } | null;
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${API_BASE_URL}${path}`, init);
  if (!resp.ok) {
    let detail = `request failed: ${resp.status}`;
    try {
      const problem = (await resp.json()) as { detail?: string };
      if (problem.detail) detail = problem.detail;
    } catch {
      // Preserve the status fallback for non-JSON failures.
    }
    throw new Error(detail);
  }
  return (await resp.json()) as T;
}

export async function fetchLocations(): Promise<Collection<SourceLocation>> {
  return apiFetch("/api/v1/locations");
}

export async function browseDirectory(
  path: string | null,
  pathStyle: PathStyle,
): Promise<BrowseResult> {
  const params = new URLSearchParams({ path_style: pathStyle });
  if (path !== null) params.set("path", path);
  const result = await apiFetch<Resource<BrowseResult>>(`/api/v1/locations/browse?${params}`);
  return result.data;
}

export async function fetchLocationCapabilities(): Promise<LocationCapabilities> {
  const result = await apiFetch<Resource<LocationCapabilities>>("/api/v1/locations/capabilities");
  return result.data;
}

export async function pickFolderNative(): Promise<PickedFolder> {
  const result = await apiFetch<Resource<PickedFolder>>("/api/v1/locations/pick-folder", {
    method: "POST",
  });
  return result.data;
}

export async function createLocation(body: LocationCreate): Promise<SourceLocation> {
  const result = await apiFetch<Resource<SourceLocation>>("/api/v1/locations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return result.data;
}

export async function deleteLocation(
  location: Pick<SourceLocation, "id" | "revision">,
): Promise<void> {
  const resp = await fetch(`${API_BASE_URL}/api/v1/locations/${location.id}`, {
    method: "DELETE",
    headers: { "If-Match": `"location-${location.id}-${location.revision}"` },
  });
  if (!resp.ok) {
    let detail = `request failed: ${resp.status}`;
    try {
      const problem = (await resp.json()) as { detail?: string };
      if (problem.detail) detail = problem.detail;
    } catch {
      // 204 and non-JSON failures keep the status fallback.
    }
    throw new Error(detail);
  }
}

export async function requestLocationScan(locationId: string): Promise<Job> {
  const result = await apiFetch<Resource<Job>>(`/api/v1/locations/${locationId}/scan`, {
    method: "POST",
    headers: { "Idempotency-Key": crypto.randomUUID().replaceAll("-", "") },
  });
  return result.data;
}

export async function fetchJobs(): Promise<Collection<Job>> {
  return apiFetch("/api/v1/jobs");
}
