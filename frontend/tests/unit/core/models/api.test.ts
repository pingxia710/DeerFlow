import { beforeEach, expect, test, rs } from "@rstest/core";

const fetchWithAuth = rs.fn();

rs.mock("@/core/api/fetcher", () => ({
  DEFAULT_NON_STREAMING_REQUEST_TIMEOUT_MS: 15_000,
  fetch: fetchWithAuth,
}));

rs.mock("@/core/config", () => ({
  getBackendBaseURL: () => "",
}));

rs.mock("@/core/static-mode", () => ({
  isStaticWebsiteOnly: () => false,
}));

beforeEach(() => {
  fetchWithAuth.mockReset();
});

test("loadModels bounds the boot-time backend request", async () => {
  fetchWithAuth.mockResolvedValueOnce(
    new Response(
      JSON.stringify({
        models: [{ id: "deepseek-command-room" }],
        token_usage: { enabled: true },
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    ),
  );

  const { loadModels } = await import("@/core/models/api");

  await expect(loadModels()).resolves.toMatchObject({
    token_usage: { enabled: true },
  });
  expect(fetchWithAuth).toHaveBeenCalledWith("/api/models", {
    timeoutMs: 15_000,
  });
});

test("loadModels throws a clear error for gateway failures", async () => {
  fetchWithAuth.mockResolvedValueOnce(
    new Response("<html>Gateway Timeout</html>", { status: 504 }),
  );

  const { loadModels } = await import("@/core/models/api");

  await expect(loadModels()).rejects.toThrow("Failed to load models: 504");
});
