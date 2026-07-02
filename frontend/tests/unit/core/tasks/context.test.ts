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
