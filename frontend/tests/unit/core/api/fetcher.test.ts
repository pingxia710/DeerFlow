import { afterEach, expect, test, rs } from "@rstest/core";

import {
  UnauthorizedError,
  fetch,
  isUnauthorizedError,
} from "@/core/api/fetcher";

afterEach(() => {
  rs.unstubAllGlobals();
});

test("auth fetch throws on 401 without requiring browser navigation globals", async () => {
  rs.stubGlobal(
    "fetch",
    rs.fn(async () => new Response(null, { status: 401 })),
  );
  rs.stubGlobal("window", undefined);

  await expect(fetch("/api/test")).rejects.toBeInstanceOf(UnauthorizedError);
  await expect(fetch("/api/test")).rejects.toMatchObject({ status: 401 });
});

test("auth fetch includes credentials and csrf header for state-changing requests", async () => {
  const fetchMock = rs.fn(
    async (_input: RequestInfo | URL, _init?: RequestInit) =>
      new Response(null, { status: 204 }),
  );
  rs.stubGlobal("fetch", fetchMock);
  rs.stubGlobal("document", {
    cookie: "session_id=abc; csrf_token=csrf%20token",
  });

  await fetch("/api/test", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });

  const init = fetchMock.mock.calls[0]?.[1];
  const headers = new Headers(init?.headers);
  expect(init?.credentials).toBe("include");
  expect(headers.get("Content-Type")).toBe("application/json");
  expect(headers.get("X-CSRF-Token")).toBe("csrf token");
});

test("auth fetch does not add csrf header for read-only requests", async () => {
  const fetchMock = rs.fn(
    async (_input: RequestInfo | URL, _init?: RequestInit) =>
      new Response(null, { status: 204 }),
  );
  rs.stubGlobal("fetch", fetchMock);
  rs.stubGlobal("document", { cookie: "csrf_token=csrf-token" });

  await fetch("/api/test");

  const init = fetchMock.mock.calls[0]?.[1];
  expect(init?.credentials).toBe("include");
  expect(new Headers(init?.headers).has("X-CSRF-Token")).toBe(false);
});

test("isUnauthorizedError recognizes typed and status-shaped 401 errors", () => {
  expect(isUnauthorizedError(new UnauthorizedError())).toBe(true);
  expect(isUnauthorizedError({ status: 401 })).toBe(true);
  expect(isUnauthorizedError(new Error("Unauthorized"))).toBe(false);
});
