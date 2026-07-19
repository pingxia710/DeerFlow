import { defineConfig, devices } from "@playwright/test";

const appUrl = process.env.E3_APP_URL;
const gatewayUrl = process.env.E3_GATEWAY_URL;

if (!appUrl || !gatewayUrl) {
  throw new Error(
    "E3_APP_URL and E3_GATEWAY_URL must be supplied by run_wp1_e3.py",
  );
}

export default defineConfig({
  testDir: "./tests/e2e-e3",
  fullyParallel: false,
  workers: 1,
  timeout: 90_000,
  use: {
    baseURL: appUrl,
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  metadata: { e3GatewayUrl: gatewayUrl },
});
