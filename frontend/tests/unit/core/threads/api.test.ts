import { beforeEach, expect, test, rs } from "@rstest/core";

const fetchWithAuth = rs.fn();

rs.mock("@/core/api/fetcher", () => ({
  DEFAULT_NON_STREAMING_REQUEST_TIMEOUT_MS: 15_000,
  fetch: fetchWithAuth,
}));

beforeEach(() => {
  fetchWithAuth.mockReset();
});

test("fetchThreadTokenUsage uses shared auth fetch without JSON GET headers", async () => {
  fetchWithAuth.mockResolvedValue({
    ok: true,
    json: async () => ({
      thread_id: "thread-1",
      total_input_tokens: 3,
      total_output_tokens: 4,
      total_tokens: 7,
      total_runs: 1,
      by_model: { unknown: { tokens: 7, runs: 1 } },
      by_caller: {
        lead_agent: 0,
        subagent: 0,
        middleware: 0,
      },
    }),
  });

  const { fetchThreadTokenUsage } = await import("@/core/threads/api");

  await expect(fetchThreadTokenUsage("thread-1")).resolves.toMatchObject({
    thread_id: "thread-1",
    total_tokens: 7,
  });

  expect(fetchWithAuth).toHaveBeenCalledWith(
    expect.stringContaining("/api/threads/thread-1/token-usage"),
    {
      method: "GET",
      timeoutMs: 15_000,
    },
  );
});

test("fetchThreadTokenUsage returns null for unavailable token usage", async () => {
  fetchWithAuth.mockResolvedValue({
    ok: false,
    status: 404,
  });

  const { fetchThreadTokenUsage } = await import("@/core/threads/api");

  await expect(fetchThreadTokenUsage("thread-1")).resolves.toBeNull();
});

test("fetchThreadContextUsage uses shared auth fetch", async () => {
  fetchWithAuth.mockResolvedValue({
    ok: true,
    json: async () => ({
      thread_id: "thread-1",
      latest: {
        run_id: "run-1",
        caller: "lead_agent",
        llm_call_index: 1,
        message_count: 4,
        char_count: 200,
        estimated_tokens: 50,
        role_counts: { human: 2, ai: 2 },
        seq: 3,
        created_at: "2026-06-28T10:00:00+00:00",
      },
      latest_lead: null,
      by_caller: {},
      recent: [],
    }),
  });

  const { fetchThreadContextUsage } = await import("@/core/threads/api");

  await expect(fetchThreadContextUsage("thread-1")).resolves.toMatchObject({
    thread_id: "thread-1",
    latest: { estimated_tokens: 50 },
  });

  expect(fetchWithAuth).toHaveBeenCalledWith(
    expect.stringContaining("/api/threads/thread-1/context-usage"),
    {
      method: "GET",
      timeoutMs: 15_000,
    },
  );
});

test("fetchThreadContextDetail returns the complete snapshot text", async () => {
  const fullTaskResult = `subagent result ${"结果".repeat(10_000)}`;
  fetchWithAuth.mockResolvedValue({
    ok: true,
    json: async () => ({
      run_id: "run/1",
      caller: "lead_agent",
      llm_call_index: 3,
      message_count: 1,
      tool_schema_count: 0,
      char_count: fullTaskResult.length,
      estimated_tokens: 0,
      role_counts: { tool: 1 },
      has_full_text: true,
      seq: 9,
      created_at: "2026-07-13T10:00:00+00:00",
      messages: [{ role: "tool", name: "task", content: fullTaskResult }],
      tool_schemas: [],
    }),
  });

  const api = await import("@/core/threads/api");
  const fetchThreadContextDetail = (
    api as typeof api & {
      fetchThreadContextDetail: (
        threadId: string,
        runId: string,
        seq: number,
      ) => Promise<{ messages: Array<{ content: string }> } | null>;
    }
  ).fetchThreadContextDetail;

  expect(typeof fetchThreadContextDetail).toBe("function");
  await expect(
    fetchThreadContextDetail("thread 1", "run/1", 9),
  ).resolves.toMatchObject({
    messages: [{ content: fullTaskResult }],
  });
  expect(fetchWithAuth).toHaveBeenCalledWith(
    expect.stringContaining("/api/threads/thread%201/context-usage/run%2F1/9"),
    {
      method: "GET",
      timeoutMs: 15_000,
    },
  );
});
