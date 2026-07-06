import { expect, test } from "@rstest/core";

test("getSubtaskStorageKey isolates the same task id across threads", async () => {
  const { getSubtaskStorageKey } = await import("@/core/tasks/context");

  expect(getSubtaskStorageKey("task-1", "thread-a")).not.toBe(
    getSubtaskStorageKey("task-1", "thread-b"),
  );
  expect(getSubtaskStorageKey("task-1", "thread-a")).toBe(
    getSubtaskStorageKey("task-1", "thread-a"),
  );
});

test("getSubtaskStorageKey preserves the legacy unscoped key", async () => {
  const { getSubtaskStorageKey } = await import("@/core/tasks/context");

  expect(getSubtaskStorageKey("task-1", null)).toBe("task-1");
  expect(getSubtaskStorageKey("task-1", undefined)).toBe("task-1");
});

test("mergeSubtaskUpdate lets running evidence recover a stale failed task", async () => {
  const { mergeSubtaskUpdate } = await import("@/core/tasks/context");

  expect(
    mergeSubtaskUpdate(
      {
        id: "task-1",
        threadId: "thread-a",
        subagent_type: "executor",
        description: "check",
        prompt: "check",
        status: "failed",
        error: "old inferred failure",
      },
      {
        id: "task-1",
        threadId: "thread-a",
        status: "in_progress",
      },
      123,
    ),
  ).toMatchObject({
    id: "task-1",
    threadId: "thread-a",
    status: "in_progress",
    startedAt: 123,
    error: undefined,
  });
});

test("mergeSubtaskUpdate keeps completed tasks terminal on later running events", async () => {
  const { mergeSubtaskUpdate } = await import("@/core/tasks/context");

  expect(
    mergeSubtaskUpdate(
      {
        id: "task-1",
        threadId: "thread-a",
        subagent_type: "executor",
        description: "check",
        prompt: "check",
        status: "completed",
        result: "done",
      },
      {
        id: "task-1",
        threadId: "thread-a",
        status: "in_progress",
      },
      123,
    ),
  ).toMatchObject({
    id: "task-1",
    threadId: "thread-a",
    status: "completed",
    result: "done",
  });
});

test("mergeSubtaskUpdate does not invent volatile startedAt timestamps", async () => {
  const { mergeSubtaskUpdate } = await import("@/core/tasks/context");

  expect(
    mergeSubtaskUpdate(undefined, {
      id: "task-1",
      threadId: "thread-a",
      status: "in_progress",
    }),
  ).not.toHaveProperty("startedAt");
});

test("didSubtaskChange ignores repeated equivalent updates", async () => {
  const { didSubtaskChange } = await import("@/core/tasks/context");
  const task = {
    id: "task-1",
    threadId: "thread-a",
    subagent_type: "executor",
    description: "check",
    prompt: "check",
    status: "completed" as const,
    result: "done",
  };

  expect(didSubtaskChange(task, { ...task })).toBe(false);
  expect(didSubtaskChange(task, { ...task, result: "new result" })).toBe(true);
});

test("mergeSubtaskUpdate does not persist notification metadata", async () => {
  const { mergeSubtaskUpdate } = await import("@/core/tasks/context");

  expect(
    mergeSubtaskUpdate(undefined, {
      id: "task-1",
      threadId: "thread-a",
      notify: true,
      status: "completed",
    }),
  ).not.toHaveProperty("notify");
});

test("settleRunningSubtasksForRun marks same-run active task cards failed on timeout", async () => {
  const { settleRunningSubtasksForRun } = await import("@/core/tasks/context");

  const tasks = {
    active: {
      id: "task-active",
      threadId: "thread-a",
      runId: "run-a",
      subagent_type: "executor",
      description: "still running",
      prompt: "work",
      status: "in_progress" as const,
    },
    completed: {
      id: "task-completed",
      threadId: "thread-a",
      runId: "run-a",
      subagent_type: "executor",
      description: "done",
      prompt: "work",
      status: "completed" as const,
      result: "done",
    },
    otherRun: {
      id: "task-other-run",
      threadId: "thread-a",
      runId: "run-b",
      subagent_type: "executor",
      description: "other run",
      prompt: "work",
      status: "in_progress" as const,
    },
    otherThread: {
      id: "task-other-thread",
      threadId: "thread-b",
      runId: "run-a",
      subagent_type: "executor",
      description: "other thread",
      prompt: "work",
      status: "in_progress" as const,
    },
  };

  const settled = settleRunningSubtasksForRun(tasks, {
    threadId: "thread-a",
    runId: "run-a",
    status: "timeout",
    terminalReason: "timeout",
  });

  expect(settled.active).toMatchObject({
    status: "failed",
    actionResultStatus: "timeout",
    terminalReason: "timeout",
    error: "Parent run timed out before this subtask completed.",
  });
  expect(settled).toMatchObject({
    completed: { status: "completed" },
    otherRun: { status: "in_progress" },
    otherThread: { status: "in_progress" },
  });
});

test("clearSubtasksForThreadInState removes only target-thread tasks", async () => {
  const { clearSubtasksForThreadInState } =
    await import("@/core/tasks/context");
  const tasks = {
    target: {
      id: "task-target",
      threadId: "thread-a",
      runId: "run-a",
      subagent_type: "executor",
      description: "target",
      prompt: "work",
      status: "in_progress" as const,
    },
    otherThread: {
      id: "task-other",
      threadId: "thread-b",
      runId: "run-b",
      subagent_type: "executor",
      description: "other",
      prompt: "work",
      status: "in_progress" as const,
    },
    unscoped: {
      id: "task-unscoped",
      runId: "run-a",
      subagent_type: "executor",
      description: "legacy",
      prompt: "work",
      status: "in_progress" as const,
    },
  };

  expect(clearSubtasksForThreadInState(tasks, "thread-a")).toEqual({
    otherThread: tasks.otherThread,
    unscoped: tasks.unscoped,
  });
  expect(clearSubtasksForThreadInState(tasks, "missing-thread")).toBe(tasks);
});
