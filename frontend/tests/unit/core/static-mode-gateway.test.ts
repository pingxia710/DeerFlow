import { beforeEach, expect, test, rs } from "@rstest/core";

const fetchWithAuth = rs.fn();

rs.mock("@/core/api/fetcher", () => ({
  DEFAULT_NON_STREAMING_REQUEST_TIMEOUT_MS: 15_000,
  fetch: fetchWithAuth,
}));

rs.mock("@/core/config", () => ({
  getBackendBaseURL: () => "/backend",
}));

rs.mock("@/core/static-mode", () => ({
  isStaticWebsiteOnly: () => true,
}));

beforeEach(() => {
  fetchWithAuth.mockReset();
});

test("static mode returns no context usage without requesting Gateway", async () => {
  const { fetchThreadContextUsage } = await import("@/core/threads/api");

  await expect(fetchThreadContextUsage("demo-thread")).resolves.toBeNull();
  expect(fetchWithAuth).not.toHaveBeenCalled();
});

test("static mode returns an empty channel catalog without requesting Gateway", async () => {
  const { listChannelProviders } = await import("@/core/channels/api");

  await expect(listChannelProviders()).resolves.toEqual({
    enabled: false,
    providers: [],
  });
  expect(fetchWithAuth).not.toHaveBeenCalled();
});

test("static mode returns no skills without requesting Gateway", async () => {
  const { loadSkills } = await import("@/core/skills/api");

  await expect(loadSkills()).resolves.toEqual([]);
  expect(fetchWithAuth).not.toHaveBeenCalled();
});

test("static mode disables suggestions without requesting Gateway", async () => {
  const { loadSuggestionsConfig } = await import("@/core/suggestions/api");

  await expect(loadSuggestionsConfig()).resolves.toEqual({ enabled: false });
  expect(fetchWithAuth).not.toHaveBeenCalled();
});
