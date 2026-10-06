import { expect, test } from "@playwright/test";
import path from "node:path";

// Needs the full stack with MongoDB seeded (`docker compose -f infrastructure/compose.yaml up -d --build`).
test.use({ viewport: { width: 1280, height: 900 }, colorScheme: "light" });

const OUT = path.resolve(__dirname, "../../../docs/screenshots");

/** "742 ms" / "6.2 s" / "0.35 ms" -> milliseconds. */
function toMs(text: string): number {
  const match = text.trim().match(/^([\d.]+)\s*(ms|s)$/);
  if (!match) throw new Error(`not a duration: ${text}`);
  return match[2] === "s" ? parseFloat(match[1]) * 1000 : parseFloat(match[1]);
}

test("a developer investigates a slow MongoDB query, tests an index idea and exports the evidence", async ({ page }) => {
  // 1. Choose MongoDB and a slow query from its own catalog; the editor switches to JSON.
  await page.goto("/analyses/new");
  await expect(page.getByRole("heading", { name: "New analysis" })).toBeVisible();
  await page.getByRole("radio", { name: "MongoDB" }).check();
  await expect(page.getByText(/one find or aggregate as json/i)).toBeVisible();
  await page.getByRole("button", { name: /orders of one customer: a collection scan/i }).click();
  await expect(page.getByLabel("MongoDB query")).toHaveValue(/"find": "orders"/);
  if (process.env.QD_SCREENSHOTS) {
    await page.waitForTimeout(2500); // let Monaco load
    await page.screenshot({ path: path.join(OUT, "10-mongo-new-analysis.png") });
  }

  // 2. Run it with execution so real statistics are collected.
  await page.getByRole("radio", { name: /execute with analysis/i }).check();
  await expect(page.getByRole("note")).toContainText(/read-only user/i);
  await page.getByRole("button", { name: /analyze and run query/i }).click();

  // 3. The plan shows documents examined against returned, with no planner estimates.
  await expect(page).toHaveURL(/\/analyses\/\d+$/);
  await expect(page.getByRole("heading", { name: "Execution plan" })).toBeVisible();
  const scan = page.locator(".react-flow__node").filter({ hasText: "COLLSCAN" }).first();
  await expect(scan).toContainText(/examined [\d.]+k? docs/);
  await expect(scan).toContainText(/per result/);
  await expect(scan).not.toContainText(/\best\b/);
  await scan.click();
  const details = page.getByRole("complementary", { name: /operator details/i });
  await expect(details).toContainText("Documents examined");
  await expect(details).toContainText(/reads every document in the collection/i);

  // 4. The findings cite their evidence.
  const findings = page.locator("section[aria-labelledby='findings-h']");
  await expect(findings).toContainText("Collection scan");
  await expect(findings).toContainText(/docs examined/i);
  await expect(findings).not.toContainText("[object Object]");
  if (process.env.QD_SCREENSHOTS) {
    await page.waitForTimeout(800);
    await page.locator("section[aria-labelledby='plan-h']").screenshot({ path: path.join(OUT, "11-mongo-plan.png") });
  }

  // 5. The index idea is a suggestion until it is measured, and its command is shown as MongoDB runs it.
  const idea = page.getByRole("article").first();
  await expect(idea).toContainText("Suggested");
  await expect(idea).toContainText('db.orders.createIndex({"customer_id": 1})');
  await expect(idea).toContainText(/no hypothetical indexes/i);

  // 6. Approve a benchmark (explicit confirmation required) and wait for the measured comparison.
  const runButton = idea.getByRole("button", { name: /run benchmark/i });
  await expect(runButton).toBeDisabled();
  await idea.getByRole("checkbox", { name: /real index in a temporary copy of the collections/i }).check();
  await runButton.click();
  const outcome = page.locator('[data-outcome="faster"]').first();
  await expect(outcome).toBeVisible({ timeout: 120_000 });
  await expect(outcome).toContainText(/\d+% faster/);
  const summary = page.getByRole("table", { name: "Summary" }).first();
  const cells = await summary.getByRole("row", { name: /median/i }).getByRole("cell").allTextContents();
  const [original, withIndex] = cells.map(toMs);
  expect(original).toBeGreaterThan(withIndex);
  await expect(idea).toContainText("Benchmark-validated");

  // 7. Export the report: it carries the measured numbers and the MongoDB command.
  const [download] = await Promise.all([page.waitForEvent("download"), page.getByRole("link", { name: "Markdown" }).click()]);
  const chunks: Buffer[] = [];
  for await (const chunk of await download.createReadStream()) chunks.push(chunk as Buffer);
  const markdown = Buffer.concat(chunks).toString("utf-8");
  expect(markdown).toContain("Median");
  expect(markdown).toContain("Benchmark-validated");
  expect(markdown).toContain("db.orders.createIndex");
  expect(markdown).not.toMatch(/mongodb:\/\//);
});

test("the workload page ranks MongoDB query shapes from the profiler", async ({ page }) => {
  await page.goto("/workload");
  await page.getByRole("radio", { name: "MongoDB" }).check();
  await page.getByRole("button", { name: /generate demo traffic/i }).click();
  await expect(page.getByRole("status")).toContainText(/import again/i);
  // The traffic takes a while: import until the ranking has its shapes.
  const rows = page.getByRole("table").getByRole("row");
  await expect(async () => {
    await page.getByRole("button", { name: /import profiler data/i }).click();
    await expect(rows.nth(8)).toBeVisible({ timeout: 3_000 });
  }).toPass({ timeout: 120_000, intervals: [5_000] });
  await expect(page.getByText(/not a find or an aggregate/i)).toBeVisible();
  const first = rows.nth(1);
  await expect(first).toContainText('"find":"orders"'); // the frequent, moderately slow query leads
  if (process.env.QD_SCREENSHOTS) {
    await page.waitForTimeout(500);
    await page.screenshot({ path: path.join(OUT, "12-mongo-workload.png"), fullPage: true });
  }
  await first.getByRole("link", { name: /analyze/i }).click();
  await expect(page).toHaveURL(/engine=mongodb/);
  await expect(page.getByRole("radio", { name: "MongoDB" })).toBeChecked();
  await expect(page.getByLabel("MongoDB query")).not.toHaveValue("");
});

test("history shows which database each analysis ran on", async ({ page }) => {
  await page.goto("/analyses/new");
  const history = page.getByRole("region", { name: "Previous analyses" });
  await expect(history.getByText("MongoDB").first()).toBeVisible();
});
