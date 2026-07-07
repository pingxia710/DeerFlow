import type { Message, Run } from "@langchain/langgraph-sdk";
import { expect, test } from "@rstest/core";

import { applySubtaskUpdateInState } from "@/core/tasks/context";
import {
  applyNativeRoundsToSnapshotRuns,
  applySnapshotRunMessagePageState,
  applyTaskEventRunMessages,
  buildRunMessagesUrl,
  buildThreadRuntimeSnapshotUrl,
  buildVisibleHistoryMessages,
  completeOptimisticUploadMessages,
  findLatestUnloadedRunIndex,
  getNextRunMessagesBeforeSeq,
  getOldestRunMessageSeq,
  getLatestRunTerminalNotice,
  getTerminalTransitionRunIds,
  getSupersededRunIds,
  getSummarizationMiddlewareMessages,
  getVisibleOptimisticMessages,
  HISTORY_CREATED_AT_KEY,
  isAbortError,
  isTaskEventRunMessage,
  isTaskEventRunMessageForRequest,
  isVisibleHistoryRunMessage,
  latestRoundIdFromSnapshot,
  MAX_CONSECUTIVE_EMPTY_RUN_LOADS,
  mergeFetchedRunMessages,
  mergeMessages,
  partitionKnownRunIds,
  readThreadRuntimeSnapshotResponse,
  readRunMessagesPageResponse,
  resetLoadedRunStateForRefresh,
  removeSetItems,
  resolveThreadHistoryReset,
  roundIdOfRun,
  runMessagesPageHasMore,
  shouldAutoContinueOnEmptyRun,
  shouldAutoContinueRunHistory,
  shouldAutoLoadLatestRun,
  shouldReleaseQueuedThreadMessage,
  shouldShowLiveThreadState,
  shouldShowThreadHistory,
  taskLanesForLatestRound,
  taskLaneSubtaskUpdate,
  taskEventRunMessageKey,
  threadRuntimeSnapshotQueryKey,
  threadRunsQueryKey,
} from "@/core/threads/hooks";
import type { RunMessage } from "@/core/threads/types";

function runMessage(seq?: number): RunMessage {
  return {
    run_id: "run-1",
    ...(seq === undefined ? {} : { seq }),
    content: {
      id: seq === undefined ? "message" : `message-${seq}`,
      type: "ai",
      content: "message",
    } as Message,
    metadata: { caller: "" },
    created_at: "2026-05-22T00:00:00Z",
  };
}

test("mergeMessages removes duplicate messages already present in history", () => {
  const human = {
    id: "human-1",
    type: "human",
    content: "Design an agent",
  } as Message;
  const ai = {
    id: "ai-1",
    type: "ai",
    content: "Let's design it.",
  } as Message;

  expect(mergeMessages([human, ai, human, ai], [], [])).toEqual([human, ai]);
});

test("threadRunsQueryKey keeps run lists separate from thread metadata", () => {
  expect(threadRunsQueryKey("thread-a")).toEqual([
    "thread",
    "thread-a",
    "runs",
  ]);
});

test("threadRuntimeSnapshotQueryKey keeps snapshot cache separate from run lists", () => {
  expect(threadRuntimeSnapshotQueryKey("thread-a")).toEqual([
    "thread",
    "thread-a",
    "runtime-snapshot",
  ]);
});

test("native runtime snapshot closed round does not imply run success", () => {
  const restored = applyNativeRoundsToSnapshotRuns(
    [
      { run_id: "run-closed", status: "running" },
      { run_id: "run-blocked", status: "pending" },
      { run_id: "run-waiting", status: "running" },
    ] as unknown as Run[],
    [
      {
        thread_id: "thread-1",
        round_id: "round-closed",
        current_run_id: "run-closed",
        state: "closed",
      },
      {
        thread_id: "thread-1",
        round_id: "round-blocked",
        current_run_id: "run-blocked",
        state: "blocked",
      },
      {
        thread_id: "thread-1",
        round_id: "round-waiting",
        current_run_id: "run-waiting",
        state: "waiting_user",
      },
    ],
  );

  expect(restored?.map((run) => run.status)).toEqual([
    "running",
    "error",
    "interrupted",
  ]);
  expect(restored?.map((run) => roundIdOfRun(run))).toEqual([
    "round-closed",
    "round-blocked",
    "round-waiting",
  ]);
  expect(latestRoundIdFromSnapshot(restored, undefined)).toBe("round-closed");
});

test("runtime snapshot applies task lanes only for the latest native round", () => {
  const runs = [
    { run_id: "run-new", status: "running" },
    { run_id: "run-old", status: "success" },
  ] as unknown as Run[];
  const rounds = [
    {
      thread_id: "thread-1",
      round_id: "round-new",
      current_run_id: "run-new",
      state: "executing",
    },
    {
      thread_id: "thread-1",
      round_id: "round-old",
      current_run_id: "run-old",
      state: "closed",
    },
  ];

  const restored = applyNativeRoundsToSnapshotRuns(runs, rounds);
  const latestRoundId = latestRoundIdFromSnapshot(restored, rounds);
  const lanes = taskLanesForLatestRound(
    [
      {
        thread_id: "thread-1",
        run_id: "run-old",
        round_id: "round-old",
        task_id: "task-old",
        status: "completed",
      },
      {
        thread_id: "thread-1",
        run_id: "run-new",
        round_id: "round-new",
        task_id: "task-new",
        status: "in_progress",
        evidence_refs: ["ev-1"],
        artifact_refs: ["art-1"],
        output_refs: ["out-1"],
        handoff: { target_role: "evidence" },
      },
    ],
    latestRoundId,
  );
  const update = taskLaneSubtaskUpdate(lanes[0]!);

  expect(latestRoundId).toBe("round-new");
  expect(restored?.[0]?.status).toBe("running");
  expect(lanes.map((lane) => lane.task_id)).toEqual(["task-new"]);
  expect(update.metadata?.refs).toEqual({
    evidence_refs: ["ev-1"],
    artifact_refs: ["art-1"],
    output_refs: ["out-1"],
    handoff: { target_role: "evidence" },
  });
  expect(update.details?.refs).toEqual(update.metadata?.refs);
});

test("runtime snapshot recovery telemetry is additive", async () => {
  const snapshot = await readThreadRuntimeSnapshotResponse(
    new Response(
      JSON.stringify({
        thread_id: "thread-1",
        runs: [{ run_id: "run-1", status: "running" }],
        rounds: [
          {
            thread_id: "thread-1",
            round_id: "round-1",
            current_run_id: "run-1",
            state: "closed",
          },
        ],
        run_messages: [],
        task_lanes: [],
        recovery: {
          stale_inflight: {
            recovered: true,
            recovered_count: 1,
            run_ids: ["run-1"],
            terminal_reason: "worker_lost",
            runs: [{ run_id: "run-1", terminal_reason: "worker_lost" }],
          },
          snapshot_self_heal: {
            repaired: true,
            round_count: 1,
            task_lane_count: 1,
            rounds: [{ run_id: "run-1", round_id: "round-1", state: "closed" }],
            task_lanes: [
              {
                run_id: "run-1",
                round_id: "round-1",
                task_id: "task-1",
                status: "completed",
              },
            ],
          },
        },
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    ),
  );

  const restored = applyNativeRoundsToSnapshotRuns(
    snapshot.runs,
    snapshot.rounds,
  );

  expect(snapshot.recovery?.stale_inflight?.run_ids).toEqual(["run-1"]);
  expect(snapshot.recovery?.snapshot_self_heal?.repaired).toBe(true);
  expect(snapshot.recovery?.snapshot_self_heal?.round_count).toBe(1);
  expect(snapshot.recovery?.snapshot_self_heal?.task_lanes?.[0]?.task_id).toBe(
    "task-1",
  );
  expect(restored?.[0]?.status).toBe("running");
});

test("thread switching gates live state, history, and queued release by visible thread", () => {
  expect(shouldShowLiveThreadState("thread-b", "thread-a", "thread-a")).toBe(
    false,
  );
  expect(shouldShowThreadHistory("thread-b", "thread-a")).toBe(false);
  expect(
    shouldReleaseQueuedThreadMessage({
      streamFinished: true,
      sendInFlight: false,
      recovering: false,
      queuedThreadId: "thread-a",
      currentViewThreadId: "thread-b",
    }),
  ).toBe(false);
});

test("round id changes revalidate thread history without clearing visible rows", () => {
  const oldRow = {
    run_id: "run-1",
    seq: 1,
    content: {
      id: "old-ai",
      type: "ai",
      content: "old round",
    } as Message,
    metadata: { caller: "lead_agent" },
    created_at: "2026-05-22T00:00:00Z",
  } as RunMessage;
  const newRow = {
    run_id: "run-2",
    seq: 1,
    content: {
      id: "new-ai",
      type: "ai",
      content: "new round",
    } as Message,
    metadata: { caller: "lead_agent" },
    created_at: "2026-05-22T00:01:00Z",
  } as RunMessage;
  const appended = {
    id: "appended-ai",
    type: "ai",
    content: "streamed while history reloads",
  } as Message;

  expect(
    resolveThreadHistoryReset({
      enabled: true,
      threadChanged: false,
      previousRoundId: "round-1",
      latestRoundId: "round-2",
    }),
  ).toBe("revalidate");

  const mergedRows = mergeFetchedRunMessages([oldRow], [newRow], "run-2", true);

  expect(mergedRows.map((message) => message.run_id)).toEqual([
    "run-1",
    "run-2",
  ]);
  expect(
    buildVisibleHistoryMessages(
      mergedRows,
      new Set(),
      [appended],
      [{ run_id: "run-2", round_id: "round-2" } as unknown as Run],
    ).map((message) => message.id),
  ).toEqual(["new-ai", "old-ai", "appended-ai"]);
});

test("thread id or disabled history still clears thread history state", () => {
  expect(
    resolveThreadHistoryReset({
      enabled: true,
      threadChanged: true,
      previousRoundId: "round-1",
      latestRoundId: "round-2",
    }),
  ).toBe("clear");
  expect(
    resolveThreadHistoryReset({
      enabled: false,
      threadChanged: false,
      previousRoundId: "round-1",
      latestRoundId: "round-2",
    }),
  ).toBe("clear");
});

test("roundIdOfRun reads round ids from run responses", () => {
  expect(
    roundIdOfRun({ run_id: "run-1", round_id: "round-1" } as unknown as Run),
  ).toBe("round-1");
  expect(
    roundIdOfRun({
      run_id: "run-2",
      metadata: { round_id: "round-2" },
    } as unknown as Run),
  ).toBe("round-2");
});

test("mergeMessages lets live thread messages replace overlapping history", () => {
  const oldHuman = {
    id: "human-1",
    type: "human",
    content: "old",
  } as Message;
  const liveHuman = {
    id: "human-1",
    type: "human",
    content: "live",
  } as Message;
  const oldAi = {
    id: "ai-1",
    type: "ai",
    content: "old",
  } as Message;
  const liveAi = {
    id: "ai-1",
    type: "ai",
    content: "live",
  } as Message;

  expect(mergeMessages([oldHuman, oldAi], [liveHuman, liveAi], [])).toEqual([
    liveHuman,
    liveAi,
  ]);
});

test("mergeMessages deduplicates tool messages by tool_call_id", () => {
  const oldTool = {
    id: "tool-message-old",
    type: "tool",
    tool_call_id: "call-1",
    content: "old",
  } as Message;
  const liveTool = {
    id: "tool-message-live",
    type: "tool",
    tool_call_id: "call-1",
    content: "live",
  } as Message;

  expect(mergeMessages([oldTool], [liveTool], [])).toEqual([liveTool]);
});

test("mergeMessages keeps a visible history message when a hidden live message reuses its id", () => {
  const historyHuman = {
    id: "human-1",
    type: "human",
    content: "visible user prompt",
  } as Message;
  const hiddenReminder = {
    id: "human-1",
    type: "human",
    content: "<system-reminder>hidden</system-reminder>",
    additional_kwargs: { hide_from_ui: true },
  } as Message;
  const liveAi = {
    id: "ai-1",
    type: "ai",
    content: "live answer",
  } as Message;

  expect(mergeMessages([historyHuman], [hiddenReminder, liveAi], [])).toEqual([
    historyHuman,
    liveAi,
  ]);
});

test("mergeMessages lets a visible live message replace overlapping hidden history", () => {
  const hiddenHistoryHuman = {
    id: "human-1",
    type: "human",
    content: "<system-reminder>hidden</system-reminder>",
    additional_kwargs: { hide_from_ui: true },
  } as Message;
  const liveHuman = {
    id: "human-1",
    type: "human",
    content: "visible user prompt",
  } as Message;

  expect(mergeMessages([hiddenHistoryHuman], [liveHuman], [])).toEqual([
    liveHuman,
  ]);
});

test("getSummarizationMiddlewareMessages matches DeerFlow summarization update keys", () => {
  const removeAll = {
    id: "__remove_all__",
    type: "remove",
    content: "",
  } as Message;
  const summary = {
    id: "summary-1",
    type: "human",
    name: "summary",
    content: "summary",
  } as Message;

  expect(
    getSummarizationMiddlewareMessages({
      "DeerFlowSummarizationMiddleware.before_model": {
        messages: [removeAll, summary],
      },
    }),
  ).toEqual([removeAll, summary]);
});

test("getSummarizationMiddlewareMessages matches base LangChain summarization update keys", () => {
  const summary = {
    id: "summary-1",
    type: "human",
    name: "summary",
    content: "summary",
  } as Message;

  expect(
    getSummarizationMiddlewareMessages({
      "SummarizationMiddleware.before_model": {
        messages: [summary],
      },
    }),
  ).toEqual([summary]);
});

test("getSummarizationMiddlewareMessages ignores unrelated suffix-sharing update keys", () => {
  const summary = {
    id: "summary-1",
    type: "human",
    name: "summary",
    content: "summary",
  } as Message;

  expect(
    getSummarizationMiddlewareMessages({
      "OtherSummarizationMiddleware.before_model": {
        messages: [summary],
      },
    }),
  ).toBeUndefined();
});

test("getVisibleOptimisticMessages hides optimistic user input after server human arrives", () => {
  const optimisticHuman = {
    id: "opt-human-1",
    type: "human",
    content: "hello",
  } as Message;

  expect(getVisibleOptimisticMessages([optimisticHuman], 0, 1)).toEqual([]);
});

test("mergeMessages shows server human instead of optimistic duplicate after first response", () => {
  const serverHuman = {
    id: "server-human-1",
    type: "human",
    content: "hello",
  } as Message;
  const optimisticHuman = {
    id: "opt-human-1",
    type: "human",
    content: "hello",
  } as Message;
  const visibleOptimistic = getVisibleOptimisticMessages(
    [optimisticHuman],
    0,
    1,
  );

  expect(mergeMessages([], [serverHuman], visibleOptimistic)).toEqual([
    serverHuman,
  ]);
});

test("mergeMessages keeps optimistic user input before live assistant messages", () => {
  const optimisticHuman = {
    id: "opt-human-1",
    type: "human",
    content: "hello",
  } as Message;
  const liveAi = {
    id: "live-ai-1",
    type: "ai",
    content: "working",
  } as Message;

  expect(mergeMessages([], [liveAi], [optimisticHuman])).toEqual([
    optimisticHuman,
    liveAi,
  ]);
});

test("getVisibleOptimisticMessages keeps optimistic user input until server human arrives", () => {
  const optimisticHuman = {
    id: "opt-human-1",
    type: "human",
    content: "hello",
  } as Message;

  expect(getVisibleOptimisticMessages([optimisticHuman], 0, 0)).toEqual([
    optimisticHuman,
  ]);
});

test("getVisibleOptimisticMessages keeps non-human optimistic status messages", () => {
  const optimisticAi = {
    id: "opt-ai-1",
    type: "ai",
    content: "Uploading files...",
  } as Message;

  expect(getVisibleOptimisticMessages([optimisticAi], 0, 1)).toEqual([
    optimisticAi,
  ]);
});

test("getVisibleOptimisticMessages hides the upload optimistic pair after server human arrives", () => {
  const optimisticHuman = {
    id: "opt-human-1",
    type: "human",
    content: "upload this",
  } as Message;
  const optimisticUploadingAi = {
    id: "opt-ai-uploading",
    type: "ai",
    content: "Uploading files...",
  } as Message;

  expect(
    getVisibleOptimisticMessages(
      [optimisticHuman, optimisticUploadingAi],
      0,
      1,
    ),
  ).toEqual([]);
});

test("completeOptimisticUploadMessages removes uploading placeholder after upload succeeds", () => {
  const optimisticHuman = {
    id: "opt-human-1",
    type: "human",
    content: "upload this",
    additional_kwargs: {
      files: [{ filename: "doc.md", size: 0, status: "uploading" }],
      hide_from_ui: false,
    },
  } as Message;
  const optimisticUploadingAi = {
    id: "opt-ai-uploading",
    type: "ai",
    content: "文件上传中，请稍候...",
    additional_kwargs: {
      element: "task",
      upload_status: "uploading",
    },
  } as Message;

  expect(
    completeOptimisticUploadMessages(
      [optimisticHuman, optimisticUploadingAi],
      [
        {
          filename: "doc.md",
          size: 5400,
          path: "/Users/pingxia/projects/deer-flow/backend/.deer-flow/users/963870b2-72d1-4f61-b0bc-5a46617b16b7/threads/f8c1a12e-9fc1-44d1-8ec7-e266fb337ed0/user-data/uploads/doc.md",
          status: "uploaded",
        },
      ],
    ),
  ).toEqual([
    {
      ...optimisticHuman,
      additional_kwargs: {
        files: [
          {
            filename: "doc.md",
            size: 5400,
            path: "/Users/pingxia/projects/deer-flow/backend/.deer-flow/users/963870b2-72d1-4f61-b0bc-5a46617b16b7/threads/f8c1a12e-9fc1-44d1-8ec7-e266fb337ed0/user-data/uploads/doc.md",
            status: "uploaded",
          },
        ],
        hide_from_ui: false,
      },
    },
  ]);
});

test("getVisibleOptimisticMessages hides optimistic user input after later server turns", () => {
  const optimisticHuman = {
    id: "opt-human-2",
    type: "human",
    content: "follow up",
  } as Message;

  expect(getVisibleOptimisticMessages([optimisticHuman], 3, 4)).toEqual([]);
  expect(getVisibleOptimisticMessages([optimisticHuman], 3, 3)).toEqual([
    optimisticHuman,
  ]);
});

test("runMessagesPageHasMore reads backend snake_case pagination field", () => {
  expect(runMessagesPageHasMore({ data: [], has_more: true })).toBe(true);
  expect(runMessagesPageHasMore({ data: [], has_more: false })).toBe(false);
});

test("runMessagesPageHasMore keeps compatibility with camelCase pagination field", () => {
  expect(runMessagesPageHasMore({ data: [], hasMore: true })).toBe(true);
});

test("getOldestRunMessageSeq returns the cursor for the next older run page", () => {
  expect(
    getOldestRunMessageSeq([runMessage(8), runMessage(9), runMessage(10)]),
  ).toBe(8);
});

test("getOldestRunMessageSeq ignores rows without seq", () => {
  expect(getOldestRunMessageSeq([runMessage()])).toBeNull();
});

test("getNextRunMessagesBeforeSeq keeps runs pending when has_more lacks seq", () => {
  expect(
    getNextRunMessagesBeforeSeq({ data: [runMessage()], has_more: true }),
  ).toBeUndefined();
});

test("getNextRunMessagesBeforeSeq marks runs loaded when no more pages exist", () => {
  expect(
    getNextRunMessagesBeforeSeq({ data: [runMessage()], has_more: false }),
  ).toBeNull();
});

test("applySnapshotRunMessagePageState keeps paged snapshot runs loadable", () => {
  const loadedRunIds = new Set<string>();
  const runBeforeSeq = new Map<string, number>();

  applySnapshotRunMessagePageState(
    [
      {
        run_id: "run-complete",
        data: [runMessage(4)],
        has_more: false,
      },
      {
        run_id: "run-paged",
        data: [runMessage(9), runMessage(7)],
        has_more: true,
      },
      {
        run_id: "run-unknown-page",
        data: [runMessage()],
        has_more: true,
      },
    ],
    loadedRunIds,
    runBeforeSeq,
  );

  expect(loadedRunIds).toEqual(new Set(["run-complete"]));
  expect(runBeforeSeq).toEqual(new Map([["run-paged", 7]]));
});

test("buildRunMessagesUrl encodes path segments and optional before_seq", () => {
  expect(
    buildRunMessagesUrl(
      "https://api.example.test/",
      "thread/with space",
      "run?one",
      18,
    ),
  ).toBe(
    "https://api.example.test/api/threads/thread%2Fwith%20space/runs/run%3Fone/messages?before_seq=18",
  );
});

test("buildRunMessagesUrl omits before_seq when loading the latest page", () => {
  expect(
    buildRunMessagesUrl("https://api.example.test", "thread-1", "run-1"),
  ).toBe("https://api.example.test/api/threads/thread-1/runs/run-1/messages");
});

test("buildRunMessagesUrl returns a relative URL when using the nginx proxy", () => {
  expect(buildRunMessagesUrl("", "thread-1", "run-1", 42)).toBe(
    "/api/threads/thread-1/runs/run-1/messages?before_seq=42",
  );
});

test("buildThreadRuntimeSnapshotUrl encodes the thread id", () => {
  expect(
    buildThreadRuntimeSnapshotUrl(
      "https://api.example.test/",
      "thread/with space",
    ),
  ).toBe(
    "https://api.example.test/api/threads/thread%2Fwith%20space/runtime-snapshot",
  );
  expect(buildThreadRuntimeSnapshotUrl("", "thread-1")).toBe(
    "/api/threads/thread-1/runtime-snapshot",
  );
});

test("taskLaneSubtaskUpdate restores completed task lane state", () => {
  expect(
    taskLaneSubtaskUpdate({
      thread_id: "thread-1",
      run_id: "run-1",
      task_id: "task-1",
      role: "evidence",
      status: "completed",
      result_ref: "artifact://result",
      created_at: "2026-06-18T00:00:00Z",
    }),
  ).toMatchObject({
    id: "task-1",
    threadId: "thread-1",
    runId: "run-1",
    status: "completed",
    subagent_type: "evidence",
    description: "evidence task",
    prompt: "evidence task",
    result: "artifact://result",
    actionResultStatus: "completed",
    notify: true,
  });
});

test("taskLaneSubtaskUpdate maps non-active terminal lanes to failed", () => {
  expect(
    taskLaneSubtaskUpdate({
      thread_id: "thread-1",
      run_id: "run-1",
      task_id: "task-1",
      status: "blocked",
      error: "Boundary stopped task.",
    }),
  ).toMatchObject({
    id: "task-1",
    status: "failed",
    error: "Boundary stopped task.",
    terminalReason: "blocked",
  });
});

test("task event run messages update subtask state without entering visible history", () => {
  const taskEventRow = {
    run_id: "run-1",
    seq: 1,
    content: {
      type: "task_completed",
      task_id: "call-1",
      thread_id: "thread-1",
      run_id: "run-1",
      result: "done",
    },
    metadata: { caller: "task_event" },
    display: {
      visible_in_chat: false,
      surface: "control",
      reason: "task_event",
    },
    created_at: "2026-05-22T00:00:00Z",
  } as unknown as RunMessage;
  const visibleAiRow = {
    run_id: "run-1",
    seq: 2,
    content: {
      id: "ai-1",
      type: "ai",
      content: "visible",
    } as Message,
    metadata: { caller: "lead_agent" },
    created_at: "2026-05-22T00:00:01Z",
  } as RunMessage;
  const updates: unknown[] = [];

  expect(isTaskEventRunMessage(taskEventRow)).toBe(true);
  applyTaskEventRunMessages([taskEventRow], (update) => updates.push(update));

  expect(updates).toEqual([
    {
      id: "call-1",
      threadId: "thread-1",
      runId: "run-1",
      notify: true,
      status: "completed",
      result: "done",
    },
  ]);
  expect(
    buildVisibleHistoryMessages(
      [taskEventRow, visibleAiRow],
      new Set(),
      [],
    ).map((message) => message.id),
  ).toEqual(["ai-1"]);
});

test("mixed task event replay preserves each event identity", () => {
  const rows = [
    {
      run_id: "run-1",
      seq: 1,
      content: {
        type: "task_running",
        task_id: "task-a",
        thread_id: "thread-a",
        run_id: "run-1",
      },
      created_at: "2026-05-22T00:00:00Z",
    },
    {
      run_id: "run-2",
      seq: 1,
      content: {
        type: "task_completed",
        task_id: "task-b",
        thread_id: "thread-b",
        run_id: "run-2",
        result: "done b",
      },
      created_at: "2026-05-22T00:00:01Z",
    },
  ] as unknown as RunMessage[];
  const updates: unknown[] = [];

  applyTaskEventRunMessages(rows, (update) => updates.push(update));

  expect(updates).toEqual([
    {
      id: "task-a",
      threadId: "thread-a",
      runId: "run-1",
      notify: true,
      status: "in_progress",
    },
    {
      id: "task-b",
      threadId: "thread-b",
      runId: "run-2",
      notify: true,
      status: "completed",
      result: "done b",
    },
  ]);
});

test("legacy task event run messages without metadata still restore subtask state", () => {
  const taskEventRow = {
    run_id: "run-legacy",
    seq: 1,
    content: {
      type: "task_completed",
      task_id: "call-legacy",
      thread_id: "thread-1",
      run_id: "run-legacy",
      result: "legacy done",
    },
    created_at: "2026-05-22T00:00:00Z",
  } as unknown as RunMessage;
  const updates: unknown[] = [];

  expect(isTaskEventRunMessage(taskEventRow)).toBe(true);
  applyTaskEventRunMessages([taskEventRow], (update) => updates.push(update));

  expect(updates).toEqual([
    {
      id: "call-legacy",
      threadId: "thread-1",
      runId: "run-legacy",
      notify: true,
      status: "completed",
      result: "legacy done",
    },
  ]);
  expect(buildVisibleHistoryMessages([taskEventRow], new Set(), [])).toEqual(
    [],
  );
});

test("visible history run messages fall back to hiding internal rows without display", () => {
  const middlewareAiRow = {
    run_id: "run-1",
    seq: 1,
    content: {
      id: "ai-middleware-1",
      type: "ai",
      content: "middleware summary",
    } as Message,
    metadata: { caller: "middleware:summarize" },
    created_at: "2026-05-22T00:00:00Z",
  } as RunMessage;
  const middlewareHumanRow = {
    run_id: "run-1",
    seq: 2,
    content: {
      id: "human-middleware-1",
      type: "human",
      content: "middleware title",
    } as Message,
    metadata: { caller: "middleware:title" },
    created_at: "2026-05-22T00:00:01Z",
  } as RunMessage;
  const toolRow = {
    run_id: "run-1",
    seq: 3,
    content: {
      id: "tool-1",
      type: "tool",
      content: "tool output",
      tool_call_id: "call-1",
    } as Message,
    metadata: { caller: "task" },
    created_at: "2026-05-22T00:00:02Z",
  } as RunMessage;
  const taskEventRow = {
    run_id: "run-1",
    seq: 4,
    content: {
      type: "task_running",
      task_id: "call-1",
      thread_id: "thread-1",
      run_id: "run-1",
    },
    metadata: { caller: "task_event" },
    created_at: "2026-05-22T00:00:03Z",
  } as unknown as RunMessage;
  const systemRow = {
    run_id: "run-1",
    seq: 5,
    content: {
      id: "system-1",
      type: "system",
      content: "system prompt",
    } as Message,
    metadata: { caller: "lead_agent" },
    created_at: "2026-05-22T00:00:04Z",
  } as RunMessage;
  const removeRow = {
    run_id: "run-1",
    seq: 6,
    content: {
      id: "__remove_all__",
      type: "remove",
    } as Message,
    metadata: { caller: "lead_agent" },
    created_at: "2026-05-22T00:00:05Z",
  } as RunMessage;
  const hiddenRow = {
    run_id: "run-1",
    seq: 7,
    content: {
      id: "hidden-1",
      type: "ai",
      content: "hidden",
      additional_kwargs: { hide_from_ui: true },
    } as Message,
    metadata: { caller: "middleware:summarize" },
    created_at: "2026-05-22T00:00:06Z",
  } as RunMessage;
  const leadAiRow = {
    run_id: "run-1",
    seq: 8,
    content: {
      id: "ai-lead-1",
      type: "ai",
      content: "lead answer",
    } as Message,
    metadata: { caller: "lead_agent" },
    created_at: "2026-05-22T00:00:07Z",
  } as RunMessage;

  expect(isVisibleHistoryRunMessage(middlewareAiRow)).toBe(false);
  expect(isVisibleHistoryRunMessage(middlewareHumanRow)).toBe(false);
  expect(isVisibleHistoryRunMessage(toolRow)).toBe(false);
  expect(isVisibleHistoryRunMessage(taskEventRow)).toBe(false);
  expect(isVisibleHistoryRunMessage(systemRow)).toBe(false);
  expect(isVisibleHistoryRunMessage(removeRow)).toBe(false);
  expect(isVisibleHistoryRunMessage(hiddenRow)).toBe(false);
  expect(isVisibleHistoryRunMessage(leadAiRow)).toBe(true);
  expect(
    buildVisibleHistoryMessages(
      [
        middlewareAiRow,
        middlewareHumanRow,
        toolRow,
        taskEventRow,
        systemRow,
        removeRow,
        hiddenRow,
        leadAiRow,
      ],
      new Set(),
      [],
    ).map((message) => message.id),
  ).toEqual(["ai-lead-1"]);
});

test("visible history run messages keep middleware rows marked visible by backend contract", () => {
  const summarizeRow = {
    run_id: "run-1",
    seq: 1,
    content: {
      id: "ai-middleware-1",
      type: "ai",
      content: "middleware summary",
    } as Message,
    metadata: { caller: "middleware:summarize" },
    display: {
      visible_in_chat: true,
      surface: "chat",
      reason: "middleware_message",
    },
    created_at: "2026-05-22T00:00:00Z",
  } as RunMessage;
  const titleRow = {
    run_id: "run-1",
    seq: 2,
    content: {
      id: "ai-middleware-2",
      type: "ai",
      content: "middleware title",
    } as Message,
    metadata: { caller: "middleware:title" },
    display: {
      visible_in_chat: true,
      surface: "chat",
      reason: "middleware_message",
    },
    created_at: "2026-05-22T00:00:01Z",
  } as RunMessage;

  expect(
    buildVisibleHistoryMessages([summarizeRow, titleRow], new Set(), []).map(
      (message) => message.id,
    ),
  ).toEqual(["ai-middleware-1", "ai-middleware-2"]);
});

test("visible history run messages honor backend display contract", () => {
  const visibleContentHiddenByContract = {
    run_id: "run-1",
    seq: 1,
    content: {
      id: "ai-1",
      type: "ai",
      content: "normal content",
    } as Message,
    metadata: { caller: "lead_agent" },
    display: {
      visible_in_chat: false,
      surface: "control",
      reason: "control_message",
    },
    created_at: "2026-05-22T00:00:00Z",
  } as RunMessage;

  expect(isVisibleHistoryRunMessage(visibleContentHiddenByContract)).toBe(
    false,
  );
});

test("task event run messages are idempotent and scoped to the requested thread and run", () => {
  const legacyTaskEventRow = {
    run_id: "run-1",
    content: {
      type: "task_completed",
      task_id: "call-legacy",
      thread_id: "thread-1",
      run_id: "run-1",
      result: "legacy done",
    },
    metadata: { caller: "task_event" },
    created_at: "2026-05-22T00:00:01Z",
  } as unknown as RunMessage;
  const crossRunSameTaskRow = {
    ...legacyTaskEventRow,
    run_id: "run-2",
    content: {
      ...legacyTaskEventRow.content,
      run_id: "run-2",
    },
  } as unknown as RunMessage;
  const missingRunIdentityRow = {
    ...legacyTaskEventRow,
    run_id: "run-3",
    content: {
      ...legacyTaskEventRow.content,
      run_id: undefined,
    },
    created_at: "2026-05-22T00:00:02Z",
  } as unknown as RunMessage;
  const taskEventRow = {
    run_id: "run-1",
    seq: 1,
    content: {
      type: "task_running",
      task_id: "call-1",
      thread_id: "thread-1",
      run_id: "run-1",
    },
    metadata: { caller: "task_event" },
    created_at: "2026-05-22T00:00:00Z",
  } as unknown as RunMessage;
  const wrongThreadRow = {
    ...taskEventRow,
    seq: 2,
    content: {
      ...taskEventRow.content,
      thread_id: "thread-2",
    },
  } as unknown as RunMessage;
  const wrongRunRow = {
    ...taskEventRow,
    seq: 3,
    content: {
      ...taskEventRow.content,
      run_id: "run-2",
    },
  } as unknown as RunMessage;
  const appliedKeys = new Set<string>();
  const updates: unknown[] = [];

  expect(taskEventRunMessageKey(taskEventRow)).toBe("run-1:1");
  expect(taskEventRunMessageKey(legacyTaskEventRow)).toBe(
    "run-1:thread-1:call-legacy:task_completed:2026-05-22T00:00:01Z",
  );
  expect(taskEventRunMessageKey(crossRunSameTaskRow)).toBe(
    "run-2:thread-1:call-legacy:task_completed:2026-05-22T00:00:01Z",
  );
  expect(isTaskEventRunMessageForRequest(taskEventRow, "thread-1")).toBe(true);
  expect(isTaskEventRunMessageForRequest(wrongThreadRow, "thread-1")).toBe(
    false,
  );
  expect(isTaskEventRunMessageForRequest(wrongRunRow, "thread-1")).toBe(false);

  applyTaskEventRunMessages(
    [
      taskEventRow,
      taskEventRow,
      wrongThreadRow,
      wrongRunRow,
      legacyTaskEventRow,
      legacyTaskEventRow,
      crossRunSameTaskRow,
      missingRunIdentityRow,
    ],
    (update) => updates.push(update),
    "thread-1",
    appliedKeys,
  );
  applyTaskEventRunMessages(
    [taskEventRow],
    (update) => updates.push(update),
    "thread-1",
    appliedKeys,
  );

  expect(appliedKeys).toEqual(
    new Set([
      "run-1:1",
      "run-1:thread-1:call-legacy:task_completed:2026-05-22T00:00:01Z",
      "run-2:thread-1:call-legacy:task_completed:2026-05-22T00:00:01Z",
    ]),
  );
  expect(updates).toEqual([
    {
      id: "call-1",
      threadId: "thread-1",
      runId: "run-1",
      notify: true,
      status: "in_progress",
    },
    {
      id: "call-legacy",
      threadId: "thread-1",
      runId: "run-1",
      notify: true,
      status: "completed",
      result: "legacy done",
    },
    {
      id: "call-legacy",
      threadId: "thread-1",
      runId: "run-2",
      notify: true,
      status: "completed",
      result: "legacy done",
    },
  ]);
});

test("readRunMessagesPageResponse rejects html without surfacing a JSON SyntaxError", async () => {
  await expect(
    readRunMessagesPageResponse(
      new Response("<html></html>", {
        headers: { "Content-Type": "text/html" },
      }),
    ),
  ).rejects.toThrow("Failed to load thread history.");
});

test("readRunMessagesPageResponse uses backend JSON error detail", async () => {
  await expect(
    readRunMessagesPageResponse(
      new Response(JSON.stringify({ detail: "Thread missing" }), {
        status: 404,
        statusText: "Not Found",
        headers: { "Content-Type": "application/json" },
      }),
    ),
  ).rejects.toThrow("Thread missing");
});

test("isAbortError detects manual request cancellation", () => {
  const controller = new AbortController();
  controller.abort();
  expect(isAbortError(controller.signal.reason)).toBe(true);
  expect(isAbortError(new DOMException("timed out", "TimeoutError"))).toBe(
    false,
  );
});

test("findLatestUnloadedRunIndex loads the newest run first from a newest-first list", () => {
  const runs = [
    { run_id: "R6" },
    { run_id: "R5" },
    { run_id: "R4" },
    { run_id: "R3" },
    { run_id: "R2" },
    { run_id: "R1" },
  ] as unknown as Run[];
  expect(findLatestUnloadedRunIndex(runs, new Set())).toBe(0);
});

test("findLatestUnloadedRunIndex skips already-loaded runs and returns the next newest unloaded run", () => {
  const runs = [
    { run_id: "R6" },
    { run_id: "R5" },
    { run_id: "R4" },
  ] as unknown as Run[];
  expect(findLatestUnloadedRunIndex(runs, new Set(["R6"]))).toBe(1);
});

test("findLatestUnloadedRunIndex returns -1 when every run is already loaded", () => {
  const runs = [{ run_id: "R2" }, { run_id: "R1" }] as unknown as Run[];
  expect(findLatestUnloadedRunIndex(runs, new Set(["R1", "R2"]))).toBe(-1);
});

test("getTerminalTransitionRunIds selects active runs that just reached a terminal status", () => {
  const runs = [
    { run_id: "R6", status: "running" },
    { run_id: "R5", status: "success" },
    { run_id: "R4", status: "pending" },
    { run_id: "R3", status: "error" },
    { run_id: "R2", status: "timeout" },
    { run_id: "R1", status: "rolled_back" },
    { run_id: "R0", status: "worker_lost" },
    { run_id: "R-1", status: "boundary_stopped" },
  ] as unknown as Run[];

  expect(
    getTerminalTransitionRunIds(
      new Set(["R6", "R5", "R3", "R2", "R1", "R0", "R-1"]),
      runs,
    ),
  ).toEqual(["R5", "R3", "R2", "R1", "R0", "R-1"]);
});

test("getLatestRunTerminalNotice reports terminal runs without visible AI replies", () => {
  const runs = [
    {
      run_id: "run-terminal",
      status: "error",
      terminal_reason: "worker_lost",
    },
  ] as unknown as Run[];
  const humanOnlyRows = [
    {
      run_id: "run-terminal",
      seq: 1,
      content: {
        id: "human-1",
        type: "human",
        content: "question",
      } as Message,
      metadata: { caller: "lead_agent" },
      created_at: "2026-05-22T00:00:00Z",
    },
  ] satisfies RunMessage[];
  const aiRows = [
    ...humanOnlyRows,
    {
      run_id: "run-terminal",
      seq: 2,
      content: {
        id: "ai-1",
        type: "ai",
        content: "final",
      } as Message,
      metadata: { caller: "lead_agent" },
      created_at: "2026-05-22T00:00:01Z",
    },
  ] satisfies RunMessage[];

  expect(getLatestRunTerminalNotice(runs, humanOnlyRows)).toEqual({
    runId: "run-terminal",
    status: "error",
    terminalReason: "worker_lost",
    error: undefined,
  });
  expect(getLatestRunTerminalNotice(runs, aiRows)).toBeNull();
  expect(
    getLatestRunTerminalNotice(
      [{ run_id: "run-ok", status: "success" }] as unknown as Run[],
      humanOnlyRows,
    ),
  ).toEqual({
    runId: "run-ok",
    status: "success",
    terminalReason: undefined,
    error: undefined,
  });
});

test("getSupersededRunIds combines completed regenerate metadata with pending ids", () => {
  const runs = [
    {
      run_id: "run-new",
      status: "success",
      metadata: { regenerate_from_run_id: "run-old" },
    },
    {
      run_id: "run-normal",
      status: "success",
      metadata: {},
    },
  ] as unknown as Run[];

  expect(getSupersededRunIds(runs, new Set(["run-pending"]))).toEqual(
    new Set(["run-old", "run-pending"]),
  );
});

test("getSupersededRunIds ignores failed regenerate runs but keeps pending ids", () => {
  const runs = [
    {
      run_id: "run-error",
      status: "error",
      metadata: { regenerate_from_run_id: "run-old" },
    },
    {
      run_id: "run-interrupted",
      status: "interrupted",
      metadata: { regenerate_from_run_id: "run-older" },
    },
  ] as unknown as Run[];

  expect(getSupersededRunIds(runs, new Set(["run-pending"]))).toEqual(
    new Set(["run-pending"]),
  );
});

test("removeSetItems removes pending superseded ids after submit failure", () => {
  expect(
    removeSetItems(new Set(["run-old", "run-other"]), ["run-old"]),
  ).toEqual(new Set(["run-other"]));
});

test("buildVisibleHistoryMessages filters superseded runs but keeps regenerated run", () => {
  const oldHuman = {
    id: "human-1",
    type: "human",
    content: "question",
  } as Message;
  const oldAi = {
    id: "ai-old",
    type: "ai",
    content: "old answer",
  } as Message;
  const newHuman = {
    id: "human-1",
    type: "human",
    content: "question",
  } as Message;
  const newAi = {
    id: "ai-new",
    type: "ai",
    content: "new answer",
  } as Message;
  const rows: RunMessage[] = [
    {
      run_id: "run-old",
      content: oldHuman,
      metadata: { caller: "lead_agent" },
      created_at: "2026-06-18T00:00:00Z",
    },
    {
      run_id: "run-old",
      content: oldAi,
      metadata: { caller: "lead_agent" },
      created_at: "2026-06-18T00:00:01Z",
    },
    {
      run_id: "run-new",
      content: newHuman,
      metadata: { caller: "lead_agent" },
      created_at: "2026-06-18T00:00:02Z",
    },
    {
      run_id: "run-new",
      content: newAi,
      metadata: { caller: "lead_agent" },
      created_at: "2026-06-18T00:00:03Z",
    },
  ];

  expect(buildVisibleHistoryMessages(rows, new Set(["run-old"]), [])).toEqual([
    {
      ...newHuman,
      additional_kwargs: {
        deerflow_run_id: "run-new",
        deerflow_caller: "lead_agent",
        [HISTORY_CREATED_AT_KEY]: "2026-06-18T00:00:02Z",
      },
    },
    {
      ...newAi,
      additional_kwargs: {
        deerflow_run_id: "run-new",
        deerflow_caller: "lead_agent",
        [HISTORY_CREATED_AT_KEY]: "2026-06-18T00:00:03Z",
      },
    },
  ]);
});

test("buildVisibleHistoryMessages preserves same message id across different runs", () => {
  const rows: RunMessage[] = [
    {
      run_id: "run-1",
      seq: 1,
      content: {
        id: "shared-id",
        type: "human",
        content: "first run",
      } as Message,
      metadata: { caller: "lead_agent" },
      created_at: "2026-06-18T00:00:01Z",
    },
    {
      run_id: "run-2",
      seq: 2,
      content: {
        id: "shared-id",
        type: "human",
        content: "second run",
      } as Message,
      metadata: { caller: "lead_agent" },
      created_at: "2026-06-18T00:00:02Z",
    },
  ];

  const history = buildVisibleHistoryMessages(rows, new Set(), []);

  expect(history.map((message) => message.content)).toEqual([
    "first run",
    "second run",
  ]);
  expect(history.map((message) => message.additional_kwargs)).toMatchObject([
    { deerflow_run_id: "run-1", deerflow_run_seq: 1 },
    { deerflow_run_id: "run-2", deerflow_run_seq: 2 },
  ]);
  expect(
    mergeMessages(history, [], []).map((message) => message.content),
  ).toEqual(["first run", "second run"]);
});

test("mergeMessages lets live messages replace overlapping scoped history", () => {
  const rows: RunMessage[] = [
    {
      run_id: "run-latest",
      seq: 1,
      content: {
        id: "same-id",
        type: "human",
        content: "history copy",
      } as Message,
      metadata: { caller: "lead_agent" },
      created_at: "2026-06-18T00:00:01Z",
    },
  ];
  const history = buildVisibleHistoryMessages(rows, new Set(), []);
  const live = {
    id: "same-id",
    type: "human",
    content: "live copy",
  } as Message;

  expect(
    mergeMessages(history, [live], []).map((message) => message.content),
  ).toEqual(["live copy"]);
});

test("mergeMessages replaces same-run history copies when later history rows are not live", () => {
  const rows: RunMessage[] = [
    {
      run_id: "run-latest",
      seq: 1,
      content: {
        id: "human-1",
        type: "human",
        content: "history human",
      } as Message,
      metadata: { caller: "lead_agent" },
      created_at: "2026-06-18T00:00:01Z",
    },
    {
      run_id: "run-latest",
      seq: 2,
      content: {
        id: "ai-1",
        type: "ai",
        content: "history ai",
      } as Message,
      metadata: { caller: "lead_agent" },
      created_at: "2026-06-18T00:00:02Z",
    },
    {
      run_id: "run-latest",
      seq: 3,
      content: {
        id: "suggestion-1",
        type: "ai",
        content: "follow-up suggestion",
      } as Message,
      metadata: { caller: "lead_agent" },
      created_at: "2026-06-18T00:00:03Z",
    },
  ];
  const history = buildVisibleHistoryMessages(rows, new Set(), []);
  const runScopedLiveHuman = {
    id: "human-1",
    type: "human",
    content: "live human",
    additional_kwargs: { run_id: "run-latest" },
  } as Message;
  const runScopedLiveAi = {
    id: "ai-1",
    type: "ai",
    content: "live ai",
    additional_kwargs: { run_id: "run-latest" },
  } as Message;
  const checkpointLiveHuman = {
    id: "human-1",
    type: "human",
    content: "live human",
  } as Message;
  const checkpointLiveAi = {
    id: "ai-1",
    type: "ai",
    content: "live ai",
  } as Message;

  const assertMerged = (threadMessages: Message[]) => {
    const merged = mergeMessages(history, threadMessages, []);

    expect(merged.map((message) => message.content)).toEqual([
      "live human",
      "live ai",
      "follow-up suggestion",
    ]);
    expect(merged[0]?.additional_kwargs?.[HISTORY_CREATED_AT_KEY]).toBe(
      "2026-06-18T00:00:01Z",
    );
  };

  assertMerged([runScopedLiveHuman, runScopedLiveAi]);
  assertMerged([checkpointLiveHuman, checkpointLiveAi]);
});

test("buildVisibleHistoryMessages orders refreshed run rows by seq", () => {
  const rows: RunMessage[] = [
    {
      run_id: "run-1",
      seq: 3,
      content: { id: "ai-2", type: "ai", content: "third" } as Message,
      metadata: { caller: "lead_agent" },
      created_at: "2026-06-18T00:00:03Z",
    },
    {
      run_id: "run-1",
      seq: 1,
      content: { id: "human-1", type: "human", content: "first" } as Message,
      metadata: { caller: "lead_agent" },
      created_at: "2026-06-18T00:00:01Z",
    },
    {
      run_id: "run-1",
      seq: 2,
      content: { id: "ai-1", type: "ai", content: "second" } as Message,
      metadata: { caller: "lead_agent" },
      created_at: "2026-06-18T00:00:02Z",
    },
  ];

  expect(
    buildVisibleHistoryMessages(rows, new Set(), []).map(
      (message) => message.content,
    ),
  ).toEqual(["first", "second", "third"]);
});

test("buildVisibleHistoryMessages groups repeated run-local seq by run order", () => {
  const rows: RunMessage[] = [
    {
      run_id: "run-new",
      seq: 2,
      content: { id: "new-ai", type: "ai", content: "new second" } as Message,
      metadata: { caller: "lead_agent" },
      created_at: "2026-06-18T00:01:02Z",
    },
    {
      run_id: "run-old",
      seq: 2,
      content: { id: "old-ai", type: "ai", content: "old second" } as Message,
      metadata: { caller: "lead_agent" },
      created_at: "2026-06-18T00:00:02Z",
    },
    {
      run_id: "run-new",
      seq: 1,
      content: {
        id: "new-human",
        type: "human",
        content: "new first",
      } as Message,
      metadata: { caller: "lead_agent" },
      created_at: "2026-06-18T00:01:01Z",
    },
    {
      run_id: "run-old",
      seq: 1,
      content: {
        id: "old-human",
        type: "human",
        content: "old first",
      } as Message,
      metadata: { caller: "lead_agent" },
      created_at: "2026-06-18T00:00:01Z",
    },
  ];
  const runs = [
    { run_id: "run-new" },
    { run_id: "run-old" },
  ] as unknown as Run[];

  expect(
    buildVisibleHistoryMessages(rows, new Set(), [], runs).map(
      (message) => message.content,
    ),
  ).toEqual(["old first", "old second", "new first", "new second"]);
});

test("buildVisibleHistoryMessages excludes replayed run terminal lifecycle events", () => {
  const rows: RunMessage[] = [
    {
      run_id: "run-1",
      seq: 1,
      content: { id: "ai-1", type: "ai", content: "done" } as Message,
      metadata: { caller: "lead_agent" },
      created_at: "2026-07-05T00:00:01Z",
    },
    {
      run_id: "run-1",
      seq: 2,
      content: {
        type: "run.terminal",
        event_type: "run.terminal",
        status: "success",
      },
      metadata: { caller: "runtime" },
      created_at: "2026-07-05T00:00:02Z",
    },
  ] satisfies RunMessage[];

  expect(
    buildVisibleHistoryMessages(rows, new Set(), []).map(
      (message) => message.content,
    ),
  ).toEqual(["done"]);
});

test("mergeFetchedRunMessages replaces stale rows for refreshed run first page", () => {
  const previous = [
    {
      run_id: "run-old",
      seq: 1,
      content: { id: "old-ai", type: "ai", content: "stale" } as Message,
      metadata: { caller: "lead_agent" },
      created_at: "2026-06-18T00:00:01Z",
    },
    {
      run_id: "run-new",
      seq: 1,
      content: { id: "new-ai", type: "ai", content: "new" } as Message,
      metadata: { caller: "lead_agent" },
      created_at: "2026-06-18T00:01:01Z",
    },
  ] satisfies RunMessage[];
  const fetched = [
    {
      run_id: "run-old",
      seq: 1,
      content: { id: "old-ai-final", type: "ai", content: "final" } as Message,
      metadata: { caller: "lead_agent" },
      created_at: "2026-06-18T00:00:02Z",
    },
  ] satisfies RunMessage[];
  const runs = [
    { run_id: "run-new" },
    { run_id: "run-old" },
  ] as unknown as Run[];

  const merged = mergeFetchedRunMessages(previous, fetched, "run-old", true);

  expect(
    buildVisibleHistoryMessages(merged, new Set(), [], runs).map(
      (message) => message.content,
    ),
  ).toEqual(["final", "new"]);
});

test("mergeFetchedRunMessages preserves older loaded rows outside a refreshed latest page", () => {
  const previous = [
    {
      run_id: "run-1",
      seq: 1,
      content: {
        id: "ai-older",
        type: "ai",
        content: "older answer",
      } as Message,
      metadata: { caller: "lead_agent" },
      created_at: "2026-06-18T00:00:01Z",
    },
    {
      run_id: "run-1",
      seq: 2,
      content: {
        id: "ai-stale",
        type: "ai",
        content: "stale latest",
      } as Message,
      metadata: { caller: "lead_agent" },
      created_at: "2026-06-18T00:00:02Z",
    },
  ] satisfies RunMessage[];
  const fetched = [
    {
      run_id: "run-1",
      seq: 2,
      content: {
        id: "tool-1",
        type: "tool",
        content: "tool output",
        tool_call_id: "call-1",
      } as Message,
      metadata: { caller: "task" },
      created_at: "2026-06-18T00:00:03Z",
    },
  ] satisfies RunMessage[];

  const merged = mergeFetchedRunMessages(previous, fetched, "run-1", true);

  expect(
    buildVisibleHistoryMessages(merged, new Set(), []).map(
      (message) => message.content,
    ),
  ).toEqual(["older answer"]);
});

test("mergeFetchedRunMessages keeps existing rows when loading an older page", () => {
  const previous = [
    {
      run_id: "run-1",
      seq: 2,
      content: { id: "ai-2", type: "ai", content: "second" } as Message,
      metadata: { caller: "lead_agent" },
      created_at: "2026-06-18T00:00:02Z",
    },
  ] satisfies RunMessage[];
  const fetched = [
    {
      run_id: "run-1",
      seq: 1,
      content: { id: "ai-1", type: "ai", content: "first" } as Message,
      metadata: { caller: "lead_agent" },
      created_at: "2026-06-18T00:00:01Z",
    },
  ] satisfies RunMessage[];

  const merged = mergeFetchedRunMessages(previous, fetched, "run-1", false);

  expect(
    buildVisibleHistoryMessages(merged, new Set(), []).map(
      (message) => message.content,
    ),
  ).toEqual(["first", "second"]);
});

test("partitionKnownRunIds keeps unknown refresh ids pending until run list catches up", () => {
  const runs = [
    { run_id: "run-known" },
    { run_id: "run-other" },
  ] as unknown as Run[];

  expect(partitionKnownRunIds(["run-missing", "run-known"], runs)).toEqual({
    known: ["run-known"],
    pending: ["run-missing"],
  });
  expect(
    partitionKnownRunIds(["run-missing"], [
      { run_id: "run-missing" },
    ] as unknown as Run[]),
  ).toEqual({
    known: ["run-missing"],
    pending: [],
  });
});

test("resetLoadedRunStateForRefresh marks known runs unloaded and keeps unknown runs pending", () => {
  const loadedRunIds = new Set(["run-known", "run-other"]);
  const runBeforeSeq = new Map([
    ["run-known", 42],
    ["run-other", 7],
  ]);
  const runs = [
    { run_id: "run-known" },
    { run_id: "run-other" },
  ] as unknown as Run[];

  expect(
    resetLoadedRunStateForRefresh(
      ["run-missing", "run-known"],
      runs,
      loadedRunIds,
      runBeforeSeq,
    ),
  ).toEqual({ known: ["run-known"], pending: ["run-missing"] });
  expect([...loadedRunIds]).toEqual(["run-other"]);
  expect([...runBeforeSeq.entries()]).toEqual([["run-other", 7]]);
});

test("buildVisibleHistoryMessages preserves run message created_at for elapsed timers", () => {
  const messages = buildVisibleHistoryMessages([runMessage(1)], new Set(), []);

  expect(messages[0]?.additional_kwargs?.[HISTORY_CREATED_AT_KEY]).toBe(
    "2026-05-22T00:00:00Z",
  );
});

test("buildVisibleHistoryMessages tolerates legacy rows without metadata", () => {
  const messages = buildVisibleHistoryMessages(
    [
      {
        run_id: "legacy-run",
        seq: 1,
        content: {
          id: "legacy-ai",
          type: "ai",
          content: "legacy answer",
        } as Message,
        created_at: "2026-06-18T00:00:01Z",
      } as RunMessage,
    ],
    new Set(),
    [],
  );

  expect(messages.map((message) => message.content)).toEqual(["legacy answer"]);
});

test("mergeMessages preserves history created_at when live message replaces history", () => {
  const history = {
    id: "same",
    type: "ai",
    content: "history",
    additional_kwargs: {
      [HISTORY_CREATED_AT_KEY]: "2026-05-22T00:00:00Z",
    },
  } as Message;
  const live = { id: "same", type: "ai", content: "live" } as Message;

  expect(mergeMessages([history], [live], [])[0]?.additional_kwargs).toEqual({
    [HISTORY_CREATED_AT_KEY]: "2026-05-22T00:00:00Z",
  });
});

test("loading runs in newest-first order and prepending pages yields chronological messages (regression for #3352)", () => {
  // Simulate backend list_by_thread returning newest first.
  const runs = [
    { run_id: "R6" },
    { run_id: "R5" },
    { run_id: "R4" },
    { run_id: "R3" },
    { run_id: "R2" },
    { run_id: "R1" },
  ] as unknown as Run[];
  const runIdToContent: Record<string, string> = {
    R1: "A",
    R2: "B",
    R3: "C",
    R4: "D",
    R5: "E",
    R6: "F",
  };

  const loaded = new Set<string>();
  let messages: Message[] = [];

  while (true) {
    const index = findLatestUnloadedRunIndex(runs, loaded);
    if (index === -1) break;
    const run = runs[index]!;
    const pageMessages = [
      {
        id: run.run_id,
        type: "human",
        content: runIdToContent[run.run_id],
      } as Message,
    ];
    // Mirror loadMessages: prepend new page to existing messages.
    messages = [...pageMessages, ...messages];
    loaded.add(run.run_id);
  }

  expect(messages.map((m) => m.content)).toEqual([
    "A",
    "B",
    "C",
    "D",
    "E",
    "F",
  ]);
});

test("shouldAutoContinueOnEmptyRun does not continue when the run produced messages", () => {
  expect(shouldAutoContinueOnEmptyRun(3, 0)).toBe(false);
  expect(shouldAutoContinueOnEmptyRun(1, 4)).toBe(false);
});

test("shouldAutoContinueOnEmptyRun continues when an empty run is below the safety cap", () => {
  expect(shouldAutoContinueOnEmptyRun(0, 0)).toBe(true);
  expect(
    shouldAutoContinueOnEmptyRun(0, MAX_CONSECUTIVE_EMPTY_RUN_LOADS - 1),
  ).toBe(true);
});

test("shouldAutoContinueOnEmptyRun stops once consecutive empty loads reach the cap", () => {
  expect(shouldAutoContinueOnEmptyRun(0, MAX_CONSECUTIVE_EMPTY_RUN_LOADS)).toBe(
    false,
  );
  expect(
    shouldAutoContinueOnEmptyRun(0, MAX_CONSECUTIVE_EMPTY_RUN_LOADS + 1),
  ).toBe(false);
});

test("shouldAutoContinueOnEmptyRun honors a custom safety cap when provided", () => {
  expect(shouldAutoContinueOnEmptyRun(0, 0, 1)).toBe(true);
  expect(shouldAutoContinueOnEmptyRun(0, 1, 1)).toBe(false);
});

test("shouldAutoContinueRunHistory continues across visible runs while older runs remain", () => {
  expect(
    shouldAutoContinueRunHistory({
      hasMoreUnloadedRuns: true,
      visibleMessageCount: 2,
      consecutiveEmptyLoads: 0,
    }),
  ).toBe(true);
});

test("shouldAutoContinueRunHistory stops when no older runs remain", () => {
  expect(
    shouldAutoContinueRunHistory({
      hasMoreUnloadedRuns: false,
      visibleMessageCount: 2,
      consecutiveEmptyLoads: 0,
    }),
  ).toBe(false);
});

test("simulating auto-continue across empty runs skips empty contributions and lands on the next run with content (issue #3352 follow-up)", () => {
  const runs = [
    { run_id: "R6" },
    { run_id: "R5" },
    { run_id: "R4" },
    { run_id: "R3" },
    { run_id: "R2" },
    { run_id: "R1" },
  ] as unknown as Run[];
  const runIdToMessages: Record<string, Message[]> = {
    R6: [{ id: "R6", type: "human", content: "F" } as Message],
    R5: [{ id: "R5", type: "human", content: "E" } as Message],
    R4: [],
    R3: [],
    R2: [],
    R1: [{ id: "R1", type: "human", content: "A" } as Message],
  };

  const loaded = new Set<string>();
  let messages: Message[] = [];

  loaded.add("R6");
  loaded.add("R5");
  messages = [...runIdToMessages.R5!, ...runIdToMessages.R6!];

  let consecutiveEmptyLoads = 0;
  let visited = 0;
  const visitedRunIds: string[] = [];
  while (true) {
    const index = findLatestUnloadedRunIndex(runs, loaded);
    if (index === -1) break;
    const run = runs[index]!;
    visited += 1;
    visitedRunIds.push(run.run_id);
    const pageMessages = runIdToMessages[run.run_id] ?? [];
    messages = [...pageMessages, ...messages];
    loaded.add(run.run_id);
    if (
      !shouldAutoContinueOnEmptyRun(pageMessages.length, consecutiveEmptyLoads)
    ) {
      consecutiveEmptyLoads = 0;
      break;
    }
    consecutiveEmptyLoads += 1;
  }

  expect(visitedRunIds).toEqual(["R4", "R3", "R2", "R1"]);
  expect(visited).toBe(4);
  expect(messages.map((m) => m.content)).toEqual(["A", "E", "F"]);
});

test("shouldAutoContinueOnEmptyRun input must use the post-filter visible count, not the raw page size (middleware-only runs should still trigger auto-continue)", () => {
  const filteredVisibleCount = 0;
  const rawPageSize = 3; // pretend the raw page had 3 middleware-only entries
  expect(shouldAutoContinueOnEmptyRun(filteredVisibleCount, 0)).toBe(true);
  expect(shouldAutoContinueOnEmptyRun(rawPageSize, 0)).toBe(false);
});

test("shouldAutoLoadLatestRun only auto-loads a new latest run", () => {
  expect(shouldAutoLoadLatestRun("run-2", "run-1")).toBe(true);
  expect(shouldAutoLoadLatestRun("run-2", "run-2")).toBe(false);
  expect(shouldAutoLoadLatestRun(null, "run-2")).toBe(false);
});

test("buildVisibleHistoryMessages preserves run origin without overwriting additional kwargs", () => {
  const history = buildVisibleHistoryMessages(
    [
      {
        run_id: "run-with-provider-kwargs",
        seq: 7,
        content: {
          id: "ai-provider",
          type: "ai",
          content: "provider fields stay",
          additional_kwargs: {
            provider_field: "keep-me",
            deerflow_run_id: "provider-owned-value",
          },
        } as Message,
        metadata: { caller: "research_agent" },
        created_at: "2026-06-18T00:00:07Z",
      },
    ],
    new Set(),
    [],
  );

  expect(history[0]?.additional_kwargs).toMatchObject({
    provider_field: "keep-me",
    deerflow_run_id: "run-with-provider-kwargs",
    deerflow_run_seq: 7,
    deerflow_caller: "research_agent",
    [HISTORY_CREATED_AT_KEY]: "2026-06-18T00:00:07Z",
  });
});

test("P2-b offline replay keeps command-room task state run-scoped across interleaved sessions", () => {
  const rooms = Array.from(
    { length: 5 },
    (_, roomIndex) => `command-room-${roomIndex + 1}`,
  );
  const rows: RunMessage[] = [];
  const runs: Run[] = [];
  let tick = 0;

  for (const room of rooms) {
    for (let threadIndex = 1; threadIndex <= 2; threadIndex += 1) {
      const threadId = `${room}-owner-${threadIndex}-thread`;
      for (let round = 1; round <= 2; round += 1) {
        const runId = `${threadId}-round-${round}-run`;
        runs.unshift({ run_id: runId } as unknown as Run);
        rows.push({
          run_id: runId,
          seq: 1,
          content: {
            id: `${runId}-human`,
            type: "human",
            content: `${room} owner ${threadIndex} round ${round}`,
          } as Message,
          metadata: { caller: "lead_agent" },
          created_at: `2026-07-07T00:00:${String(tick++).padStart(2, "0")}Z`,
        });
        const subtaskCount = round === 1 ? 5 : 6;
        for (let subtask = 1; subtask <= subtaskCount; subtask += 1) {
          const sharedTaskId = `shared-task-${subtask}`;
          rows.push({
            run_id: runId,
            seq: 10 + subtask,
            content: {
              type: "task_completed",
              task_id: sharedTaskId,
              thread_id: threadId,
              run_id: runId,
              result: `${runId}:${sharedTaskId}:done`,
            },
            metadata: { caller: "task_event" },
            display: {
              visible_in_chat: false,
              surface: "control",
              reason: "task_event",
            },
            created_at: `2026-07-07T00:00:${String(tick++).padStart(2, "0")}Z`,
          } as unknown as RunMessage);
        }
        rows.push({
          run_id: runId,
          seq: 2,
          content: {
            id: `${runId}-ai`,
            type: "ai",
            content: `${runId} visible answer`,
          } as Message,
          metadata: { caller: "lead_agent" },
          created_at: `2026-07-07T00:00:${String(tick++).padStart(2, "0")}Z`,
        });
      }
    }
  }

  const interleaved = rows.sort((a, b) => {
    const aSeq = typeof a.seq === "number" ? a.seq : 0;
    const bSeq = typeof b.seq === "number" ? b.seq : 0;
    return aSeq - bSeq || a.run_id.localeCompare(b.run_id);
  });
  const targetThread = "command-room-3-owner-2-thread";
  let tasks = {};
  const updates: unknown[] = [];
  applyTaskEventRunMessages(
    interleaved,
    (update) => {
      updates.push(update);
      tasks = applySubtaskUpdateInState(tasks, update);
    },
    targetThread,
  );

  expect(updates).toHaveLength(11);
  expect(
    Object.values(tasks)
      .map((task) => `${task.threadId}:${task.runId}:${task.id}:${task.result}`)
      .sort(),
  ).toEqual(
    [
      ...Array.from({ length: 5 }, (_, i) => {
        const taskId = `shared-task-${i + 1}`;
        const runId = `${targetThread}-round-1-run`;
        return `${targetThread}:${runId}:${taskId}:${runId}:${taskId}:done`;
      }),
      ...Array.from({ length: 6 }, (_, i) => {
        const taskId = `shared-task-${i + 1}`;
        const runId = `${targetThread}-round-2-run`;
        return `${targetThread}:${runId}:${taskId}:${runId}:${taskId}:done`;
      }),
    ].sort(),
  );

  const visible = buildVisibleHistoryMessages(
    interleaved.filter((row) => row.run_id.startsWith(targetThread)),
    new Set(),
    [],
    runs.filter((run) => run.run_id.startsWith(targetThread)),
  );

  expect(visible.map((message) => message.content)).toEqual([
    "command-room-3 owner 2 round 1",
    `${targetThread}-round-1-run visible answer`,
    "command-room-3 owner 2 round 2",
    `${targetThread}-round-2-run visible answer`,
  ]);
  expect(
    visible.every((message) =>
      message.additional_kwargs?.deerflow_run_id?.startsWith(targetThread),
    ),
  ).toBe(true);

  const wrongThreadUpdates: unknown[] = [];
  applyTaskEventRunMessages(
    interleaved.filter((row) => row.run_id.startsWith(targetThread)),
    (update) => wrongThreadUpdates.push(update),
    "command-room-wrong-owner-thread",
  );
  expect(wrongThreadUpdates).toEqual([]);
});
