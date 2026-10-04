export interface CountTile {
  label: string;
  value: number | string;
  tone?: "up" | "down" | "warn" | "muted";
}

/** A row of labelled stat tiles (Phase 9 scan breakdown). */
export function CountsRow({ counts }: { counts: CountTile[] }) {
  return (
    <dl className="counts-row">
      {counts.map((c) => (
        <div key={c.label} className={`count-tile${c.tone ? ` tone-${c.tone}` : ""}`}>
          <dt>{c.label}</dt>
          <dd>{typeof c.value === "number" ? c.value.toLocaleString() : c.value}</dd>
        </div>
      ))}
    </dl>
  );
}
