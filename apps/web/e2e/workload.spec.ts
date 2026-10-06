import { expect, test } from "@playwright/test";
import path from "node:path";

// Needs the full stack with demo traffic in pg_stat_statements (the seed job or the page's "Generate demo traffic").
test.use({ viewport: { width: 1280, height: 900 }, colorScheme: "light" });

const OUT = path.resolve(__dirname, "../../../docs/screenshots");

test("import the workload, read the ranking and open a query", async ({ page }) => {
  await page.goto("/workload");
  await page.getByRole("button", { name: /import statistics/i }).click();
  const rows = page.getByRole("table").getByRole("row");
  await expect(rows.nth(8)).toBeVisible(); // header + at least 8 queries
  await expect(page.getByRole("columnheader", { name: /estimated p95/i })).toBeVisible();
  for (const title of ["Highest total time", "Slowest queries (estimated p95)", "Missing benchmark validation"]) {
    await expect(page.getByRole("heading", { name: title })).toBeVisible();
  }
  await expect(page.getByRole("meter").first()).toBeVisible();
  if (process.env.QD_SCREENSHOTS) {
    await page.waitForTimeout(500);
    await page.screenshot({ path: path.join(OUT, "06-workload.png"), fullPage: true });
  }

  await rows.nth(1).getByRole("link").first().click();
  await expect(page).toHaveURL(/\/workload\/[0-9a-f]{16}$/);
  await expect(page.getByRole("heading", { name: /^Query [0-9a-f]{8}$/ })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Trend across imports" })).toBeVisible();
  if (process.env.QD_SCREENSHOTS) {
    await page.waitForTimeout(800);
    await page.screenshot({ path: path.join(OUT, "07-workload-query.png"), fullPage: true });
  }
  await page.getByRole("link", { name: /analyze this query/i }).click();
  await expect(page).toHaveURL(/\/analyses\/new\?sql=/);
});

test("History groups analyses by query", async ({ page }) => {
  await page.goto("/projects");
  await expect(page.getByRole("heading", { name: "All analyses" })).toBeVisible();
  await expect(page.getByRole("columnheader", { name: /measured p95/i })).toBeVisible();
});
