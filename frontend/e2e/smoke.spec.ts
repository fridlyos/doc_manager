import { expect, test } from "@playwright/test";
import { installMock } from "./mock";

// Every screen is reachable + renders its heading (spec 9.i smoke pass).
const SCREENS: [string, string][] = [
  ["/", "System status"],
  ["/locations", "Locations"],
  ["/ask", "Ask"],
  ["/search", "Search"],
  ["/documents", "Documents"],
  ["/duplicates", "Duplicates"],
  ["/coverage", "Coverage"],
  ["/sync-plans", "Sync"],
  ["/errors", "Errors"],
  ["/jobs", "Jobs"],
];

for (const [path, heading] of SCREENS) {
  test(`screen ${path} renders`, async ({ page }) => {
    await installMock(page);
    await page.goto(path);
    await expect(page.getByRole("heading", { name: new RegExp(heading, "i") })).toBeVisible();
  });
}

test("skip link and primary nav are present", async ({ page }) => {
  await installMock(page);
  await page.goto("/");
  await expect(page.getByRole("link", { name: "Skip to main content" })).toBeAttached();
  await expect(page.getByRole("navigation", { name: "Primary navigation" })).toBeVisible();
});
