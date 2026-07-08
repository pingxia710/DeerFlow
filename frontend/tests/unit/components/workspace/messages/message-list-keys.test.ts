import { expect, test } from "@rstest/core";

import {
  getMessageGroupKey,
  getSubtaskCardKey,
  hasTerminalSubtaskForTask,
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

test("getSubtaskCardKey separates same run and taskId across different roundIds", () => {
  expect(getSubtaskCardKey("task-1", "run-1", "round-1")).not.toBe(
    getSubtaskCardKey("task-1", "run-1", "round-2"),
  );
});

test("getSubtaskCardKey is stable for same taskId and same runId", () => {
  expect(getSubtaskCardKey("task-1", "run-1")).toBe(
    getSubtaskCardKey("task-1", "run-1"),
  );
});

test("getSubtaskCardKey uses stable no-round fallback for missing roundId", () => {
  expect(getSubtaskCardKey("task-1", "run-1")).toBe(
    getSubtaskCardKey("task-1", "run-1", null),
  );
});

test("getSubtaskCardKey keeps legacy key when runId is missing", () => {
  expect(getSubtaskCardKey("task-1")).toBe("task-group-task-1");
  expect(getSubtaskCardKey("task-1", null)).toBe("task-group-task-1");
});

test("terminal task suppresses stale inferred running replay from another run", () => {
  expect(
    hasTerminalSubtaskForTask(
      [
        {
          id: "task-1",
          threadId: "thread-1",
          runId: "run-completed",
          status: "completed",
          subagent_type: "test",
          description: "done",
          prompt: "do it",
        },
      ],
      {
        threadId: "thread-1",
        runId: "run-replay",
        taskId: "task-1",
      },
    ),
  ).toBe(true);
});

test("terminal task does not suppress same-run or different-round cards", () => {
  const terminal = {
    id: "task-1",
    threadId: "thread-1",
    runId: "run-1",
    roundId: "round-1",
    status: "completed" as const,
    subagent_type: "test",
    description: "done",
    prompt: "do it",
  };

  expect(
    hasTerminalSubtaskForTask([terminal], {
      threadId: "thread-1",
      runId: "run-1",
      taskId: "task-1",
      roundId: "round-1",
    }),
  ).toBe(false);
  expect(
    hasTerminalSubtaskForTask([terminal], {
      threadId: "thread-1",
      runId: "run-2",
      taskId: "task-1",
      roundId: "round-2",
    }),
  ).toBe(false);
});
