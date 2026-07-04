import type { Message, Run } from "@langchain/langgraph-sdk";
import { expect, test } from "@rstest/core";

import {
  applyTaskEventRunMessages,
  buildRunMessagesUrl,
  buildVisibleHistoryMessages,
  completeOptimisticUploadMessages,
  findLatestUnloadedRunIndex,
  getNextRunMessagesBeforeSeq,
  getOldestRunMessageSeq,
  getRecentRunIdsForRevalidation,
  getTerminalTransitionRunIds,
  getSupersededRunIds,
  getSummarizationMiddlewareMessages,
  getVisibleOptimisticMessages,
  HISTORY_CREATED_AT_KEY,
  isAbortError,
  isTaskEventRunMessage,
  isTaskEventRunMessageForRequest,
  isVisibleHistoryRunMessage,
  MAX_CONSECUTIVE_EMPTY_RUN_LOADS,
  mergeMessages,
  readRunMessagesPageResponse,
  removeSetItems,
  runMessagesPageHasMore,
  shouldAutoContinueOnEmptyRun,
  shouldAutoLoadLatestRun,
  taskEventRunMessageKey,
} from "@/core/threads/hooks";
import type { RunMessage } from "@/core/threads/types";

function runMessage(seq?: number): RunMessage {
  return {
    run_id: "run-1",
    ...(seq === undefined ? {} : { seq }),
    content: {} as Message,
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
          path: "/mnt/user-data/uploads/doc.md",
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
            path: "/mnt/user-data/uploads/doc.md",
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

test("visible history run messages keep middleware chat but filter task events and hidden UI", () => {
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
  const taskEventRow = {
    run_id: "run-1",
    seq: 3,
    content: {
      type: "task_running",
      task_id: "call-1",
      thread_id: "thread-1",
      run_id: "run-1",
    },
    metadata: { caller: "task_event" },
    created_at: "2026-05-22T00:00:02Z",
  } as unknown as RunMessage;
  const hiddenRow = {
    run_id: "run-1",
    seq: 4,
    content: {
      id: "hidden-1",
      type: "ai",
      content: "hidden",
      additional_kwargs: { hide_from_ui: true },
    } as Message,
    metadata: { caller: "middleware:summarize" },
    created_at: "2026-05-22T00:00:03Z",
  } as RunMessage;

  expect(isVisibleHistoryRunMessage(middlewareAiRow)).toBe(true);
  expect(isVisibleHistoryRunMessage(middlewareHumanRow)).toBe(true);
  expect(isVisibleHistoryRunMessage(taskEventRow)).toBe(false);
  expect(isVisibleHistoryRunMessage(hiddenRow)).toBe(false);
  expect(
    buildVisibleHistoryMessages(
      [middlewareAiRow, middlewareHumanRow, taskEventRow, hiddenRow],
      new Set(),
      [],
    ).map((message) => message.id),
  ).toEqual(["ai-middleware-1", "human-middleware-1"]);
});

test("task event run messages are idempotent and scoped to the requested thread and run", () => {
  const taskEventRow = {
    run_id: "run-1",
    seq: 1,
    content: {
      type: "task_running",
      task_id: "call-1",
      thread_id: "thread-1",
      run_id: "run-1",
      message: { type: "ai", content: "working" },
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
  expect(isTaskEventRunMessageForRequest(taskEventRow, "thread-1")).toBe(true);
  expect(isTaskEventRunMessageForRequest(wrongThreadRow, "thread-1")).toBe(
    false,
  );
  expect(isTaskEventRunMessageForRequest(wrongRunRow, "thread-1")).toBe(false);

  applyTaskEventRunMessages(
    [taskEventRow, taskEventRow, wrongThreadRow, wrongRunRow],
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

  expect(appliedKeys).toEqual(new Set(["run-1:1"]));
  expect(updates).toEqual([
    {
      id: "call-1",
      threadId: "thread-1",
      notify: true,
      status: "in_progress",
      latestMessage: { type: "ai", content: "working" },
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

test("getRecentRunIdsForRevalidation chooses the newest active run by default", () => {
  const runs = [
    { run_id: "R6", status: "success" },
    { run_id: "R5", status: "running" },
    { run_id: "R4", status: "pending" },
  ] as unknown as Run[];

  expect(getRecentRunIdsForRevalidation(runs)).toEqual(["R5"]);
});

test("getRecentRunIdsForRevalidation can revalidate multiple recent runs", () => {
  const runs = [
    { run_id: "R6", status: "running" },
    { run_id: "R5", status: "success" },
    { run_id: "R4", status: "pending" },
  ] as unknown as Run[];

  expect(getRecentRunIdsForRevalidation(runs, 2)).toEqual(["R6", "R4"]);
});

test("getTerminalTransitionRunIds selects active runs that just reached a terminal status", () => {
  const runs = [
    { run_id: "R6", status: "running" },
    { run_id: "R5", status: "success" },
    { run_id: "R4", status: "pending" },
    { run_id: "R3", status: "error" },
  ] as unknown as Run[];

  expect(
    getTerminalTransitionRunIds(new Set(["R6", "R5", "R3"]), runs),
  ).toEqual(["R5", "R3"]);
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
        [HISTORY_CREATED_AT_KEY]: "2026-06-18T00:00:02Z",
      },
    },
    {
      ...newAi,
      additional_kwargs: {
        [HISTORY_CREATED_AT_KEY]: "2026-06-18T00:00:03Z",
      },
    },
  ]);
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

test("buildVisibleHistoryMessages preserves run message created_at for elapsed timers", () => {
  const messages = buildVisibleHistoryMessages([runMessage(1)], new Set(), []);

  expect(messages[0]?.additional_kwargs?.[HISTORY_CREATED_AT_KEY]).toBe(
    "2026-05-22T00:00:00Z",
  );
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
