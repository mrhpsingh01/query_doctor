import { test, expect } from "@playwright/test";
test("showcase and paste work with every API request blocked", async ({ page }) => {
  const apiCalls: string[] = [];
  await page.route("**/api/**", async route => { apiCalls.push(route.request().url()); await route.abort(); });
  await page.goto("/");
  await expect(page.getByRole("button", { name: /97% Scan Waste/ })).toBeVisible();
  const renderMs = await page.evaluate(async () => {
    const button = [...document.querySelectorAll("button")].find(b => b.textContent?.includes("97% Scan Waste"))!;
    const start = performance.now();
    button.click();
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    return performance.now() - start;
  });
  console.log(`Showcase click-to-two-animation-frames: ${renderMs.toFixed(1)} ms`);
  for (const title of [/97% Scan Waste/, /Sort Spill/, /Unindexed MongoDB/, /False Positive/]) {
    await page.getByRole("button", { name: title }).click();
    await expect(page.getByRole("group", { name: "Execution plan" })).toBeVisible();
  }
  // the rejected index: Suggested and Planner-validated are earned, Benchmark-validated visibly FAILS
  await page.goto("/");
  await page.getByRole("button", { name: /False Positive/ }).click();
  const ladder = page.getByRole("list", { name: "Verification ladder" }).getByRole("listitem");
  await expect(ladder).toHaveCount(3);
  await expect(ladder.nth(0)).toHaveAttribute("data-status", "pass");
  await expect(ladder.nth(1)).toHaveAttribute("data-status", "pass");
  await expect(ladder.nth(2)).toHaveAttribute("data-status", "fail");
  await expect(ladder.nth(2)).toContainText("FAILED: Benchmark-validated");
  await page.goto("/analyses/new");
  await page.getByRole("textbox", { name: "Paste EXPLAIN Output" }).fill('{"Plan":{"Node Type":"Seq Scan","Relation Name":"orders","Filter":"customer_id = 1","Actual Total Time":5,"Actual Loops":1}}');
  await page.getByRole("button", { name: "Inspect plan" }).click();
  await expect(page.getByText(/Suggested \(Unverified/)).toBeVisible();
  expect(apiCalls).toEqual([]);
  await page.goto("/");
  await page.getByRole("button", { name: /97% Scan Waste/ }).click();
  await page.screenshot({ path: "test-results/offline-showcase.png", fullPage: true });
});
