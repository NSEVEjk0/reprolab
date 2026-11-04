import { defineConfig, devices } from "@playwright/test";
export default defineConfig({
  testDir:"./tests/browser",timeout:45_000,retries:0,workers:1,
  use:{baseURL:"http://127.0.0.1:8080",screenshot:"only-on-failure",trace:"retain-on-failure"},
  projects:[{name:"chromium",use:{...devices["Desktop Chrome"]}}],
  webServer:{command:"python3 -m reprolab.server",url:"http://127.0.0.1:8080/api/health",timeout:30_000,reuseExistingServer:false}
});
