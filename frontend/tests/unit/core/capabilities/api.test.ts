import { beforeEach, expect, test, rs } from "@rstest/core";

const fetchWithAuth = rs.fn();

rs.mock("@/core/api/fetcher", () => ({
  DEFAULT_NON_STREAMING_REQUEST_TIMEOUT_MS: 15_000,
  fetch: fetchWithAuth,
}));

rs.mock("@/core/config", () => ({
  getBackendBaseURL: () => "",
}));

beforeEach(() => {
  fetchWithAuth.mockReset();
});

test("loadCapabilitySnapshot uses the global endpoint before a thread exists", async () => {
  fetchWithAuth.mockResolvedValueOnce(
    new Response(JSON.stringify({ version: 1, command_room_runtime: {} }), {
      status: 200,
    }),
  );
  const { loadCapabilitySnapshot } = await import("@/core/capabilities/api");

  await expect(loadCapabilitySnapshot()).resolves.toMatchObject({ version: 1 });
  expect(fetchWithAuth).toHaveBeenCalledWith("/api/capabilities", {
    timeoutMs: 15_000,
  });
});

test("loadCapabilitySnapshot uses the owner-checked thread endpoint", async () => {
  fetchWithAuth.mockResolvedValueOnce(
    new Response(JSON.stringify({ version: 1, thread_id: "thread-1" }), {
      status: 200,
    }),
  );
  const { loadCapabilitySnapshot } = await import("@/core/capabilities/api");

  await loadCapabilitySnapshot("thread-1");

  expect(fetchWithAuth).toHaveBeenCalledWith(
    "/api/threads/thread-1/capabilities",
    { timeoutMs: 15_000 },
  );
});

test("loadCapabilitySnapshot rejects gateway errors", async () => {
  fetchWithAuth.mockResolvedValueOnce(
    new Response(
      JSON.stringify({ detail: "Capability snapshot unavailable" }),
      {
        status: 503,
      },
    ),
  );
  const { loadCapabilitySnapshot } = await import("@/core/capabilities/api");

  await expect(loadCapabilitySnapshot()).rejects.toMatchObject({
    name: "CapabilityRequestError",
    status: 503,
    message: "Capability snapshot unavailable",
  });
});
