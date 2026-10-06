import { expect, test } from "@playwright/test";
import path from "node:path";

test.use({ viewport: { width: 1280, height: 900 }, colorScheme: "light" });

test("the models page shows the comparison and says what orders suggestions", async ({ page }) => {
  await page.goto("/models");
  await expect(page.getByRole("heading", { name: "Recommendation model" })).toBeVisible();
  await expect(page.getByRole("table", { name: /model comparison/i })).toBeVisible();
  await expect(page.getByText(/the planner score keeps ordering suggestions/i)).toBeVisible();
  await expect(page.getByRole("region", { name: /monitoring/i })).toBeVisible();
  if (process.env.QD_SCREENSHOTS) {
    await page.waitForTimeout(800);
    await page.screenshot({ path: path.resolve(__dirname, "../../../docs/screenshots/08-models.png"), fullPage: true });
  }
});

test("an analysis shows a prediction block apart from the evidence badge", async ({ page }) => {
  await page.goto("/analyses/new?sample=recent_orders_by_customer");
  await page.getByRole("button", { name: /analyze query/i }).click();
  await expect(page.getByRole("heading", { name: "Execution plan" })).toBeVisible({ timeout: 60_000 });
  const block = page.locator("[data-prediction]").first();
  await expect(block.getByRole("heading", { name: "Prediction" })).toBeVisible();
  await expect(block).toContainText(/not a measurement/i);
  await expect(page.getByText("Planner-validated").first()).toBeVisible();
  await page.getByRole("button", { name: "Helpful" }).first().click();
  await expect(page.getByText(/thanks for the feedback/i).first()).toBeVisible();
});
