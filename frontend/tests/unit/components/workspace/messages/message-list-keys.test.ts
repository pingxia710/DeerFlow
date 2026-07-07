import { expect, test } from "@rstest/core";

import {
  getMessageGroupKey,
  getSubtaskCardKey,
} from "@/components/workspace/messages/message-list";

test("getMessageGroupKey uses stable type-id key without index when id exists", () => {
  expect(getMessageGroupKey({ type: "assistant", id: "group-1" }, 42)).toBe(
    "assistant-group-1",
  );
});

test("getMessageGroupKey falls back to index and type when id is missing", () => {
  expect(getMessageGroupKey({ type: "assistant:subagent" }, 3)).toBe(
    "fallback-3-assistant:subagent",
  );
});

test("getSubtaskCardKey separates same taskId across different runIds", () => {
  expect(getSubtaskCardKey("task-1", "run-1")).not.toBe(
    getSubtaskCardKey("task-1", "run-2"),
  );
});

test("getSubtaskCardKey is stable for same taskId and same runId", () => {
  expect(getSubtaskCardKey("task-1", "run-1")).toBe(
    getSubtaskCardKey("task-1", "run-1"),
  );
});

test("getSubtaskCardKey keeps legacy key when runId is missing", () => {
  expect(getSubtaskCardKey("task-1")).toBe("task-group-task-1");
  expect(getSubtaskCardKey("task-1", null)).toBe("task-group-task-1");
});
