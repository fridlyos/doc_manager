import { ReactNode, useEffect, useRef } from "react";

const FOCUSABLE =
  "a[href], button:not([disabled]), input:not([disabled]), select:not([disabled])," +
  ' textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

interface ModalProps {
  label: string;
  onClose: () => void;
  children: ReactNode;
}

/**
 * Accessible dialog primitive (Phase 9 / 8.i backlog): role=dialog + aria-modal,
 * Escape + overlay-click to close, focus moved in on open and **restored** to the
 * previously-focused element on close, and a Tab focus-trap so keyboard focus
 * cannot leave the dialog.
 */
export function Modal({ label, onClose, children }: ModalProps) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const restoreRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    restoreRef.current = document.activeElement as HTMLElement | null;
    dialogRef.current?.focus();
    return () => restoreRef.current?.focus?.();
  }, []);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        onClose();
        return;
      }
      if (event.key !== "Tab") return;
      const root = dialogRef.current;
      if (!root) return;
      const items = Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE));
      if (items.length === 0) {
        event.preventDefault();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement;
      if (event.shiftKey && active === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-label={label}
        tabIndex={-1}
        ref={dialogRef}
        onClick={(event) => event.stopPropagation()}
      >
        {children}
      </div>
    </div>
  );
}
