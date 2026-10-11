interface ProgressBarProps {
  value: number;
  max: number;
  label: string;
  // When true, render a moving "work in progress" bar with no known total yet
  // (e.g. a queued scan, or discovery before any file count is known).
  indeterminate?: boolean;
}

/** Accessible `value / max` bar (Phase 9). Text is shown, not colour-only. */
export function ProgressBar({ value, max, label, indeterminate = false }: ProgressBarProps) {
  const pct = max > 0 ? Math.min(100, Math.round((value / max) * 100)) : 0;
  if (indeterminate) {
    return (
      <div
        className="progressbar progressbar-indeterminate"
        role="progressbar"
        aria-label={label}
        aria-valuetext="in progress"
      >
        <div className="progressbar-fill" />
        <span className="progressbar-text">working…</span>
      </div>
    );
  }
  return (
    <div
      className="progressbar"
      role="progressbar"
      aria-label={label}
      aria-valuenow={value}
      aria-valuemin={0}
      aria-valuemax={max}
      aria-valuetext={`${value} of ${max}`}
    >
      <div className="progressbar-fill" style={{ width: `${pct}%` }} />
      <span className="progressbar-text">
        {value.toLocaleString()} / {max.toLocaleString()} ({pct}%)
      </span>
    </div>
  );
}
