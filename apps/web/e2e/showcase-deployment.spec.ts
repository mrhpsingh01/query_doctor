import { test, expect } from "@playwright/test";

test("public showcase contains no routes that require a backend", async ({ page }) => {
  test.skip(!process.env.QD_EXPECT_SHOWCASE_ONLY, "Run against a build with NEXT_PUBLIC_QD_DEMO_MODE=true");
  const apiRequests: string[] = [];
  await page.route("**/api/**", async route => { apiRequests.push(route.request().url()); await route.abort(); });
  await page.goto("/");
  await expect(page.getByRole("link", { name:"Run locally", exact:true })).toBeVisible();
  await expect(page.getByRole("link", { name:"Workload", exact:true })).toBeVisible();
  await page.getByRole("button", { name:/97% Scan Waste/ }).click();
  await expect(page.getByText(/^Output checksum:.*SHA-256 of sorted/)).toBeVisible();
  await page.getByRole("link", { name:"Paste EXPLAIN", exact:true }).click();
  await expect(page.getByRole("textbox", { name:"Paste EXPLAIN Output" })).toBeVisible();
  await expect(page.getByRole("tab", { name:"Live query" })).toHaveCount(0);
  await page.goto("/analyses/new?mode=live&sql=SELECT%201");
  await expect(page.getByRole("textbox", { name:"Paste EXPLAIN Output" })).toBeVisible();
  // the Workload tab is pre-loaded with a real recorded capture: the Ranked Fix Queue needs no pg_stat_statements
  await page.goto("/workload");
  await expect(page).toHaveURL(/\/workload$/);
  const queue = page.getByRole("region", { name: "Ranked Fix Queue" });
  await expect(queue).toContainText(/Benchmark-Validated indexes could recover approximately/);
  await expect(queue.getByRole("listitem").first()).toContainText("CREATE INDEX");
  await expect(page.getByRole("button", { name: /import|generate demo|approve/i })).toHaveCount(0);
  // routes that genuinely need a database still bounce to the showcase
  await page.goto("/workload/0123456789abcdef");
  await expect(page).toHaveURL(/\/$/);
  expect(apiRequests).toEqual([]);
});
