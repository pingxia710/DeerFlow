import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import type { Message, Run } from "@langchain/langgraph-sdk";
import { afterEach, expect, test, rs } from "@rstest/core";
import { QueryClient } from "@tanstack/react-query";

import type { Subtask } from "@/core/tasks";
import type { SubtaskUpdate } from "@/core/tasks/context";
import type { RunMessage } from "@/core/threads/types";

interface TaskEventContractCase {
  event_type: string;
  status: string;
  action_result_status: string;
  terminal_reason: string | null;
}

interface TaskEventContract {
  schema_version: string;
  terminal_cases: TaskEventContractCase[];
}

const TASK_EVENT_FIXTURE_NAMES = [
  "started",
  "running_without_message",
  "completed",
  "failed",
  "cancelled",
  "timed_out",
] as const;

const TASK_EVENT_CONTRACT: TaskEventContract = JSON.parse(
  readFileSync(
    resolve(__dirname, "../../../../../contracts/task_event_contract.json"),
    "utf-8",
  ),
) as TaskEventContract;

function readTaskEventFixture(name: string): Record<string, unknown> {
  return JSON.parse(
    readFileSync(
      resolve(
        __dirname,
        `../../../../../contracts/fixtures/task_events/${name}.json`,
      ),
      "utf-8",
    ),
  ) as Record<string, unknown>;
}

function makeRunMessage(
  runId: string,
  seq: number,
  content: Message,
  overrides: Partial<Omit<RunMessage, "run_id" | "seq" | "content">> = {},
): RunMessage {
  return {
    run_id: runId,
    seq,
    created_at: `2024-01-01T00:00:${String(seq).padStart(2, "0")}.000Z`,
    metadata: { caller: "lead_agent" },
    display: { visible_in_chat: true, reason: "test" },
    content,
    ...overrides,
  };
}

function makeLocalStorage(): Storage {
  const values = new Map<string, string>();
  return {
    get length() {
      return values.size;
    },
    clear: rs.fn(() => values.clear()),
    getItem: rs.fn((key: string) => values.get(key) ?? null),
    key: rs.fn((index: number) => [...values.keys()][index] ?? null),
    removeItem: rs.fn((key: string) => {
      values.delete(key);
    }),
    setItem: rs.fn((key: string, value: string) => {
      values.set(key, value);
    }),
  } as Storage;
}

function stubBrowserWindow(storage = makeLocalStorage()) {
  rs.stubGlobal("localStorage", storage);
  rs.stubGlobal("window", {
    addEventListener: rs.fn(),
    clearTimeout: globalThis.clearTimeout,
    localStorage: storage,
    location: { origin: "http://localhost:2026" },
    removeEventListener: rs.fn(),
    setTimeout: globalThis.setTimeout,
  });
  return storage;
}

async function loadThreadHooksWithRunProbe(
  getRun: (threadId: string, runId: string) => Promise<unknown>,
) {
  rs.resetModules();
  rs.doMock("@/core/api", () => ({
    clearReconnectRun: rs.fn(),
    getAPIClient: () => ({
      runs: {
        get: getRun,
      },
    }),
  }));
  return import("@/core/threads/hooks");
}

afterEach(() => {
  rs.useRealTimers();
  rs.restoreAllMocks();
  rs.unstubAllGlobals();
  rs.doUnmock("@/core/api");
  rs.resetModules();
});

test("resolveAssistantId uses the custom agent name when present", async () => {
  const { resolveAssistantId } = await import("@/core/threads/hooks");

  expect(resolveAssistantId("command-room")).toBe("command-room");
});

test("resolveAssistantId falls back to the default lead agent", async () => {
  const { resolveAssistantId } = await import("@/core/threads/hooks");

  expect(resolveAssistantId(undefined)).toBe("lead_agent");
});

test("createOptimisticMessageId uses randomUUID when available", async () => {
  const randomUUID = rs.fn(() => "uuid-1");
  rs.stubGlobal("crypto", { randomUUID });
  const { createOptimisticMessageId } = await import("@/core/threads/hooks");

  expect(createOptimisticMessageId("opt-human")).toBe("opt-human-uuid-1");
  expect(randomUUID).toHaveBeenCalledTimes(1);
});

test("isSameSendRequest requires matching request and thread ownership", async () => {
  const { isSameSendRequest } = await import("@/core/threads/hooks");
  const request = { requestId: "send-1", threadId: "thread-a" };

  expect(isSameSendRequest(request, request)).toBe(true);
  expect(
    isSameSendRequest({ requestId: "send-2", threadId: "thread-a" }, request),
  ).toBe(false);
  expect(
    isSameSendRequest({ requestId: "send-1", threadId: "thread-b" }, request),
  ).toBe(false);
  expect(isSameSendRequest(null, request)).toBe(false);
});

test("async send ownership rejects stale upload and regenerate continuations after a thread switch", async () => {
  const { isSameSendRequest } = await import("@/core/threads/hooks");
  const uploadRequest = { requestId: "send-upload", threadId: "thread-a" };
  const regenerateRequest = { requestId: "regen-1", threadId: "thread-a" };

  expect(isSameSendRequest(null, uploadRequest)).toBe(false);
  expect(
    isSameSendRequest(
      { requestId: "send-upload", threadId: "thread-b" },
      uploadRequest,
    ),
  ).toBe(false);
  expect(
    isSameSendRequest(
      { requestId: "regen-1", threadId: "thread-b" },
      regenerateRequest,
    ),
  ).toBe(false);
});

test("getScopedToolEndEvent attaches stream thread and run ownership", async () => {
  const { getScopedToolEndEvent } = await import("@/core/threads/hooks");

  expect(
    getScopedToolEndEvent(
      { name: "setup_agent", data: { ok: true } },
      "thread-a",
      "run-a",
    ),
  ).toEqual({
    name: "setup_agent",
    data: { ok: true },
    threadId: "thread-a",
    runId: "run-a",
  });
  expect(
    getScopedToolEndEvent({ name: "setup_agent" }, "thread-a", null),
  ).toBeNull();
});

test("isSameStreamErrorRecoveryRun requires matching thread and run ownership", async () => {
  const { isSameStreamErrorRecoveryRun } = await import("@/core/threads/hooks");
  const recovery = { threadId: "thread-a", runId: "run-a" };

  expect(isSameStreamErrorRecoveryRun(recovery, "thread-a", "run-a")).toBe(
    true,
  );
  expect(isSameStreamErrorRecoveryRun(recovery, "thread-b", "run-a")).toBe(
    false,
  );
  expect(isSameStreamErrorRecoveryRun(recovery, "thread-a", "run-b")).toBe(
    false,
  );
});

test("buildThreadRunContext forces command-room into ultra subagent mode", async () => {
  const { buildThreadRunContext } = await import("@/core/threads/hooks");

  expect(
    buildThreadRunContext(
      {
        agent_name: "command-room",
        model_name: "safe-model",
        mode: "flash",
        reasoning_effort: "xhigh",
        reasoning_summary: "concise",
        text_verbosity: "high",
      },
      "thread-123",
    ),
  ).toMatchObject({
    agent_name: "command-room",
    model_name: "safe-model",
    mode: "ultra",
    thinking_enabled: true,
    is_plan_mode: true,
    subagent_enabled: true,
    reasoning_effort: "xhigh",
    reasoning_summary: "concise",
    text_verbosity: "high",
    thread_id: "thread-123",
  });
});

test("buildThreadRunContext defaults command-room reasoning to high", async () => {
  const { buildThreadRunContext } = await import("@/core/threads/hooks");

  expect(
    buildThreadRunContext(
      {
        agent_name: "command-room",
        model_name: "safe-model",
        mode: "flash",
      },
      "thread-123",
    ),
  ).toMatchObject({
    mode: "ultra",
    reasoning_effort: "high",
    subagent_enabled: true,
  });
});

test("buildThreadRunContext defaults ordinary thinking chat to xhigh", async () => {
  const { buildThreadRunContext } = await import("@/core/threads/hooks");

  expect(
    buildThreadRunContext(
      {
        model_name: "safe-model",
        mode: "pro",
      },
      "thread-456",
    ),
  ).toMatchObject({
    model_name: "safe-model",
    mode: "pro",
    thinking_enabled: true,
    is_plan_mode: true,
    subagent_enabled: false,
    reasoning_effort: "xhigh",
    thread_id: "thread-456",
  });
});

test("buildThreadRunContext normalizes retired low reasoning effort to xhigh", async () => {
  const { buildThreadRunContext } = await import("@/core/threads/hooks");

  expect(
    buildThreadRunContext(
      {
        model_name: "safe-model",
        mode: "thinking",
        reasoning_effort: "low",
      },
      "thread-789",
    ),
  ).toMatchObject({
    reasoning_effort: "xhigh",
  });
});

test("shouldShowLiveThreadState hides a stream from another visible thread", async () => {
  const { shouldShowLiveThreadState } = await import("@/core/threads/hooks");

  expect(shouldShowLiveThreadState("thread-b", "thread-a", null)).toBe(false);
  expect(shouldShowLiveThreadState("thread-b", "thread-b", null)).toBe(true);
  expect(shouldShowLiveThreadState("thread-b", null, "thread-b")).toBe(true);
});

test("shouldShowThreadHistory hides history from another visible thread", async () => {
  const { shouldShowThreadHistory } = await import("@/core/threads/hooks");

  expect(shouldShowThreadHistory("thread-b", "thread-a")).toBe(false);
  expect(shouldShowThreadHistory("thread-b", "thread-b")).toBe(true);
});

test("getThreadMessagesWithLiveSnapshot keeps completed live messages over stale history", async () => {
  const { getThreadMessagesWithLiveSnapshot } =
    await import("@/core/threads/hooks");
  const staleHistory = [
    {
      id: "old-human",
      type: "human",
      content: [{ type: "text", text: "previous prompt" }],
    },
    {
      id: "old-ai",
      type: "ai",
      content: "previous answer",
    },
  ] as Message[];
  const liveTurn = [
    {
      id: "new-human",
      type: "human",
      content: [{ type: "text", text: "fresh prompt" }],
    },
    {
      id: "new-ai",
      type: "ai",
      content: "fresh answer",
    },
  ] as Message[];

  expect(
    getThreadMessagesWithLiveSnapshot({
      viewThreadId: "thread-a",
      threadMessages: staleHistory,
      liveSnapshot: {
        threadId: "thread-a",
        runId: "run-a",
        messages: liveTurn,
      },
      pendingSupersededMessageIds: new Set(),
    }).map((message) => message.content),
  ).toEqual([
    [{ type: "text", text: "previous prompt" }],
    "previous answer",
    [{ type: "text", text: "fresh prompt" }],
    "fresh answer",
  ]);
});

test("getThreadMessagesWithLiveSnapshot ignores snapshots from another thread", async () => {
  const { getThreadMessagesWithLiveSnapshot } =
    await import("@/core/threads/hooks");
  const currentMessages = [
    {
      id: "current-ai",
      type: "ai",
      content: "current thread answer",
    },
  ] as Message[];

  expect(
    getThreadMessagesWithLiveSnapshot({
      viewThreadId: "thread-b",
      threadMessages: currentMessages,
      liveSnapshot: {
        threadId: "thread-a",
        runId: "run-a",
        messages: [
          {
            id: "other-ai",
            type: "ai",
            content: "other thread answer",
          },
        ] as Message[],
      },
      pendingSupersededMessageIds: new Set(),
    }),
  ).toEqual(currentMessages);
});

test("run status helpers classify terminal and inflight statuses", async () => {
  const { isActiveRunStatus, isTerminalRunStatus } =
    await import("@/core/threads/hooks");
  const terminalStatuses = [
    "success",
    "error",
    "timeout",
    "interrupted",
    "cancelled",
    "timed_out",
    "boundary_stopped",
    "worker_lost",
    "rolled_back",
    "rollback_failed",
  ];
  const activeStatuses = ["pending", "running", "cancelling", "rolling_back"];

  for (const status of terminalStatuses) {
    expect(isTerminalRunStatus(status)).toBe(true);
    expect(isActiveRunStatus(status)).toBe(false);
  }
  for (const status of activeStatuses) {
    expect(isActiveRunStatus(status)).toBe(true);
    expect(isTerminalRunStatus(status)).toBe(false);
  }
});

test("mergeRunsWithTerminalPrecedence keeps snapshot terminal evidence over stale queried active runs", async () => {
  const { isActiveRunStatus, mergeRunsWithTerminalPrecedence } =
    await import("@/core/threads/hooks");
  const terminalStatuses = [
    "success",
    "error",
    "timeout",
    "interrupted",
    "cancelled",
    "timed_out",
    "boundary_stopped",
    "worker_lost",
    "rolled_back",
    "rollback_failed",
  ];

  for (const status of terminalStatuses) {
    const result = mergeRunsWithTerminalPrecedence({
      snapshotRuns: [
        {
          run_id: `run-${status}`,
          status,
          terminal_reason: `reason-${status}`,
        } as unknown as Run,
      ],
      queriedRuns: [
        { run_id: `run-${status}`, status: "running" } as unknown as Run,
      ],
    });

    expect(result?.[0]).toMatchObject({
      run_id: `run-${status}`,
      status,
      terminal_reason: `reason-${status}`,
    });
    expect(isActiveRunStatus(result?.[0]?.status)).toBe(false);
  }
});

test("mergeRunsWithTerminalPrecedence keeps queried terminal evidence over snapshot active runs", async () => {
  const { mergeRunsWithTerminalPrecedence } =
    await import("@/core/threads/hooks");

  const result = mergeRunsWithTerminalPrecedence({
    snapshotRuns: [
      { run_id: "run-success", status: "running" } as unknown as Run,
      { run_id: "run-error", status: "rolling_back" } as unknown as Run,
    ],
    queriedRuns: [
      {
        run_id: "run-success",
        status: "success",
        terminal_reason: "success",
      } as unknown as Run,
      {
        run_id: "run-error",
        status: "error",
        terminal_reason: "failed",
      } as unknown as Run,
    ],
  });

  expect(result).toEqual([
    expect.objectContaining({
      run_id: "run-success",
      status: "success",
      terminal_reason: "success",
    }),
    expect.objectContaining({
      run_id: "run-error",
      status: "error",
      terminal_reason: "failed",
    }),
  ]);
});

test("mergeRunsWithTerminalPrecedence preserves queried new runs and snapshot-only runs", async () => {
  const { mergeRunsWithTerminalPrecedence } =
    await import("@/core/threads/hooks");

  const result = mergeRunsWithTerminalPrecedence({
    snapshotRuns: [
      {
        run_id: "snapshot-only",
        status: "success",
        terminal_reason: "success",
      } as unknown as Run,
    ],
    queriedRuns: [
      { run_id: "queried-new", status: "running" } as unknown as Run,
    ],
  });

  expect(result).toEqual([
    expect.objectContaining({ run_id: "queried-new", status: "running" }),
    expect.objectContaining({
      run_id: "snapshot-only",
      status: "success",
      terminal_reason: "success",
    }),
  ]);
});

test("shouldRefreshRunHistoryForThread rejects explicit refreshes for another thread", async () => {
  const { shouldRefreshRunHistoryForThread } =
    await import("@/core/threads/hooks");

  expect(shouldRefreshRunHistoryForThread("thread-a", "thread-b")).toBe(false);
  expect(shouldRefreshRunHistoryForThread("thread-b", "thread-b")).toBe(true);
  expect(shouldRefreshRunHistoryForThread(null, "thread-b")).toBe(true);
  expect(shouldRefreshRunHistoryForThread(undefined, "thread-b")).toBe(true);
});

test("reconcileTaskEventRunHistory passes event thread id to refreshRuns", async () => {
  const {
    reconcileTaskEventRunHistory,
    threadRunsQueryKey,
    threadRuntimeSnapshotQueryKey,
  } = await import("@/core/threads/hooks");
  const client = new QueryClient();
  const threadId = "task-event-thread";
  const runId = "task-event-run";
  const refreshed: Array<{
    threadId: string | null | undefined;
    runIds: string[];
  }> = [];
  client.setQueryData(threadRunsQueryKey(threadId), "cached-runs");
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

  expect(refreshed).toEqual([{ threadId, runIds: [runId] }]);
  expect(
    client.getQueryState(threadRunsQueryKey(threadId))?.isInvalidated,
  ).toBe(true);
  expect(
    client.getQueryState(threadRuntimeSnapshotQueryKey(threadId))
      ?.isInvalidated,
  ).toBe(true);
});

test("reconcileTerminalRunHistory passes event thread id to refreshRuns", async () => {
  const { reconcileTerminalRunHistory } = await import("@/core/threads/hooks");
  const client = new QueryClient();
  const threadId = "terminal-event-thread";
  const runId = "terminal-event-run";
  const refreshed: Array<{
    threadId: string | null | undefined;
    runIds: string[];
  }> = [];

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

  expect(refreshed).toEqual([{ threadId, runIds: [runId] }]);
});

test("resolveThreadStreamFinishMeta returns onFinish thread and run metadata", async () => {
  const { resolveThreadStreamFinishMeta } =
    await import("@/core/threads/hooks");

  expect(
    resolveThreadStreamFinishMeta({
      run: { thread_id: "thread-from-run", run_id: "run-from-run" },
      streamThreadId: "thread-from-ref",
      streamRunId: "run-from-ref",
    }),
  ).toEqual({ threadId: "thread-from-run", runId: "run-from-run" });
  expect(
    resolveThreadStreamFinishMeta({
      run: null,
      streamThreadId: "thread-from-ref",
      streamRunId: "run-from-ref",
    }),
  ).toEqual({ threadId: "thread-from-ref", runId: "run-from-ref" });
});

test("resolveVisibleTaskRunningThreadId only accepts the current live thread", async () => {
  const { resolveVisibleTaskRunningThreadId } =
    await import("@/core/threads/hooks");

  expect(
    resolveVisibleTaskRunningThreadId({
      viewThreadId: "thread-b",
      liveMessagesThreadId: "thread-a",
    }),
  ).toBeNull();
  expect(
    resolveVisibleTaskRunningThreadId({
      viewThreadId: "thread-b",
      liveMessagesThreadId: "thread-b",
    }),
  ).toBe("thread-b");
});

test("shouldReleaseQueuedThreadMessage releases visible queued thread after terminal/error/end", async () => {
  const { shouldReleaseQueuedThreadMessage } =
    await import("@/core/threads/hooks");

  const base = {
    sendInFlight: false,
    recovering: false,
    queuedThreadId: "thread-a",
    currentViewThreadId: "thread-a",
  };

  expect(
    shouldReleaseQueuedThreadMessage({ ...base, streamFinished: false }),
  ).toBe(false);
  // Timeout streams can settle through run.terminal/onFinish before the SDK's
  // loading snapshot catches up. Owned terminal settlement is enough to release
  // the queued follow-up for the same thread.
  expect(
    shouldReleaseQueuedThreadMessage({
      ...base,
      streamFinished: true,
    }),
  ).toBe(true);
  expect(
    shouldReleaseQueuedThreadMessage({
      ...base,
      recovering: true,
      streamFinished: true,
    }),
  ).toBe(false);
  expect(
    shouldReleaseQueuedThreadMessage({
      ...base,
      streamFinished: true,
      currentViewThreadId: "thread-b",
    }),
  ).toBe(false);
});

test("shouldQueueThreadMessage queues during the pre-loading send window", async () => {
  const { shouldQueueThreadMessage } = await import("@/core/threads/hooks");

  expect(
    shouldQueueThreadMessage({
      isLoading: false,
      streamFinished: false,
      recovering: false,
      sendInFlight: true,
    }),
  ).toBe(true);
  expect(
    shouldQueueThreadMessage({
      isLoading: true,
      streamFinished: false,
      recovering: false,
      sendInFlight: false,
    }),
  ).toBe(true);
  expect(
    shouldQueueThreadMessage({
      isLoading: false,
      streamFinished: false,
      recovering: true,
      sendInFlight: false,
    }),
  ).toBe(true);
  expect(
    shouldQueueThreadMessage({
      isLoading: false,
      streamFinished: false,
      recovering: false,
      sendInFlight: false,
    }),
  ).toBe(false);
  expect(
    shouldQueueThreadMessage({
      isLoading: true,
      streamFinished: true,
      recovering: false,
      sendInFlight: false,
    }),
  ).toBe(false);
});

test("queued follow-up release ignores stream recovery owned by another thread", async () => {
  const {
    isThreadRecoveringFromStreamError,
    shouldReleaseQueuedThreadMessage,
  } = await import("@/core/threads/hooks");
  const recovery = { threadId: "thread-a", runId: "run-a" };

  expect(isThreadRecoveringFromStreamError(recovery, "thread-b")).toBe(false);
  expect(
    shouldReleaseQueuedThreadMessage({
      streamFinished: true,
      sendInFlight: false,
      recovering: isThreadRecoveringFromStreamError(recovery, "thread-b"),
      queuedThreadId: "thread-b",
      currentViewThreadId: "thread-b",
    }),
  ).toBe(true);
});

test("queued follow-up release requires the owning runtime when owner ids are present", async () => {
  const { shouldReleaseQueuedThreadMessage } =
    await import("@/core/threads/hooks");

  expect(
    shouldReleaseQueuedThreadMessage({
      streamFinished: true,
      sendInFlight: false,
      recovering: false,
      queuedOwnerId: "slot-a",
      currentOwnerId: "slot-b",
      queuedThreadId: "thread-b",
      currentViewThreadId: "thread-b",
    }),
  ).toBe(false);
  expect(
    shouldReleaseQueuedThreadMessage({
      streamFinished: true,
      sendInFlight: false,
      recovering: false,
      queuedOwnerId: "slot-a",
      currentOwnerId: "slot-a",
      queuedThreadId: "old-display-id",
      currentViewThreadId: "created-thread-id",
    }),
  ).toBe(true);
});

test("shouldTreatTerminalEventAsCurrentStream only releases queue for the owned stream run", async () => {
  const {
    shouldReleaseQueuedThreadMessage,
    shouldTreatTerminalEventAsCurrentStream,
  } = await import("@/core/threads/hooks");
  const terminalEvent = { thread_id: "thread-a", run_id: "run-a" };

  expect(
    shouldTreatTerminalEventAsCurrentStream(
      terminalEvent.thread_id,
      terminalEvent.run_id,
      "thread-a",
      "run-a",
    ),
  ).toBe(true);
  expect(
    shouldTreatTerminalEventAsCurrentStream(
      terminalEvent.thread_id,
      terminalEvent.run_id,
      "thread-b",
      "run-a",
    ),
  ).toBe(false);
  expect(
    shouldTreatTerminalEventAsCurrentStream(
      terminalEvent.thread_id,
      terminalEvent.run_id,
      "thread-a",
      "run-b",
    ),
  ).toBe(false);

  expect(
    shouldReleaseQueuedThreadMessage({
      streamFinished: shouldTreatTerminalEventAsCurrentStream(
        terminalEvent.thread_id,
        terminalEvent.run_id,
        "thread-a",
        "run-a",
      ),
      sendInFlight: false,
      recovering: false,
      queuedThreadId: "thread-a",
      currentViewThreadId: "thread-a",
    }),
  ).toBe(true);
  expect(
    shouldReleaseQueuedThreadMessage({
      streamFinished: shouldTreatTerminalEventAsCurrentStream(
        terminalEvent.thread_id,
        terminalEvent.run_id,
        "thread-b",
        "run-a",
      ),
      sendInFlight: false,
      recovering: false,
      queuedThreadId: "thread-a",
      currentViewThreadId: "thread-a",
    }),
  ).toBe(false);
});

test("shouldTreatStreamFinishAsCurrentStream accepts same-thread finish without run metadata", async () => {
  const { shouldTreatStreamFinishAsCurrentStream } =
    await import("@/core/threads/hooks");

  expect(
    shouldTreatStreamFinishAsCurrentStream(
      "thread-a",
      null,
      "thread-a",
      "run-a",
    ),
  ).toBe(true);
  expect(
    shouldTreatStreamFinishAsCurrentStream(
      "thread-a",
      "run-a",
      "thread-a",
      "run-a",
    ),
  ).toBe(true);
  expect(
    shouldTreatStreamFinishAsCurrentStream(
      "thread-a",
      null,
      "thread-b",
      "run-a",
    ),
  ).toBe(false);
  expect(
    shouldTreatStreamFinishAsCurrentStream(
      "thread-a",
      "run-a",
      "thread-a",
      "run-b",
    ),
  ).toBe(false);
});

test("getVisibleThreadError hides transient stream errors while recovery owns the run", async () => {
  const { getVisibleThreadError } = await import("@/core/threads/hooks");
  const error = new Error("network stream dropped");

  expect(getVisibleThreadError(error, true)).toBeUndefined();
  expect(getVisibleThreadError(error, false)).toBe(error);
});

test("shouldShowStreamErrorToast suppresses ordinary stream error toast during recovery", async () => {
  const { shouldShowStreamErrorToast } = await import("@/core/threads/hooks");

  expect(
    shouldShowStreamErrorToast({ threadId: "thread-a", runId: "run-a" }),
  ).toBe(false);
});

test("shouldShowStreamErrorToast keeps ordinary stream error toast when recovery fails", async () => {
  const { shouldShowStreamErrorToast } = await import("@/core/threads/hooks");

  expect(shouldShowStreamErrorToast(null)).toBe(true);
});

test("readRunMessagesPageResponse preserves HTTP status on history load errors", async () => {
  const { getThreadHistoryLoadErrorKind, readRunMessagesPageResponse } =
    await import("@/core/threads/hooks");

  await expect(
    readRunMessagesPageResponse(
      new Response(JSON.stringify({ detail: "forbidden" }), {
        status: 403,
        statusText: "Forbidden",
      }),
    ),
  ).rejects.toMatchObject({ message: "forbidden", status: 403 });

  expect(getThreadHistoryLoadErrorKind({ status: 403 })).toBe("forbidden");
  expect(getThreadHistoryLoadErrorKind({ status: 404 })).toBe("not-found");
  expect(getThreadHistoryLoadErrorKind(new Error("network"))).toBe("failed");
});

test("clearDeletedThreadClientState removes deleted thread scoped caches and thread model", async () => {
  const storage = stubBrowserWindow();
  const client = new QueryClient();
  const threadId = "deleted-thread";
  const otherThreadId = "other-thread";
  const {
    clearDeletedThreadClientState,
    getManualThreadTitleLock,
    setManualThreadTitleLock,
    threadRunsQueryKey,
    threadRuntimeSnapshotQueryKey,
  } = await import("@/core/threads/hooks");
  const { threadContextUsageQueryKey, threadTokenUsageQueryKey } =
    await import("@/core/threads/token-usage");
  const { THREAD_MODEL_KEY_PREFIX } = await import("@/core/settings/local");
  const { getThreadModelSnapshot, updateThreadSettings } =
    await import("@/core/settings/store");

  updateThreadSettings(threadId, "context", { model_name: "model-a" });
  setManualThreadTitleLock(threadId, "Locked title");
  client.setQueryData(["threads", "search"], ["global-search"]);
  client.setQueryData(threadRunsQueryKey(threadId), ["stale-runs"]);
  client.setQueryData(threadRuntimeSnapshotQueryKey(threadId), {
    thread_id: threadId,
  });
  client.setQueryData(["thread", "metadata", threadId, false], {
    thread_id: threadId,
  });
  client.setQueryData(threadTokenUsageQueryKey(threadId), { total_tokens: 1 });
  client.setQueryData(threadContextUsageQueryKey(threadId), {
    latest: { estimated_tokens: 1 },
  });
  client.setQueryData(["uploads", "list", threadId], ["stale-upload"]);
  client.setQueryData(["thread", threadId, "run", "run-1"], {
    run_id: "run-1",
  });
  client.setQueryData(["artifact", "report.md", threadId, false], {
    content: "stale",
  });
  client.setQueryData(threadRunsQueryKey(otherThreadId), ["other-runs"]);
  client.setQueryData(["thread", otherThreadId, "run", "run-2"], {
    run_id: "run-2",
  });
  client.setQueryData(threadRuntimeSnapshotQueryKey(otherThreadId), {
    thread_id: otherThreadId,
  });
  client.setQueryData(["uploads", "list", otherThreadId], ["other-upload"]);
  client.setQueryData(["artifact", "report.md", otherThreadId, false], {
    content: "other",
  });
  const clearSubtasksForThread = rs.fn();

  clearDeletedThreadClientState(client, threadId, { clearSubtasksForThread });

  expect(client.getQueryData(["threads", "search"])).toEqual(["global-search"]);
  expect(client.getQueryData(threadRunsQueryKey(threadId))).toBeUndefined();
  expect(
    client.getQueryData(threadRuntimeSnapshotQueryKey(threadId)),
  ).toBeUndefined();
  expect(
    client.getQueryData(["thread", "metadata", threadId, false]),
  ).toBeUndefined();
  expect(
    client.getQueryData(threadTokenUsageQueryKey(threadId)),
  ).toBeUndefined();
  expect(
    client.getQueryData(threadContextUsageQueryKey(threadId)),
  ).toBeUndefined();
  expect(client.getQueryData(["uploads", "list", threadId])).toBeUndefined();
  expect(
    client.getQueryData(["thread", threadId, "run", "run-1"]),
  ).toBeUndefined();
  expect(
    client.getQueryData(["artifact", "report.md", threadId, false]),
  ).toBeUndefined();
  expect(client.getQueryData(threadRunsQueryKey(otherThreadId))).toEqual([
    "other-runs",
  ]);
  expect(
    client.getQueryData(["thread", otherThreadId, "run", "run-2"]),
  ).toEqual({
    run_id: "run-2",
  });
  expect(
    client.getQueryData(threadRuntimeSnapshotQueryKey(otherThreadId)),
  ).toEqual({ thread_id: otherThreadId });
  expect(client.getQueryData(["uploads", "list", otherThreadId])).toEqual([
    "other-upload",
  ]);
  expect(
    client.getQueryData(["artifact", "report.md", otherThreadId, false]),
  ).toEqual({ content: "other" });
  expect(getManualThreadTitleLock(threadId)).toBeUndefined();
  expect(storage.getItem(`${THREAD_MODEL_KEY_PREFIX}${threadId}`)).toBeNull();
  expect(getThreadModelSnapshot(threadId)).toBeUndefined();
  expect(clearSubtasksForThread).toHaveBeenCalledWith(threadId);
});

test("stopBackgroundRunProbesForThread clears same-thread probes only", async () => {
  rs.useFakeTimers();
  stubBrowserWindow();
  const getRun = rs.fn(async (_threadId: string, runId: string) => ({
    run_id: runId,
    status: "success",
  }));
  const { startBackgroundRunProbe, stopBackgroundRunProbesForThread } =
    await loadThreadHooksWithRunProbe(getRun);
  const client = new QueryClient();

  startBackgroundRunProbe({
    queryClient: client,
    threadId: "thread-a",
    runId: "run-1",
  });
  startBackgroundRunProbe({
    queryClient: client,
    threadId: "thread-a",
    runId: "run-2",
  });
  startBackgroundRunProbe({
    queryClient: client,
    threadId: "thread-b",
    runId: "run-1",
  });

  stopBackgroundRunProbesForThread("thread-a");
  await rs.advanceTimersByTimeAsync(5000);

  expect(getRun).toHaveBeenCalledTimes(1);
  expect(getRun).toHaveBeenCalledWith("thread-b", "run-1");
});

test("deleted thread background probe does not write cache after fetch returns", async () => {
  rs.useFakeTimers();
  stubBrowserWindow();
  const threadId = "late-deleted-thread";
  const runId = "late-run";
  let resolveRun!: (run: unknown) => void;
  const runPromise = new Promise<unknown>((resolve) => {
    resolveRun = resolve;
  });
  const getRun = rs.fn(() => runPromise);
  const {
    clearDeletedThreadClientState,
    getThreadActivitySnapshot,
    startBackgroundRunProbe,
  } = await loadThreadHooksWithRunProbe(getRun);
  const client = new QueryClient();
  client.setQueryData(
    ["threads", "search"],
    [{ thread_id: threadId, status: "busy", values: {}, metadata: {} }],
  );

  startBackgroundRunProbe({ queryClient: client, threadId, runId });
  await rs.advanceTimersByTimeAsync(5000);
  expect(getRun).toHaveBeenCalledTimes(1);

  clearDeletedThreadClientState(client, threadId);
  resolveRun({ run_id: runId, status: "success" });
  await Promise.resolve();
  await Promise.resolve();

  expect(
    client.getQueryData<Array<{ status: string }>>(["threads", "search"])?.[0]
      ?.status,
  ).toBe("busy");
  expect(getThreadActivitySnapshot().finished.has(threadId)).toBe(false);
});

test("shouldShowThreadRunningStatus trusts backend terminal status over stale local running state", async () => {
  const { shouldShowThreadRunningStatus } =
    await import("@/core/threads/hooks");

  expect(shouldShowThreadRunningStatus("running", false)).toBe(true);
  expect(shouldShowThreadRunningStatus("pending", false)).toBe(true);
  expect(shouldShowThreadRunningStatus("busy", false)).toBe(true);
  expect(shouldShowThreadRunningStatus("idle", true)).toBe(false);
  expect(shouldShowThreadRunningStatus("error", true)).toBe(false);
  expect(shouldShowThreadRunningStatus("timeout", true)).toBe(false);
  expect(shouldShowThreadRunningStatus("worker_lost", true)).toBe(false);
  expect(shouldShowThreadRunningStatus("boundary_stopped", true)).toBe(false);
  expect(shouldShowThreadRunningStatus("rolled_back", true)).toBe(false);
  expect(shouldShowThreadRunningStatus(undefined, true)).toBe(true);
});

test("resolveVisibleTaskRunningThreadId prefers task event identity across thread switches", async () => {
  const { resolveVisibleTaskRunningThreadId } =
    await import("@/core/threads/hooks");

  expect(
    resolveVisibleTaskRunningThreadId({
      eventThreadId: "thread-a",
      streamThreadId: "thread-b",
      viewThreadId: "thread-b",
      liveMessagesThreadId: "thread-b",
    }),
  ).toBe("thread-a");
});

test("applyTaskEventToSubtask accepts shared known task event fixtures", async () => {
  const { applyTaskEventToSubtask } = await import("@/core/threads/hooks");

  for (const name of TASK_EVENT_FIXTURE_NAMES) {
    const updates: unknown[] = [];
    const event = readTaskEventFixture(name);

    expect(applyTaskEventToSubtask(event, (task) => updates.push(task))).toBe(
      true,
    );
    expect(updates).toHaveLength(1);
  }
});

test("applyTaskEventToSubtask rejects shared unknown task event fixture", async () => {
  const { applyTaskEventToSubtask } = await import("@/core/threads/hooks");
  const updates: unknown[] = [];

  expect(
    applyTaskEventToSubtask(readTaskEventFixture("unknown"), (task) =>
      updates.push(task),
    ),
  ).toBe(false);
  expect(updates).toEqual([]);
});

test("applyTaskEventToSubtask preserves cancelled and timed_out fixture terminal metadata", async () => {
  const { applyTaskEventToSubtask } = await import("@/core/threads/hooks");

  for (const [name, actionResultStatus, terminalReason] of [
    ["cancelled", "cancelled", "user_cancelled"],
    ["timed_out", "timed_out", "timed_out"],
  ] as const) {
    const updates: unknown[] = [];

    expect(
      applyTaskEventToSubtask(readTaskEventFixture(name), (task) =>
        updates.push(task),
      ),
    ).toBe(true);
    expect(updates[0]).toMatchObject({
      status: "failed",
      actionResultStatus,
      terminalReason,
    });
  }
});

test("asTaskEvent accepts missing legacy schema but rejects unknown future schema", async () => {
  const { TASK_EVENT_SCHEMA_VERSION, asTaskEvent } =
    await import("@/core/threads/hooks");
  const base = {
    event_type: "task_completed",
    task_id: "task-1",
    thread_id: "thread-1",
    run_id: "run-1",
  };

  expect(TASK_EVENT_SCHEMA_VERSION).toBe(TASK_EVENT_CONTRACT.schema_version);
  expect(asTaskEvent(base)).toMatchObject(base);
  expect(
    asTaskEvent({ ...base, schema_version: "deerflow.task-event/vNext" }),
  ).toBeNull();
});

test("asRunTerminalEvent accepts terminal custom replay without treating it as a task event", async () => {
  const { asRunTerminalEvent, asTaskEvent } =
    await import("@/core/threads/hooks");
  const event = {
    type: "run.terminal",
    event_type: "run.terminal",
    thread_id: "thread-1",
    run_id: "run-1",
    status: "success",
    terminal_reason: "success",
  };

  expect(asTaskEvent(event)).toBeNull();
  expect(asRunTerminalEvent(event)).toEqual(event);
});

test("asRunTerminalEvent rejects incomplete terminal custom replay", async () => {
  const { asRunTerminalEvent } = await import("@/core/threads/hooks");

  expect(
    asRunTerminalEvent({
      type: "run.terminal",
      thread_id: "thread-1",
      status: "success",
      terminal_reason: "success",
    }),
  ).toBeNull();
});

test("applyTaskEventToSubtask accepts redacted task event fields", async () => {
  const { applyTaskEventToSubtask } = await import("@/core/threads/hooks");
  const updates: unknown[] = [];

  expect(
    applyTaskEventToSubtask(
      {
        type: "task_completed",
        task_id: "task-1",
        thread_id: "thread-1",
        run_id: "run-1",
        summary: "Task completed",
        result_preview: "safe preview",
        redacted: true,
      },
      (task) => updates.push(task),
    ),
  ).toBe(true);

  expect(updates).toEqual([
    {
      id: "task-1",
      threadId: "thread-1",
      runId: "run-1",
      notify: true,
      status: "completed",
      result: "safe preview",
    },
  ]);
});

test("applyTaskEventToSubtask does not expose raw redacted completion result", async () => {
  const { applyTaskEventToSubtask } = await import("@/core/threads/hooks");
  const updates: unknown[] = [];

  expect(
    applyTaskEventToSubtask(
      {
        event_type: "task_completed",
        schema_version: TASK_EVENT_CONTRACT.schema_version,
        task_id: "task-redacted-completed",
        thread_id: "thread-1",
        run_id: "run-1",
        status: "completed",
        redacted: true,
        result_preview: "safe preview",
        result: "raw secret result",
        action_result: {
          status: "completed",
          summary: "compact safe summary",
        },
      },
      (task) => updates.push(task),
    ),
  ).toBe(true);

  expect(updates).toEqual([
    {
      id: "task-redacted-completed",
      threadId: "thread-1",
      runId: "run-1",
      notify: true,
      status: "completed",
      result: "safe preview",
      actionResultStatus: "completed",
    },
  ]);
});

test("applyTaskEventToSubtask accepts canonical event_type and action_result summary", async () => {
  const { applyTaskEventToSubtask } = await import("@/core/threads/hooks");
  const updates: unknown[] = [];

  expect(
    applyTaskEventToSubtask(
      {
        event_type: "task_completed",
        schema_version: TASK_EVENT_CONTRACT.schema_version,
        task_id: "task-1",
        thread_id: "thread-1",
        run_id: "run-1",
        status: "completed",
        action_result: {
          status: "completed",
          summary: "from action_result",
        },
      },
      (task) => updates.push(task),
    ),
  ).toBe(true);

  expect(updates).toEqual([
    {
      id: "task-1",
      threadId: "thread-1",
      runId: "run-1",
      notify: true,
      status: "completed",
      result: "from action_result",
      actionResultStatus: "completed",
    },
  ]);
});

for (const c of TASK_EVENT_CONTRACT.terminal_cases) {
  test(`applyTaskEventToSubtask follows task event contract: ${c.event_type}`, async () => {
    const { applyTaskEventToSubtask } = await import("@/core/threads/hooks");
    const updates: unknown[] = [];
    const isCompleted = c.status === "completed";

    expect(
      applyTaskEventToSubtask(
        {
          type: c.event_type,
          event_type: c.event_type,
          schema_version: TASK_EVENT_CONTRACT.schema_version,
          task_id: `task-${c.status}`,
          thread_id: "thread-1",
          run_id: "run-1",
          status: c.status,
          action_result: {
            status: c.action_result_status,
            terminal_reason: c.terminal_reason,
            summary: "contract summary",
            error: isCompleted ? undefined : `${c.terminal_reason} detail`,
          },
        },
        (task) => updates.push(task),
      ),
    ).toBe(true);

    expect(updates[0]).toMatchObject({
      id: `task-${c.status}`,
      threadId: "thread-1",
      status: isCompleted ? "completed" : "failed",
      actionResultStatus: c.action_result_status,
    });
    if (c.terminal_reason) {
      expect(updates[0]).toMatchObject({ terminalReason: c.terminal_reason });
    }
    if (isCompleted) {
      expect(updates[0]).toMatchObject({ result: "contract summary" });
    } else {
      expect(updates[0]).toMatchObject({
        error: `${c.terminal_reason} detail`,
      });
    }
  });
}

test("applyTaskEventToSubtask rejects unknown terminal task event enums", async () => {
  const { applyTaskEventToSubtask } = await import("@/core/threads/hooks");
  const updates: unknown[] = [];

  expect(
    applyTaskEventToSubtask(
      {
        event_type: "task_terminal_vNext",
        schema_version: TASK_EVENT_CONTRACT.schema_version,
        task_id: "task-unknown",
        thread_id: "thread-1",
        run_id: "run-1",
        status: "renamed_terminal",
      },
      (task) => updates.push(task),
    ),
  ).toBe(false);

  expect(updates).toEqual([]);
});

test("applyTaskEventToSubtask rejects unknown content shapes", async () => {
  const { applyTaskEventToSubtask } = await import("@/core/threads/hooks");
  const updates: unknown[] = [];

  expect(
    applyTaskEventToSubtask(
      {
        event_type: "task_completed",
        schema_version: TASK_EVENT_CONTRACT.schema_version,
        status: "completed",
      },
      (task) => updates.push(task),
    ),
  ).toBe(false);

  expect(updates).toEqual([]);
});

test("applyTaskEventToSubtask rejects task events without full run identity", async () => {
  const { applyTaskEventToSubtask } = await import("@/core/threads/hooks");
  const updates: unknown[] = [];

  expect(
    applyTaskEventToSubtask(
      {
        type: "task_completed",
        task_id: "task-1",
        thread_id: "thread-1",
        result_preview: "safe preview",
      },
      (task) => updates.push(task),
    ),
  ).toBe(false);

  expect(updates).toEqual([]);
});

test("applyTaskEventToSubtask does not expose raw redacted fallbacks", async () => {
  const { applyTaskEventToSubtask } = await import("@/core/threads/hooks");
  const updates: unknown[] = [];

  expect(
    applyTaskEventToSubtask(
      {
        event_type: "task_failed",
        schema_version: TASK_EVENT_CONTRACT.schema_version,
        task_id: "task-redacted",
        thread_id: "thread-1",
        run_id: "run-1",
        status: "failed",
        redacted: true,
        error: "raw secret error",
        action_result: {
          status: "failed",
          terminal_reason: "failed",
          error: "raw action error",
        },
      },
      (task) => updates.push(task),
    ),
  ).toBe(true);

  expect(updates).toEqual([
    {
      id: "task-redacted",
      threadId: "thread-1",
      runId: "run-1",
      notify: true,
      status: "failed",
      actionResultStatus: "failed",
      terminalReason: "failed",
    },
  ]);
});

test("applyTaskEventToSubtask ignores task_running message payload", async () => {
  const { applyTaskEventToSubtask } = await import("@/core/threads/hooks");
  const updates: unknown[] = [];

  expect(
    applyTaskEventToSubtask(
      {
        event_type: "task_running",
        schema_version: TASK_EVENT_CONTRACT.schema_version,
        task_id: "task-running",
        thread_id: "thread-1",
        run_id: "run-1",
        status: "in_progress",
        message: { content: "reserved raw payload must not be exposed" },
      },
      (task) => updates.push(task),
    ),
  ).toBe(true);

  expect(updates).toEqual([
    {
      id: "task-running",
      threadId: "thread-1",
      runId: "run-1",
      notify: true,
      status: "in_progress",
    },
  ]);
});

test("run terminal settles task card after started and running events without task completion", async () => {
  const {
    getSubtaskStorageKey,
    mergeSubtaskUpdate,
    settleRunningSubtasksForRun,
  } = await import("@/core/tasks/context");
  const { applyTaskEventToSubtask } = await import("@/core/threads/hooks");
  let tasks: Record<string, Subtask> = {};
  const update = (task: SubtaskUpdate) => {
    const storageKey = getSubtaskStorageKey({
      id: task.id,
      threadId: task.threadId,
      runId: task.runId,
    });
    tasks = {
      ...tasks,
      [storageKey]: mergeSubtaskUpdate(tasks[storageKey], task),
    };
  };
  const baseEvent = {
    schema_version: TASK_EVENT_CONTRACT.schema_version,
    task_id: "task-terminal",
    thread_id: "thread-1",
    run_id: "run-1",
  };
  const storageKey = getSubtaskStorageKey({
    id: "task-terminal",
    threadId: "thread-1",
    runId: "run-1",
  });

  expect(
    applyTaskEventToSubtask(
      {
        ...baseEvent,
        event_type: "task_started",
        status: "in_progress",
        description: "subtask",
        subagent_type: "executor",
        prompt: "work",
      },
      update,
    ),
  ).toBe(true);
  expect(
    applyTaskEventToSubtask(
      {
        ...baseEvent,
        event_type: "task_running",
        status: "in_progress",
      },
      update,
    ),
  ).toBe(true);
  expect(tasks[storageKey]).toMatchObject({
    status: "in_progress",
    runId: "run-1",
  });

  tasks = settleRunningSubtasksForRun(tasks, {
    threadId: "thread-1",
    runId: "run-1",
    status: "timeout",
    terminalReason: "timeout",
  });

  expect(tasks[storageKey]).toMatchObject({
    status: "failed",
    actionResultStatus: "timeout",
    terminalReason: "timeout",
  });
});

test("late task events update only their own run-scoped subtask", async () => {
  const { getSubtaskStorageKey, mergeSubtaskUpdate } =
    await import("@/core/tasks/context");
  const { applyTaskEventToSubtask } = await import("@/core/threads/hooks");
  let tasks: Record<string, Subtask> = {};
  const update = (task: SubtaskUpdate) => {
    const storageKey = getSubtaskStorageKey({
      id: task.id,
      threadId: task.threadId,
      runId: task.runId,
    });
    tasks = {
      ...tasks,
      [storageKey]: mergeSubtaskUpdate(tasks[storageKey], task),
    };
  };
  const runAKey = getSubtaskStorageKey({
    id: "shared-task",
    threadId: "thread-1",
    runId: "run-a",
  });
  const runBKey = getSubtaskStorageKey({
    id: "shared-task",
    threadId: "thread-1",
    runId: "run-b",
  });

  expect(
    applyTaskEventToSubtask(
      {
        event_type: "task_started",
        schema_version: TASK_EVENT_CONTRACT.schema_version,
        task_id: "shared-task",
        thread_id: "thread-1",
        run_id: "run-b",
        status: "in_progress",
        description: "run B task",
        subagent_type: "executor",
        prompt: "work B",
      },
      update,
    ),
  ).toBe(true);
  expect(
    applyTaskEventToSubtask(
      {
        event_type: "task_completed",
        schema_version: TASK_EVENT_CONTRACT.schema_version,
        task_id: "shared-task",
        thread_id: "thread-1",
        run_id: "run-a",
        status: "completed",
        result_preview: "run A done",
      },
      update,
    ),
  ).toBe(true);

  expect(tasks[runAKey]).toMatchObject({
    runId: "run-a",
    status: "completed",
    result: "run A done",
  });
  expect(tasks[runBKey]).toMatchObject({
    runId: "run-b",
    status: "in_progress",
    description: "run B task",
  });
});

test("applyTaskEventRunMessages replays persisted task events with run seq dedupe", async () => {
  const { applyTaskEventRunMessages } = await import("@/core/threads/hooks");
  const updates: unknown[] = [];
  const applied = new Set<string>();
  const messages = [
    {
      run_id: "run-1",
      seq: 1,
      created_at: "2024-01-01T00:00:00.000Z",
      metadata: { caller: "task_event" },
      content: {
        event_type: "task_started",
        schema_version: TASK_EVENT_CONTRACT.schema_version,
        task_id: "task-1",
        thread_id: "thread-1",
        run_id: "run-1",
        description: "start",
      },
    },
    {
      run_id: "run-1",
      seq: 1,
      created_at: "2024-01-01T00:00:01.000Z",
      metadata: { caller: "task_event" },
      content: {
        event_type: "task_completed",
        schema_version: TASK_EVENT_CONTRACT.schema_version,
        task_id: "task-1",
        thread_id: "thread-1",
        run_id: "run-1",
        result_preview: "duplicate should not apply",
      },
    },
    {
      run_id: "run-1",
      seq: 2,
      created_at: "2024-01-01T00:00:02.000Z",
      metadata: { caller: "task_event" },
      content: {
        event_type: "task_completed",
        schema_version: TASK_EVENT_CONTRACT.schema_version,
        task_id: "task-1",
        thread_id: "thread-1",
        run_id: "run-1",
        result_preview: "safe done",
      },
    },
  ];

  applyTaskEventRunMessages(
    messages as never,
    (task) => updates.push(task),
    "thread-1",
    applied,
  );

  expect([...applied]).toEqual(["run-1:1", "run-1:2"]);
  expect(updates).toEqual([
    expect.objectContaining({
      id: "task-1",
      threadId: "thread-1",
      status: "in_progress",
      description: "start",
      startedAt: Date.parse("2024-01-01T00:00:00.000Z"),
    }),
    expect.objectContaining({
      id: "task-1",
      threadId: "thread-1",
      status: "completed",
      result: "safe done",
    }),
  ]);
});

test("applyTaskEventRunMessages dedupes legacy task events without seq", async () => {
  const { applyTaskEventRunMessages } = await import("@/core/threads/hooks");
  const updates: unknown[] = [];
  const applied = new Set<string>();
  const message = {
    run_id: "run-legacy",
    created_at: "2024-01-01T00:00:00.000Z",
    metadata: { caller: "task_event" },
    content: {
      event_type: "task_completed",
      schema_version: TASK_EVENT_CONTRACT.schema_version,
      task_id: "task-legacy",
      thread_id: "thread-1",
      run_id: "run-legacy",
      result_preview: "done",
    },
  };

  applyTaskEventRunMessages(
    [message, message] as never,
    (task) => updates.push(task),
    "thread-1",
    applied,
  );

  expect(updates).toEqual([
    expect.objectContaining({
      id: "task-legacy",
      threadId: "thread-1",
      status: "completed",
      result: "done",
    }),
  ]);
  expect([...applied]).toEqual([
    "run-legacy:thread-1:task-legacy:task_completed:2024-01-01T00:00:00.000Z",
  ]);
});

test("buildVisibleHistoryMessages excludes task_event run messages", async () => {
  const { buildVisibleHistoryMessages, isTaskEventRunMessage } =
    await import("@/core/threads/hooks");
  const rows = [
    {
      run_id: "run-1",
      seq: 1,
      created_at: "2024-01-01T00:00:00.000Z",
      metadata: { caller: "task_event" },
      content: {
        event_type: "task_completed",
        schema_version: TASK_EVENT_CONTRACT.schema_version,
        task_id: "task-1",
        thread_id: "thread-1",
        run_id: "run-1",
      },
    },
    {
      run_id: "run-1",
      seq: 2,
      created_at: "2024-01-01T00:00:01.000Z",
      metadata: { caller: "lead_agent" },
      content: { id: "msg-1", type: "ai", content: "visible" },
    },
  ];

  expect(isTaskEventRunMessage(rows[0] as never)).toBe(true);
  expect(buildVisibleHistoryMessages(rows as never, new Set(), [])).toEqual([
    expect.objectContaining({ content: "visible" }),
  ]);
});

test("buildVisibleHistoryMessages keeps same message id from different runs", async () => {
  const { buildVisibleHistoryMessages } = await import("@/core/threads/hooks");
  const rows = [
    makeRunMessage("run-a", 1, {
      id: "shared-message",
      type: "ai",
      content: "run A visible",
    } as Message),
    makeRunMessage("run-b", 1, {
      id: "shared-message",
      type: "ai",
      content: "run B visible",
    } as Message),
  ];

  expect(
    buildVisibleHistoryMessages(rows, new Set(), []).map(
      (message) => message.content,
    ),
  ).toEqual(["run A visible", "run B visible"]);
});

test("buildVisibleHistoryMessages keeps same tool call id from different runs", async () => {
  const { buildVisibleHistoryMessages } = await import("@/core/threads/hooks");
  const rows = [
    makeRunMessage("run-a", 1, {
      id: "tool-message-a",
      type: "ai",
      content: "run A tool result",
      tool_call_id: "shared-tool-call",
    } as Message),
    makeRunMessage("run-b", 1, {
      id: "tool-message-b",
      type: "ai",
      content: "run B tool result",
      tool_call_id: "shared-tool-call",
    } as Message),
  ];

  expect(
    buildVisibleHistoryMessages(rows, new Set(), []).map(
      (message) => message.content,
    ),
  ).toEqual(["run A tool result", "run B tool result"]);
});

test("mergeMessages keeps visible same-run message over hidden control message", async () => {
  const { mergeMessages } = await import("@/core/threads/hooks");

  expect(
    mergeMessages(
      [],
      [
        {
          id: "same-run-message",
          type: "ai",
          content: "hidden control",
          additional_kwargs: {
            deerflow_run_id: "run-a",
            hide_from_ui: true,
          },
        } as Message,
        {
          id: "same-run-message",
          type: "ai",
          content: "visible reply",
          additional_kwargs: { deerflow_run_id: "run-a" },
        } as Message,
      ],
      [],
    ).map((message) => message.content),
  ).toEqual(["visible reply"]);
});

test("mergeMessages does not let unscoped live messages remove run-scoped history", async () => {
  const { buildVisibleHistoryMessages, mergeMessages } =
    await import("@/core/threads/hooks");
  const history = buildVisibleHistoryMessages(
    [
      makeRunMessage("run-a", 1, {
        id: "shared-message",
        type: "ai",
        content: "history from run A",
      } as Message),
    ],
    new Set(),
    [],
  );

  expect(
    mergeMessages(
      history,
      [
        {
          id: "shared-message",
          type: "ai",
          content: "live without run id",
        } as Message,
      ],
      [],
    ).map((message) => message.content),
  ).toEqual(["history from run A", "live without run id"]);
});

test("buildVisibleHistoryMessages hides rows with visible_in_chat false", async () => {
  const { buildVisibleHistoryMessages } = await import("@/core/threads/hooks");
  const rows = [
    makeRunMessage(
      "run-a",
      1,
      {
        id: "hidden-message",
        type: "ai",
        content: "should stay hidden",
      } as Message,
      { display: { visible_in_chat: false, reason: "control" } },
    ),
  ];

  expect(buildVisibleHistoryMessages(rows, new Set(), [])).toEqual([]);
});

test("mergeSubtaskUpdate does not regress terminal task events back to running", async () => {
  const { mergeSubtaskUpdate } = await import("@/core/tasks/context");

  for (const previous of [
    {
      id: "task-failed",
      status: "failed" as const,
      subagent_type: "test",
      description: "test",
      prompt: "test",
      error: "failed",
      actionResultStatus: "failed",
      terminalReason: "failed",
    },
    {
      id: "task-cancelled",
      status: "failed" as const,
      subagent_type: "test",
      description: "test",
      prompt: "test",
      error: "cancelled",
      actionResultStatus: "cancelled",
      terminalReason: "user_cancelled",
    },
    {
      id: "task-timed-out",
      status: "failed" as const,
      subagent_type: "test",
      description: "test",
      prompt: "test",
      error: "timed out",
      actionResultStatus: "timed_out",
      terminalReason: "timed_out",
    },
  ]) {
    expect(
      mergeSubtaskUpdate(previous, {
        id: previous.id,
        status: "in_progress",
        notify: true,
      }),
    ).toMatchObject({ status: "failed" });
  }
});
