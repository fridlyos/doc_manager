import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  DuplicateGroupInfo,
  fetchDuplicateGroup,
  fetchDuplicates,
  rebuildDuplicates,
} from "../api/client";

type KindFilter = "all" | "exact" | "text";

const FILTERS: { value: KindFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "exact", label: "Exact (same file)" },
  { value: "text", label: "Text (same content)" },
];

export function DuplicatesPage() {
  const queryClient = useQueryClient();
  const [kind, setKind] = useState<KindFilter>("all");
  const [expanded, setExpanded] = useState<string | null>(null);

  const groups = useQuery({
    queryKey: ["duplicates", kind],
    queryFn: () => fetchDuplicates(kind === "all" ? undefined : kind),
    refetchInterval: 10_000,
  });

  const rebuild = useMutation({
    mutationFn: rebuildDuplicates,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["duplicates"] });
    },
  });

  return (
    <section>
      <h2>Duplicates</h2>
      <div className="filter-bar">
        {FILTERS.map((f) => (
          <button
            key={f.value}
            className={kind === f.value ? "chip chip-active" : "chip"}
            onClick={() => setKind(f.value)}
          >
            {f.label}
          </button>
        ))}
        <button disabled={rebuild.isPending} onClick={() => rebuild.mutate()}>
          {rebuild.isPending ? "Queuing…" : "Rebuild report"}
        </button>
      </div>
      {rebuild.isSuccess && <p className="notice">Rebuild queued.</p>}
      {rebuild.isError && <p className="error">Rebuild failed: {String(rebuild.error)}</p>}
      {groups.isLoading && <p>Loading duplicates…</p>}
      {groups.isError && <p className="error">Unable to load: {String(groups.error)}</p>}
      {groups.data?.data.length === 0 && <p className="empty">No duplicate groups.</p>}
      {groups.data && groups.data.data.length > 0 && (
        <table className="resources">
          <thead>
            <tr>
              <th>Kind</th>
              <th>Members</th>
              <th>Hash</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {groups.data.data.map((group) => (
              <DuplicateRow
                key={group.id}
                group={group}
                expanded={expanded === group.id}
                onToggle={() => setExpanded(expanded === group.id ? null : group.id)}
              />
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function DuplicateRow({
  group,
  expanded,
  onToggle,
}: {
  group: DuplicateGroupInfo;
  expanded: boolean;
  onToggle: () => void;
}) {
  const detail = useQuery({
    queryKey: ["duplicates", "group", group.id],
    queryFn: () => fetchDuplicateGroup(group.id),
    enabled: expanded,
  });

  return (
    <>
      <tr>
        <td>
          <span className={`chip dup-${group.kind}`}>{group.kind}</span>
        </td>
        <td>{group.member_count}</td>
        <td>
          <code>{group.group_hash.slice(0, 12)}…</code>
        </td>
        <td>
          <button className="linklike" onClick={onToggle}>
            {expanded ? "Hide paths" : "Show paths"}
          </button>
        </td>
      </tr>
      {expanded && (
        <tr className="detail-row">
          <td colSpan={4}>
            {detail.isLoading && <p>Loading members…</p>}
            {detail.data && (
              <ul className="dup-members">
                {detail.data.members?.map((m) => (
                  <li key={m.catalog_entry_id}>
                    <code>{m.display_path}</code>
                    <span className={`chip status-${m.state}`}>{m.state}</span>
                  </li>
                ))}
              </ul>
            )}
          </td>
        </tr>
      )}
    </>
  );
}
