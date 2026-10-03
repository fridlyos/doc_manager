import { defineConfig, devices } from "@playwright/test";

// Browser E2E (Phase 9.i). Drives the real UI in Chromium against a mocked API
// (route interception) so no backend/PG is needed. Run: `npm run test:e2e`
// (after `npx playwright install chromium`). Kept out of the unit CI gate.
export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  fullyParallel: true,
  use: {
    baseURL: "http://localhost:5173",
    headless: true,
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "npm run dev -- --port 5173 --strictPort",
    url: "http://localhost:5173",
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
});
