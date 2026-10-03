# Accessibility review

A self-review of the React UI (`frontend/`) against keyboard navigation, focus
management, labels/ARIA, colour contrast, and status announcements, per open
decision #4 (a checklist + the low-risk fixes now; an automated axe pass is a
follow-up). Findings are concrete (file references); fixes applied this pass are
marked, the rest are a prioritised backlog.

## What is already good

- Single `<main>` landmark and a labelled `<nav aria-label="Primary navigation">`
  with one `<h1>` (`app/App.tsx`); each page opens with an `<h2>`.
- `NavLink` sets `aria-current="page"` on the active route automatically.
- Form controls use wrapping `<label>` (implicit association) on the Ask, Search,
  and Locations forms; the data-boundary badge carries an `aria-label`
  (`pages/AskPage.tsx`).
- The folder picker is a proper dialog: `role="dialog"`, `aria-modal="true"`,
  `aria-label`, focuses itself on open, and closes on `Escape`
  (`components/FolderPickerModal.tsx`).
- Disabled buttons also change their text (`Ask` → `Asking…`), so state is not
  conveyed by the `disabled` attribute alone.

## Fixed this pass

- **Keyboard focus was invisible.** The dark theme + custom control colours washed
  out the user-agent focus outline. Added an explicit `:focus-visible` outline for
  links, buttons, inputs, selects, and `[tabindex]` (`styles/global.css`).
- **Streamed Ask was silent to screen readers.** The answer region now has
  `aria-live="polite"` + `aria-busy={streaming}`, and the Ask/provider error lines
  are `role="alert"`, so the streaming answer, the final result, and failures are
  announced (`pages/AskPage.tsx`).

## Backlog (prioritised; follow-ups)

1. **Modal focus trap + restore** (`FolderPickerModal.tsx`). The dialog focuses
   itself and handles `Escape`, but `Tab` can move focus to the page behind it and
   focus is not returned to the trigger on close. Add a focus trap and restore
   focus to the opener. *(Medium effort; deferred to avoid a risky hand-rolled trap
   in this pass.)*
2. **Skip link.** Add a "skip to main content" link as the first focusable element
   so keyboard users can bypass the 10-item nav (`app/App.tsx` + `styles/global.css`).
3. **Status conveyed by colour alone.** Catalog/job/availability states use colour
   classes (`status-indexed`, `availability-missing`, `status-building`, …) with
   the state word as text — text is present, but some badges rely on hue to
   distinguish *kinds*. Verify each state also reads unambiguously in monochrome;
   add an icon/shape where only colour differs.
4. **Contrast of muted/disabled text.** `--muted` (`#9aa2ad`) on `--bg` (`#0f1115`)
   is comfortably > 4.5:1, but `--disabled` (`#6e7681`) used for
   `status-queued`/`status-unsupported`/`status-discovered` is borderline (~4.3:1)
   for small text. Nudge `--disabled` lighter to clear WCAG AA.
5. **Long-running job announcements.** The Jobs/System pages poll status; confirm
   the polled status text sits in a `role="status"`/`aria-live` region so progress
   is announced without a manual refresh (mirror the Ask fix).
6. **Automated axe pass** in the frontend test setup (`@axe-core/react` or
   `jest-axe`) as a regression guard once the above land.

## Browser workflow verification (manual checklist)

Run the primary workflows keyboard-only (Tab/Shift-Tab/Enter/Escape) and with a
screen reader, confirming focus is always visible and state changes are announced:

- [ ] Add a location (Locations form + folder-picker dialog) — reachable, labelled,
      Escape closes, focus returns to the trigger (after backlog #1).
- [ ] Scan/index and watch job status update (Jobs/System) — announced.
- [ ] Search — results reachable; score/availability readable without colour.
- [ ] Ask — streamed answer + citations announced (`aria-live`); errors as alerts.
- [ ] Duplicates / Coverage / Sync Plans — tables navigable, states readable.

## Related

- [`runbook.md`](runbook.md) — health endpoints the System page surfaces.
- Frontend component tests live beside each page (`*.test.tsx`).
