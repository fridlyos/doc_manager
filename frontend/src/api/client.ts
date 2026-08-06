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

// --- Sync plans (Phase 7.c/7.e) --------------------------------------------

export type SyncAction = "already_present" | "copy" | "conflict" | "manual_review";

export interface SyncPlan {
  id: string;
  source_location_id: string;
  target_location_id: string;
  status: "building" | "ready" | "failed";
  item_count: number;
  covered_percent: number;
  summary: Record<string, number> | null;
  error_code: string | null;
  built_at: string | null;
  created_at: string;
}

export interface SyncPlanItem {
  id: string;
  action: SyncAction;
  reason: string;
  source_relative_path: string;
  source_sha256: string;
  target_relative_path: string | null;
  target_sha256: string | null;
}

export async function fetchSyncPlans(): Promise<Collection<SyncPlan>> {
  return apiFetch("/api/v1/sync-plans");
}

export async function fetchSyncPlan(id: string): Promise<SyncPlan> {
  const result = await apiFetch<Resource<SyncPlan>>(`/api/v1/sync-plans/${id}`);
  return result.data;
}

export async function fetchSyncPlanItems(
  id: string,
  action?: SyncAction,
): Promise<Collection<SyncPlanItem>> {
  const query = action ? `?filter[action]=${action}` : "";
  return apiFetch(`/api/v1/sync-plans/${id}/items${query}`);
}

export async function createSyncPlan(
  sourceLocationId: string,
  targetLocationId: string,
): Promise<SyncPlan> {
  const result = await apiFetch<Resource<SyncPlan>>("/api/v1/sync-plans", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": crypto.randomUUID().replaceAll("-", ""),
    },
    body: JSON.stringify({
      source_location_id: sourceLocationId,
      target_location_id: targetLocationId,
    }),
  });
  return result.data;
}

// --- Ask (RAG generation) ---------------------------------------------------

export type DataBoundary = "local" | "external";

export interface ProviderInfo {
  provider_id: string;
  data_boundary: DataBoundary;
  eligible: boolean;
}

export interface AskCitation {
  citation_id: string;
  ordinal: number;
  chunk_id: string;
  page_start: number | null;
  page_end: number | null;
  snippet: string;
  similarity_score: number;
  availability: "current" | "missing" | "historical";
  paths: SearchPath[];
}

export interface AskResultData {
  id: string;
  status: "completed" | "insufficient_evidence" | "refused" | "external_confirmation_required";
  answer: string | null;
  answer_format: string;
  provider: { provider_id: string; model_id: string | null; data_boundary: DataBoundary; invoked: boolean };
  data_boundary: {
    classification: DataBoundary;
    external_transfer_occurred: boolean;
    external_payload: Record<string, number | boolean>;
  };
  retrieval: { candidate_count: number; selected_evidence_count: number; sufficient: boolean };
  citations: AskCitation[];
  finish_reason: string | null;
  usage: { input_tokens: number | null; output_tokens: number | null; total_tokens: number | null } | null;
  timing: { retrieval_ms: number; generation_ms: number | null; total_ms: number };
  warnings: string[];
  confirmation?: {
    provider_id: string;
    evidence_blocks: number;
    evidence_characters: number;
    [key: string]: number | string;
  };
}

export interface AskRequestBody {
  question: string;
  provider_id: string;
  external_processing_acknowledged?: boolean;
  model_id?: string;
  filters?: { source_location_ids?: string[]; extensions?: string[] };
  retrieval?: { top_k?: number; score_threshold?: number };
}

export interface AskStreamEvent {
  event: string;
  data: Record<string, unknown>;
}

export async function fetchProviders(): Promise<ProviderInfo[]> {
  const result = await apiFetch<Resource<ProviderInfo[]>>("/api/v1/system/providers");
  return result.data;
}

function parseSseFrame(raw: string): AskStreamEvent | null {
  if (!raw.trim() || raw.startsWith(":")) return null; // comment / keep-alive
  let event = "message";
  let data = "";
  for (const line of raw.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data += line.slice(5).trim();
  }
  if (!data) return null;
  return { event, data: JSON.parse(data) as Record<string, unknown> };
}

// Streams the Ask SSE response, invoking `onEvent` per normalized event. Consumes
// the response body with fetch + a ReadableStream reader (browser EventSource
// cannot POST the required JSON body).
// --- Duplicates + coverage (Phase 6.c/6.e) ---------------------------------

export interface DuplicateMemberInfo {
  catalog_entry_id: string;
  source_location_id: string;
  display_path: string;
  state: string;
  sha256: string;
}

export interface DuplicateGroupInfo {
  id: string;
  kind: "exact" | "text";
  group_hash: string;
  member_count: number;
  built_at: string | null;
  members?: DuplicateMemberInfo[];
}

export interface CoverageEntry {
  source_location_id: string;
  name: string;
  total: number;
  by_state: Record<string, number>;
}

export async function fetchDuplicates(
  kind?: "exact" | "text",
): Promise<Collection<DuplicateGroupInfo>> {
  const query = kind ? `?filter[kind]=${kind}` : "";
  return apiFetch(`/api/v1/duplicates${query}`);
}

export async function fetchDuplicateGroup(id: string): Promise<DuplicateGroupInfo> {
  const result = await apiFetch<Resource<DuplicateGroupInfo>>(`/api/v1/duplicates/${id}`);
  return result.data;
}

export async function fetchCoverage(): Promise<CoverageEntry[]> {
  const result = await apiFetch<Resource<CoverageEntry[]>>("/api/v1/coverage");
  return result.data;
}

export async function rebuildDuplicates(): Promise<Job> {
  const result = await apiFetch<Resource<Job>>("/api/v1/duplicates/rebuild", {
    method: "POST",
    headers: { "Idempotency-Key": crypto.randomUUID().replaceAll("-", "") },
  });
  return result.data;
}

export async function askStream(
  body: AskRequestBody,
  onEvent: (event: AskStreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const resp = await fetch(`${API_BASE_URL}/api/v1/ask/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!resp.ok || !resp.body) {
    let detail = `ask request failed: ${resp.status}`;
    try {
      const problem = (await resp.json()) as { detail?: string };
      if (problem.detail) detail = problem.detail;
    } catch {
      // keep the status fallback
    }
    throw new Error(detail);
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buffer.indexOf("\n\n")) >= 0) {
      const frame = parseSseFrame(buffer.slice(0, idx));
      buffer = buffer.slice(idx + 2);
      if (frame) onEvent(frame);
    }
  }
}
