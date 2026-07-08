import { expect, test } from "@rstest/core";

import {
  applySubtaskUpdateInState,
  getLegacySubtaskStorageKey,
  getSubtaskLookupKeys,
  getSubtaskStorageKey,
  mergeSubtaskUpdate,
  normalizeSubtaskRoundId,
  selectSubtasksForRun,
  selectSubtasksForThread,
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
  const legacyRunKey = JSON.stringify([
    "subtask",
    "thread-1",
    "run-2",
    "task-1",
  ]);
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
    })[0],
  ).toBe(strongKey);
  expect(
    tasks[
      getSubtaskLookupKeys({
        id: "task-1",
        threadId: "thread-1",
        runId: "run-2",
      })[0]!
    ],
  ).toBeUndefined();
  expect(
    getSubtaskLookupKeys({
      id: "task-1",
      threadId: "thread-1",
      runId: "run-2",
    }),
  ).toEqual([strongKey, legacyRunKey, legacyKey, "task-1"]);
});

test("legacy subtask lookup without runId still uses legacy key", () => {
  const legacyKey = getLegacySubtaskStorageKey("task-1", "thread-1");
  expect(getSubtaskLookupKeys({ id: "task-1", threadId: "thread-1" })).toEqual([
    legacyKey,
    "task-1",
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

test("subtask updates with roundId are stored under round-scoped key", () => {
  const tasks = applySubtaskUpdateInState(
    {},
    subtaskFixture({
      id: "task-1",
      threadId: "thread-1",
      runId: "run-1",
      roundId: "round-1",
      status: "in_progress",
    }),
  );

  expect(Object.keys(tasks)).toEqual([
    getSubtaskStorageKey({
      id: "task-1",
      threadId: "thread-1",
      runId: "run-1",
      roundId: "round-1",
    }),
  ]);
});

test("missing roundId uses stable no-round fallback and updates same fallback task", () => {
  const first = applySubtaskUpdateInState(
    {},
    subtaskFixture({
      id: "task-1",
      threadId: "thread-1",
      runId: "run-1",
      status: "in_progress",
    }),
  );
  const key = getSubtaskStorageKey({
    id: "task-1",
    threadId: "thread-1",
    runId: "run-1",
  });
  const second = applySubtaskUpdateInState(
    first,
    subtaskFixture({
      id: "task-1",
      threadId: "thread-1",
      runId: "run-1",
      status: "completed",
      result: "done",
    }),
  );

  expect(key).toContain(normalizeSubtaskRoundId(undefined));
  expect(Object.keys(second)).toEqual([key]);
  expect(second[key]).toMatchObject({ status: "completed", result: "done" });
});

test("same thread and task id stay isolated across different runs", () => {
  const run1Key = getSubtaskStorageKey({
    id: "task-1",
    threadId: "thread-1",
    runId: "run-1",
  });
  const run2Key = getSubtaskStorageKey({
    id: "task-1",
    threadId: "thread-1",
    runId: "run-2",
  });
  const tasks = applySubtaskUpdateInState(
    applySubtaskUpdateInState(
      {},
      subtaskFixture({
        id: "task-1",
        threadId: "thread-1",
        runId: "run-1",
        status: "completed",
        result: "run 1 result",
      }),
    ),
    subtaskFixture({
      id: "task-1",
      threadId: "thread-1",
      runId: "run-2",
      status: "in_progress",
    }),
  );

  expect(tasks[run1Key]).toMatchObject({
    runId: "run-1",
    status: "completed",
    result: "run 1 result",
  });
  expect(tasks[run2Key]).toMatchObject({
    runId: "run-2",
    status: "in_progress",
  });
});

test("same run and task id stay isolated across different rounds", () => {
  const round1Key = getSubtaskStorageKey({
    id: "task-1",
    threadId: "thread-1",
    runId: "run-1",
    roundId: "round-1",
  });
  const round2Key = getSubtaskStorageKey({
    id: "task-1",
    threadId: "thread-1",
    runId: "run-1",
    roundId: "round-2",
  });
  const tasks = applySubtaskUpdateInState(
    applySubtaskUpdateInState(
      {},
      subtaskFixture({
        id: "task-1",
        threadId: "thread-1",
        runId: "run-1",
        roundId: "round-1",
        status: "completed",
        result: "round 1 result",
      }),
    ),
    subtaskFixture({
      id: "task-1",
      threadId: "thread-1",
      runId: "run-1",
      roundId: "round-2",
      status: "in_progress",
    }),
  );

  expect(tasks[round1Key]).toMatchObject({
    roundId: "round-1",
    status: "completed",
    result: "round 1 result",
  });
  expect(tasks[round2Key]).toMatchObject({
    roundId: "round-2",
    status: "in_progress",
  });
});

test("same task id stays isolated across different threads", () => {
  const thread1Key = getSubtaskStorageKey({
    id: "task-1",
    threadId: "thread-1",
    runId: "run-1",
    roundId: "round-1",
  });
  const thread2Key = getSubtaskStorageKey({
    id: "task-1",
    threadId: "thread-2",
    runId: "run-1",
    roundId: "round-1",
  });
  const tasks = applySubtaskUpdateInState(
    applySubtaskUpdateInState(
      {},
      subtaskFixture({
        id: "task-1",
        threadId: "thread-1",
        runId: "run-1",
        roundId: "round-1",
        status: "completed",
        result: "thread 1 result",
      }),
    ),
    subtaskFixture({
      id: "task-1",
      threadId: "thread-2",
      runId: "run-1",
      roundId: "round-1",
      status: "in_progress",
    }),
  );

  expect(tasks[thread1Key]).toMatchObject({
    threadId: "thread-1",
    status: "completed",
  });
  expect(tasks[thread2Key]).toMatchObject({
    threadId: "thread-2",
    status: "in_progress",
  });
});

test("round-scoped update migrates matching legacy run key", () => {
  const legacyRunKey = JSON.stringify([
    "subtask",
    "thread-1",
    "run-1",
    "task-1",
  ]);
  const roundKey = getSubtaskStorageKey({
    id: "task-1",
    threadId: "thread-1",
    runId: "run-1",
    roundId: "round-1",
  });
  const tasks = applySubtaskUpdateInState(
    {
      [legacyRunKey]: subtaskFixture({
        id: "task-1",
        threadId: "thread-1",
        runId: "run-1",
        status: "in_progress",
      }),
    },
    subtaskFixture({
      id: "task-1",
      threadId: "thread-1",
      runId: "run-1",
      roundId: "round-1",
      status: "completed",
      result: "migrated",
    }),
  );

  expect(tasks[legacyRunKey]).toBeUndefined();
  expect(tasks[roundKey]).toMatchObject({
    roundId: "round-1",
    status: "completed",
    result: "migrated",
  });
});

test("round-scoped terminal update migrates matching no-round running task", () => {
  const noRoundKey = getSubtaskStorageKey({
    id: "task-1",
    threadId: "thread-1",
    runId: "run-1",
  });
  const roundKey = getSubtaskStorageKey({
    id: "task-1",
    threadId: "thread-1",
    runId: "run-1",
    roundId: "round-1",
  });
  const running = applySubtaskUpdateInState(
    {},
    subtaskFixture({
      id: "task-1",
      threadId: "thread-1",
      runId: "run-1",
      status: "in_progress",
      startedAt: 1_000,
    }),
  );
  const completed = applySubtaskUpdateInState(
    running,
    subtaskFixture({
      id: "task-1",
      threadId: "thread-1",
      runId: "run-1",
      roundId: "round-1",
      status: "completed",
      result: "done",
    }),
  );

  expect(running[noRoundKey]?.status).toBe("in_progress");
  expect(completed[noRoundKey]).toBeUndefined();
  expect(Object.keys(completed)).toEqual([roundKey]);
  expect(completed[roundKey]).toMatchObject({
    roundId: "round-1",
    status: "completed",
    result: "done",
    startedAt: 1_000,
  });
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

test("settleRunningSubtasksForRun without roundId settles all active subtasks in matching run", () => {
  let tasks: Record<string, Subtask> = {};
  for (const task of [
    subtaskUpdateFixture({
      id: "same-task",
      threadId: "thread-1",
      runId: "run-1",
      roundId: "round-1",
      status: "in_progress",
    }),
    subtaskUpdateFixture({
      id: "same-task",
      threadId: "thread-1",
      runId: "run-1",
      roundId: "round-2",
      status: "in_progress",
    }),
    subtaskUpdateFixture({
      id: "same-task",
      threadId: "thread-1",
      runId: "run-2",
      roundId: "round-1",
      status: "in_progress",
    }),
  ]) {
    tasks = applySubtaskUpdateInState(tasks, task);
  }

  const settled = settleRunningSubtasksForRun(tasks, {
    threadId: "thread-1",
    runId: "run-1",
    status: "interrupted",
  });

  expect(
    selectSubtasksForRun(settled, "thread-1", "run-1")
      .map((task) => [task.roundId, task.status])
      .sort(),
  ).toEqual([
    ["round-1", "failed"],
    ["round-2", "failed"],
  ]);
  expect(selectSubtasksForRun(settled, "thread-1", "run-2")[0]?.status).toBe(
    "in_progress",
  );
});

test("settleRunningSubtasksForRun with roundId settles only matching round", () => {
  let tasks: Record<string, Subtask> = {};
  for (const task of [
    subtaskUpdateFixture({
      id: "same-task",
      threadId: "thread-1",
      runId: "run-1",
      roundId: "round-1",
      status: "in_progress",
    }),
    subtaskUpdateFixture({
      id: "same-task",
      threadId: "thread-1",
      runId: "run-1",
      roundId: "round-2",
      status: "in_progress",
    }),
    subtaskUpdateFixture({
      id: "same-task",
      threadId: "thread-2",
      runId: "run-1",
      roundId: "round-1",
      status: "in_progress",
    }),
  ]) {
    tasks = applySubtaskUpdateInState(tasks, task);
  }

  const settled = settleRunningSubtasksForRun(tasks, {
    threadId: "thread-1",
    runId: "run-1",
    roundId: "round-1",
    status: "error",
  });

  expect(
    selectSubtasksForRun(settled, "thread-1", "run-1")
      .map((task) => [task.roundId, task.status])
      .sort(),
  ).toEqual([
    ["round-1", "failed"],
    ["round-2", "in_progress"],
  ]);
  expect(
    selectSubtasksForRun(settled, "thread-2", "run-1", "round-1")[0]?.status,
  ).toBe("in_progress");
});

test("settleRunningSubtasksForRun with roundId also settles no-round running task", () => {
  const tasks = applySubtaskUpdateInState(
    {},
    subtaskUpdateFixture({
      id: "task-1",
      threadId: "thread-1",
      runId: "run-1",
      status: "in_progress",
    }),
  );

  const settled = settleRunningSubtasksForRun(tasks, {
    threadId: "thread-1",
    runId: "run-1",
    roundId: "round-1",
    status: "error",
  });

  expect(selectSubtasksForRun(settled, "thread-1", "run-1")[0]).toMatchObject({
    status: "failed",
    terminalReason: "error",
  });
});

test("selectSubtasksForRun and selectSubtasksForThread keep round-scoped task identities", () => {
  const makeTask = (
    id: string,
    threadId: string,
    runId: string,
    roundId?: string,
  ) => ({
    id,
    threadId,
    runId,
    ...(roundId ? { roundId } : {}),
    status: "in_progress" as const,
    subagent_type: "researcher",
    description: `${id} description`,
    prompt: `${id} prompt`,
  });
  const tasks = {
    a: makeTask("same-task", "thread-a", "run-a", "round-1"),
    b: makeTask("same-task", "thread-a", "run-a", "round-2"),
    c: makeTask("same-task", "thread-a", "run-b", "round-1"),
    d: makeTask("same-task", "thread-b", "run-a", "round-1"),
  };

  expect(
    selectSubtasksForRun(tasks, "thread-a", "run-a")
      .map((task) => task.roundId)
      .sort(),
  ).toEqual(["round-1", "round-2"]);
  expect(
    selectSubtasksForRun(tasks, "thread-a", "run-a", "round-2").map(
      (task) => task.roundId,
    ),
  ).toEqual(["round-2"]);
  expect(
    selectSubtasksForThread(tasks, "thread-a")
      .map((task) => `${task.runId}:${task.roundId}`)
      .sort(),
  ).toEqual(["run-a:round-1", "run-a:round-2", "run-b:round-1"]);
  expect(selectSubtasksForRun(tasks, "thread-a", undefined)).toEqual([]);
});
