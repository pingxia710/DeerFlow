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
