import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import type { Message, Run } from "@langchain/langgraph-sdk";
import { afterEach, expect, test, rs } from "@rstest/core";
import { QueryClient } from "@tanstack/react-query";

import type { Subtask } from "@/core/tasks";
import {
  applySubtaskUpdateInState,
  settleRunningSubtasksForRun,
  type SubtaskUpdate,
} from "@/core/tasks/context";
import { applyTaskEventToSubtask } from "@/core/threads/task-events";
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

test("runtime snapshot polling stays active for background tasks and their wakeup grace window", async () => {
  const {
    getThreadRuntimeSnapshotRefetchInterval,
    hasRuntimeSnapshotActivity,
    shouldPollThreadRuntimeSnapshot,
  } = await import("@/core/threads/hooks");
  const now = Date.parse("2026-07-15T10:00:10.000Z");

  expect(
    shouldPollThreadRuntimeSnapshot(
      {
        runs: [],
        task_lanes: [{ status: "in_progress" }],
      },
      now,
    ),
  ).toBe(true);
  expect(
    shouldPollThreadRuntimeSnapshot(
      {
        runs: [],
        task_lanes: [
          {
            status: "completed",
            completed_at: "2026-07-15T10:00:05.000Z",
          },
        ],
      },
      now,
    ),
  ).toBe(true);
  expect(
    shouldPollThreadRuntimeSnapshot(
      {
        runs: [],
        task_lanes: [
          {
            status: "completed",
            completed_at: "2026-07-15T09:59:00.000Z",
          },
        ],
      },
      now,
    ),
  ).toBe(false);
  expect(
    hasRuntimeSnapshotActivity({
      runs: [],
      task_lanes: [{ status: "in_progress" }],
    }),
  ).toBe(true);
  expect(
    hasRuntimeSnapshotActivity({
      runs: [{ status: "running" }],
      task_lanes: [{ status: "completed" }],
    }),
  ).toBe(true);
  expect(
    hasRuntimeSnapshotActivity({
      runs: [{ status: "success" }],
      task_lanes: [{ status: "completed" }],
    }),
  ).toBe(false);
  expect(
    getThreadRuntimeSnapshotRefetchInterval(
      { runs: [], task_lanes: [{ status: "in_progress" }] },
      false,
    ),
  ).toBe(1_500);
  expect(
    getThreadRuntimeSnapshotRefetchInterval(
      { runs: [], task_lanes: [{ status: "in_progress" }] },
      true,
    ),
  ).toBe(15_000);
});

test("wake-facts polling only follows the active scope, pauses hidden, and backs off", async () => {
  const { getThreadWakeFactsRefetchInterval, shouldPollThreadWakeFacts } =
    await import("@/core/threads/hooks");
  const snapshot = {
    runs: [
      { run_id: "run-active", status: "running" },
      { run_id: "run-terminal", status: "success" },
    ],
  };
  const activeScope = { runId: "run-active", roundId: "round-active" };

  expect(shouldPollThreadWakeFacts(snapshot, activeScope)).toBe(true);
  expect(
    shouldPollThreadWakeFacts(snapshot, {
      runId: "run-terminal",
      roundId: "round-terminal",
    }),
  ).toBe(false);
  expect(
    getThreadWakeFactsRefetchInterval(snapshot, activeScope, 0, true),
  ).toBe(1_500);
  expect(
    getThreadWakeFactsRefetchInterval(snapshot, activeScope, 1, true),
  ).toBe(3_000);
  expect(
    getThreadWakeFactsRefetchInterval(snapshot, activeScope, 9, true),
  ).toBe(12_000);
  expect(
    getThreadWakeFactsRefetchInterval(snapshot, activeScope, 0, false),
  ).toBe(false);
});

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

async function loadThreadHooksWithRunAndFetch(
  getRun: (threadId: string, runId: string) => Promise<unknown>,
  fetchImpl: typeof fetch,
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
  rs.doMock("@/core/api/fetcher", () => ({
    DEFAULT_NON_STREAMING_REQUEST_TIMEOUT_MS: 30_000,
    fetch: fetchImpl,
  }));
  return import("@/core/threads/hooks");
}

afterEach(() => {
  rs.useRealTimers();
  rs.restoreAllMocks();
  rs.unstubAllGlobals();
  rs.doUnmock("@/core/api");
  rs.doUnmock("@/core/api/fetcher");
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

test("workspace teardown clears process-local thread ownership state", async () => {
  const hooks = await import("@/core/threads/hooks");
  const clearAllThreadSingletonState = (hooks as Record<string, unknown>)
    .clearAllThreadSingletonState;

  hooks.setManualThreadTitleLock("locked-thread", "Private title");
  hooks.clearThreadSingletonState("deleted-thread");
  hooks.markThreadFinished("finished-thread");

  expect(typeof clearAllThreadSingletonState).toBe("function");
  if (typeof clearAllThreadSingletonState !== "function") return;

  clearAllThreadSingletonState();

  expect(hooks.getManualThreadTitleLock("locked-thread")).toBeUndefined();
  expect(hooks.isDeletedThreadTombstoned("deleted-thread")).toBe(false);
  expect(hooks.getThreadActivitySnapshot()).toEqual({
    running: new Set(),
    finished: new Set(),
  });
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

test("stop before run creation defers only for the owning send request", async () => {
  const { createThreadRuntimeOwnerSnapshot, getDeferredThreadStopRequest } =
    await import("@/core/threads/hooks");
  const request = {
    requestId: "send-1",
    threadId: "pending-thread",
    displayThreadId: "pending-thread",
    runtimeOwnerId: "runtime-a",
  };
  const owner = createThreadRuntimeOwnerSnapshot({
    threadId: null,
    runId: null,
    displayThreadId: "pending-thread",
    runtimeOwnerId: "runtime-a",
  });

  expect(
    getDeferredThreadStopRequest({
      runId: null,
      activeRequest: request,
      currentOwner: owner,
    }),
  ).toBe(request);
  expect(
    getDeferredThreadStopRequest({
      runId: "run-1",
      activeRequest: request,
      currentOwner: owner,
    }),
  ).toBeNull();
  expect(
    getDeferredThreadStopRequest({
      runId: null,
      activeRequest: request,
      currentOwner: createThreadRuntimeOwnerSnapshot({
        threadId: "thread-b",
        runId: null,
        displayThreadId: "thread-b",
        runtimeOwnerId: "runtime-b",
      }),
    }),
  ).toBeNull();
});

test("stream error run metadata binds only the owning deferred stop", async () => {
  const { createThreadRuntimeOwnerSnapshot, shouldBindDeferredThreadStopRun } =
    await import("@/core/threads/hooks");
  const pendingRequest = {
    requestId: "send-1",
    threadId: "pending-thread",
    displayThreadId: "pending-thread",
    runtimeOwnerId: "runtime-a",
  };
  const owner = createThreadRuntimeOwnerSnapshot({
    threadId: "pending-thread",
    runId: null,
    displayThreadId: "pending-thread",
    runtimeOwnerId: "runtime-a",
  });

  expect(
    shouldBindDeferredThreadStopRun({
      pendingRequest,
      deferredRequest: pendingRequest,
      threadId: "pending-thread",
      runId: "run-1",
      currentOwner: owner,
    }),
  ).toBe(true);
  expect(
    shouldBindDeferredThreadStopRun({
      pendingRequest,
      deferredRequest: pendingRequest,
      threadId: "pending-thread",
      runId: null,
      currentOwner: owner,
    }),
  ).toBe(false);
  expect(
    shouldBindDeferredThreadStopRun({
      pendingRequest,
      deferredRequest: { ...pendingRequest, requestId: "send-2" },
      threadId: "pending-thread",
      runId: "run-1",
      currentOwner: owner,
    }),
  ).toBe(false);
  expect(
    shouldBindDeferredThreadStopRun({
      pendingRequest,
      deferredRequest: pendingRequest,
      threadId: "pending-thread",
      runId: "stale-run",
      currentOwner: createThreadRuntimeOwnerSnapshot({
        threadId: "pending-thread",
        runId: "current-run",
        displayThreadId: "pending-thread",
        runtimeOwnerId: "runtime-a",
      }),
    }),
  ).toBe(false);
  expect(
    shouldBindDeferredThreadStopRun({
      pendingRequest,
      deferredRequest: pendingRequest,
      threadId: "pending-thread",
      runId: "run-1",
      currentOwner: createThreadRuntimeOwnerSnapshot({
        threadId: "pending-thread",
        runId: null,
        displayThreadId: "pending-thread",
        runtimeOwnerId: "runtime-b",
      }),
    }),
  ).toBe(false);
});

test("successful runtime snapshots remain visible when the full run list fails", async () => {
  const { resolveThreadHistoryError } = await import("@/core/threads/hooks");
  const runsError = new Error("later run page failed");
  const loadError = new Error("message page failed");

  expect(
    resolveThreadHistoryError({
      loadError: null,
      runsError,
      hasSnapshot: true,
    }),
  ).toBeNull();
  expect(
    resolveThreadHistoryError({
      loadError: null,
      runsError,
      hasSnapshot: false,
    }),
  ).toBe(runsError);
  expect(
    resolveThreadHistoryError({
      loadError,
      runsError,
      hasSnapshot: true,
    }),
  ).toBe(loadError);
});

test("async send ownership rejects stale upload and regenerate continuations after a thread switch", async () => {
  const { isSameSendRequest, shouldApplyUploadContinuation } =
    await import("@/core/threads/hooks");
  const uploadRequest = {
    requestId: "send-upload",
    threadId: "thread-a",
    displayThreadId: "thread-a",
    runtimeOwnerId: "runtime-a",
  };
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
  expect(
    shouldApplyUploadContinuation({
      activeRequest: uploadRequest,
      request: uploadRequest,
      currentViewThreadId: "thread-b",
      visibleOnly: true,
    }),
  ).toBe(false);
  expect(
    shouldApplyUploadContinuation({
      activeRequest: uploadRequest,
      request: uploadRequest,
      currentViewThreadId: "thread-a",
      visibleOnly: true,
    }),
  ).toBe(true);
  expect(
    shouldApplyUploadContinuation({
      activeRequest: uploadRequest,
      request: uploadRequest,
      isDeletedThread: true,
    }),
  ).toBe(false);
  expect(
    shouldApplyUploadContinuation({
      activeRequest: { ...uploadRequest, requestId: "next-send" },
      request: uploadRequest,
    }),
  ).toBe(false);
});

test("/new attachment upload resolves a backend thread before upload", async () => {
  const { uploadPromptFilesForThreadSend } =
    await import("@/core/threads/hooks");
  const createThread = rs.fn(async (requestedThreadId: string) => ({
    thread_id:
      requestedThreadId === "pending-display-thread"
        ? "backend-thread"
        : requestedThreadId,
  }));
  const upload = rs.fn(async (_uploadThreadId: string) => ({
    success: true,
    message: "ok",
    skipped_files: [],
    files: [
      {
        filename: "note.txt",
        size: 4,
        path: "/tmp/note.txt",
        virtual_path:
          "/Users/pingxia/projects/deer-flow/backend/.deer-flow/users/963870b2-72d1-4f61-b0bc-5a46617b16b7/threads/5a484122-3cb4-41ff-b9e5-1dff92a6af50/user-data/uploads/note.txt",
        artifact_url: "/api/artifacts/note.txt",
      },
    ],
  }));

  const result = await uploadPromptFilesForThreadSend({
    threadId: "pending-display-thread",
    backendThreadId: null,
    fileParts: [
      {
        type: "file",
        filename: "note.txt",
        mediaType: "text/plain",
        url: "",
        file: new File(["demo"], "note.txt", { type: "text/plain" }),
      },
    ],
    createThread,
    upload,
  });

  expect(createThread).toHaveBeenCalledWith("pending-display-thread");
  expect(upload).toHaveBeenCalledWith("backend-thread", [expect.any(File)]);
  expect(upload).not.toHaveBeenCalledWith("pending-display-thread", [
    expect.any(File),
  ]);
  expect(result?.threadId).toBe("backend-thread");
});

test("partial attachment upload failure aborts the send flow", async () => {
  const { uploadPromptFilesForThreadSend } =
    await import("@/core/threads/hooks");

  await expect(
    uploadPromptFilesForThreadSend({
      threadId: "thread-a",
      backendThreadId: "thread-a",
      fileParts: [
        {
          type: "file",
          filename: "unsafe.txt",
          mediaType: "text/plain",
          url: "",
          file: new File(["demo"], "unsafe.txt", { type: "text/plain" }),
        },
      ],
      createThread: rs.fn(async () => "unexpected-thread"),
      upload: rs.fn(async () => ({
        success: false,
        message: "Successfully uploaded 0 file(s); skipped 1 unsafe file(s)",
        skipped_files: ["unsafe.txt"],
        files: [],
      })),
    }),
  ).rejects.toThrow("skipped 1 unsafe file(s)");
});

test("stale upload continuation is ignored after request ownership changes", async () => {
  const { uploadPromptFilesForThreadSend } =
    await import("@/core/threads/hooks");
  let ownsRequest = true;
  const upload = rs.fn(async () => {
    ownsRequest = false;
    return {
      success: true,
      message: "ok",
      skipped_files: [],
      files: [
        {
          filename: "note.txt",
          size: 4,
          path: "/tmp/note.txt",
          virtual_path:
            "/Users/pingxia/projects/deer-flow/backend/.deer-flow/users/963870b2-72d1-4f61-b0bc-5a46617b16b7/threads/5a484122-3cb4-41ff-b9e5-1dff92a6af50/user-data/uploads/note.txt",
          artifact_url: "/api/artifacts/note.txt",
        },
      ],
    };
  });

  const result = await uploadPromptFilesForThreadSend({
    threadId: "thread-a",
    backendThreadId: "thread-a",
    fileParts: [
      {
        type: "file",
        filename: "note.txt",
        mediaType: "text/plain",
        url: "",
        file: new File(["demo"], "note.txt", { type: "text/plain" }),
      },
    ],
    createThread: rs.fn(async () => "unexpected-thread"),
    upload,
    shouldContinue: () => ownsRequest,
  });

  expect(result).toBeNull();
  expect(upload).toHaveBeenCalledWith("thread-a", [expect.any(File)]);
});

test("stale thread-not-found upload error stops before stream submit", async () => {
  const { uploadPromptFilesForThreadSend } =
    await import("@/core/threads/hooks");
  const { UploadRequestError, isStaleThreadUploadError } =
    await import("@/core/uploads/api");
  const streamSubmit = rs.fn();
  let thrown: unknown;

  try {
    await uploadPromptFilesForThreadSend({
      threadId: "stale-thread",
      backendThreadId: "stale-thread",
      fileParts: [
        {
          type: "file",
          filename: "note.txt",
          mediaType: "text/plain",
          url: "",
          file: new File(["demo"], "note.txt", { type: "text/plain" }),
        },
      ],
      createThread: rs.fn(async () => "unexpected-thread"),
      upload: rs.fn(async () => {
        throw new UploadRequestError(
          "Thread stale-thread not found",
          404,
          "stale-thread",
        );
      }),
    });
    streamSubmit();
  } catch (error) {
    thrown = error;
  }

  expect(isStaleThreadUploadError(thrown)).toBe(true);
  expect(streamSubmit).not.toHaveBeenCalled();
});

test("existing-thread attachment upload uses the saved backend thread", async () => {
  const { uploadPromptFilesForThreadSend } =
    await import("@/core/threads/hooks");
  const createThread = rs.fn(async () => "unexpected-thread");
  const upload = rs.fn(async () => ({
    success: true,
    message: "ok",
    skipped_files: [],
    files: [],
  }));

  const result = await uploadPromptFilesForThreadSend({
    threadId: "thread-a",
    backendThreadId: "thread-a",
    fileParts: [
      {
        type: "file",
        filename: "note.txt",
        mediaType: "text/plain",
        url: "",
        file: new File(["demo"], "note.txt", { type: "text/plain" }),
      },
    ],
    createThread,
    upload,
  });

  expect(createThread).not.toHaveBeenCalled();
  expect(upload).toHaveBeenCalledWith("thread-a", [expect.any(File)]);
  expect(result).toEqual({ threadId: "thread-a", files: [] });
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

test("buildThreadRunContext leaves absent command-room reasoning for the Gateway", async () => {
  const { buildThreadRunContext } = await import("@/core/threads/hooks");

  const context = buildThreadRunContext(
    {
      agent_name: "command-room",
      model_name: "safe-model",
      mode: "flash",
    },
    "thread-123",
  );

  expect(context).toMatchObject({
    mode: "ultra",
    subagent_enabled: true,
  });
  expect(context.reasoning_effort).toBeUndefined();
});

test("buildThreadRunContext leaves absent ordinary reasoning for the Gateway", async () => {
  const { buildThreadRunContext } = await import("@/core/threads/hooks");

  const context = buildThreadRunContext(
    {
      model_name: "safe-model",
      mode: "pro",
    },
    "thread-456",
  );

  expect(context).toMatchObject({
    model_name: "safe-model",
    mode: "pro",
    thinking_enabled: true,
    is_plan_mode: true,
    subagent_enabled: false,
    thread_id: "thread-456",
  });
  expect(context.reasoning_effort).toBeUndefined();
});

test("buildThreadRunContext preserves legacy reasoning for Gateway normalization", async () => {
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
    reasoning_effort: "low",
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

test("mergeMessages keeps persisted task ownership on an unscoped live replacement", async () => {
  const { mergeMessages } = await import("@/core/threads/hooks");
  const persistedHuman = {
    id: "human-1",
    type: "human",
    content: "Persisted prompt",
    additional_kwargs: {
      deerflow_run_id: "run-1",
      history_created_at: "2026-07-12T00:00:00.000Z",
    },
  } as Message;
  const persistedTaskCall = {
    id: "task-call",
    type: "ai",
    content: "",
    tool_calls: [
      {
        id: "task-1",
        name: "task",
        args: { description: "Persisted task" },
      },
    ],
    additional_kwargs: {
      deerflow_run_id: "run-1",
      deerflow_round_id: "round-1",
      history_created_at: "2026-07-12T00:00:00.000Z",
    },
  } as Message;
  const liveTaskCall = {
    ...persistedTaskCall,
    additional_kwargs: { turn_duration: 5 },
  } as Message;
  const liveHuman = {
    ...persistedHuman,
    additional_kwargs: {},
  } as Message;

  const messages = mergeMessages(
    [persistedHuman, persistedTaskCall],
    [liveHuman, liveTaskCall],
    [],
  );

  expect(messages[1]?.additional_kwargs).toMatchObject({
    deerflow_run_id: "run-1",
    deerflow_round_id: "round-1",
    history_created_at: "2026-07-12T00:00:00.000Z",
    turn_duration: 5,
  });
});

test("getThreadMessagesWithLiveSnapshot drops partial live state after authoritative terminal settlement", async () => {
  const {
    buildVisibleHistoryMessages,
    getThreadMessagesWithLiveSnapshot,
    mergeMessages,
  } = await import("@/core/threads/hooks");
  const partialMessage = {
    id: "ai-1",
    type: "ai",
    content: "partial answer",
    additional_kwargs: { run_id: "run-1" },
  } as Message;
  const history = buildVisibleHistoryMessages(
    [
      makeRunMessage("run-1", 1, {
        id: "ai-1",
        type: "ai",
        content: "complete answer",
      } as Message),
    ],
    new Set(),
    [],
  );
  const persistedMessages = getThreadMessagesWithLiveSnapshot({
    viewThreadId: "thread-1",
    threadMessages: [partialMessage],
    liveSnapshot: {
      threadId: "thread-1",
      runId: "run-1",
      messages: [partialMessage],
    },
    pendingSupersededMessageIds: new Set(),
    liveRunSettled: true,
  });

  expect(
    mergeMessages(history, persistedMessages, []).map(
      (message) => message.content,
    ),
  ).toEqual(["complete answer"]);
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

test("mergeRunsWithTerminalPrecedence promotes a newer snapshot-only background run", async () => {
  const { mergeRunsWithTerminalPrecedence } =
    await import("@/core/threads/hooks");

  const result = mergeRunsWithTerminalPrecedence({
    snapshotRuns: [
      {
        run_id: "background-wakeup",
        status: "success",
        created_at: "2026-07-15T11:02:44.000Z",
      } as unknown as Run,
      {
        run_id: "human-run",
        status: "success",
        created_at: "2026-07-15T10:58:29.000Z",
      } as unknown as Run,
    ],
    queriedRuns: [
      {
        run_id: "human-run",
        status: "success",
        created_at: "2026-07-15T10:58:29.000Z",
      } as unknown as Run,
    ],
  });

  expect(result?.map((run) => run.run_id)).toEqual([
    "background-wakeup",
    "human-run",
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

test("reconcileTaskEventRunHistory refreshes run messages and invalidates run list per terminal task event", async () => {
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

test("reconcileTaskEventRunHistory refreshes each terminal task event and invalidates runs plus snapshot", async () => {
  const {
    reconcileTaskEventRunHistory,
    threadRunsQueryKey,
    threadRuntimeSnapshotQueryKey,
  } = await import("@/core/threads/hooks");
  const client = new QueryClient();
  const threadId = "multi-task-event-thread";
  const runId = "multi-task-event-run";
  const refreshed: Array<{
    threadId: string | null | undefined;
    runIds: string[];
  }> = [];
  client.setQueryData(threadRunsQueryKey(threadId), "cached-runs");
  client.setQueryData(threadRuntimeSnapshotQueryKey(threadId), "snapshot");

  for (const [index, type] of ["task_completed", "task_failed"].entries()) {
    expect(
      reconcileTaskEventRunHistory(
        client,
        {
          type,
          task_id: `task-${index}`,
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
  }

  expect(refreshed).toEqual([
    { threadId, runIds: [runId] },
    { threadId, runIds: [runId] },
  ]);
  expect(
    client.getQueryState(threadRunsQueryKey(threadId))?.isInvalidated,
  ).toBe(true);
  expect(
    client.getQueryState(threadRuntimeSnapshotQueryKey(threadId))
      ?.isInvalidated,
  ).toBe(true);
});

test("reconcileTerminalRunHistory refreshes run messages and invalidates runs plus snapshot", async () => {
  const {
    reconcileTerminalRunHistory,
    threadRunsQueryKey,
    threadRuntimeSnapshotQueryKey,
  } = await import("@/core/threads/hooks");
  const client = new QueryClient();
  const threadId = "terminal-event-thread";
  const runId = "terminal-event-run";
  const refreshed: Array<{
    threadId: string | null | undefined;
    runIds: string[];
  }> = [];
  const settled: unknown[] = [];
  client.setQueryData(threadRunsQueryKey(threadId), "cached-runs");
  client.setQueryData(threadRuntimeSnapshotQueryKey(threadId), "snapshot");

  expect(
    reconcileTerminalRunHistory(
      client,
      {
        type: "run.terminal",
        event_type: "run.terminal",
        thread_id: threadId,
        run_id: runId,
        round_id: "terminal-round",
        status: "success",
        terminal_reason: "success",
      },
      (params) =>
        refreshed.push({
          threadId: params?.threadId,
          runIds: [...(params?.runIds ?? [])],
        }),
      (terminal) => settled.push(terminal),
    ),
  ).toBe(true);

  expect(refreshed).toEqual([{ threadId, runIds: [runId] }]);
  expect(settled).toEqual([
    {
      threadId,
      runId,
      roundId: "terminal-round",
      status: "success",
      terminalReason: "success",
    },
  ]);
  expect(
    client.getQueryState(threadRunsQueryKey(threadId))?.isInvalidated,
  ).toBe(true);
  expect(
    client.getQueryState(threadRuntimeSnapshotQueryKey(threadId))
      ?.isInvalidated,
  ).toBe(true);
});

test("applyBackgroundRunProbeResult invalidates runs and snapshot for final stream settlement", async () => {
  const {
    applyBackgroundRunProbeResult,
    threadRunsQueryKey,
    threadRuntimeSnapshotQueryKey,
  } = await import("@/core/threads/hooks");
  const client = new QueryClient();
  const threadId = "finish-thread";
  const runId = "finish-run";
  client.setQueryData(threadRunsQueryKey(threadId), "cached-runs");
  client.setQueryData(threadRuntimeSnapshotQueryKey(threadId), "snapshot");

  expect(
    applyBackgroundRunProbeResult(client, threadId, runId, "success"),
  ).toBe(true);
  expect(
    client.getQueryState(threadRunsQueryKey(threadId))?.isInvalidated,
  ).toBe(true);
  expect(
    client.getQueryState(threadRuntimeSnapshotQueryKey(threadId))
      ?.isInvalidated,
  ).toBe(true);
});

test("resolveThreadStreamFinishMeta returns onFinish thread and run metadata", async () => {
  const { resolveThreadStreamFinishMeta } =
    await import("@/core/threads/hooks");

  expect(
    resolveThreadStreamFinishMeta({
      run: { thread_id: "thread-from-run", run_id: "run-from-run" },
      streamOwner: { threadId: "thread-from-owner", runId: "run-from-owner" },
    }),
  ).toEqual({ threadId: "thread-from-run", runId: "run-from-run" });
  expect(
    resolveThreadStreamFinishMeta({
      run: null,
      streamOwner: { threadId: "thread-from-owner", runId: "run-from-owner" },
    }),
  ).toEqual({ threadId: "thread-from-owner", runId: "run-from-owner" });
});

test("terminal stream fallback commits a missing start exactly once", async () => {
  const { shouldCommitStreamStart } = await import("@/core/threads/hooks");

  expect(
    shouldCommitStreamStart({
      started: false,
      threadId: "created-thread",
      runId: "created-run",
    }),
  ).toBe(true);
  expect(
    shouldCommitStreamStart({
      started: true,
      threadId: "created-thread",
      runId: "created-run",
    }),
  ).toBe(false);
  expect(
    shouldCommitStreamStart({
      started: false,
      threadId: "created-thread",
      runId: null,
    }),
  ).toBe(false);
});

test("resolveVisibleTaskRunningThreadId uses event or stream owner, not current route fallback", async () => {
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
      streamThreadId: "thread-a",
      viewThreadId: "thread-b",
      liveMessagesThreadId: "thread-b",
    }),
  ).toBe("thread-a");
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
  expect(
    shouldReleaseQueuedThreadMessage({
      ...base,
      streamFinished: true,
      queuedOwnerId: "owner-a",
      currentOwnerId: "owner-b",
    }),
  ).toBe(false);
  expect(
    shouldReleaseQueuedThreadMessage({
      ...base,
      streamFinished: true,
      queuedOwnerId: "owner-a",
      currentOwnerId: "owner-a",
      currentViewThreadId: "thread-b",
    }),
  ).toBe(true);
});

test("failed queued send pauses automatic release without losing the message", async () => {
  const hooks = await import("@/core/threads/hooks");
  const settleAttempt = Reflect.get(
    hooks,
    "settleQueuedThreadMessageAttempt",
  ) as
    | (<T>(
        queue: readonly T[],
        attempted: T,
        succeeded: boolean,
      ) => { queue: T[]; failed: T | null; paused: boolean })
    | undefined;
  const attempted = { id: "queued-1", text: "retry me" };
  const later = { id: "queued-2", text: "send later" };

  expect(typeof settleAttempt).toBe("function");
  if (!settleAttempt) return;

  expect(settleAttempt([attempted, later], attempted, false)).toEqual({
    queue: [later],
    failed: attempted,
    paused: true,
  });
  expect(
    hooks.shouldReleaseQueuedThreadMessage({
      streamFinished: true,
      sendInFlight: false,
      recovering: false,
      queuedOwnerId: "owner-a",
      currentOwnerId: "owner-a",
      queuedThreadId: "thread-a",
      currentViewThreadId: "thread-a",
      paused: true,
    } as Parameters<typeof hooks.shouldReleaseQueuedThreadMessage>[0] & {
      paused: boolean;
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

test("shouldTreatStreamFinishAsCurrentStream requires run metadata for an owned run", async () => {
  const { shouldTreatStreamFinishAsCurrentStream } =
    await import("@/core/threads/hooks");

  expect(
    shouldTreatStreamFinishAsCurrentStream(
      "thread-a",
      null,
      "thread-a",
      "run-a",
    ),
  ).toBe(false);
  expect(
    shouldTreatStreamFinishAsCurrentStream("thread-a", null, "thread-a", null),
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

test("current stream finish side effects ignore stale thread/run ownership", async () => {
  const { shouldRunCurrentStreamFinishSideEffects } =
    await import("@/core/threads/hooks");

  expect(
    shouldRunCurrentStreamFinishSideEffects({
      eventThreadId: "thread-a",
      eventRunId: "run-a",
      streamThreadId: "thread-b",
      streamRunId: "run-b",
    }),
  ).toBe(false);
  expect(
    shouldRunCurrentStreamFinishSideEffects({
      eventThreadId: "thread-b",
      eventRunId: "run-b",
      streamThreadId: "thread-b",
      streamRunId: "run-b",
    }),
  ).toBe(true);
});

test("metadata-less finish cannot claim an existing run owner", async () => {
  const {
    resolveThreadStreamFinishMeta,
    shouldReleaseQueuedThreadMessage,
    shouldRunCurrentStreamFinishSideEffects,
  } = await import("@/core/threads/hooks");
  const finishMeta = resolveThreadStreamFinishMeta({
    run: null,
    streamOwner: { threadId: "thread-b", runId: "run-b" },
  });
  const staleFinishOwnsCurrent = shouldRunCurrentStreamFinishSideEffects({
    eventThreadId: null,
    eventRunId: null,
    streamThreadId: "thread-b",
    streamRunId: "run-b",
  });

  expect(finishMeta).toEqual({ threadId: "thread-b", runId: "run-b" });
  expect(staleFinishOwnsCurrent).toBe(false);
  expect(
    shouldReleaseQueuedThreadMessage({
      streamFinished: staleFinishOwnsCurrent,
      sendInFlight: false,
      recovering: false,
      queuedThreadId: "thread-b",
      currentViewThreadId: "thread-b",
    }),
  ).toBe(false);
});

test("metadata-less finish uses captured stream owner before releasing queue", async () => {
  const {
    resolveThreadStreamFinishMeta,
    shouldReleaseQueuedThreadMessage,
    shouldRunCurrentStreamFinishSideEffects,
  } = await import("@/core/threads/hooks");
  const finishMeta = resolveThreadStreamFinishMeta({
    run: null,
    streamOwner: { threadId: "thread-a", runId: "run-a" },
  });
  const finishOwnsCapturedStream = shouldRunCurrentStreamFinishSideEffects({
    eventThreadId: finishMeta.threadId,
    eventRunId: finishMeta.runId,
    streamThreadId: "thread-a",
    streamRunId: "run-a",
    runtimeOwnerId: "slot-a",
    displayThreadId: "thread-b",
  });

  expect(finishOwnsCapturedStream).toBe(true);
  expect(
    shouldReleaseQueuedThreadMessage({
      streamFinished: finishOwnsCapturedStream,
      sendInFlight: false,
      recovering: false,
      queuedOwnerId: "slot-a",
      currentOwnerId: "slot-a",
      queuedThreadId: "thread-a",
      currentViewThreadId: "thread-b",
    }),
  ).toBe(true);
});

test("metadata-less finish is accepted only before a run owner exists", async () => {
  const { shouldRunCurrentStreamFinishSideEffects } =
    await import("@/core/threads/hooks");

  expect(
    shouldRunCurrentStreamFinishSideEffects({
      eventThreadId: "thread-b",
      eventRunId: null,
      streamThreadId: "thread-b",
      streamRunId: null,
    }),
  ).toBe(true);
  expect(
    shouldRunCurrentStreamFinishSideEffects({
      eventThreadId: "thread-b",
      eventRunId: null,
      streamThreadId: "thread-b",
      streamRunId: "run-b",
    }),
  ).toBe(false);
});

test("stream title updates use event or stream owner, not current route fallback", async () => {
  const { shouldApplyStreamTitleUpdate } = await import("@/core/threads/hooks");

  expect(
    shouldApplyStreamTitleUpdate({
      eventThreadId: "thread-a",
      eventRunId: "run-a",
      streamThreadId: "thread-b",
      streamRunId: "run-b",
      viewThreadId: "thread-b",
      liveMessagesThreadId: "thread-b",
    }),
  ).toBe(false);
  expect(
    shouldApplyStreamTitleUpdate({
      eventThreadId: "thread-b",
      eventRunId: "run-a",
      streamThreadId: "thread-b",
      streamRunId: "run-b",
      viewThreadId: "thread-b",
      liveMessagesThreadId: "thread-b",
    }),
  ).toBe(false);
  expect(
    shouldApplyStreamTitleUpdate({
      eventThreadId: "thread-b",
      eventRunId: "run-b",
      streamThreadId: "thread-b",
      streamRunId: "run-b",
      viewThreadId: "thread-b",
    }),
  ).toBe(true);
  expect(
    shouldApplyStreamTitleUpdate({
      eventThreadId: "thread-a",
      eventRunId: "run-a",
      streamThreadId: "thread-a",
      streamRunId: "run-a",
      viewThreadId: "thread-b",
      liveMessagesThreadId: "thread-b",
    }),
  ).toBe(true);
  expect(
    shouldApplyStreamTitleUpdate({
      streamThreadId: "thread-b",
      streamRunId: "run-b",
      viewThreadId: "thread-b",
      liveMessagesThreadId: "thread-b",
    }),
  ).toBe(false);
  expect(
    shouldApplyStreamTitleUpdate({
      eventThreadId: "thread-b",
      streamThreadId: "thread-b",
      streamRunId: null,
      viewThreadId: "thread-b",
      liveMessagesThreadId: "thread-b",
    }),
  ).toBe(true);
});

test("stale A stream finish/title cannot release B queue or update B title", async () => {
  const {
    shouldApplyStreamTitleUpdate,
    shouldReleaseQueuedThreadMessage,
    shouldRunCurrentStreamFinishSideEffects,
  } = await import("@/core/threads/hooks");
  const staleFinishOwnsCurrent = shouldRunCurrentStreamFinishSideEffects({
    eventThreadId: "thread-a",
    eventRunId: "run-a",
    streamThreadId: "thread-b",
    streamRunId: "run-b",
  });

  expect(staleFinishOwnsCurrent).toBe(false);
  expect(
    shouldReleaseQueuedThreadMessage({
      streamFinished: staleFinishOwnsCurrent,
      sendInFlight: false,
      recovering: false,
      queuedThreadId: "thread-b",
      currentViewThreadId: "thread-b",
    }),
  ).toBe(false);
  expect(
    shouldApplyStreamTitleUpdate({
      eventThreadId: "thread-a",
      eventRunId: "run-a",
      streamThreadId: "thread-b",
      streamRunId: "run-b",
      viewThreadId: "thread-b",
      liveMessagesThreadId: "thread-b",
    }),
  ).toBe(false);
});

test("old run finish after new run started does not release new queue or mark the thread idle", async () => {
  const {
    shouldReleaseQueuedThreadMessage,
    shouldRunCurrentStreamFinishSideEffects,
  } = await import("@/core/threads/hooks");
  const oldFinishOwnsCurrent = shouldRunCurrentStreamFinishSideEffects({
    eventThreadId: "thread-a",
    eventRunId: "run-old",
    streamThreadId: "thread-a",
    streamRunId: "run-new",
    runtimeOwnerId: "slot-a",
    displayThreadId: "thread-a",
  });

  expect(oldFinishOwnsCurrent).toBe(false);
  expect(
    shouldReleaseQueuedThreadMessage({
      streamFinished: oldFinishOwnsCurrent,
      sendInFlight: false,
      recovering: false,
      queuedOwnerId: "slot-a",
      currentOwnerId: "slot-a",
      queuedThreadId: "thread-a",
      currentViewThreadId: "thread-a",
    }),
  ).toBe(false);

  const newFinishOwnsCurrent = shouldRunCurrentStreamFinishSideEffects({
    eventThreadId: "thread-a",
    eventRunId: "run-new",
    streamThreadId: "thread-a",
    streamRunId: "run-new",
    runtimeOwnerId: "slot-a",
    displayThreadId: "thread-a",
  });

  expect(newFinishOwnsCurrent).toBe(true);
  expect(
    shouldReleaseQueuedThreadMessage({
      streamFinished: newFinishOwnsCurrent,
      sendInFlight: false,
      recovering: false,
      queuedOwnerId: "slot-a",
      currentOwnerId: "slot-a",
      queuedThreadId: "thread-a",
      currentViewThreadId: "thread-a",
    }),
  ).toBe(true);
});

test("same-thread new run claim requires clearing the previous run owner first", async () => {
  const { createThreadRuntimeOwnerSnapshot, shouldClaimThreadRuntimeOwner } =
    await import("@/core/threads/hooks");
  const previousRunOwner = createThreadRuntimeOwnerSnapshot({
    threadId: "thread-a",
    runId: "run-old",
    runtimeOwnerId: "slot-a",
    displayThreadId: "thread-a",
  });
  const preparedOwner = createThreadRuntimeOwnerSnapshot({
    threadId: "thread-a",
    runId: null,
    runtimeOwnerId: "slot-a",
    displayThreadId: "thread-a",
  });

  expect(
    shouldClaimThreadRuntimeOwner({
      eventThreadId: "thread-a",
      eventRunId: "run-new",
      currentOwner: previousRunOwner,
    }),
  ).toBe(false);
  expect(
    shouldClaimThreadRuntimeOwner({
      eventThreadId: "thread-a",
      eventRunId: "run-new",
      currentOwner: preparedOwner,
    }),
  ).toBe(true);
});

test("stale stream error from previous run does not own current recovery UI", async () => {
  const {
    createThreadRuntimeOwnerSnapshot,
    getVisibleThreadError,
    isCurrentThreadRuntimeOwnerEvent,
  } = await import("@/core/threads/hooks");
  const currentOwner = createThreadRuntimeOwnerSnapshot({
    threadId: "thread-a",
    runId: "run-new",
    runtimeOwnerId: "slot-a",
    displayThreadId: "thread-a",
  });
  const staleErrorOwnsCurrentUi = isCurrentThreadRuntimeOwnerEvent({
    eventThreadId: "thread-a",
    eventRunId: "run-old",
    currentOwner,
    requireEventThreadId: true,
  });
  const error = new Error("old stream failed");

  expect(staleErrorOwnsCurrentUi).toBe(false);
  expect(getVisibleThreadError(error, staleErrorOwnsCurrentUi)).toBe(error);
});

test("terminal event with mismatched run_id refreshes that run without current stream side effects", async () => {
  const {
    reconcileTerminalRunHistory,
    shouldTreatTerminalEventAsCurrentStream,
    threadRunsQueryKey,
    threadRuntimeSnapshotQueryKey,
  } = await import("@/core/threads/hooks");
  const client = new QueryClient();
  const threadId = "terminal-mismatch-thread";
  const oldRunId = "run-old";
  const refreshed: unknown[] = [];
  const settled: unknown[] = [];
  client.setQueryData(
    ["threads", "search"],
    [{ thread_id: threadId, status: "busy", values: {}, metadata: {} }],
  );
  client.setQueryData(threadRunsQueryKey(threadId), "cached-runs");
  client.setQueryData(threadRuntimeSnapshotQueryKey(threadId), "snapshot");

  const ownsCurrent = shouldTreatTerminalEventAsCurrentStream(
    threadId,
    oldRunId,
    threadId,
    "run-new",
  );
  expect(ownsCurrent).toBe(false);
  expect(
    reconcileTerminalRunHistory(
      client,
      {
        type: "run.terminal",
        event_type: "run.terminal",
        thread_id: threadId,
        run_id: oldRunId,
        status: "success",
        terminal_reason: "success",
      },
      (params) => refreshed.push(params),
      (terminal) => settled.push(terminal),
      { applyThreadSideEffects: ownsCurrent },
    ),
  ).toBe(true);

  expect(refreshed).toEqual([{ threadId, runIds: [oldRunId] }]);
  expect(settled).toEqual([]);
  expect(
    client.getQueryData<Array<{ status: string }>>(["threads", "search"])?.[0]
      ?.status,
  ).toBe("busy");
});

test("route switch during loading detaches visible live state without dropping background owner", async () => {
  const {
    createThreadRuntimeOwnerSnapshot,
    shouldPreserveRuntimeOwnerOnRouteSwitch,
    shouldShowLiveThreadState,
  } = await import("@/core/threads/hooks");
  const backgroundOwner = createThreadRuntimeOwnerSnapshot({
    threadId: "thread-a",
    runId: "run-a",
    runtimeOwnerId: "slot-a",
    displayThreadId: "thread-a",
  });

  expect(
    shouldPreserveRuntimeOwnerOnRouteSwitch({
      currentOwner: backgroundOwner,
      nextDisplayThreadId: "thread-b",
      streamFinished: false,
      sendInFlight: false,
    }),
  ).toBe(true);
  expect(shouldShowLiveThreadState("thread-b", "thread-a", "thread-a")).toBe(
    false,
  );
});

test("tombstoned deleted thread ignores late stream, history, probe, and task callbacks", async () => {
  stubBrowserWindow();
  const {
    applyBackgroundRunProbeResult,
    clearDeletedThreadClientState,
    getThreadActivitySnapshot,
    isCurrentThreadRuntimeOwnerEvent,
    isDeletedThreadTombstoned,
    markThreadBusyInCaches,
    markThreadFinished,
    reconcileTaskEventRunHistory,
    reconcileTerminalRunHistory,
    threadRunsQueryKey,
    threadRuntimeSnapshotQueryKey,
    upsertThreadInSearchCache,
  } = await import("@/core/threads/hooks");
  const client = new QueryClient();
  const threadId = "tombstoned-thread";
  const runId = "late-run";
  const refreshed: unknown[] = [];
  const settled: unknown[] = [];
  client.setQueryData(["threads", "search"], []);
  client.setQueryData(threadRunsQueryKey(threadId), "cached-runs");
  client.setQueryData(threadRuntimeSnapshotQueryKey(threadId), "snapshot");

  clearDeletedThreadClientState(client, threadId);
  expect(isDeletedThreadTombstoned(threadId)).toBe(true);

  markThreadBusyInCaches(client, threadId);
  markThreadFinished(threadId);
  upsertThreadInSearchCache(client, {
    thread_id: threadId,
    created_at: "2026-01-01T00:00:00.000Z",
    updated_at: "2026-01-01T00:00:00.000Z",
    metadata: {},
    status: "busy",
    values: { title: "late", messages: [], artifacts: [] },
    interrupts: {},
  });
  expect(
    applyBackgroundRunProbeResult(client, threadId, runId, "success"),
  ).toBe(true);
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
      (params) => refreshed.push(params),
      (terminal) => settled.push(terminal),
    ),
  ).toBe(true);
  expect(
    reconcileTaskEventRunHistory(
      client,
      {
        event_type: "task_completed",
        schema_version: TASK_EVENT_CONTRACT.schema_version,
        task_id: "task-late",
        thread_id: threadId,
        run_id: runId,
      },
      (params) => refreshed.push(params),
    ),
  ).toBe(true);

  expect(isCurrentThreadRuntimeOwnerEvent({ currentOwner: null })).toBe(false);
  expect(client.getQueryData(["threads", "search"])).toEqual([]);
  expect(client.getQueryData(threadRunsQueryKey(threadId))).toBeUndefined();
  expect(
    client.getQueryData(threadRuntimeSnapshotQueryKey(threadId)),
  ).toBeUndefined();
  expect(getThreadActivitySnapshot().running.has(threadId)).toBe(false);
  expect(getThreadActivitySnapshot().finished.has(threadId)).toBe(false);
  expect(refreshed).toEqual([]);
  expect(settled).toEqual([]);
});

test("tombstoned deleted thread ignores late upload continuations", async () => {
  const {
    clearDeletedThreadClientState,
    isDeletedThreadTombstoned,
    shouldApplyUploadContinuation,
  } = await import("@/core/threads/hooks");
  const client = new QueryClient();
  const request = {
    requestId: "send-upload",
    threadId: "deleted-thread",
    displayThreadId: "deleted-thread",
    runtimeOwnerId: "runtime-deleted",
  };

  clearDeletedThreadClientState(client, request.threadId);

  expect(isDeletedThreadTombstoned(request.threadId)).toBe(true);
  expect(
    shouldApplyUploadContinuation({
      activeRequest: request,
      request,
      currentViewThreadId: request.threadId,
      isDeletedThread: isDeletedThreadTombstoned(request.threadId),
      visibleOnly: true,
    }),
  ).toBe(false);
});

test("local stop settlement releases same-run follow-up and refreshes snapshot fallback", async () => {
  const {
    beginLocalRunCancellation,
    finishLocalRunCancellation,
    getThreadActivitySnapshot,
    shouldQueueThreadMessage,
    shouldReleaseQueuedThreadMessage,
    threadRunsQueryKey,
    threadRuntimeSnapshotQueryKey,
  } = await import("@/core/threads/hooks");
  const client = new QueryClient();
  const threadId = "stop-thread";
  const runId = "stop-run";
  const settled: unknown[] = [];
  client.setQueryData(threadRunsQueryKey(threadId), "cached-runs");
  client.setQueryData(threadRuntimeSnapshotQueryKey(threadId), "snapshot");

  expect(
    beginLocalRunCancellation({ queryClient: client, threadId, runId }),
  ).toEqual({ threadId, runId });
  expect(getThreadActivitySnapshot().running.has(threadId)).toBe(true);
  expect(
    client.getQueryState(threadRuntimeSnapshotQueryKey(threadId))
      ?.isInvalidated,
  ).toBe(true);

  expect(
    finishLocalRunCancellation({
      queryClient: client,
      threadId,
      runId,
      settleRunSubtasks: (terminal) => settled.push(terminal),
    }),
  ).toEqual({ threadId, runId });

  expect(getThreadActivitySnapshot().running.has(threadId)).toBe(false);
  expect(settled).toEqual([
    {
      threadId,
      runId,
      status: "interrupted",
      terminalReason: "user_cancelled",
    },
  ]);
  expect(
    shouldReleaseQueuedThreadMessage({
      streamFinished: true,
      sendInFlight: false,
      recovering: false,
      queuedThreadId: threadId,
      currentViewThreadId: threadId,
    }),
  ).toBe(true);
  expect(
    shouldQueueThreadMessage({
      isLoading: false,
      streamFinished: true,
      recovering: false,
      sendInFlight: false,
    }),
  ).toBe(false);
});

test("cancel 202 keeps the run recoverable instead of marking interrupted", async () => {
  const {
    getThreadActivitySnapshot,
    keepRunCancellationRecovering,
    threadRunsQueryKey,
    threadRuntimeSnapshotQueryKey,
  } = await import("@/core/threads/hooks");
  const client = new QueryClient();
  const threadId = "cancel-accepted-thread";
  const runId = "cancel-accepted-run";
  const settled: unknown[] = [];
  client.setQueryData(
    ["threads", "search"],
    [{ thread_id: threadId, status: "running", values: {}, metadata: {} }],
  );
  client.setQueryData(threadRunsQueryKey(threadId), "cached-runs");
  client.setQueryData(threadRuntimeSnapshotQueryKey(threadId), "snapshot");

  expect(
    keepRunCancellationRecovering({
      queryClient: client,
      threadId,
      runId,
      isMock: true,
      settleRunSubtasks: (terminal) => settled.push(terminal),
    }),
  ).toEqual({ threadId, runId });

  expect(getThreadActivitySnapshot().running.has(threadId)).toBe(true);
  expect(
    client.getQueryData<Array<{ status: string }>>(["threads", "search"])?.[0]
      ?.status,
  ).toBe("cancelling");
  expect(settled).toEqual([]);
  expect(
    client.getQueryState(threadRuntimeSnapshotQueryKey(threadId))
      ?.isInvalidated,
  ).toBe(true);
});

test("cancel 204 settles only after authoritative terminal reconciliation", async () => {
  const {
    getThreadActivitySnapshot,
    keepRunCancellationRecovering,
    reconcileRunCancellationAuthority,
  } = await import("@/core/threads/hooks");
  const client = new QueryClient();
  const threadId = "cancel-204-thread";
  const runId = "cancel-204-run";
  const settled: unknown[] = [];

  keepRunCancellationRecovering({
    queryClient: client,
    threadId,
    runId,
    isMock: true,
    settleRunSubtasks: (terminal) => settled.push(terminal),
  });
  expect(getThreadActivitySnapshot().running.has(threadId)).toBe(true);

  expect(
    reconcileRunCancellationAuthority({
      queryClient: client,
      threadId,
      runId,
      run: {
        run_id: runId,
        status: "interrupted",
        terminal_reason: "user_cancelled",
      },
      settleRunSubtasks: (terminal) => settled.push(terminal),
    }),
  ).toBe("terminal");

  expect(getThreadActivitySnapshot().running.has(threadId)).toBe(false);
  expect(settled).toEqual([
    {
      threadId,
      runId,
      status: "interrupted",
      terminalReason: "user_cancelled",
    },
  ]);
});

test("cancel 409 with terminal authority clears busy without generic recovery", async () => {
  const {
    getThreadActivitySnapshot,
    keepRunCancellationRecovering,
    reconcileRunCancellationAuthority,
  } = await import("@/core/threads/hooks");
  const client = new QueryClient();
  const threadId = "cancel-409-terminal-thread";
  const runId = "cancel-409-terminal-run";
  client.setQueryData(
    ["threads", "search"],
    [{ thread_id: threadId, status: "cancelling", values: {}, metadata: {} }],
  );

  keepRunCancellationRecovering({
    queryClient: client,
    threadId,
    runId,
    isMock: true,
  });
  expect(
    reconcileRunCancellationAuthority({
      queryClient: client,
      threadId,
      runId,
      run: { run_id: runId, status: "success" },
    }),
  ).toBe("terminal");

  expect(getThreadActivitySnapshot().running.has(threadId)).toBe(false);
  expect(
    client.getQueryData<Array<{ status: string }>>(["threads", "search"])?.[0]
      ?.status,
  ).toBe("idle");
});

test("cancel 409 with active authority keeps recoverable busy state", async () => {
  const { getThreadActivitySnapshot, reconcileRunCancellationAuthority } =
    await import("@/core/threads/hooks");
  const client = new QueryClient();
  const threadId = "cancel-409-active-thread";
  const runId = "cancel-409-active-run";
  const settled: unknown[] = [];
  client.setQueryData(
    ["threads", "search"],
    [{ thread_id: threadId, status: "running", values: {}, metadata: {} }],
  );

  expect(
    reconcileRunCancellationAuthority({
      queryClient: client,
      threadId,
      runId,
      run: { run_id: runId, status: "running" },
      isMock: true,
      settleRunSubtasks: (terminal) => settled.push(terminal),
    }),
  ).toBe("active");

  expect(getThreadActivitySnapshot().running.has(threadId)).toBe(true);
  expect(
    client.getQueryData<Array<{ status: string }>>(["threads", "search"])?.[0]
      ?.status,
  ).toBe("cancelling");
  expect(settled).toEqual([]);
});

test("cancel 409 falls back to runtime snapshot when run detail is unavailable", async () => {
  const threadId = "cancel-snapshot-thread";
  const runId = "cancel-snapshot-run";
  const getRun = rs.fn(async () => {
    throw Object.assign(new Error("HTTP 409"), { status: 409 });
  });
  const fetchSnapshot = rs.fn(
    async () =>
      new Response(
        JSON.stringify({
          thread_id: threadId,
          runs: [{ run_id: runId, status: "worker_lost" }],
          run_messages: [],
        }),
        { status: 200 },
      ),
  );
  const {
    getThreadActivitySnapshot,
    reconcileRunCancellationFromAuthority,
    threadRuntimeSnapshotQueryKey,
  } = await loadThreadHooksWithRunAndFetch(getRun, fetchSnapshot);
  const client = new QueryClient();

  expect(
    await reconcileRunCancellationFromAuthority({
      queryClient: client,
      threadId,
      runId,
      isMock: true,
    }),
  ).toBe("terminal");

  expect(fetchSnapshot).toHaveBeenCalledTimes(1);
  expect(client.getQueryData(threadRuntimeSnapshotQueryKey(threadId))).toEqual({
    thread_id: threadId,
    runs: [{ run_id: runId, status: "worker_lost" }],
    run_messages: [],
  });
  expect(getThreadActivitySnapshot().running.has(threadId)).toBe(false);
});

test("double local stop settlement is idempotent and keeps snapshot fallback invalidated", async () => {
  const {
    finishLocalRunCancellation,
    threadRunsQueryKey,
    threadRuntimeSnapshotQueryKey,
  } = await import("@/core/threads/hooks");
  const client = new QueryClient();
  const threadId = "double-stop-thread";
  const runId = "double-stop-run";
  client.setQueryData(threadRunsQueryKey(threadId), "cached-runs");
  client.setQueryData(threadRuntimeSnapshotQueryKey(threadId), "snapshot");

  expect(() =>
    finishLocalRunCancellation({ queryClient: client, threadId, runId }),
  ).not.toThrow();
  expect(() =>
    finishLocalRunCancellation({ queryClient: client, threadId, runId }),
  ).not.toThrow();
  expect(
    client.getQueryState(threadRuntimeSnapshotQueryKey(threadId))
      ?.isInvalidated,
  ).toBe(true);
});

test("stream_recovery_required uses error thread/run owner for snapshot backfill", async () => {
  const {
    applyStreamErrorRecovery,
    resolveRunStreamRecoveryErrorOwner,
    threadRuntimeSnapshotQueryKey,
  } = await import("@/core/threads/hooks");
  const { RunStreamRecoveryRequiredError } = await import("@/core/api");
  const client = new QueryClient();
  const threadId = "recovery-required-thread";
  const runId = "recovery-required-run";
  const error = new RunStreamRecoveryRequiredError({
    threadId,
    runId,
    reason: "stream_recovery_required",
  });
  client.setQueryData(threadRuntimeSnapshotQueryKey(threadId), "snapshot");

  const owner = resolveRunStreamRecoveryErrorOwner(
    error,
    "current-route-thread",
    "current-route-run",
  );
  expect(owner).toEqual({ threadId, runId });
  expect(
    applyStreamErrorRecovery({
      queryClient: client,
      threadId: owner?.threadId,
      runId: owner?.runId,
      isMock: true,
    }),
  ).toEqual({ threadId, runId });
  expect(
    client.getQueryState(threadRuntimeSnapshotQueryKey(threadId))
      ?.isInvalidated,
  ).toBe(true);
});

test("stream recovery runtime owner uses captured stream owner instead of visible owner", async () => {
  const {
    applyStreamErrorRecovery,
    createThreadRuntimeOwnerSnapshot,
    resolveStreamErrorRecoveryRuntimeOwnerId,
  } = await import("@/core/threads/hooks");
  const client = new QueryClient();
  const threadId = "background-recovery-thread";
  const runId = "background-recovery-run";

  const runtimeOwnerId = resolveStreamErrorRecoveryRuntimeOwnerId({
    eventThreadId: threadId,
    eventRunId: runId,
    streamOwner: createThreadRuntimeOwnerSnapshot({
      threadId,
      runId,
      runtimeOwnerId: "slot-a",
      displayThreadId: threadId,
    }),
    currentOwner: createThreadRuntimeOwnerSnapshot({
      threadId: "visible-thread-b",
      runId: "visible-run-b",
      runtimeOwnerId: "slot-b",
      displayThreadId: "visible-thread-b",
    }),
    errorOwnsCurrentUi: false,
  });

  expect(runtimeOwnerId).toBe("slot-a");
  expect(
    applyStreamErrorRecovery({
      queryClient: client,
      threadId,
      runId,
      runtimeOwnerId,
      isMock: true,
    }),
  ).toEqual({ threadId, runId, runtimeOwnerId: "slot-a" });
});

test("stream recovery runtime owner does not retarget unmatched background errors", async () => {
  const {
    applyStreamErrorRecovery,
    createThreadRuntimeOwnerSnapshot,
    resolveStreamErrorRecoveryRuntimeOwnerId,
  } = await import("@/core/threads/hooks");
  const client = new QueryClient();
  const threadId = "orphan-recovery-thread";
  const runId = "orphan-recovery-run";

  const runtimeOwnerId = resolveStreamErrorRecoveryRuntimeOwnerId({
    eventThreadId: threadId,
    eventRunId: runId,
    streamOwner: createThreadRuntimeOwnerSnapshot({
      threadId: "previous-thread-c",
      runId: "previous-run-c",
      runtimeOwnerId: "slot-c",
      displayThreadId: "previous-thread-c",
    }),
    currentOwner: createThreadRuntimeOwnerSnapshot({
      threadId: "visible-thread-b",
      runId: "visible-run-b",
      runtimeOwnerId: "slot-b",
      displayThreadId: "visible-thread-b",
    }),
    errorOwnsCurrentUi: false,
  });

  expect(runtimeOwnerId).toBeNull();
  expect(
    applyStreamErrorRecovery({
      queryClient: client,
      threadId,
      runId,
      runtimeOwnerId,
      isMock: true,
    }),
  ).toEqual({ threadId, runId });
});

test("stream recovery runtime owner can fall back to current owner for current-ui errors", async () => {
  const {
    createThreadRuntimeOwnerSnapshot,
    resolveStreamErrorRecoveryRuntimeOwnerId,
  } = await import("@/core/threads/hooks");

  expect(
    resolveStreamErrorRecoveryRuntimeOwnerId({
      eventThreadId: "visible-thread",
      eventRunId: "visible-run",
      streamOwner: null,
      currentOwner: createThreadRuntimeOwnerSnapshot({
        threadId: "visible-thread",
        runId: "visible-run",
        runtimeOwnerId: "slot-visible",
        displayThreadId: "visible-thread",
      }),
      errorOwnsCurrentUi: true,
    }),
  ).toBe("slot-visible");
});

test("stream recovery runtime owner keeps captured thread slot before run id is known", async () => {
  const {
    createThreadRuntimeOwnerSnapshot,
    resolveStreamErrorRecoveryRuntimeOwnerId,
  } = await import("@/core/threads/hooks");

  expect(
    resolveStreamErrorRecoveryRuntimeOwnerId({
      eventThreadId: "thread-a",
      eventRunId: "run-a",
      streamOwner: createThreadRuntimeOwnerSnapshot({
        threadId: "thread-a",
        runId: null,
        runtimeOwnerId: "slot-a",
        displayThreadId: "thread-a",
      }),
      currentOwner: createThreadRuntimeOwnerSnapshot({
        threadId: "thread-b",
        runId: "run-b",
        runtimeOwnerId: "slot-b",
        displayThreadId: "thread-b",
      }),
      errorOwnsCurrentUi: false,
    }),
  ).toBe("slot-a");
});

test("inactive stream 409 uses snapshot recovery and suppresses normal error toast", async () => {
  const {
    applyStreamErrorRecovery,
    resolveRunStreamRecoveryErrorOwner,
    shouldShowStreamErrorToast,
    threadRuntimeSnapshotQueryKey,
  } = await import("@/core/threads/hooks");
  const { RunStreamRecoveryRequiredError } = await import("@/core/api");
  const client = new QueryClient();
  const threadId = "inactive-stream-thread";
  const runId = "inactive-stream-run";
  const error = new RunStreamRecoveryRequiredError({
    threadId,
    runId,
    reason: "inactive_run_stream",
    status: 409,
  });
  client.setQueryData(threadRuntimeSnapshotQueryKey(threadId), "snapshot");

  const owner = resolveRunStreamRecoveryErrorOwner(
    error,
    "visible-thread",
    "visible-run",
  );
  const recoveryRun = applyStreamErrorRecovery({
    queryClient: client,
    threadId: owner?.threadId,
    runId: owner?.runId,
    isMock: true,
  });

  expect(recoveryRun).toEqual({ threadId, runId });
  expect(shouldShowStreamErrorToast(recoveryRun)).toBe(false);
  expect(
    client.getQueryState(threadRuntimeSnapshotQueryKey(threadId))
      ?.isInvalidated,
  ).toBe(true);
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

test("getMissingThreadHistoryError recognizes only enabled missing thread history errors", async () => {
  const { getMissingThreadHistoryError } = await import("@/core/threads/hooks");
  const notFoundError = { status: 404 };
  const forbiddenError = { status: 403 };
  const networkError = new Error("network");

  expect(
    getMissingThreadHistoryError({
      enabled: true,
      threadId: "deleted-thread",
      tombstoned: false,
      snapshotError: notFoundError,
      runsError: networkError,
    }),
  ).toBe(notFoundError);
  expect(
    getMissingThreadHistoryError({
      enabled: true,
      threadId: "deleted-thread",
      tombstoned: false,
      snapshotError: networkError,
      runsError: forbiddenError,
    }),
  ).toBe(forbiddenError);
  expect(
    getMissingThreadHistoryError({
      enabled: false,
      threadId: "deleted-thread",
      tombstoned: false,
      snapshotError: notFoundError,
    }),
  ).toBeNull();
  expect(
    getMissingThreadHistoryError({
      enabled: true,
      threadId: "deleted-thread",
      tombstoned: true,
      snapshotError: notFoundError,
    }),
  ).toBeNull();
  expect(
    getMissingThreadHistoryError({
      enabled: true,
      threadId: "deleted-thread",
      tombstoned: false,
      snapshotError: networkError,
    }),
  ).toBeNull();
});

test("missing thread cleanup stays scoped to the failed history request", () => {
  const hooksSource = readFileSync(
    resolve(__dirname, "../../../../src/core/threads/hooks.ts"),
    "utf-8",
  );
  const cleanupStart = hooksSource.indexOf(
    "const clearMissingThreadHistoryState = useCallback",
  );
  const cleanupEnd = hooksSource.indexOf(
    "\n\n  const loadMessages = useCallback",
    cleanupStart,
  );
  const cleanupSource = hooksSource.slice(cleanupStart, cleanupEnd);
  const loadMessagesStart = hooksSource.indexOf(
    "const loadMessages = useCallback",
  );
  const loadMessagesEnd = hooksSource.indexOf(
    "\n\n  useEffect(() => {",
    loadMessagesStart,
  );
  const loadMessagesSource = hooksSource.slice(
    loadMessagesStart,
    loadMessagesEnd,
  );

  expect(cleanupSource).toContain(
    "if (missingThreadId !== threadIdRef.current)",
  );
  expect(loadMessagesSource).toContain(
    "let requestThreadId = threadIdRef.current;",
  );
  expect(loadMessagesSource).toContain(
    "clearMissingThreadHistoryState(requestThreadId, err);",
  );
});

test("fallback history continues from a control-only latest run to older visible messages", async () => {
  const {
    buildVisibleHistoryMessages,
    findLatestUnloadedRunIndex,
    isVisibleHistoryRunMessage,
    shouldAutoContinueRunHistory,
  } = await import("@/core/threads/hooks");
  const runs = [
    { run_id: "latest-control-only" },
    { run_id: "older-visible" },
  ] as unknown as Run[];
  const pages = new Map<string, RunMessage[]>([
    [
      "latest-control-only",
      [
        {
          run_id: "latest-control-only",
          seq: 1,
          created_at: "2026-07-11T00:00:01Z",
          metadata: { caller: "lead_agent" },
          display: { visible_in_chat: false, reason: "task_event" },
          content: {
            schema_version: "deerflow.task-event/v1",
            event_type: "task_completed",
            task_id: "task-1",
            thread_id: "thread-1",
            run_id: "latest-control-only",
          },
        },
      ],
    ],
    [
      "older-visible",
      [
        makeRunMessage("older-visible", 1, {
          type: "ai",
          id: "older-answer",
          content: "restored older answer",
        } as Message),
      ],
    ],
  ]);
  const loadedRunIds = new Set<string>();
  const loadedRows: RunMessage[] = [];
  const visitedRunIds: string[] = [];
  let consecutiveEmptyLoads = 0;

  while (true) {
    const run = runs[findLatestUnloadedRunIndex(runs, loadedRunIds)];
    if (!run) {
      break;
    }
    visitedRunIds.push(run.run_id);
    const page = pages.get(run.run_id) ?? [];
    loadedRows.push(...page);
    loadedRunIds.add(run.run_id);
    const visibleMessageCount = page.filter(isVisibleHistoryRunMessage).length;
    if (
      !shouldAutoContinueRunHistory({
        hasMoreUnloadedRuns:
          findLatestUnloadedRunIndex(runs, loadedRunIds) !== -1,
        visibleMessageCount,
        consecutiveEmptyLoads,
      })
    ) {
      break;
    }
    consecutiveEmptyLoads =
      visibleMessageCount === 0 ? consecutiveEmptyLoads + 1 : 0;
  }

  expect(visitedRunIds).toEqual(["latest-control-only", "older-visible"]);
  expect(
    buildVisibleHistoryMessages(loadedRows, new Set(), [], runs).map(
      (message) => message.content,
    ),
  ).toEqual(["restored older answer"]);

  const hooksSource = readFileSync(
    resolve(__dirname, "../../../../src/core/threads/hooks.ts"),
    "utf-8",
  );
  const loadMessagesStart = hooksSource.indexOf(
    "const loadMessages = useCallback",
  );
  const loadMessagesEnd = hooksSource.indexOf(
    "\n\n  useEffect(() => {",
    loadMessagesStart,
  );
  const loadMessagesSource = hooksSource.slice(
    loadMessagesStart,
    loadMessagesEnd,
  );

  expect(loadMessagesSource).toMatch(
    /result\.data\.filter\(\s*isVisibleHistoryRunMessage,?\s*\)\.length/,
  );
  expect(loadMessagesSource).toContain("shouldAutoContinueRunHistory({");
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
  const { uploadListQueryKey } = await import("@/core/uploads/hooks");
  const { THREAD_MODEL_KEY_PREFIX } = await import("@/core/settings/local");
  const { getThreadModelSnapshot, updateThreadSettings } =
    await import("@/core/settings/store");

  updateThreadSettings("chat", threadId, "context", {
    model_name: "model-a",
  });
  updateThreadSettings("agent:command-room", threadId, "context", {
    model_name: "model-b",
  });
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
  client.setQueryData(uploadListQueryKey(threadId), ["stale-upload"]);
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
  client.setQueryData(uploadListQueryKey(otherThreadId), ["other-upload"]);
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
  expect(client.getQueryData(uploadListQueryKey(threadId))).toBeUndefined();
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
  expect(client.getQueryData(uploadListQueryKey(otherThreadId))).toEqual([
    "other-upload",
  ]);
  expect(
    client.getQueryData(["artifact", "report.md", otherThreadId, false]),
  ).toEqual({ content: "other" });
  expect(getManualThreadTitleLock(threadId)).toBeUndefined();
  expect(storage.getItem(`${THREAD_MODEL_KEY_PREFIX}${threadId}`)).toBeNull();
  expect(getThreadModelSnapshot("chat", threadId)).toBeUndefined();
  expect(
    getThreadModelSnapshot("agent:command-room", threadId),
  ).toBeUndefined();
  expect(clearSubtasksForThread).toHaveBeenCalledWith(threadId);
});

test("deleteThreadRemote treats direct 404 after SDK delete as idempotent", async () => {
  const threadId = "double-delete-thread";
  const sdkDelete = rs.fn(async () => undefined);
  const fetchDelete = rs.fn(
    async () => new Response("missing", { status: 404 }),
  );
  const onRemoteDeleted = rs.fn();
  const { deleteThreadRemote } = await loadThreadHooksWithRunAndFetch(
    async () => ({}),
    fetchDelete,
  );

  await expect(
    deleteThreadRemote({
      threadId,
      apiClient: {
        threads: {
          delete: sdkDelete,
        },
      } as never,
      onRemoteDeleted,
    }),
  ).resolves.toBeUndefined();

  expect(sdkDelete).toHaveBeenCalledWith(threadId);
  expect(fetchDelete).toHaveBeenCalledTimes(1);
  expect(onRemoteDeleted).toHaveBeenCalledTimes(1);
});

test("deleteThreadRemote reports local cleanup failures after remote deletion", async () => {
  const sdkDelete = rs.fn(async () => undefined);
  const fetchDelete = rs.fn(
    async () =>
      new Response(JSON.stringify({ detail: "Failed to delete thread runs" }), {
        status: 500,
        headers: { "Content-Type": "application/json" },
      }),
  );
  const onRemoteDeleted = rs.fn();
  const { deleteThreadRemote, getThreadDeleteFailureState } =
    await loadThreadHooksWithRunAndFetch(async () => ({}), fetchDelete);

  let failure: unknown;
  try {
    await deleteThreadRemote({
      threadId: "partially-deleted-thread",
      apiClient: { threads: { delete: sdkDelete } } as never,
      onRemoteDeleted,
    });
  } catch (error) {
    failure = error;
  }

  expect(failure).toMatchObject({
    name: "ThreadDeleteError",
    phase: "local",
    message: "Failed to delete thread runs",
  });
  expect(getThreadDeleteFailureState(failure)).toBe("partial");
  expect(onRemoteDeleted).toHaveBeenCalledTimes(1);
});

test("deleteThreadRemote identifies a deleting thread tombstone", async () => {
  const sdkDelete = rs.fn(async () => undefined);
  const fetchDelete = rs.fn(
    async () =>
      new Response(JSON.stringify({ detail: "Thread is being deleted" }), {
        status: 409,
        headers: { "Content-Type": "application/json" },
      }),
  );
  const onRemoteDeleted = rs.fn();
  const { deleteThreadRemote, getThreadDeleteFailureState } =
    await loadThreadHooksWithRunAndFetch(async () => ({}), fetchDelete);

  let failure: unknown;
  try {
    await deleteThreadRemote({
      threadId: "deleting-thread",
      apiClient: { threads: { delete: sdkDelete } } as never,
      onRemoteDeleted,
    });
  } catch (error) {
    failure = error;
  }

  expect(failure).toMatchObject({
    name: "ThreadDeleteError",
    phase: "local",
    message: "Thread is being deleted",
  });
  expect(getThreadDeleteFailureState(failure)).toBe("deleting");
  expect(onRemoteDeleted).toHaveBeenCalledTimes(1);
});

test("deleteThreadRemote leaves client state alone when conversation deletion fails", async () => {
  const sdkDelete = rs.fn(async () => {
    throw new Error("offline");
  });
  const fetchDelete = rs.fn(async () => new Response(null, { status: 204 }));
  const onRemoteDeleted = rs.fn();
  const { deleteThreadRemote, getThreadDeleteFailureState } =
    await loadThreadHooksWithRunAndFetch(async () => ({}), fetchDelete);

  let failure: unknown;
  try {
    await deleteThreadRemote({
      threadId: "offline-thread",
      apiClient: { threads: { delete: sdkDelete } } as never,
      onRemoteDeleted,
    });
  } catch (error) {
    failure = error;
  }

  expect(failure).toMatchObject({
    name: "ThreadDeleteError",
    phase: "remote",
    message: "offline",
  });
  expect(getThreadDeleteFailureState(failure)).toBe("failed");
  expect(fetchDelete).not.toHaveBeenCalled();
  expect(onRemoteDeleted).not.toHaveBeenCalled();
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

test("retry recovery reloads the exhausted run and lets its terminal task event replace unknown", async () => {
  rs.useFakeTimers();
  stubBrowserWindow();
  const threadId = "recovery-thread";
  const runId = "recovery-run";
  const taskId = "recovery-task";
  let attempts = 0;
  let terminalAvailable = false;
  const getRun = rs.fn(async () => {
    attempts += 1;
    if (attempts <= 11) {
      throw new Error("stream disconnected");
    }
    terminalAvailable = true;
    return { run_id: runId, status: "success" };
  });
  const { retryBackgroundRunRecovery, startBackgroundRunProbe } =
    await loadThreadHooksWithRunProbe(getRun);
  let tasks: Record<string, Subtask> = {};
  const updateSubtask = (update: SubtaskUpdate) => {
    tasks = applySubtaskUpdateInState(tasks, update);
  };

  expect(
    applyTaskEventToSubtask(
      {
        event_type: "task_started",
        task_id: taskId,
        thread_id: threadId,
        run_id: runId,
        description: "recover this task",
        subagent_type: "executor",
        prompt: "recover",
      },
      updateSubtask,
    ),
  ).toBe(true);

  startBackgroundRunProbe({
    queryClient: new QueryClient(),
    threadId,
    runId,
    settleRunSubtasks: (terminal) => {
      tasks = settleRunningSubtasksForRun(tasks, terminal);
    },
  });
  await rs.advanceTimersByTimeAsync(310_000);

  expect(getRun).toHaveBeenCalledTimes(11);
  expect(Object.values(tasks)).toContainEqual(
    expect.objectContaining({
      id: taskId,
      status: "unknown",
      actionResultStatus: "recovery_failed",
      terminalReason: "recovery_exhausted",
    }),
  );

  const refreshRuns = rs.fn(() => {
    if (!terminalAvailable) {
      return;
    }
    applyTaskEventToSubtask(
      {
        event_type: "task_completed",
        task_id: taskId,
        thread_id: threadId,
        run_id: runId,
        result: "completed after retry",
      },
      updateSubtask,
    );
  });
  await retryBackgroundRunRecovery({
    queryClient: new QueryClient(),
    threadId,
    runId,
    refreshRuns,
    settleRunSubtasks: (terminal) => {
      tasks = settleRunningSubtasksForRun(tasks, terminal);
    },
  });

  expect(getRun).toHaveBeenCalledTimes(12);
  expect(getRun).toHaveBeenLastCalledWith(threadId, runId);
  expect(refreshRuns).toHaveBeenLastCalledWith({
    threadId,
    runIds: [runId],
  });
  expect(Object.values(tasks)).toContainEqual(
    expect.objectContaining({
      id: taskId,
      status: "completed",
      result: "completed after retry",
    }),
  );
});

test("retry recovery deduplicates concurrent target-run syncs", async () => {
  stubBrowserWindow();
  const threadId = "dedupe-thread";
  const runId = "dedupe-run";
  let resolveRun!: (run: { run_id: string; status: string }) => void;
  const run = new Promise<{ run_id: string; status: string }>((resolve) => {
    resolveRun = resolve;
  });
  const getRun = rs.fn(async () => run);
  const { retryBackgroundRunRecovery } =
    await loadThreadHooksWithRunProbe(getRun);
  const refreshRuns = rs.fn();
  const first = retryBackgroundRunRecovery({
    queryClient: new QueryClient(),
    threadId,
    runId,
    refreshRuns,
  });
  const second = retryBackgroundRunRecovery({
    queryClient: new QueryClient(),
    threadId,
    runId,
    refreshRuns,
  });

  expect(second).toBe(first);
  expect(getRun).toHaveBeenCalledTimes(1);
  resolveRun({ run_id: runId, status: "success" });
  await first;
  expect(getRun).toHaveBeenLastCalledWith(threadId, runId);
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

test("applyTaskEventToSubtask preserves event identity across conversation switches", async () => {
  const { applyTaskEventToSubtask } = await import("@/core/threads/hooks");
  const updates: SubtaskUpdate[] = [];

  expect(
    applyTaskEventToSubtask(
      {
        type: "task_running",
        task_id: "task-1",
        thread_id: "event-thread",
        run_id: "event-run",
        round_id: "event-round",
      },
      (task) => updates.push(task),
      "current-view-or-stream-thread",
    ),
  ).toBe(true);

  expect(updates).toEqual([
    {
      id: "task-1",
      threadId: "event-thread",
      runId: "event-run",
      roundId: "event-round",
      notify: true,
      status: "in_progress",
    },
  ]);
});

test("applyTaskEventToSubtask resolves roundId with stable payload priority", async () => {
  const { applyTaskEventToSubtask } = await import("@/core/threads/hooks");
  const updates: SubtaskUpdate[] = [];

  expect(
    applyTaskEventToSubtask(
      {
        event_type: "task_started",
        task_id: "task-1",
        thread_id: "thread-1",
        run_id: "run-1",
        round_id: "top-level-round",
        roundId: "camel-round",
        content: { round_id: "content-round" },
        metadata: { round_id: "metadata-round" },
      },
      (task) => updates.push(task),
    ),
  ).toBe(true);
  expect(updates[0]).toMatchObject({
    id: "task-1",
    threadId: "thread-1",
    runId: "run-1",
    roundId: "metadata-round",
  });

  updates.length = 0;
  expect(
    applyTaskEventToSubtask(
      {
        event_type: "task_started",
        task_id: "task-1",
        thread_id: "thread-1",
        run_id: "run-1",
        round_id: "top-level-round",
        roundId: "camel-round",
        content: { round_id: "content-round" },
      },
      (task) => updates.push(task),
    ),
  ).toBe(true);
  expect(updates[0]).toMatchObject({ roundId: "content-round" });

  updates.length = 0;
  expect(
    applyTaskEventToSubtask(
      {
        event_type: "task_started",
        task_id: "task-1",
        thread_id: "thread-1",
        run_id: "run-1",
        round_id: "top-level-round",
        roundId: "camel-round",
      },
      (task) => updates.push(task),
    ),
  ).toBe(true);
  expect(updates[0]).toMatchObject({ roundId: "top-level-round" });

  updates.length = 0;
  expect(
    applyTaskEventToSubtask(
      {
        event_type: "task_started",
        task_id: "task-1",
        thread_id: "thread-1",
        run_id: "run-1",
        roundId: "camel-round",
      },
      (task) => updates.push(task),
    ),
  ).toBe(true);
  expect(updates[0]).toMatchObject({ roundId: "camel-round" });
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
    round_id: "round-1",
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
    applySubtaskUpdateInState,
    getSubtaskStorageKey,
    settleRunningSubtasksForRun,
  } = await import("@/core/tasks/context");
  const { applyTaskEventToSubtask } = await import("@/core/threads/hooks");
  let tasks: Record<string, Subtask> = {};
  const update = (task: SubtaskUpdate) => {
    tasks = applySubtaskUpdateInState(tasks, task);
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
  const { applySubtaskUpdateInState, getSubtaskStorageKey } =
    await import("@/core/tasks/context");
  const { applyTaskEventToSubtask } = await import("@/core/threads/hooks");
  let tasks: Record<string, Subtask> = {};
  const update = (task: SubtaskUpdate) => {
    tasks = applySubtaskUpdateInState(tasks, task);
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

test("task events update only their own round-scoped subtask", async () => {
  const { applySubtaskUpdateInState, getSubtaskStorageKey } =
    await import("@/core/tasks/context");
  const { applyTaskEventToSubtask } = await import("@/core/threads/hooks");
  let tasks: Record<string, Subtask> = {};
  const update = (task: SubtaskUpdate) => {
    tasks = applySubtaskUpdateInState(tasks, task);
  };
  const roundAKey = getSubtaskStorageKey({
    id: "shared-task",
    threadId: "thread-1",
    runId: "run-1",
    roundId: "round-a",
  });
  const roundBKey = getSubtaskStorageKey({
    id: "shared-task",
    threadId: "thread-1",
    runId: "run-1",
    roundId: "round-b",
  });

  expect(
    applyTaskEventToSubtask(
      {
        event_type: "task_started",
        schema_version: TASK_EVENT_CONTRACT.schema_version,
        task_id: "shared-task",
        thread_id: "thread-1",
        run_id: "run-1",
        round_id: "round-b",
        status: "in_progress",
        description: "round B task",
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
        run_id: "run-1",
        round_id: "round-a",
        status: "completed",
        result_preview: "round A done",
      },
      update,
    ),
  ).toBe(true);

  expect(tasks[roundAKey]).toMatchObject({
    runId: "run-1",
    roundId: "round-a",
    status: "completed",
    result: "round A done",
  });
  expect(tasks[roundBKey]).toMatchObject({
    runId: "run-1",
    roundId: "round-b",
    status: "in_progress",
    description: "round B task",
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

test("applyTaskEventRunMessages keeps same task id in different rounds distinct without seq", async () => {
  const { applyTaskEventRunMessages } = await import("@/core/threads/hooks");
  const updates: unknown[] = [];
  const applied = new Set<string>();
  const baseMessage = {
    run_id: "run-legacy",
    created_at: "2024-01-01T00:00:00.000Z",
    metadata: { caller: "task_event" },
  };

  applyTaskEventRunMessages(
    [
      {
        ...baseMessage,
        content: {
          event_type: "task_completed",
          schema_version: TASK_EVENT_CONTRACT.schema_version,
          task_id: "task-legacy",
          thread_id: "thread-1",
          run_id: "run-legacy",
          round_id: "round-a",
          result_preview: "round A",
        },
      },
      {
        ...baseMessage,
        content: {
          event_type: "task_completed",
          schema_version: TASK_EVENT_CONTRACT.schema_version,
          task_id: "task-legacy",
          thread_id: "thread-1",
          run_id: "run-legacy",
          round_id: "round-b",
          result_preview: "round B",
        },
      },
    ] as never,
    (task) => updates.push(task),
    "thread-1",
    applied,
  );

  expect(updates).toEqual([
    expect.objectContaining({
      id: "task-legacy",
      roundId: "round-a",
      result: "round A",
    }),
    expect.objectContaining({
      id: "task-legacy",
      roundId: "round-b",
      result: "round B",
    }),
  ]);
  expect([...applied].sort()).toEqual([
    "run-legacy:thread-1:round-a:task-legacy:task_completed:2024-01-01T00:00:00.000Z",
    "run-legacy:thread-1:round-b:task-legacy:task_completed:2024-01-01T00:00:00.000Z",
  ]);
});

test("applyTaskEventRunMessages deterministically replays five runs of six task updates", async () => {
  const { applyTaskEventRunMessages } = await import("@/core/threads/hooks");
  const updates: SubtaskUpdate[] = [];
  const applied = new Set<string>();
  const messages = Array.from({ length: 5 }, (_, runIndex) =>
    Array.from({ length: 6 }, (_, taskIndex) => ({
      run_id: `run-${runIndex}`,
      seq: taskIndex + 1,
      created_at: `2024-01-01T00:${String(runIndex).padStart(2, "0")}:${String(taskIndex).padStart(2, "0")}.000Z`,
      metadata: { caller: "task_event" },
      content: {
        event_type: "task_running",
        schema_version: TASK_EVENT_CONTRACT.schema_version,
        task_id: `task-${taskIndex}`,
        thread_id: `thread-${runIndex}`,
        run_id: `run-${runIndex}`,
        description: `run ${runIndex} task ${taskIndex}`,
      },
    })),
  ).flat();

  for (let runIndex = 0; runIndex < 5; runIndex += 1) {
    applyTaskEventRunMessages(
      messages as never,
      (task) => updates.push(task),
      `thread-${runIndex}`,
      applied,
    );
  }

  const identities = updates.map(
    (update) => `${update.threadId}:${update.runId}:${update.id}`,
  );

  expect(updates).toHaveLength(30);
  expect(new Set(identities).size).toBe(30);
  expect(new Set(identities)).toEqual(
    new Set(
      Array.from({ length: 5 }, (_, runIndex) =>
        Array.from(
          { length: 6 },
          (_, taskIndex) =>
            `thread-${runIndex}:run-${runIndex}:task-${taskIndex}`,
        ),
      ).flat(),
    ),
  );
  expect(applied.size).toBe(30);
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

test("stream recovery required backfills messages from runtime snapshot", async () => {
  const {
    applySnapshotRunMessagePageState,
    buildVisibleHistoryMessages,
    getVisibleThreadError,
    threadRuntimeSnapshotQueryKey,
  } = await import("@/core/threads/hooks");
  const client = new QueryClient();
  const threadId = "snapshot-backfill-thread";
  const runId = "snapshot-backfill-run";
  const snapshotMessages = [
    makeRunMessage(runId, 1, {
      type: "human",
      id: "snapshot-backfill-human",
      content: [{ type: "text", text: "recover my visible prompt" }],
    } as Message),
    makeRunMessage(runId, 2, {
      type: "ai",
      id: "snapshot-backfill-ai",
      content: "Recovered visible answer from runtime snapshot",
    } as Message),
  ];
  const loadedRunIds = new Set<string>();
  const runBeforeSeq = new Map<string, number>();

  client.setQueryData(threadRuntimeSnapshotQueryKey(threadId), {
    thread_id: threadId,
    runs: [{ run_id: runId, status: "success" }],
    run_messages: [{ run_id: runId, data: snapshotMessages, hasMore: false }],
  });
  applySnapshotRunMessagePageState(
    [{ run_id: runId, data: snapshotMessages, hasMore: false }],
    loadedRunIds,
    runBeforeSeq,
  );

  expect(loadedRunIds.has(runId)).toBe(true);
  expect(runBeforeSeq.has(runId)).toBe(false);
  expect(
    buildVisibleHistoryMessages(
      snapshotMessages,
      new Set(),
      [],
      [{ run_id: runId, status: "success" } as unknown as Run],
    ).map((message) => message.content),
  ).toEqual([
    [{ type: "text", text: "recover my visible prompt" }],
    "Recovered visible answer from runtime snapshot",
  ]);
  expect(
    getVisibleThreadError(
      new Error("transient recovery transport error"),
      true,
    ),
  ).toBeUndefined();
});

test("late terminal event after local cancellation settle is idempotent and keeps thread settled", async () => {
  const {
    finishLocalRunCancellation,
    getThreadActivitySnapshot,
    reconcileTerminalRunHistory,
    threadRunsQueryKey,
    threadRuntimeSnapshotQueryKey,
  } = await import("@/core/threads/hooks");
  const client = new QueryClient();
  const threadId = "late-cancel-thread";
  const runId = "late-cancel-run";
  const settled: unknown[] = [];
  const refreshed: unknown[] = [];
  client.setQueryData(threadRunsQueryKey(threadId), "cached-runs");
  client.setQueryData(threadRuntimeSnapshotQueryKey(threadId), "snapshot");

  expect(
    finishLocalRunCancellation({
      queryClient: client,
      threadId,
      runId,
      settleRunSubtasks: (terminal) => settled.push(terminal),
    }),
  ).toEqual({ threadId, runId });

  expect(
    reconcileTerminalRunHistory(
      client,
      {
        type: "run.terminal",
        event_type: "run.terminal",
        thread_id: threadId,
        run_id: runId,
        status: "interrupted",
        terminal_reason: "user_cancelled",
      },
      (params) => refreshed.push(params),
    ),
  ).toBe(true);

  expect(getThreadActivitySnapshot().running.has(threadId)).toBe(false);
  expect(settled).toEqual([
    {
      threadId,
      runId,
      status: "interrupted",
      terminalReason: "user_cancelled",
    },
  ]);
  expect(refreshed).toEqual([{ threadId, runIds: [runId] }]);
  expect(
    client.getQueryState(threadRunsQueryKey(threadId))?.isInvalidated,
  ).toBe(true);
  expect(
    client.getQueryState(threadRuntimeSnapshotQueryKey(threadId))
      ?.isInvalidated,
  ).toBe(true);
});

test("local cancellation settle only clears the matching same-thread run", async () => {
  const {
    clearThreadActivity,
    finishLocalRunCancellation,
    getThreadActivitySnapshot,
    markThreadBusyInCaches,
  } = await import("@/core/threads/hooks");
  const client = new QueryClient();
  const threadId = "same-thread-cancel-thread";
  const oldRunId = "same-thread-cancel-old";
  const newRunId = "same-thread-cancel-new";

  markThreadBusyInCaches(client, threadId, { runId: oldRunId });
  markThreadBusyInCaches(client, threadId, { runId: newRunId });

  expect(
    finishLocalRunCancellation({
      queryClient: client,
      threadId,
      runId: oldRunId,
    }),
  ).toEqual({ threadId, runId: oldRunId });

  expect(getThreadActivitySnapshot().running.has(threadId)).toBe(true);
  expect(getThreadActivitySnapshot().finished.has(threadId)).toBe(false);

  expect(
    finishLocalRunCancellation({
      queryClient: client,
      threadId,
      runId: newRunId,
    }),
  ).toEqual({ threadId, runId: newRunId });

  expect(getThreadActivitySnapshot().running.has(threadId)).toBe(false);
  clearThreadActivity(threadId);
});

test("taskLaneSubtaskUpdate maps completed lane result and duration_ms safely", async () => {
  const { taskLaneSubtaskUpdate } = await import("@/core/threads/hooks");
  const base = {
    thread_id: "thread-lane",
    run_id: "run-lane",
    round_id: "round-lane",
    task_id: "task-lane",
    role: "researcher",
    status: "completed",
    completed_at: "2026-01-01T00:00:01.000Z",
  };

  expect(
    taskLaneSubtaskUpdate({
      ...base,
      result: "COMPLETED_TASK_RESULT",
      result_ref: "result-ref",
      evidence_ref: "evidence-ref",
      duration_ms: 1234,
    }),
  ).toMatchObject({
    id: "task-lane",
    threadId: "thread-lane",
    runId: "run-lane",
    roundId: "round-lane",
    status: "completed",
    result: "COMPLETED_TASK_RESULT",
    durationMs: 1234,
    metadata: {
      refs: {
        result_ref: "result-ref",
        evidence_ref: "evidence-ref",
      },
    },
  });

  expect(
    taskLaneSubtaskUpdate({ ...base, duration_ms: "1234" }),
  ).not.toHaveProperty("durationMs");
});

test("terminal task recovery ignores record timestamps without task timing", async () => {
  const { applyTaskEventToSubtask, taskLaneSubtaskUpdate } =
    await import("@/core/threads/hooks");
  const laneUpdate = taskLaneSubtaskUpdate({
    thread_id: "thread-lane",
    run_id: "run-lane",
    task_id: "task-lane",
    role: "researcher",
    status: "completed",
    created_at: "2026-01-01T00:00:00.000Z",
    updated_at: "2026-07-11T00:00:00.000Z",
  });
  expect(laneUpdate).not.toHaveProperty("startedAt");
  expect(laneUpdate).not.toHaveProperty("finishedAt");

  const updates: unknown[] = [];
  applyTaskEventToSubtask(
    {
      schema_version: "deerflow.task-event/v1",
      event_type: "task_completed",
      task_id: "task-event",
      thread_id: "thread-event",
      run_id: "run-event",
      status: "completed",
    },
    (task) => updates.push(task),
    undefined,
    Date.parse("2026-07-11T00:00:00.000Z"),
  );
  expect(updates[0]).not.toHaveProperty("finishedAt");
});

test("applyTaskEventToSubtask maps valid duration_ms and ignores invalid duration_ms", async () => {
  const { applyTaskEventToSubtask } = await import("@/core/threads/hooks");
  const updates: unknown[] = [];
  const base = {
    schema_version: "deerflow.task-event/v1",
    event_type: "task_completed",
    task_id: "task-duration",
    thread_id: "thread-duration",
    run_id: "run-duration",
    status: "completed",
    completed_at: "2026-01-01T00:00:01.000Z",
  };

  expect(
    applyTaskEventToSubtask({ ...base, duration_ms: 1234 }, (task) =>
      updates.push(task),
    ),
  ).toBe(true);
  expect(updates[0]).toMatchObject({ durationMs: 1234 });

  for (const duration_ms of [null, -1, Number.NaN, Infinity, "1234"]) {
    updates.length = 0;
    expect(
      applyTaskEventToSubtask({ ...base, duration_ms }, (task) =>
        updates.push(task),
      ),
    ).toBe(true);
    expect(updates[0]).not.toHaveProperty("durationMs");
  }
});

test("taskLaneSubtaskUpdate preserves lane metadata over fallbacks", async () => {
  const { taskLaneSubtaskUpdate } = await import("@/core/threads/hooks");

  const update = taskLaneSubtaskUpdate({
    thread_id: "thread-1",
    run_id: "run-1",
    round_id: "round-1",
    task_id: "task-1",
    role: "researcher",
    status: "completed",
    description: "Search the docs",
    prompt: "Find runtime replay behavior",
    subagent_type: "browser",
    result: "done",
  });

  expect(update.description).toBe("Search the docs");
  expect(update.prompt).toBe("Find runtime replay behavior");
  expect(update.subagent_type).toBe("browser");
  expect(update.result).toBe("done");
});

test("mergeSubtaskUpdate keeps tool metadata while accepting lane terminal result", async () => {
  const { mergeSubtaskUpdate } = await import("@/core/tasks/context");

  const previous = mergeSubtaskUpdate(undefined, {
    id: "task-1",
    threadId: "thread-1",
    runId: "run-1",
    status: "in_progress",
    subagent_type: "browser",
    description: "Tool description",
    prompt: "Tool prompt",
  });

  const merged = mergeSubtaskUpdate(previous, {
    id: "task-1",
    threadId: "thread-1",
    runId: "run-1",
    status: "completed",
    subagent_type: "researcher",
    description: "researcher task",
    prompt: "researcher task",
    result: "lane result",
  });

  expect(merged.status).toBe("completed");
  expect(merged.result).toBe("lane result");
  expect(merged.description).toBe("Tool description");
  expect(merged.prompt).toBe("Tool prompt");
  expect(merged.subagent_type).toBe("browser");
});

test("mergeSubtaskUpdate does not let stale replay reopen terminal subtasks", async () => {
  const { mergeSubtaskUpdate } = await import("@/core/tasks/context");

  const previous = mergeSubtaskUpdate(undefined, {
    id: "task-1",
    status: "completed",
    subagent_type: "browser",
    description: "Tool description",
    prompt: "Tool prompt",
    result: "final result",
  });

  const merged = mergeSubtaskUpdate(previous, {
    id: "task-1",
    status: "in_progress",
    subagent_type: "browser",
    description: "Tool description",
    prompt: "Tool prompt",
  });

  expect(merged.status).toBe("completed");
  expect(merged.result).toBe("final result");
});
