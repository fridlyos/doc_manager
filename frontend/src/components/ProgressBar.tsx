interface ProgressBarProps {
  value: number;
  max: number;
  label: string;
}

/** Accessible `value / max` bar (Phase 9). Text is shown, not colour-only. */
export function ProgressBar({ value, max, label }: ProgressBarProps) {
  const pct = max > 0 ? Math.min(100, Math.round((value / max) * 100)) : 0;
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
        {value.toLocaleString()} / {max.toLocaleString()}
      </span>
    </div>
  );
}
