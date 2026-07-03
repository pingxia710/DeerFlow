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

const TASK_EVENT_CONTRACT: TaskEventContract = JSON.parse(
  readFileSync(
    resolve(__dirname, "../../../../../contracts/task_event_contract.json"),
    "utf-8",
  ),
) as TaskEventContract;

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
    });
    if (isCompleted) {
      expect(updates[0]).toMatchObject({ result: "contract summary" });
    } else {
      expect(updates[0]).toMatchObject({
        error: `${c.terminal_reason} detail`,
      });
    }
  });
}

test("applyTaskEventToSubtask fails safe for unknown terminal task event enums", async () => {
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
  ).toBe(true);

  expect(updates).toEqual([
    {
      id: "task-unknown",
      threadId: "thread-1",
      notify: true,
      status: "failed",
      error: "Unknown task event terminal status: renamed_terminal",
    },
  ]);
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
