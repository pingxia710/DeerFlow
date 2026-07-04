import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { expect, test } from "@rstest/core";

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

test("resolveAssistantId uses the custom agent name when present", async () => {
  const { resolveAssistantId } = await import("@/core/threads/hooks");

  expect(resolveAssistantId("command-room")).toBe("command-room");
});

test("resolveAssistantId falls back to the default lead agent", async () => {
  const { resolveAssistantId } = await import("@/core/threads/hooks");

  expect(resolveAssistantId(undefined)).toBe("lead_agent");
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

test("shouldReleaseQueuedThreadMessage waits for explicit stream completion", async () => {
  const { shouldReleaseQueuedThreadMessage } =
    await import("@/core/threads/hooks");

  const base = {
    isLoading: false,
    sendInFlight: false,
    queuedThreadId: "thread-a",
    currentViewThreadId: "thread-a",
  };

  expect(
    shouldReleaseQueuedThreadMessage({ ...base, streamFinished: false }),
  ).toBe(false);
  expect(
    shouldReleaseQueuedThreadMessage({ ...base, streamFinished: true }),
  ).toBe(true);
});

test("keepQueuedMessagesForThread drops queued sends from other chats", async () => {
  const { keepQueuedMessagesForThread } = await import("@/core/threads/hooks");

  expect(
    keepQueuedMessagesForThread(
      [
        { threadId: "thread-a", text: "from a" },
        { threadId: "thread-b", text: "from b" },
      ],
      "thread-b",
    ),
  ).toEqual([{ threadId: "thread-b", text: "from b" }]);
  expect(keepQueuedMessagesForThread([{ threadId: "thread-a" }], null)).toEqual(
    [],
  );
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
      notify: true,
      status: "in_progress",
    },
  ]);
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
