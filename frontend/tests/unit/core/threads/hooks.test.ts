import { expect, test } from "@rstest/core";

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
