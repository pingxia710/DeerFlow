import { defineConfig, devices } from "@playwright/test";

const GATEWAY_PORT = process.env.PLAYWRIGHT_REAL_BACKEND_GATEWAY_PORT ?? "8011";
const FRONTEND_PORT =
  process.env.PLAYWRIGHT_REAL_BACKEND_FRONTEND_PORT ?? "3100";
const GATEWAY_URL = `http://127.0.0.1:${GATEWAY_PORT}`;
const APP_URL = `http://localhost:${FRONTEND_PORT}`;
const FRONTEND_WEB_SERVER_TIMEOUT_MS = 360_000;

/**
 * Layer 2 of the record/replay e2e: the REAL Next.js frontend rendering data
 * from a REAL gateway whose LLM is the deterministic `ReplayChatModel` (no API
 * key). This is separate from `playwright.config.ts` (which mocks the backend)
 * so the mock-based suite is untouched.
 *
 * Two webServers are started: the replay gateway (:8011 by default) and the
 * frontend (:3100 by default, pointed at the gateway). Auth-disabled mode is enabled on both
 * servers so the no-cookie e2e contract is covered; specs that need session
 * cookies still register a throwaway test account at runtime.
 */
export default defineConfig({
  testDir: "./tests/e2e-real-backend",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: process.env.CI ? "github" : "html",
  timeout: 90_000,

  use: {
    baseURL: APP_URL,
    trace: "on-first-retry",
  },

  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],

  webServer: [
    {
      command: `uv run python scripts/run_replay_gateway.py --port ${GATEWAY_PORT} --cors ${APP_URL}`,
      cwd: "../backend",
      url: `${GATEWAY_URL}/health`,
      reuseExistingServer: process.env.PLAYWRIGHT_REUSE_EXISTING_SERVER === "1",
      timeout: 180_000,
      stdout: "pipe",
      stderr: "pipe",
      // Mount the test-only run/message seeder used by multi-run-order.spec.ts
      // (#3352). The endpoint exists only on this replay gateway, never in the
      // production app.
      env: {
        DEERFLOW_ENABLE_TEST_SEED: "1",
        DEER_FLOW_AUTH_DISABLED: "1",
      },
    },
    {
      command: "pnpm build && pnpm start",
      url: APP_URL,
      reuseExistingServer: process.env.PLAYWRIGHT_REUSE_EXISTING_SERVER === "1",
      timeout: FRONTEND_WEB_SERVER_TIMEOUT_MS,
      env: {
        SKIP_ENV_VALIDATION: "1",
        DEER_FLOW_AUTH_DISABLED: "1",
        BETTER_AUTH_SECRET: "local-dev-secret",
        NEXT_DIST_DIR: ".next-e2e-real",
        PORT: FRONTEND_PORT,
        // Leave NEXT_PUBLIC_* unset so the frontend uses its built-in
        // next.config rewrites (same-origin proxy) instead of talking to the
        // gateway cross-origin — cross-origin fetches drop the auth cookies.
        // Just point that proxy at the replay gateway.
        DEER_FLOW_INTERNAL_GATEWAY_BASE_URL: GATEWAY_URL,
      },
    },
  ],
});
