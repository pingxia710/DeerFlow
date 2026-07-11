import type { Run } from "@langchain/langgraph-sdk";
import { describe, expect, rs, test } from "@rstest/core";
import { QueryClient, type InfiniteData } from "@tanstack/react-query";

import {
  applyBackgroundRunProbeResult,
  applyStreamErrorRecovery,
  clearDeletedThreadClientState,
  filterInfiniteThreadsCache,
  getInfiniteThreadsNextPageParam,
  getManualThreadTitleLock,
  getStreamErrorMessage,
  getThreadActivitySnapshot,
  hasTerminalStreamErrorRecoveryRun,
  INFINITE_THREADS_PAGE_SIZE,
  INFINITE_THREADS_QUERY_KEY_PREFIX,
  invalidateTerminalRunQueries,
  mapInfiniteThreadsCache,
  markThreadBusyInCaches,
  markThreadFinished,
  clearThreadActivity,
  clearThreadFinishedActivity,
  isThreadRecoveringFromStreamError,
  reconcileTaskEventRunHistory,
  reconcileTerminalRunHistory,
  renameThreadRemote,
  setManualThreadTitleLock,
  shouldKeepStreamErrorRecoveryRun,
  shouldCommitStreamStartFromError,
  shouldReleaseQueuedThreadMessage,
  threadRunsQueryKey,
  threadRuntimeSnapshotQueryKey,
  stopBackgroundRunProbeRecovery,
  upsertThreadInInfiniteCache,
  upsertThreadInSearchCache,
  getBackgroundRunProbeDelay,
  shouldStopBackgroundRunProbe,
} from "@/core/threads/hooks";
import {
  threadContextUsageQueryKey,
  threadTokenUsageQueryKey,
} from "@/core/threads/token-usage";
import type { AgentThread } from "@/core/threads/types";

// Issue #3482: the sidebar and /workspace/chats list used to be capped at
// 50 threads because `useThreads()` exits as soon as `threads.length >=
// params.limit`.  These pure helpers back the `useInfiniteThreads()`
// pagination logic and the mirrored cache writes that keep rename / delete
// / stream-finish in sync with both the legacy array cache and the new
// infinite cache.

function makeThread(id: string, title = `Title ${id}`): AgentThread {
  return {
    thread_id: id,
    created_at: "2025-01-01T00:00:00Z",
    updated_at: "2025-01-01T00:00:00Z",
    metadata: {},
    status: "idle",
    values: { title },
  } as unknown as AgentThread;
}

function makePage(start: number, size: number): AgentThread[] {
  return Array.from({ length: size }, (_, i) => makeThread(`t-${start + i}`));
}

function makeInfiniteData(pages: AgentThread[][]): InfiniteData<AgentThread[]> {
  return {
    pages,
    pageParams: pages.map((_, i) => i * INFINITE_THREADS_PAGE_SIZE),
  };
}

describe("getInfiniteThreadsNextPageParam", () => {
  test("returns next offset when the last page is full", () => {
    const page1 = makePage(0, INFINITE_THREADS_PAGE_SIZE);
    expect(getInfiniteThreadsNextPageParam(page1, [page1])).toBe(
      INFINITE_THREADS_PAGE_SIZE,
    );
  });

  test("returns next offset across multiple full pages", () => {
    const page1 = makePage(0, INFINITE_THREADS_PAGE_SIZE);
    const page2 = makePage(
      INFINITE_THREADS_PAGE_SIZE,
      INFINITE_THREADS_PAGE_SIZE,
    );
    expect(getInfiniteThreadsNextPageParam(page2, [page1, page2])).toBe(
      INFINITE_THREADS_PAGE_SIZE * 2,
    );
  });

  test("returns undefined when the last page is short (end of list)", () => {
    const page1 = makePage(0, INFINITE_THREADS_PAGE_SIZE);
    const page2 = makePage(INFINITE_THREADS_PAGE_SIZE, 10);
    expect(
      getInfiniteThreadsNextPageParam(page2, [page1, page2]),
    ).toBeUndefined();
  });

  test("returns undefined when the last page is empty", () => {
    const page1 = makePage(0, INFINITE_THREADS_PAGE_SIZE);
    expect(getInfiniteThreadsNextPageParam([], [page1, []])).toBeUndefined();
  });

  test("respects a custom page size", () => {
    const page1 = makePage(0, 5);
    expect(getInfiniteThreadsNextPageParam(page1, [page1], 5)).toBe(5);
    expect(getInfiniteThreadsNextPageParam(page1, [page1], 10)).toBeUndefined();
  });
});

describe("mapInfiniteThreadsCache", () => {
  test("returns undefined when oldData is undefined", () => {
    expect(mapInfiniteThreadsCache(undefined, (t) => t)).toBeUndefined();
  });

  test("updates the matching thread across multiple pages", () => {
    const page1 = [makeThread("a"), makeThread("b")];
    const page2 = [makeThread("c"), makeThread("d")];
    const data = makeInfiniteData([page1, page2]);

    const updated = mapInfiniteThreadsCache(data, (t) =>
      t.thread_id === "c"
        ? { ...t, values: { ...t.values, title: "renamed" } }
        : t,
    );

    expect(updated?.pages[0]?.[0]?.values?.title).toBe("Title a");
    expect(updated?.pages[1]?.[0]?.thread_id).toBe("c");
    expect(updated?.pages[1]?.[0]?.values?.title).toBe("renamed");
    expect(updated?.pages[1]?.[1]?.values?.title).toBe("Title d");
  });

  test("preserves pageParams", () => {
    const data = makeInfiniteData([[makeThread("a")]]);
    const updated = mapInfiniteThreadsCache(data, (t) => t);
    expect(updated?.pageParams).toEqual(data.pageParams);
  });
});

describe("filterInfiniteThreadsCache", () => {
  test("returns undefined when oldData is undefined", () => {
    expect(filterInfiniteThreadsCache(undefined, () => true)).toBeUndefined();
  });

  test("removes matching threads across all pages", () => {
    const page1 = [makeThread("a"), makeThread("b")];
    const page2 = [makeThread("b"), makeThread("c")];
    const data = makeInfiniteData([page1, page2]);

    const filtered = filterInfiniteThreadsCache(
      data,
      (t) => t.thread_id !== "b",
    );

    expect(filtered?.pages[0]?.map((t) => t.thread_id)).toEqual(["a"]);
    expect(filtered?.pages[1]?.map((t) => t.thread_id)).toEqual(["c"]);
  });

  test("keeps an emptied page as an empty array (does not drop the page)", () => {
    const page1 = [makeThread("a")];
    const page2 = [makeThread("b")];
    const data = makeInfiniteData([page1, page2]);

    const filtered = filterInfiniteThreadsCache(
      data,
      (t) => t.thread_id !== "a",
    );

    expect(filtered?.pages).toHaveLength(2);
    expect(filtered?.pages[0]).toEqual([]);
    expect(filtered?.pages[1]?.[0]?.thread_id).toBe("b");
  });

  test("does not regress next offset when an earlier page has been shrunk by a delete", () => {
    // Simulate two full pages already loaded.
    const page1 = Array.from({ length: 50 }, (_, i) => ({
      thread_id: `a${i}`,
    }));
    const page2 = Array.from({ length: 50 }, (_, i) => ({
      thread_id: `b${i}`,
    }));

    // Offset right after fetching page 2 (this is the value TanStack Query
    // freezes into pageParams).
    const offsetAfterPage2 = getInfiniteThreadsNextPageParam(
      page2 as unknown as AgentThread[],
      [page1, page2] as unknown as AgentThread[][],
    );
    expect(offsetAfterPage2).toBe(100);

    // Now a delete mutation runs filterInfiniteThreadsCache and shrinks
    // page 1 from 50 to 49 entries. TanStack does NOT re-invoke
    // getNextPageParam on cache mutations; the previously-computed offset
    // (100) remains the param for the next fetchNextPage() call, so the
    // helper is consistent with how the library uses its return value.
    const shrunkPage1 = page1.slice(0, 49);
    const recomputed = getInfiniteThreadsNextPageParam(
      page2 as unknown as AgentThread[],
      [shrunkPage1, page2] as unknown as AgentThread[][],
    );
    // We document the recomputed value for completeness, but in practice
    // useDeleteThread invalidates the query in onSettled, so pages are
    // refetched from offset 0 rather than relying on this number.
    expect(recomputed).toBe(99);
  });
});

describe("markThreadBusyInCaches", () => {
  test("marks the matching thread busy in search and infinite caches", () => {
    const client = new QueryClient();
    client.setQueryData(
      ["threads", "search"],
      [makeThread("a"), makeThread("b")],
    );
    client.setQueryData(
      [...INFINITE_THREADS_QUERY_KEY_PREFIX, {}],
      makeInfiniteData([[makeThread("a")], [makeThread("b")]]),
    );

    markThreadBusyInCaches(client, "b");

    const search = client.getQueryData<AgentThread[]>(["threads", "search"]);
    const infinite = client.getQueryData<InfiniteData<AgentThread[]>>([
      ...INFINITE_THREADS_QUERY_KEY_PREFIX,
      {},
    ]);

    expect(search?.[0]?.status).toBe("idle");
    expect(search?.[1]?.status).toBe("busy");
    expect(infinite?.pages[0]?.[0]?.status).toBe("idle");
    expect(infinite?.pages[1]?.[0]?.status).toBe("busy");
  });

  test("tracks local running and finished activity", () => {
    markThreadBusyInCaches(new QueryClient(), "activity-thread");
    expect(getThreadActivitySnapshot().running.has("activity-thread")).toBe(
      true,
    );
    expect(getThreadActivitySnapshot().finished.has("activity-thread")).toBe(
      false,
    );

    markThreadFinished("activity-thread");
    expect(getThreadActivitySnapshot().running.has("activity-thread")).toBe(
      false,
    );
    expect(getThreadActivitySnapshot().finished.has("activity-thread")).toBe(
      true,
    );

    clearThreadFinishedActivity("activity-thread");
    expect(getThreadActivitySnapshot().finished.has("activity-thread")).toBe(
      false,
    );

    markThreadBusyInCaches(new QueryClient(), "activity-thread");
    markThreadFinished("activity-thread");
    clearThreadActivity("activity-thread");
    expect(getThreadActivitySnapshot().finished.has("activity-thread")).toBe(
      false,
    );
  });

  test("keeps newer same-thread run active when an older run finishes", () => {
    const client = new QueryClient();
    const threadId = "activity-owned-thread";
    client.setQueryData(["threads", "search"], [makeThread(threadId)]);

    markThreadBusyInCaches(client, threadId, { runId: "run-old" });
    markThreadBusyInCaches(client, threadId, { runId: "run-new" });
    markThreadFinished(threadId, { runId: "run-old" });

    expect(getThreadActivitySnapshot().running.has(threadId)).toBe(true);
    expect(getThreadActivitySnapshot().finished.has(threadId)).toBe(false);
    expect(
      client.getQueryData<AgentThread[]>(["threads", "search"])?.[0]?.status,
    ).toBe("busy");

    markThreadFinished(threadId, { runId: "run-new" });

    expect(getThreadActivitySnapshot().running.has(threadId)).toBe(false);
    expect(getThreadActivitySnapshot().finished.has(threadId)).toBe(true);
    clearThreadActivity(threadId);
  });

  test("settles runtime-owner pre-run activity without leaking legacy busy state", () => {
    const client = new QueryClient();
    const threadId = "activity-runtime-owner-thread";
    const runtimeOwnerId = "runtime-owner-a";
    client.setQueryData(["threads", "search"], [makeThread(threadId)]);

    markThreadBusyInCaches(client, threadId, { runtimeOwnerId });
    markThreadFinished(threadId, { runtimeOwnerId });

    expect(getThreadActivitySnapshot().running.has(threadId)).toBe(false);
    expect(getThreadActivitySnapshot().finished.has(threadId)).toBe(true);

    markThreadBusyInCaches(client, threadId, { runtimeOwnerId });
    markThreadBusyInCaches(client, threadId, {
      runId: "run-owned",
      runtimeOwnerId,
    });
    markThreadFinished(threadId, {
      runId: "run-owned",
      runtimeOwnerId,
    });

    expect(getThreadActivitySnapshot().running.has(threadId)).toBe(false);
    expect(getThreadActivitySnapshot().finished.has(threadId)).toBe(true);
    clearThreadActivity(threadId);
  });
});

describe("applyBackgroundRunProbeResult", () => {
  test("leaves active runs alone", () => {
    const client = new QueryClient();
    const threadId = "probe-active-thread";
    client.setQueryData(["threads", "search"], [makeThread(threadId)]);
    markThreadBusyInCaches(client, threadId, { runId: "run-active" });

    expect(
      applyBackgroundRunProbeResult(client, threadId, "run-active", "running"),
    ).toBe(false);

    const search = client.getQueryData<AgentThread[]>(["threads", "search"]);
    expect(search?.[0]?.status).toBe("busy");
    expect(getThreadActivitySnapshot().running.has(threadId)).toBe(true);

    clearThreadActivity(threadId);
  });

  test("marks successful background runs finished and restores idle cache status", () => {
    const client = new QueryClient();
    const threadId = "probe-success-thread";
    client.setQueryData(["threads", "search"], [makeThread(threadId)]);
    client.setQueryData(
      [...INFINITE_THREADS_QUERY_KEY_PREFIX, {}],
      makeInfiniteData([[makeThread(threadId)]]),
    );
    markThreadBusyInCaches(client, threadId, { runId: "run-success" });

    expect(
      applyBackgroundRunProbeResult(client, threadId, "run-success", "success"),
    ).toBe(true);

    const search = client.getQueryData<AgentThread[]>(["threads", "search"]);
    const infinite = client.getQueryData<InfiniteData<AgentThread[]>>([
      ...INFINITE_THREADS_QUERY_KEY_PREFIX,
      {},
    ]);
    expect(search?.[0]?.status).toBe("idle");
    expect(infinite?.pages[0]?.[0]?.status).toBe("idle");
    expect(getThreadActivitySnapshot().running.has(threadId)).toBe(false);
    expect(getThreadActivitySnapshot().finished.has(threadId)).toBe(true);

    clearThreadActivity(threadId);
  });

  test("does not clear newer same-thread activity when an old probe settles", () => {
    const client = new QueryClient();
    const threadId = "probe-owned-thread";
    const oldRunId = "probe-run-old";
    const newRunId = "probe-run-new";
    client.setQueryData(["threads", "search"], [makeThread(threadId)]);
    client.setQueryData(
      [...INFINITE_THREADS_QUERY_KEY_PREFIX, {}],
      makeInfiniteData([[makeThread(threadId)]]),
    );
    markThreadBusyInCaches(client, threadId, { runId: oldRunId });
    markThreadBusyInCaches(client, threadId, { runId: newRunId });

    expect(
      applyBackgroundRunProbeResult(client, threadId, oldRunId, "success"),
    ).toBe(true);

    const searchAfterOld = client.getQueryData<AgentThread[]>([
      "threads",
      "search",
    ]);
    const infiniteAfterOld = client.getQueryData<InfiniteData<AgentThread[]>>([
      ...INFINITE_THREADS_QUERY_KEY_PREFIX,
      {},
    ]);
    expect(getThreadActivitySnapshot().running.has(threadId)).toBe(true);
    expect(getThreadActivitySnapshot().finished.has(threadId)).toBe(false);
    expect(searchAfterOld?.[0]?.status).toBe("busy");
    expect(infiniteAfterOld?.pages[0]?.[0]?.status).toBe("busy");

    expect(
      applyBackgroundRunProbeResult(client, threadId, newRunId, "success"),
    ).toBe(true);

    const searchAfterNew = client.getQueryData<AgentThread[]>([
      "threads",
      "search",
    ]);
    const infiniteAfterNew = client.getQueryData<InfiniteData<AgentThread[]>>([
      ...INFINITE_THREADS_QUERY_KEY_PREFIX,
      {},
    ]);
    expect(getThreadActivitySnapshot().running.has(threadId)).toBe(false);
    expect(getThreadActivitySnapshot().finished.has(threadId)).toBe(true);
    expect(searchAfterNew?.[0]?.status).toBe("idle");
    expect(infiniteAfterNew?.pages[0]?.[0]?.status).toBe("idle");

    clearThreadActivity(threadId);
  });

  test("clears failed background runs and records terminal cache status", () => {
    const client = new QueryClient();
    const threadId = "probe-timeout-thread";
    const settled: unknown[] = [];
    client.setQueryData(["threads", "search"], [makeThread(threadId)]);
    markThreadBusyInCaches(client, threadId, { runId: "run-timeout" });

    expect(
      applyBackgroundRunProbeResult(
        client,
        threadId,
        "run-timeout",
        "timeout",
        {
          settleRunSubtasks: (terminal) => settled.push(terminal),
          terminalReason: "timeout",
        },
      ),
    ).toBe(true);

    const search = client.getQueryData<AgentThread[]>(["threads", "search"]);
    expect(search?.[0]?.status).toBe("timeout");
    expect(getThreadActivitySnapshot().running.has(threadId)).toBe(false);
    expect(getThreadActivitySnapshot().finished.has(threadId)).toBe(false);
    expect(settled).toEqual([
      {
        threadId,
        runId: "run-timeout",
        status: "timeout",
        terminalReason: "timeout",
      },
    ]);
  });

  test("clears lost background runs and records terminal cache status", () => {
    const client = new QueryClient();
    const threadId = "probe-worker-lost-thread";
    client.setQueryData(["threads", "search"], [makeThread(threadId)]);
    markThreadBusyInCaches(client, threadId, { runId: "run-worker-lost" });

    expect(
      applyBackgroundRunProbeResult(
        client,
        threadId,
        "run-worker-lost",
        "worker_lost",
      ),
    ).toBe(true);

    const search = client.getQueryData<AgentThread[]>(["threads", "search"]);
    expect(search?.[0]?.status).toBe("worker_lost");
    expect(getThreadActivitySnapshot().running.has(threadId)).toBe(false);
    expect(getThreadActivitySnapshot().finished.has(threadId)).toBe(false);
  });
});

describe("stream error recovery", () => {
  test("keeps known stream-error runs busy and invalidates run recovery sources", () => {
    const client = new QueryClient();
    const threadId = "stream-error-thread";
    const runId = "stream-error-run";
    client.setQueryData(["threads", "search"], [makeThread(threadId)]);
    client.setQueryData(threadRunsQueryKey(threadId), "cached");
    client.setQueryData(threadRuntimeSnapshotQueryKey(threadId), "cached");

    const recovery = applyStreamErrorRecovery({
      queryClient: client,
      threadId,
      runId,
      isMock: true,
    });

    expect(recovery).toEqual({ threadId, runId });
    expect(isThreadRecoveringFromStreamError(recovery, threadId)).toBe(true);
    expect(
      client.getQueryData<AgentThread[]>(["threads", "search"])?.[0]?.status,
    ).toBe("busy");
    expect(getThreadActivitySnapshot().running.has(threadId)).toBe(true);
    expect(
      client.getQueryState(threadRunsQueryKey(threadId))?.isInvalidated,
    ).toBe(true);
    expect(
      client.getQueryState(threadRuntimeSnapshotQueryKey(threadId))
        ?.isInvalidated,
    ).toBe(true);

    clearThreadActivity(threadId);
  });

  test("commits stream start from error only when run metadata is available", () => {
    expect(
      shouldCommitStreamStartFromError({
        started: false,
        threadId: "thread-from-error",
        runId: "run-from-error",
      }),
    ).toBe(true);
    expect(
      shouldCommitStreamStartFromError({
        started: true,
        threadId: "thread-from-error",
        runId: "run-from-error",
      }),
    ).toBe(false);
    expect(
      shouldCommitStreamStartFromError({
        started: false,
        threadId: "thread-from-error",
        runId: null,
      }),
    ).toBe(false);
  });

  test("hides Codex stream incomplete details in visible stream errors", () => {
    const message = getStreamErrorMessage(
      new Error(
        "LLM request failed: Codex API stream ended without response.completed event",
      ),
    );

    expect(message).toContain("temporarily unavailable");
    expect(message).not.toContain("response.completed");
  });

  test("stream recovery refreshes snapshot and lets queued follow-up conditions settle", () => {
    const client = new QueryClient();
    const threadId = "stream-error-settle-thread";
    const runId = "stream-error-settle-run";
    client.setQueryData(["threads", "search"], [makeThread(threadId)]);
    client.setQueryData(threadRunsQueryKey(threadId), "cached-runs");
    client.setQueryData(threadRuntimeSnapshotQueryKey(threadId), {
      runs: [{ run_id: runId, status: "running" }],
    });

    const recovery = applyStreamErrorRecovery({
      queryClient: client,
      threadId,
      runId,
      isMock: true,
    });

    expect(recovery).toEqual({ threadId, runId });
    expect(getThreadActivitySnapshot().running.has(threadId)).toBe(true);
    expect(
      client.getQueryState(threadRuntimeSnapshotQueryKey(threadId))
        ?.isInvalidated,
    ).toBe(true);

    expect(
      applyBackgroundRunProbeResult(client, threadId, runId, "worker_lost"),
    ).toBe(true);
    expect(getThreadActivitySnapshot().running.has(threadId)).toBe(false);
    expect(
      shouldKeepStreamErrorRecoveryRun(recovery, getThreadActivitySnapshot()),
    ).toBe(false);
    expect(
      shouldReleaseQueuedThreadMessage({
        streamFinished: true,
        sendInFlight: false,
        recovering: false,
        queuedThreadId: threadId,
        currentViewThreadId: threadId,
      }),
    ).toBe(true);
  });

  test("does not keep old same-thread recovery from newer run activity", () => {
    const client = new QueryClient();
    const threadId = "same-thread-recovery-thread";
    const oldRunId = "same-thread-recovery-old";
    const newRunId = "same-thread-recovery-new";
    const oldRecovery = { threadId, runId: oldRunId };

    markThreadBusyInCaches(client, threadId, { runId: newRunId });

    expect(getThreadActivitySnapshot().running.has(threadId)).toBe(true);
    expect(
      shouldKeepStreamErrorRecoveryRun(
        oldRecovery,
        getThreadActivitySnapshot(),
      ),
    ).toBe(false);
    expect(
      shouldKeepStreamErrorRecoveryRun(
        { threadId },
        getThreadActivitySnapshot(),
      ),
    ).toBe(true);

    clearThreadActivity(threadId);
  });

  test("old recovery settle does not clear newer same-thread recovery", () => {
    const client = new QueryClient();
    const threadId = "same-thread-recovery-settle-thread";
    const oldRunId = "same-thread-recovery-settle-old";
    const newRunId = "same-thread-recovery-settle-new";
    client.setQueryData(["threads", "search"], [makeThread(threadId)]);

    const oldRecovery = applyStreamErrorRecovery({
      queryClient: client,
      threadId,
      runId: oldRunId,
      isMock: true,
    });
    const newRecovery = applyStreamErrorRecovery({
      queryClient: client,
      threadId,
      runId: newRunId,
      isMock: true,
    });

    expect(oldRecovery).toEqual({ threadId, runId: oldRunId });
    expect(newRecovery).toEqual({ threadId, runId: newRunId });
    expect(
      applyBackgroundRunProbeResult(client, threadId, oldRunId, "success"),
    ).toBe(true);

    expect(getThreadActivitySnapshot().running.has(threadId)).toBe(true);
    expect(
      shouldKeepStreamErrorRecoveryRun(
        newRecovery,
        getThreadActivitySnapshot(),
      ),
    ).toBe(true);
    expect(
      client.getQueryData<AgentThread[]>(["threads", "search"])?.[0]?.status,
    ).toBe("busy");

    clearThreadActivity(threadId);
  });

  test("keeps runtime-owner recovery only while that owner is active", () => {
    const client = new QueryClient();
    const threadId = "runtime-owner-recovery-thread";
    const runId = "runtime-owner-recovery-run";
    const runtimeOwnerId = "runtime-owner-recovery-slot";

    const recovery = applyStreamErrorRecovery({
      queryClient: client,
      threadId,
      runId,
      runtimeOwnerId,
      isMock: true,
    });

    expect(recovery).toEqual({ threadId, runId, runtimeOwnerId });
    expect(
      shouldKeepStreamErrorRecoveryRun(recovery, getThreadActivitySnapshot()),
    ).toBe(true);

    markThreadFinished(threadId, { runId, runtimeOwnerId });

    expect(
      shouldKeepStreamErrorRecoveryRun(recovery, getThreadActivitySnapshot()),
    ).toBe(false);
    clearThreadActivity(threadId);
  });

  test("run-owned recovery ignores newer runtime-owner activity from the same slot", () => {
    const client = new QueryClient();
    const threadId = "same-slot-recovery-thread";
    const oldRunId = "same-slot-recovery-old";
    const runtimeOwnerId = "same-slot-runtime-owner";
    const recovery = { threadId, runId: oldRunId, runtimeOwnerId };

    markThreadBusyInCaches(client, threadId, {
      runId: oldRunId,
      runtimeOwnerId,
    });
    markThreadFinished(threadId, { runId: oldRunId, runtimeOwnerId });
    markThreadBusyInCaches(client, threadId, { runtimeOwnerId });

    expect(getThreadActivitySnapshot().running.has(threadId)).toBe(true);
    expect(
      shouldKeepStreamErrorRecoveryRun(recovery, getThreadActivitySnapshot()),
    ).toBe(false);

    clearThreadActivity(threadId);
  });

  test("clears local activity when a stream error has no known run id", () => {
    const client = new QueryClient();
    const threadId = "stream-error-missing-run-thread";
    markThreadBusyInCaches(client, threadId);

    expect(
      applyStreamErrorRecovery({
        queryClient: client,
        threadId,
        runId: null,
        isMock: true,
      }),
    ).toBeNull();
    expect(getThreadActivitySnapshot().running.has(threadId)).toBe(false);
  });

  test("detects when a recovering stream-error run reaches terminal history", () => {
    const recovery = { threadId: "thread-1", runId: "run-1" };

    expect(
      hasTerminalStreamErrorRecoveryRun(recovery, [
        { run_id: "run-1", status: "running" },
      ] as unknown as Run[]),
    ).toBe(false);
    expect(
      hasTerminalStreamErrorRecoveryRun(recovery, [
        { run_id: "run-1", status: "success" },
      ] as unknown as Run[]),
    ).toBe(true);
  });

  test("releases legacy recovery ownership when local running activity is gone", () => {
    const recovery = { threadId: "thread-1", runId: "run-1" };

    expect(
      shouldKeepStreamErrorRecoveryRun(recovery, {
        running: new Set(["thread-1"]),
        finished: new Set(),
      }),
    ).toBe(false);
    expect(
      shouldKeepStreamErrorRecoveryRun(
        { threadId: "thread-1" },
        {
          running: new Set(["thread-1"]),
          finished: new Set(),
        },
      ),
    ).toBe(true);
    expect(
      shouldKeepStreamErrorRecoveryRun(recovery, {
        running: new Set(),
        finished: new Set(),
      }),
    ).toBe(false);
  });
});

describe("invalidateTerminalRunQueries", () => {
  test("invalidates run-list and usage queries for the terminal run thread", () => {
    const client = new QueryClient();
    const threadId = "terminal-thread";
    const keys = [
      ["threads", "search"],
      [...INFINITE_THREADS_QUERY_KEY_PREFIX, {}],
      threadRunsQueryKey(threadId),
      threadRuntimeSnapshotQueryKey(threadId),
      threadTokenUsageQueryKey(threadId),
      threadContextUsageQueryKey(threadId),
    ];
    for (const key of keys) {
      client.setQueryData(key, "cached");
    }

    invalidateTerminalRunQueries(client, threadId);

    expect(keys.map((key) => client.getQueryState(key)?.isInvalidated)).toEqual(
      [true, true, true, true, true, true],
    );
  });
});

describe("run history reconciliation", () => {
  test("task terminal events invalidate the run list and refresh that run", () => {
    const client = new QueryClient();
    const threadId = "task-terminal-thread";
    const runId = "task-terminal-run";
    const refreshed: Array<{
      threadId: string | null | undefined;
      runIds: string[];
    }> = [];
    client.setQueryData(threadRunsQueryKey(threadId), "cached");
    client.setQueryData(threadRuntimeSnapshotQueryKey(threadId), "snapshot");

    expect(
      reconcileTaskEventRunHistory(
        client,
        {
          type: "task_completed",
          task_id: "task-1",
          thread_id: threadId,
          run_id: runId,
        },
        (params) =>
          refreshed.push({
            threadId: params?.threadId,
            runIds: [...(params?.runIds ?? [])],
          }),
      ),
    ).toBe(true);

    expect(
      client.getQueryState(threadRunsQueryKey(threadId))?.isInvalidated,
    ).toBe(true);
    expect(
      client.getQueryState(threadRuntimeSnapshotQueryKey(threadId))
        ?.isInvalidated,
    ).toBe(true);
    expect(refreshed).toEqual([{ threadId, runIds: [runId] }]);
  });

  test("run terminal events clear local running state and refresh that run", () => {
    const client = new QueryClient();
    const threadId = "terminal-reconcile-thread";
    const runId = "terminal-reconcile-run";
    const refreshed: Array<{
      threadId: string | null | undefined;
      runIds: string[];
    }> = [];
    client.setQueryData(["threads", "search"], [makeThread(threadId)]);
    markThreadBusyInCaches(client, threadId, { runId });

    expect(
      reconcileTerminalRunHistory(
        client,
        {
          type: "run.terminal",
          event_type: "run.terminal",
          thread_id: threadId,
          run_id: runId,
          status: "success",
          terminal_reason: "success",
        },
        (params) =>
          refreshed.push({
            threadId: params?.threadId,
            runIds: [...(params?.runIds ?? [])],
          }),
      ),
    ).toBe(true);

    expect(getThreadActivitySnapshot().running.has(threadId)).toBe(false);
    expect(getThreadActivitySnapshot().finished.has(threadId)).toBe(true);
    expect(
      client.getQueryData<AgentThread[]>(["threads", "search"])?.[0]?.status,
    ).toBe("idle");
    expect(refreshed).toEqual([{ threadId, runIds: [runId] }]);

    clearThreadActivity(threadId);
  });

  test("run terminal events settle same-run active task cards before later error frames", () => {
    const client = new QueryClient();
    const threadId = "terminal-timeout-thread";
    const runId = "terminal-timeout-run";
    const refreshed: Array<{
      threadId: string | null | undefined;
      runIds: string[];
    }> = [];
    const settled: unknown[] = [];
    client.setQueryData(["threads", "search"], [makeThread(threadId)]);
    markThreadBusyInCaches(client, threadId, { runId });

    expect(
      reconcileTerminalRunHistory(
        client,
        {
          type: "run.terminal",
          event_type: "run.terminal",
          thread_id: threadId,
          run_id: runId,
          status: "timeout",
          terminal_reason: "timeout",
        },
        (params) =>
          refreshed.push({
            threadId: params?.threadId,
            runIds: [...(params?.runIds ?? [])],
          }),
        (terminal) => settled.push(terminal),
      ),
    ).toBe(true);

    expect(settled).toEqual([
      {
        threadId,
        runId,
        status: "timeout",
        terminalReason: "timeout",
      },
    ]);
    expect(getThreadActivitySnapshot().running.has(threadId)).toBe(false);
    expect(getThreadActivitySnapshot().finished.has(threadId)).toBe(false);
    expect(
      client.getQueryData<AgentThread[]>(["threads", "search"])?.[0]?.status,
    ).toBe("timeout");
    expect(refreshed).toEqual([{ threadId, runIds: [runId] }]);

    clearThreadActivity(threadId);
  });
});

describe("clearDeletedThreadClientState", () => {
  test("removes deleted thread activity and thread-scoped caches", () => {
    const client = new QueryClient();
    const threadId = "deleted-thread";
    const otherThreadId = "other-thread";
    markThreadBusyInCaches(client, threadId);
    client.setQueryData(["thread", threadId, "runs"], ["stale-run"]);
    client.setQueryData(["thread", otherThreadId, "runs"], ["other-run"]);
    client.setQueryData(["thread", "metadata", threadId, false], {
      thread_id: threadId,
    });
    client.setQueryData(["thread-token-usage", threadId], { total_tokens: 1 });
    client.setQueryData(["thread-context-usage", threadId], {
      latest: { estimated_tokens: 1 },
    });

    clearDeletedThreadClientState(client, threadId);

    expect(getThreadActivitySnapshot().running.has(threadId)).toBe(false);
    expect(client.getQueryData(["thread", threadId, "runs"])).toBeUndefined();
    expect(client.getQueryData(["thread", otherThreadId, "runs"])).toEqual([
      "other-run",
    ]);
    expect(
      client.getQueryData(["thread", "metadata", threadId, false]),
    ).toBeUndefined();
    expect(
      client.getQueryData(["thread-token-usage", threadId]),
    ).toBeUndefined();
    expect(
      client.getQueryData(["thread-context-usage", threadId]),
    ).toBeUndefined();
  });
});

describe("manual thread title lock", () => {
  test("stores the user's manual title for stream-title guards", () => {
    setManualThreadTitleLock("manual-title-thread", "Pinned Title");

    expect(getManualThreadTitleLock("manual-title-thread")).toBe(
      "Pinned Title",
    );
  });

  test("failed remote rename does not install a manual title lock", async () => {
    const updateState = rs.fn(async () => {
      throw new Error("rename failed");
    });

    await expect(
      renameThreadRemote({
        threadId: "failed-rename-thread",
        title: "Never committed",
        apiClient: { threads: { updateState } } as never,
      }),
    ).rejects.toThrow("rename failed");

    expect(getManualThreadTitleLock("failed-rename-thread")).toBeUndefined();
  });

  test("successful remote rename installs the lock after the write commits", async () => {
    const updateState = rs.fn(async () => {
      expect(
        getManualThreadTitleLock("committed-rename-thread"),
      ).toBeUndefined();
    });

    await renameThreadRemote({
      threadId: "committed-rename-thread",
      title: "Committed title",
      apiClient: { threads: { updateState } } as never,
    });

    expect(updateState).toHaveBeenCalledWith("committed-rename-thread", {
      values: { title: "Committed title" },
    });
    expect(getManualThreadTitleLock("committed-rename-thread")).toBe(
      "Committed title",
    );
  });
});

describe("upsertThreadInInfiniteCache", () => {
  function seedClient(initial?: InfiniteData<AgentThread[]>): QueryClient {
    const client = new QueryClient();
    if (initial) {
      client.setQueryData([...INFINITE_THREADS_QUERY_KEY_PREFIX, {}], initial);
    }
    return client;
  }

  function readCache(
    client: QueryClient,
  ): InfiniteData<AgentThread[]> | undefined {
    return client.getQueryData([...INFINITE_THREADS_QUERY_KEY_PREFIX, {}]);
  }

  test("no-op when the infinite cache has not been initialised yet", () => {
    const client = seedClient();
    upsertThreadInInfiniteCache(client, makeThread("new"));
    expect(readCache(client)).toBeUndefined();
  });

  test("prepends a brand-new thread to the first page", () => {
    const client = seedClient({
      pages: [[makeThread("a"), makeThread("b")]],
      pageParams: [0],
    });
    upsertThreadInInfiniteCache(client, makeThread("new"));
    const cache = readCache(client);
    expect(cache?.pages[0]?.map((t) => t.thread_id)).toEqual(["new", "a", "b"]);
  });

  test("merges into the existing entry instead of duplicating it", () => {
    const existing = makeThread("a", "Old title");
    const client = seedClient({
      pages: [[existing, makeThread("b")]],
      pageParams: [0],
    });
    // Simulate an onCreated upsert that races with a thread already in cache:
    // the upsert copy should win for fresh status/title/updated_at, but no
    // duplicate row should appear.
    upsertThreadInInfiniteCache(client, {
      ...makeThread("a", "New title"),
      updated_at: "2025-02-01T00:00:00Z",
      status: "busy",
    });
    const cache = readCache(client);
    const ids = cache?.pages[0]?.map((t) => t.thread_id);
    expect(ids).toEqual(["a", "b"]);
    expect(cache?.pages[0]?.[0]?.values.title).toBe("New title");
    expect(cache?.pages[0]?.[0]?.status).toBe("busy");
    expect(cache?.pages[0]?.[0]?.updated_at).toBe("2025-02-01T00:00:00Z");
  });
});

describe("upsertThreadInSearchCache", () => {
  test("fresh upsert fields win over stale array cache entries", () => {
    const client = new QueryClient();
    client.setQueryData(["threads", "search"], [makeThread("a", "Old title")]);

    upsertThreadInSearchCache(client, {
      ...makeThread("a", "New title"),
      updated_at: "2025-03-01T00:00:00Z",
      status: "busy",
    });

    const cache = client.getQueryData<AgentThread[]>(["threads", "search"]);
    expect(cache?.map((t) => t.thread_id)).toEqual(["a"]);
    expect(cache?.[0]?.values.title).toBe("New title");
    expect(cache?.[0]?.status).toBe("busy");
    expect(cache?.[0]?.updated_at).toBe("2025-03-01T00:00:00Z");
  });
});

describe("background run probe policy", () => {
  test("backs off with a capped delay", () => {
    expect(getBackgroundRunProbeDelay(1)).toBe(5000);
    expect(getBackgroundRunProbeDelay(2)).toBe(10000);
    expect(getBackgroundRunProbeDelay(10)).toBe(30000);
  });

  test("stops on max attempts and auth/not-found errors", () => {
    expect(shouldStopBackgroundRunProbe(12)).toBe(true);
    expect(shouldStopBackgroundRunProbe(1, { status: 401 })).toBe(true);
    expect(shouldStopBackgroundRunProbe(1, { statusCode: 403 })).toBe(true);
    expect(shouldStopBackgroundRunProbe(1, { status: 404 })).toBe(true);
    expect(shouldStopBackgroundRunProbe(1, { status: 500 })).toBe(false);
  });

  test("clears local recovery state and refreshes lists when probing gives up", () => {
    const client = new QueryClient();
    const threadId = "probe-exhausted-thread";
    client.setQueryData(["threads", "search"], [makeThread(threadId)]);
    client.setQueryData(threadRunsQueryKey(threadId), "cached");
    markThreadBusyInCaches(client, threadId);

    stopBackgroundRunProbeRecovery(client, threadId);

    expect(getThreadActivitySnapshot().running.has(threadId)).toBe(false);
    expect(
      client.getQueryState(threadRunsQueryKey(threadId))?.isInvalidated,
    ).toBe(true);
    expect(client.getQueryState(["threads", "search"])?.isInvalidated).toBe(
      true,
    );
  });
});
