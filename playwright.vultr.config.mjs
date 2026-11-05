import { defineConfig, devices } from "@playwright/test";

if (!process.env.REPROLAB_VERIFY_ORIGIN?.startsWith("https://")) {
  throw new Error("Set the actual HTTPS Vultr origin for the live verification.");
}

export default defineConfig({
  testDir: "./tests/browser",
  testMatch: "live-vultr.spec.mjs",
  timeout: 260_000,
  retries: 0,
  workers: 1,
  reporter: "line",
  use: {
    baseURL: process.env.REPROLAB_VERIFY_ORIGIN,
    trace: "off",
    screenshot: "off",
    video: "off",
  },
  projects: [{name: "chromium", use: {...devices["Desktop Chrome"]}}],
});
