import { expect, test } from "@playwright/test";
import path from "node:path";

test.use({ viewport: { width: 1280, height: 900 }, colorScheme: "light" });

test("an analysis can be explained from numbered facts, with the source shown", async ({ page }) => {
  await page.goto("/analyses/new?sample=recent_orders_by_customer");
  await page.getByRole("radio", { name: /execute with analysis/i }).check();
  await page.getByRole("button", { name: /analyze and run query/i }).click();
  await expect(page.getByRole("heading", { name: "Execution plan" })).toBeVisible({ timeout: 60_000 });
  await page.getByRole("button", { name: /explain the plan/i }).click();
  const panel = page.locator("section[aria-labelledby='explain-h']");
  await expect(panel.getByText("Template")).toBeVisible();
  await expect(panel).toContainText(/slowest step/i);
  await panel.getByText(/facts used/i).click();
  await expect(panel.getByText("F1", { exact: true })).toBeVisible();
  if (process.env.QD_SCREENSHOTS) {
    await panel.scrollIntoViewIfNeeded();
    await panel.screenshot({ path: path.resolve(__dirname, "../../../docs/screenshots/09-explain.png") });
  }
  await page.getByRole("button", { name: /pull-request comment/i }).click();
  await expect(panel.getByRole("button", { name: /copy comment/i })).toBeVisible();
});
