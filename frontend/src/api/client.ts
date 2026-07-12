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
