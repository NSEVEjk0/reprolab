import { test, expect } from "@playwright/test";
test("a real sandbox reference has inspectable evidence, a portable download and a usable mobile view", async ({page}) => {
  await page.goto("/");
  await expect(page.getByRole("button",{name:"Start a live repair"})).toBeDisabled();
  await page.getByRole("button",{name:/Discount rounding/}).click();
  await page.getByRole("button",{name:"Inspect a sandbox example"}).click();
  await expect(page.locator("#result-badge")).toHaveText("Reference passes");
  await expect(page.locator("#origin")).toContainText("no AI call");
  await expect(page.locator("#candidate-passed")).toHaveText("20");
  await expect(page.locator("#bundle-check")).toContainText("agree");
  await expect(page.locator("#candidate-code")).toContainText("ROUND_HALF_UP");
  const download = page.waitForEvent("download");
  await page.getByRole("button",{name:"Download evidence"}).click();
  expect((await download).suggestedFilename()).toBe("reprolab-rounding.json");
  await page.screenshot({path:"reports/reprolab-desktop.png",fullPage:true});
  await page.setViewportSize({width:390,height:844});
  await expect(page.locator("#candidate-passed")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await page.screenshot({path:"reports/reprolab-mobile.png",fullPage:true});
});
test("live requests fail explicitly when inference is not connected", async ({request}) => {
  const response = await request.post("/api/jobs",{headers:{Origin:"http://127.0.0.1:8080"},data:{fixture_id:"month-end"}});
  expect(response.status()).toBe(503);
  const privateJob = await request.get("/api/jobs/" + "a".repeat(32));
  expect(privateJob.status()).toBe(404);
});
