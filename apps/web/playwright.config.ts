import { defineConfig } from "@playwright/test";

// The journey runs against the real stack (`docker compose -f infrastructure/compose.yaml up -d --build`), with the web
// app served by the dev server below unless one is already running on this port. System Chrome is used so no browser
// has to be downloaded.
const PORT = Number(process.env.QD_WEB_PORT ?? 3100);

export default defineConfig({
  testDir: "./e2e",
  timeout: 180_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: `http://localhost:${PORT}`,
    channel: "chrome",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: {
    command: `npm run dev -- --port ${PORT}`,
    url: `http://localhost:${PORT}`,
    reuseExistingServer: true,
    timeout: 120_000,
  },
});
