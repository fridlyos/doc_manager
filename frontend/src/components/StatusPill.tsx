// Status word, not hue alone (8.i): the label text always carries the meaning;
// the `status-{status}` class only tints it. Reuses the existing CSS classes.
export function StatusPill({ status }: { status: string }) {
  return <span className={`status status-${status}`}>{status.replace(/_/g, " ")}</span>;
}
