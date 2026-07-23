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

export type DocumentState =
  | "discovered"
  | "queued"
  | "indexed"
  | "failed"
  | "missing"
  | "unsupported";

export interface DocumentContentObject {
  id: string;
  page_count: number;
  character_count: number;
  extractor_name: string;
  extractor_version: string;
  normalization_version: string;
}

export interface DocumentSummary {
  id: string;
  source_location_id: string;
  display_path: string;
  file_name: string;
  extension: string;
  mime_type: string | null;
  state: DocumentState;
  size_bytes: number | null;
  modified_at: string | null;
  sha256: string | null;
  extraction_status: string | null;
  error: { code: string; message: string } | null;
  content_object: DocumentContentObject | null;
  indexed_at: string | null;
  updated_at: string;
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

export async function fetchDocuments(state?: DocumentState): Promise<Collection<DocumentSummary>> {
  const params = new URLSearchParams();
  if (state) params.set("filter[state]", state);
  const query = params.toString();
  return apiFetch(`/api/v1/documents${query ? `?${query}` : ""}`);
}

export async function fetchErrors(): Promise<Collection<DocumentSummary>> {
  return apiFetch("/api/v1/errors");
}

export async function reindexDocument(documentId: string): Promise<Job> {
  const result = await apiFetch<Resource<Job>>(`/api/v1/documents/${documentId}/reindex`, {
    method: "POST",
    headers: { "Idempotency-Key": crypto.randomUUID().replaceAll("-", "") },
  });
  return result.data;
}

export interface SearchPath {
  catalog_entry_id: string;
  source_location_id: string;
  display_path: string;
  state: string;
  is_primary: boolean;
}

export interface SearchHit {
  chunk_id: string;
  content_object_id: string;
  similarity_score: number;
  page_start: number | null;
  page_end: number | null;
  snippet: string;
  availability: "current" | "missing" | "historical";
  paths: SearchPath[];
}

export interface SearchResponse {
  results: SearchHit[];
  result_count: number;
  top_k: number;
}

export interface SearchRequest {
  query: string;
  filters?: {
    source_location_ids?: string[];
    extensions?: string[];
  };
  retrieval?: {
    top_k?: number;
    score_threshold?: number;
  };
}

export async function search(body: SearchRequest): Promise<SearchResponse> {
  const result = await apiFetch<Resource<SearchResponse>>("/api/v1/search", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return result.data;
}
