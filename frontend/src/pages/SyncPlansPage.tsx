import { FormEvent, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  SyncPlan,
  SyncPlanItem,
  createSyncPlan,
  fetchLocations,
  fetchSyncPlanItems,
  fetchSyncPlans,
} from "../api/client";

const ACTION_LABELS: Record<string, string> = {
  already_present: "Already present",
  copy: "Missing (copy)",
  conflict: "Conflict",
  manual_review: "Manual review",
};

export function SyncPlansPage() {
  const queryClient = useQueryClient();
  const [source, setSource] = useState("");
  const [target, setTarget] = useState("");
  const [openPlan, setOpenPlan] = useState<string | null>(null);

  const locations = useQuery({ queryKey: ["locations"], queryFn: fetchLocations });
  const plans = useQuery({ queryKey: ["sync-plans"], queryFn: fetchSyncPlans, refetchInterval: 5_000 });

  const nameOf = useMemo(() => {
    const map = new Map((locations.data?.data ?? []).map((l) => [l.id, l.name]));
    return (id: string) => map.get(id) ?? id.slice(0, 8);
  }, [locations.data]);

  const create = useMutation({
    mutationFn: () => createSyncPlan(source, target),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["sync-plans"] });
    },
  });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (source && target && source !== target) create.mutate();
  }

  return (
    <section>
      <h2>Sync Plans</h2>
      <p className="notice">
        Read-only dry-run comparison of two locations. No files are ever moved, copied, or deleted.
      </p>

      <form className="create-form" onSubmit={submit}>
        <label>
          Source
          <select value={source} onChange={(e) => setSource(e.target.value)}>
            <option value="">Select…</option>
            {locations.data?.data.map((l) => (
              <option key={l.id} value={l.id}>
                {l.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Target
          <select value={target} onChange={(e) => setTarget(e.target.value)}>
            <option value="">Select…</option>
            {locations.data?.data.map((l) => (
              <option key={l.id} value={l.id}>
                {l.name}
              </option>
            ))}
          </select>
        </label>
        <button type="submit" disabled={create.isPending || !source || !target || source === target}>
          {create.isPending ? "Building…" : "Compare"}
        </button>
      </form>
      {create.isError && <p className="error">Could not create plan: {String(create.error)}</p>}

      {plans.data?.data.length === 0 && <p className="empty">No sync plans yet.</p>}
      {plans.data && plans.data.data.length > 0 && (
        <table className="resources">
          <thead>
            <tr>
              <th>Source → Target</th>
              <th>Status</th>
              <th>Covered</th>
              <th>Items</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {plans.data.data.map((plan) => (
              <PlanRow
                key={plan.id}
                plan={plan}
                sourceName={nameOf(plan.source_location_id)}
                targetName={nameOf(plan.target_location_id)}
                open={openPlan === plan.id}
                onToggle={() => setOpenPlan(openPlan === plan.id ? null : plan.id)}
              />
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function PlanRow({
  plan,
  sourceName,
  targetName,
  open,
  onToggle,
}: {
  plan: SyncPlan;
  sourceName: string;
  targetName: string;
  open: boolean;
  onToggle: () => void;
}) {
  const items = useQuery({
    queryKey: ["sync-plans", plan.id, "items"],
    queryFn: () => fetchSyncPlanItems(plan.id),
    enabled: open && plan.status === "ready",
  });

  return (
    <>
      <tr>
        <td>
          {sourceName} → {targetName}
        </td>
        <td>
          <span className={`status status-${plan.status}`}>{plan.status}</span>
        </td>
        <td>{plan.covered_percent.toFixed(0)}%</td>
        <td>{plan.item_count}</td>
        <td>
          <button className="linklike" onClick={onToggle} disabled={plan.status !== "ready"}>
            {open ? "Hide" : "View"}
          </button>
        </td>
      </tr>
      {open && plan.status === "ready" && (
        <tr className="detail-row">
          <td colSpan={5}>
            {items.isLoading && <p>Loading items…</p>}
            {items.data && (
              <ul className="sync-items">
                {items.data.data.map((item: SyncPlanItem) => (
                  <li key={item.id} className={`sync-item action-${item.action}`}>
                    <span className="chip">{ACTION_LABELS[item.action] ?? item.action}</span>
                    <code>{item.source_relative_path}</code>
                    {item.target_relative_path && (
                      <>
                        <span className="sync-arrow">→</span>
                        <code>{item.target_relative_path}</code>
                      </>
                    )}
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
