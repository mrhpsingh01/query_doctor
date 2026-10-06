import { expect, test } from "@playwright/test";
import path from "node:path";

// Regenerates the README screenshots from the live stack:  QD_SCREENSHOTS=1 npm run e2e -- screenshots
test.skip(!process.env.QD_SCREENSHOTS, "set QD_SCREENSHOTS=1 to regenerate docs/screenshots");
test.use({ viewport: { width: 1280, height: 900 }, colorScheme: "light" });

const OUT = path.resolve(__dirname, "../../../docs/screenshots");
const shot = (name: string) => path.join(OUT, name);

test("capture the main journey for the README", async ({ page }) => {
  await page.goto("/analyses/new?sample=recent_orders_by_customer");
  await expect(page.getByRole("button", { name: /analyze query/i })).toBeVisible();
  await page.waitForTimeout(2500); // let Monaco load
  await page.screenshot({ path: shot("01-new-analysis.png") });

  await page.getByRole("radio", { name: /execute with analysis/i }).check();
  await page.getByRole("button", { name: /analyze and run query/i }).click();
  await expect(page.getByRole("heading", { name: "Execution plan" })).toBeVisible();
  await page.locator(".react-flow__node").filter({ hasText: "Seq Scan" }).first().click();
  await expect(page.getByRole("complementary", { name: /operator details/i })).toBeVisible();
  await page.waitForTimeout(800);
  await page.locator("section[aria-labelledby='plan-h']").screenshot({ path: shot("02-plan-and-operator.png") });

  await page.locator("section[aria-labelledby='findings-h']").screenshot({ path: shot("03-findings.png") });

  const idea = page.getByRole("article").first();
  await idea.scrollIntoViewIfNeeded();
  await idea.locator("xpath=..").screenshot({ path: shot("04-index-idea.png") });

  await idea.getByRole("checkbox").check();
  await idea.getByRole("button", { name: /run benchmark/i }).click();
  await expect(page.locator('[data-outcome="faster"]').first()).toBeVisible({ timeout: 120_000 });
  await page.waitForTimeout(800);
  await page.locator("section[aria-labelledby='ideas-h']").locator("xpath=.//article[1]/..").first().screenshot({ path: shot("05-benchmark.png") });
});
