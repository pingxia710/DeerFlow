import { expect, test } from "@playwright/test";

import { AUTH_DISABLED_USER } from "../../src/core/auth/auth-disabled-user";

const APP = `http://localhost:${process.env.PLAYWRIGHT_REAL_BACKEND_FRONTEND_PORT ?? "3100"}`;

test.describe("auth-disabled contract (real backend)", () => {
  test("gateway /auth/me returns the frontend synthetic user without a cookie", async ({
    context,
  }) => {
    const resp = await context.request.get(`${APP}/api/v1/auth/me`);

    expect(resp.status(), await resp.text()).toBe(200);
    await expect(resp.json()).resolves.toEqual(AUTH_DISABLED_USER);
  });
});
