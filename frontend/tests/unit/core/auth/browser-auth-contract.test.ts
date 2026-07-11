import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { expect, test } from "@rstest/core";

const BROWSER_AUTH_CALLERS = [
  "src/app/(auth)/auth/callback/page.tsx",
  "src/app/(auth)/login/page.tsx",
  "src/app/(auth)/setup/page.tsx",
  "src/components/workspace/gateway-offline-banner.tsx",
  "src/components/workspace/settings/account-settings-page.tsx",
  "src/core/auth/AuthProvider.tsx",
] as const;

const HARDCODED_AUTH_URL =
  /(?:fetch|fetcher)\(\s*["'`]\/api\/v1\/auth|(?:\?|:)\s*["'`]\/api\/v1\/auth|window\.location\.href\s*=\s*`\/api\/v1\/auth/u;

test("browser auth callers honor the configured backend base URL", () => {
  const offenders = BROWSER_AUTH_CALLERS.filter((relativePath) => {
    const source = readFileSync(resolve(process.cwd(), relativePath), "utf-8");
    return HARDCODED_AUTH_URL.test(source);
  });

  expect(offenders).toEqual([]);
});

test("auth reconnect cleanup tolerates inaccessible session storage", async () => {
  const { clearStreamReconnectKeys } = await import("@/core/auth/AuthProvider");
  const inaccessibleStorage = {
    get length() {
      throw new DOMException("Access denied", "SecurityError");
    },
  } as unknown as Storage;

  expect(() => clearStreamReconnectKeys(inaccessibleStorage)).not.toThrow();
});

test("workspace auth revalidates the session when an inactive tab returns", () => {
  const source = readFileSync(
    resolve(process.cwd(), "src/core/auth/AuthProvider.tsx"),
    "utf-8",
  );

  expect(source).toContain(
    'window.addEventListener("focus", refreshIfVisible)',
  );
  expect(source).toContain(
    'document.addEventListener("visibilitychange", refreshIfVisible)',
  );
});
