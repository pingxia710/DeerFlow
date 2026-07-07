import { expect, test } from "@rstest/core";

import { mergeSubtaskUpdate } from "@/core/tasks/context";

test("mergeSubtaskUpdate keeps earliest startedAt and latest finishedAt on out-of-order updates", () => {
  const completed = mergeSubtaskUpdate(undefined, {
    id: "task-1",
    status: "completed",
    startedAt: 200,
    finishedAt: 500,
    durationMs: 300,
  });

  const replayedRunning = mergeSubtaskUpdate(completed, {
    id: "task-1",
    status: "in_progress",
    startedAt: 100,
  });

  expect(replayedRunning).toMatchObject({
    status: "completed",
    startedAt: 100,
    finishedAt: 500,
    durationMs: 300,
  });
});

test("mergeSubtaskUpdate ignores weaker terminal timing while accepting stronger duration", () => {
  const previous = mergeSubtaskUpdate(undefined, {
    id: "task-1",
    status: "completed",
    startedAt: 100,
    finishedAt: 500,
    durationMs: 300,
  });

  const next = mergeSubtaskUpdate(previous, {
    id: "task-1",
    status: "completed",
    finishedAt: 400,
    durationMs: 450,
  });

  expect(next).toMatchObject({
    startedAt: 100,
    finishedAt: 500,
    durationMs: 450,
  });
});
