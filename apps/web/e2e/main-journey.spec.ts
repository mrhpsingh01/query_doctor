import { expect, test } from "@playwright/test";

/** "742 ms" / "6.2 s" / "0.35 ms" -> milliseconds. */
function toMs(text: string): number {
  const match = text.trim().match(/^([\d.]+)\s*(ms|s)$/);
  if (!match) throw new Error(`not a duration: ${text}`);
  return match[2] === "s" ? parseFloat(match[1]) * 1000 : parseFloat(match[1]);
}

test("a developer investigates a slow query, tests an index idea and exports the evidence", async ({ page }) => {
  // 1. Open the sample project and pick a slow query from the catalog.
  await page.goto("/");
  await expect(page.getByRole("heading", { name: /why a query is slow/i })).toBeVisible();
  await page.getByRole("link", { name: /recent orders for one customer/i }).click();
  await expect(page).toHaveURL(/\/analyses\/new\?sample=recent_orders_by_customer/);
  await expect(page.getByRole("heading", { name: "New analysis" })).toBeVisible();

  // 2. Run it with execution so real timings are collected.
  await page.getByRole("radio", { name: /execute with analysis/i }).check();
  await expect(page.getByRole("note")).toContainText(/runs your query/i);
  await page.getByRole("button", { name: /analyze and run query/i }).click();

  // 3. The plan appears: the slow step is called out and can be opened.
  await expect(page).toHaveURL(/\/analyses\/\d+$/);
  await expect(page.getByRole("heading", { name: "Execution plan" })).toBeVisible();
  await expect(page.getByText(/of the time is spent in Seq Scan on orders/i)).toBeVisible();
  await page.locator(".react-flow__node").filter({ hasText: "Seq Scan" }).first().click();
  const details = page.getByRole("complementary", { name: /operator details/i });
  await expect(details).toContainText("Seq Scan on orders");
  await expect(details).toContainText(/reads every row of the table/i);

  // 4. The finding cites its evidence.
  const finding = page.getByRole("listitem").filter({ hasText: "Large scan discards most rows" });
  await expect(finding).toContainText("critical");
  await expect(finding).toContainText(/rows removed/i);

  // 5. The index idea is only planner-validated until it is measured.
  const idea = page.getByRole("article").first();
  await expect(idea).toContainText("Planner-validated");
  await expect(idea).toContainText(/CREATE INDEX ON orders \(customer_id, created_at DESC\)/);

  // 6. Approve a benchmark (explicit confirmation required) and wait for the measured comparison.
  const runButton = idea.getByRole("button", { name: /run benchmark/i });
  await expect(runButton).toBeDisabled();
  await idea.getByRole("checkbox", { name: /real index in an isolated copy/i }).check();
  await runButton.click();
  const outcome = page.locator('[data-outcome="faster"]').first();
  await expect(outcome).toBeVisible({ timeout: 120_000 });
  await expect(outcome).toContainText(/\d+% faster/);

  const summary = page.getByRole("table", { name: "Summary" }).first();
  const medianRow = summary.getByRole("row", { name: /median/i });
  const cells = await medianRow.getByRole("cell").allTextContents();
  const [original, withIndex] = cells.map(toMs); // the row's data cells are: original, with index
  expect(original).toBeGreaterThan(withIndex);
  await expect(idea).toContainText("Benchmark-validated"); // the evidence level was upgraded by the measurement

  // 7. Export the report: it carries the measured numbers.
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("link", { name: "Markdown" }).click(),
  ]);
  const stream = await download.createReadStream();
  const chunks: Buffer[] = [];
  for await (const chunk of stream) chunks.push(chunk as Buffer);
  const markdown = Buffer.concat(chunks).toString("utf-8");
  expect(markdown).toContain("Median");
  expect(markdown).toContain("Benchmark-validated");
  expect(markdown).not.toMatch(/postgresql:\/\//);
});
