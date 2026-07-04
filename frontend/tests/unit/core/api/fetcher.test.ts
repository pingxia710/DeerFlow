import { afterEach, expect, test, rs } from "@rstest/core";

import { fetch } from "@/core/api/fetcher";

afterEach(() => {
  rs.unstubAllGlobals();
});

test("auth fetch throws on 401 without requiring browser navigation globals", async () => {
  rs.stubGlobal(
    "fetch",
    rs.fn(async () => new Response(null, { status: 401 })),
  );
  rs.stubGlobal("window", undefined);

  await expect(fetch("/api/test")).rejects.toThrow("Unauthorized");
});
