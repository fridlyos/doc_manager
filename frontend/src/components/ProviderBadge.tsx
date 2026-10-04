import { useQuery } from "@tanstack/react-query";
import { fetchSystemStatus } from "../api/client";

/**
 * Header indicator of the data-boundary / generation mode (Phase 9 / 8.i): shows
 * search-only vs local vs external at a glance, with the provider in the title.
 * Text carries the meaning; the class only tints it.
 */
export function ProviderBadge() {
  const status = useQuery({
    queryKey: ["header", "provider"],
    queryFn: fetchSystemStatus,
    refetchInterval: 15_000,
  });
  if (!status.data) return <span className="app-badge">local</span>;
  const { search_only, external_llm_enabled, generation_provider } = status.data;
  const mode = search_only ? "search-only" : external_llm_enabled ? "external enabled" : "local";
  const cls = !search_only && external_llm_enabled ? "badge-external" : "badge-local";
  return (
    <span className={`badge ${cls}`} title={`generation provider: ${generation_provider}`}>
      {mode}
    </span>
  );
}
