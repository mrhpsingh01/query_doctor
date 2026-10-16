import { test, expect } from "@playwright/test";

// Uses the sample stack. Isolated benchmark approval has its own API/worker integration checks.
test("ranked fix queue exposes evidence and explicit batch approval", async ({ page }) => {
  let submitted: unknown;
  await page.route("**/api/projects/*/workload/batch", async route => {
    submitted = route.request().postDataJSON();
    await route.fulfill({ status: 202, contentType: "application/json", body: JSON.stringify({ analysis_ids: [123], skipped: [] }) });
  });
  await page.goto("/workload");
  const queue = page.getByRole("region", { name: "Ranked Fix Queue" });
  await expect(queue).toBeVisible();
  const approve = queue.getByRole("button", { name: /Analyze top 5.*approve isolated benchmarks/ });
  await expect(approve).toBeVisible();
  await approve.click();
  await expect(page.getByRole("status")).toContainText("Queued 1 analyses");
  expect(submitted).toMatchObject({ top_n: 5, approve_benchmarks: true });
  const refreshed = page.waitForResponse(response => /\/api\/projects\/\d+\/workload(?:\?|$)/.test(response.url()));
  await queue.getByRole("button", { name: "Refresh queue" }).click();
  expect((await refreshed).ok()).toBeTruthy();
  await page.screenshot({ path: "test-results/workload-fix-queue.png", fullPage: true });
});
