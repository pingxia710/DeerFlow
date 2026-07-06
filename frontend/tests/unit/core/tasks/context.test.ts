import { expect, test } from "@rstest/core";

test("getSubtaskStorageKey includes run scope", async () => {
  const { getSubtaskStorageKey } = await import("@/core/tasks/context");

  expect(
    getSubtaskStorageKey({
      id: "task-1",
      threadId: "thread-a",
      runId: "run-a",
    }),
  ).not.toBe(
    getSubtaskStorageKey({
      id: "task-1",
      threadId: "thread-a",
      runId: "run-b",
    }),
  );
  expect(
    getSubtaskStorageKey({
      id: "task-1",
      threadId: "thread-a",
      runId: "run-a",
    }),
  ).toBe(
    getSubtaskStorageKey({
      id: "task-1",
      threadId: "thread-a",
      runId: "run-a",
    }),
  );
});

test("getSubtaskStorageKey preserves the legacy unscoped key", async () => {
  const { getLegacySubtaskStorageKey, getSubtaskStorageKey } =
    await import("@/core/tasks/context");

  expect(getSubtaskStorageKey("task-1", null)).toBe("task-1");
  expect(getSubtaskStorageKey("task-1", undefined)).toBe("task-1");
  expect(getSubtaskStorageKey({ id: "task-1", threadId: "thread-a" })).toBe(
    getLegacySubtaskStorageKey("task-1", "thread-a"),
  );
});

test("run-scoped subtask updates do not overwrite the same task id in another run", async () => {
  const { getSubtaskStorageKey, mergeSubtaskUpdate } =
    await import("@/core/tasks/context");
  const runAKey = getSubtaskStorageKey({
    id: "task-1",
    threadId: "thread-a",
    runId: "run-a",
  });
  const runBKey = getSubtaskStorageKey({
    id: "task-1",
    threadId: "thread-a",
    runId: "run-b",
  });
  const tasks = {
    [runAKey]: mergeSubtaskUpdate(undefined, {
      id: "task-1",
      threadId: "thread-a",
      runId: "run-a",
      status: "in_progress",
      subagent_type: "executor",
      description: "run A",
      prompt: "work A",
    }),
    [runBKey]: mergeSubtaskUpdate(undefined, {
      id: "task-1",
      threadId: "thread-a",
      runId: "run-b",
      status: "in_progress",
      subagent_type: "executor",
      description: "run B",
      prompt: "work B",
    }),
  };

  const updated = {
    ...tasks,
    [runAKey]: mergeSubtaskUpdate(tasks[runAKey], {
      id: "task-1",
      threadId: "thread-a",
      runId: "run-a",
      status: "completed",
      result: "done A",
    }),
  };

  expect(updated[runAKey]).toMatchObject({
    runId: "run-a",
    status: "completed",
    result: "done A",
  });
  expect(updated[runBKey]).toMatchObject({
    runId: "run-b",
    status: "in_progress",
    description: "run B",
  });
});

test("legacy subtask updates do not overwrite existing run-scoped tasks", async () => {
  const { getSubtaskStorageKey, mergeSubtaskUpdate } =
    await import("@/core/tasks/context");
  const runKey = getSubtaskStorageKey({
    id: "task-1",
    threadId: "thread-a",
    runId: "run-a",
  });
  const legacyKey = getSubtaskStorageKey({
    id: "task-1",
    threadId: "thread-a",
  });
  const tasks = {
    [runKey]: mergeSubtaskUpdate(undefined, {
      id: "task-1",
      threadId: "thread-a",
      runId: "run-a",
      status: "completed",
      result: "scoped result",
    }),
  };
  const updated = {
    ...tasks,
    [legacyKey]: mergeSubtaskUpdate(tasks[legacyKey], {
      id: "task-1",
      threadId: "thread-a",
      status: "failed",
      error: "legacy failure",
    }),
  };

  expect(updated[runKey]).toMatchObject({
    runId: "run-a",
    status: "completed",
    result: "scoped result",
  });
  expect(updated[legacyKey]).toMatchObject({
    status: "failed",
    error: "legacy failure",
  });
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
      id: "task-active",
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
  const {
    clearSubtasksForThreadInState,
    getLegacySubtaskStorageKey,
    getSubtaskStorageKey,
  } = await import("@/core/tasks/context");
  const targetRunAKey = getSubtaskStorageKey({
    id: "task-target-a",
    threadId: "thread-a",
    runId: "run-a",
  });
  const targetRunBKey = getSubtaskStorageKey({
    id: "task-target-b",
    threadId: "thread-a",
    runId: "run-b",
  });
  const legacyTargetKey = getLegacySubtaskStorageKey("task-legacy", "thread-a");
  const otherThreadKey = getSubtaskStorageKey({
    id: "task-other",
    threadId: "thread-b",
    runId: "run-b",
  });
  const tasks = {
    [targetRunAKey]: {
      id: "task-target-a",
      threadId: "thread-a",
      runId: "run-a",
      subagent_type: "executor",
      description: "target A",
      prompt: "work",
      status: "in_progress" as const,
    },
    [targetRunBKey]: {
      id: "task-target-b",
      threadId: "thread-a",
      runId: "run-b",
      subagent_type: "executor",
      description: "target B",
      prompt: "work",
      status: "in_progress" as const,
    },
    [legacyTargetKey]: {
      id: "task-legacy",
      subagent_type: "executor",
      description: "legacy",
      prompt: "work",
      status: "in_progress" as const,
    },
    [otherThreadKey]: {
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
    [otherThreadKey]: tasks[otherThreadKey],
    unscoped: tasks.unscoped,
  });
  expect(clearSubtasksForThreadInState(tasks, "missing-thread")).toBe(tasks);
});
