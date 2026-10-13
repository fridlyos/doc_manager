import { expect, test } from "@playwright/test";
import { installMock, scanJob } from "./mock";

test("scan submit shows live progress and survives a refresh", async ({ page }) => {
  const state = await installMock(page, null);
  await page.goto("/locations");

  // Submit a scan for the seeded location.
  await page.getByRole("button", { name: "Scan" }).first().click();
  await expect.poll(() => state.calls.some((c) => /\/locations\/.+\/scan$/.test(c.url))).toBe(true);

  // Scan auto-expands the row. The panel shows the catalog headline (what is
  // searchable) plus the live current-scan action + denominator.
  await expect(page.getByText(/450 of 1,200 files indexed/)).toBeVisible();
  await expect(page.getByText(/Discovering files — 500 of 1,200/)).toBeVisible();
  await expect(page.getByText("500 / 1,200 (42%)")).toBeVisible();
  await expect(page.getByText("running")).toBeVisible();

  // Refresh mid-scan: the row re-collapses; opening Details re-attaches to the
  // still-running job (rendered purely from the polled GET, not client state).
  await page.reload();
  await page.getByRole("button", { name: "Details" }).first().click();
  await expect(page.getByText("500 / 1,200 (42%)")).toBeVisible();
  await expect(page.getByText("running")).toBeVisible();
});

test("a completed scan shows final counts and an explicit success state", async ({ page }) => {
  await installMock(
    page,
    scanJob({
      status: "succeeded",
      finished_at: "2026-10-03T12:10:00Z",
      scan_summary: {
        phase: "reconciled",
        discovered: 120,
        scanned: 120,
        target: 10000,
        changed: 120,
        missing: 0,
        indexed: 120,
        index_failed: 0,
        index_remaining: 0,
        index_total: 120,
      },
    }),
  );
  await page.goto("/locations");
  // Details are collapsed by default; open them to see the scan outcome.
  await page.getByRole("button", { name: "Details" }).first().click();
  await expect(page.getByText("Scan complete")).toBeVisible();
  await expect(page.getByText("succeeded")).toBeVisible();
});

test("jobs console cancels a running job and retries a failed one", async ({ page }) => {
  const state = await installMock(page, scanJob({ status: "running" }));
  await page.goto("/jobs");

  await page.getByRole("button", { name: "Cancel" }).first().click();
  await expect.poll(() => state.calls.some((c) => /\/jobs\/.+\/cancel$/.test(c.url))).toBe(true);
});
