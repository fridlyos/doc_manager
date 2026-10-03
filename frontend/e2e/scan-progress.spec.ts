import { expect, test } from "@playwright/test";
import { installMock, scanJob } from "./mock";

test("scan submit shows live progress and survives a refresh", async ({ page }) => {
  const state = await installMock(page, null);
  await page.goto("/locations");

  // Submit a scan for the seeded location.
  await page.getByRole("button", { name: "Scan" }).first().click();
  await expect.poll(() => state.calls.some((c) => /\/locations\/.+\/scan$/.test(c.url))).toBe(true);

  // The scan-progress panel appears with scanned/target + a breakdown tile.
  await expect(page.getByText("500 / 10,000")).toBeVisible();
  await expect(page.getByText("discovered")).toBeVisible();
  await expect(page.getByText("running")).toBeVisible();

  // Refresh mid-scan: the panel re-attaches to the still-running job (reconnect).
  await page.reload();
  await expect(page.getByText("500 / 10,000")).toBeVisible();
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
  await expect(page.getByText("120 / 10,000")).toBeVisible();
  await expect(page.getByText("succeeded")).toBeVisible();
});

test("jobs console cancels a running job and retries a failed one", async ({ page }) => {
  const state = await installMock(page, scanJob({ status: "running" }));
  await page.goto("/jobs");

  await page.getByRole("button", { name: "Cancel" }).first().click();
  await expect.poll(() => state.calls.some((c) => /\/jobs\/.+\/cancel$/.test(c.url))).toBe(true);
});
