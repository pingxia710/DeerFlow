import { afterEach, expect, rs, test } from "@rstest/core";

afterEach(() => {
  rs.restoreAllMocks();
  rs.unstubAllGlobals();
  rs.resetModules();
});

test("static demo search loads the lightweight index without fetching histories", async () => {
  const fetchMock = rs.fn(async () => ({
    ok: true,
    json: async () => [
      {
        thread_id: "older",
        created_at: "2026-07-09T00:00:00Z",
        values: { title: "Older", messages: [], artifacts: [] },
      },
      {
        thread_id: "newer",
        created_at: "2026-07-10T00:00:00Z",
        values: { title: "Newer", messages: [], artifacts: [] },
      },
    ],
  }));
  rs.stubGlobal("fetch", fetchMock);
  const { loadStaticDemoThreads } = await import("@/core/threads/static-demo");

  await expect(loadStaticDemoThreads({ limit: 1 })).resolves.toMatchObject([
    { thread_id: "newer" },
  ]);
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(fetchMock).toHaveBeenCalledWith("/demo/threads/index.json");
});

test("static demo thread requests are shared per conversation", async () => {
  const fetchMock = rs.fn(async () => ({
    ok: true,
    json: async () => ({
      thread_id: "ignored",
      created_at: "2026-07-10T00:00:00Z",
      values: { messages: [], artifacts: [] },
    }),
  }));
  rs.stubGlobal("fetch", fetchMock);
  const { loadStaticDemoThread } = await import("@/core/threads/static-demo");

  const [metadata, history] = await Promise.all([
    loadStaticDemoThread("thread-a"),
    loadStaticDemoThread("thread-a"),
  ]);

  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(metadata).toBe(history);
  expect(metadata.thread_id).toBe("thread-a");
});

test("failed static demo requests can be retried", async () => {
  const fetchMock = rs
    .fn()
    .mockResolvedValueOnce({ ok: false })
    .mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        thread_id: "ignored",
        values: { messages: [], artifacts: [] },
      }),
    });
  rs.stubGlobal("fetch", fetchMock);
  const { loadStaticDemoThread } = await import("@/core/threads/static-demo");

  await expect(loadStaticDemoThread("thread-a")).rejects.toThrow(
    "Failed to load demo thread thread-a",
  );
  await expect(loadStaticDemoThread("thread-a")).resolves.toMatchObject({
    thread_id: "thread-a",
  });
  expect(fetchMock).toHaveBeenCalledTimes(2);
});
