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
