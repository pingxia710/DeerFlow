import { expect, test } from "@rstest/core";

import {
  applySubtaskUpdateInState,
  getLegacySubtaskStorageKey,
  getSubtaskLookupKeys,
  getSubtaskStorageKey,
  mergeSubtaskUpdate,
  settleRunningSubtasksForRun,
  type SubtaskUpdate,
} from "@/core/tasks/context";
import type { Subtask } from "@/core/tasks/types";

function subtaskFixture(
  overrides: Partial<Subtask> & Pick<Subtask, "id" | "status">,
): Subtask {
  return {
    subagent_type: "test-subagent",
    description: "Test subtask",
    prompt: "Test prompt",
    ...overrides,
  };
}

function subtaskUpdateFixture(
  overrides: SubtaskUpdate & Pick<SubtaskUpdate, "id" | "status">,
): SubtaskUpdate {
  return {
    subagent_type: "test-subagent",
    description: "Test subtask",
    prompt: "Test prompt",
    ...overrides,
  };
}

test("strong subtask lookup with runId does not fall back to legacy state", () => {
  const legacyKey = getLegacySubtaskStorageKey("task-1", "thread-1");
  const strongKey = getSubtaskStorageKey({
    id: "task-1",
    threadId: "thread-1",
    runId: "run-2",
  });
  const tasks = {
    [legacyKey]: subtaskFixture({
      id: "task-1",
      threadId: "thread-1",
      runId: "run-1",
      status: "completed",
    }),
  };

  expect(
    getSubtaskLookupKeys({
      id: "task-1",
      threadId: "thread-1",
      runId: "run-2",
    }),
  ).toEqual([strongKey]);
  expect(
    tasks[
      getSubtaskLookupKeys({
        id: "task-1",
        threadId: "thread-1",
        runId: "run-2",
      })[0]!
    ],
  ).toBeUndefined();
});

test("legacy subtask lookup without runId still uses legacy key", () => {
  const legacyKey = getLegacySubtaskStorageKey("task-1", "thread-1");
  expect(getSubtaskLookupKeys({ id: "task-1", threadId: "thread-1" })).toEqual([
    legacyKey,
  ]);
});

test("subtask updates with runId are stored under strong run-scoped key", () => {
  const tasks = applySubtaskUpdateInState(
    {},
    subtaskFixture({
      id: "task-1",
      threadId: "thread-1",
      runId: "run-2",
      status: "in_progress",
    }),
  );

  expect(Object.keys(tasks)).toEqual([
    getSubtaskStorageKey({
      id: "task-1",
      threadId: "thread-1",
      runId: "run-2",
    }),
  ]);
});

test("legacy update without runId updates unique existing run-scoped task without creating legacy duplicate", () => {
  const strongKey = getSubtaskStorageKey({
    id: "task-1",
    threadId: "thread-1",
    runId: "run-1",
  });
  const legacyKey = getLegacySubtaskStorageKey("task-1", "thread-1");
  const tasks = applySubtaskUpdateInState(
    {
      [strongKey]: subtaskFixture({
        id: "task-1",
        threadId: "thread-1",
        runId: "run-1",
        status: "in_progress",
      }),
    },
    subtaskUpdateFixture({
      id: "task-1",
      threadId: "thread-1",
      status: "completed",
    }),
  );

  expect(tasks[strongKey]).toMatchObject({
    id: "task-1",
    threadId: "thread-1",
    runId: "run-1",
    status: "completed",
  });
  expect(tasks[legacyKey]).toBeUndefined();
});

test("legacy update without runId does not choose among multiple matching run-scoped tasks", () => {
  const strongKey1 = getSubtaskStorageKey({
    id: "task-1",
    threadId: "thread-1",
    runId: "run-1",
  });
  const strongKey2 = getSubtaskStorageKey({
    id: "task-1",
    threadId: "thread-1",
    runId: "run-2",
  });
  const legacyKey = getLegacySubtaskStorageKey("task-1", "thread-1");
  const tasks = applySubtaskUpdateInState(
    {
      [strongKey1]: subtaskFixture({
        id: "task-1",
        threadId: "thread-1",
        runId: "run-1",
        status: "in_progress",
      }),
      [strongKey2]: subtaskFixture({
        id: "task-1",
        threadId: "thread-1",
        runId: "run-2",
        status: "in_progress",
      }),
    },
    subtaskUpdateFixture({
      id: "task-1",
      threadId: "thread-1",
      status: "completed",
    }),
  );

  expect(tasks[strongKey1]?.status).toBe("in_progress");
  expect(tasks[strongKey2]?.status).toBe("in_progress");
  expect(tasks[legacyKey]).toMatchObject({
    id: "task-1",
    threadId: "thread-1",
    status: "completed",
  });
});

test("legacy-only update still uses legacy key", () => {
  const legacyKey = getLegacySubtaskStorageKey("task-1", "thread-1");
  const tasks = applySubtaskUpdateInState(
    {},
    subtaskUpdateFixture({
      id: "task-1",
      threadId: "thread-1",
      status: "in_progress",
    }),
  );

  expect(Object.keys(tasks)).toEqual([legacyKey]);
  expect(tasks[legacyKey]).toMatchObject({
    id: "task-1",
    threadId: "thread-1",
    status: "in_progress",
  });
});

test("later running update does not overwrite completed or terminal failed subtask", () => {
  expect(
    mergeSubtaskUpdate(
      subtaskFixture({
        id: "task-1",
        threadId: "thread-1",
        runId: "run-1",
        status: "completed",
      }),
      subtaskUpdateFixture({
        id: "task-1",
        threadId: "thread-1",
        runId: "run-1",
        status: "in_progress",
      }),
    ).status,
  ).toBe("completed");

  expect(
    mergeSubtaskUpdate(
      subtaskFixture({
        id: "task-1",
        threadId: "thread-1",
        runId: "run-1",
        status: "failed",
        terminalReason: "error",
      }),
      subtaskUpdateFixture({
        id: "task-1",
        threadId: "thread-1",
        runId: "run-1",
        status: "in_progress",
      }),
    ).status,
  ).toBe("failed");
});

test("mergeSubtaskUpdate preserves strong timing fields from weaker later updates", () => {
  const previous = mergeSubtaskUpdate(
    undefined,
    subtaskUpdateFixture({
      id: "task-timing",
      threadId: "thread-1",
      runId: "run-1",
      status: "completed",
      startedAt: 1_000,
      finishedAt: 4_000,
      durationMs: 3_000,
    }),
  );

  const next = mergeSubtaskUpdate(
    previous,
    subtaskUpdateFixture({
      id: "task-timing",
      threadId: "thread-1",
      runId: "run-1",
      status: "completed",
    }),
    10_000,
  );

  expect(next.startedAt).toBe(1_000);
  expect(next.finishedAt).toBe(4_000);
  expect(next.durationMs).toBe(3_000);
});

test("running subtask with historical startedAt keeps elapsed anchored after refresh", () => {
  const refreshed = mergeSubtaskUpdate(
    undefined,
    subtaskUpdateFixture({
      id: "task-running",
      threadId: "thread-1",
      runId: "run-1",
      status: "in_progress",
      startedAt: 1_000,
    }),
    61_000,
  );

  expect(refreshed.startedAt).toBe(1_000);
  expect(Math.floor((61_000 - refreshed.startedAt!) / 1_000)).toBe(60);
});

test("settleRunningSubtasksForRun only fails matching thread and run tasks", () => {
  let tasks: Record<string, Subtask> = {};
  for (const task of [
    subtaskUpdateFixture({
      id: "target",
      threadId: "thread-1",
      runId: "run-1",
      status: "in_progress",
    }),
    subtaskUpdateFixture({
      id: "same-thread-other-run",
      threadId: "thread-1",
      runId: "run-2",
      status: "in_progress",
    }),
    subtaskUpdateFixture({
      id: "same-run-other-thread",
      threadId: "thread-2",
      runId: "run-1",
      status: "in_progress",
    }),
    subtaskUpdateFixture({
      id: "already-done",
      threadId: "thread-1",
      runId: "run-1",
      status: "completed",
    }),
  ]) {
    tasks = applySubtaskUpdateInState(tasks, task);
  }

  const settled = settleRunningSubtasksForRun(tasks, {
    threadId: "thread-1",
    runId: "run-1",
    status: "timeout",
    terminalReason: "timed_out",
  });

  expect(
    Object.values(settled).find((task) => task.id === "target"),
  ).toMatchObject({
    status: "failed",
    actionResultStatus: "timeout",
    terminalReason: "timed_out",
  });
  expect(
    Object.values(settled).find((task) => task.id === "same-thread-other-run")
      ?.status,
  ).toBe("in_progress");
  expect(
    Object.values(settled).find((task) => task.id === "same-run-other-thread")
      ?.status,
  ).toBe("in_progress");
  expect(
    Object.values(settled).find((task) => task.id === "already-done")?.status,
  ).toBe("completed");
});
