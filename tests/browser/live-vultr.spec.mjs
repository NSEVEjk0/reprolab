import { test, expect } from "@playwright/test";
import fs from "node:fs/promises";

test("a real Vultr repair has independent checks, confirmed cleanup and replayable evidence", async ({page}) => {
  test.skip(!process.env.REPROLAB_VERIFY_ORIGIN, "Run explicitly on the configured Vultr HTTPS deployment.");
  const access = process.env.REPROLAB_VERIFY_ACCESS;
  expect(typeof access).toBe("string");
  await page.goto("/");
  await expect(page.locator("#connection")).toHaveText("Vultr repairs connected");
  await page.getByRole("button", {name: /Discount rounding/}).click();
  await page.locator("#access").fill(access);
  await expect(page.getByRole("button", {name: "Start a live repair"})).toBeEnabled();
  await page.getByRole("button", {name: "Start a live repair"}).click();
  await expect.poll(async () => {
    const badge = await page.locator("#result-badge").textContent();
    return badge === "Repair passes" || badge === "Not verified" || await page.locator("#error").isVisible();
  }, {timeout: 240_000, intervals: [1500]}).toBe(true);
  await expect(page.locator("#result-badge")).toHaveText("Repair passes");
  await expect(page.locator("#origin")).toContainText("Live repair");
  await expect(page.locator("#candidate-passed")).toHaveText("20");
  expect(Number(await page.locator("#baseline-failed").textContent())).toBeGreaterThan(0);
  await expect(page.locator("#bundle-check")).toContainText("agree");
  await expect(page.locator("#bundle-check")).not.toHaveClass(/fail/);
  const event = page.waitForEvent("download");
  await page.getByRole("button", {name: "Download evidence"}).click();
  const download = await event;
  expect(download.suggestedFilename()).toBe("reprolab-rounding.json");
  await fs.mkdir("reports", {recursive: true, mode: 0o700});
  await download.saveAs("reports/live-vultr-evidence.json");
  // Clear the operator code before taking the explicitly requested report screenshots.
  await page.locator("#access").fill("");
  await page.screenshot({path: "reports/live-vultr-desktop.png", fullPage: true});
  await page.setViewportSize({width: 390, height: 844});
  await expect(page.locator("#candidate-passed")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({path: "reports/live-vultr-mobile.png", fullPage: true});
});
